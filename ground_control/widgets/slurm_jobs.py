"""Widget that displays a set of monitored Slurm jobs and their live status.

One job is one row, buttons included -- the same shape as the GPU process list,
and for the same reason: the row is where you decide something about that job, so
the controls belong on it rather than in a separate menu.

Layout follows the GPU process table too: fixed-width columns so values line up
down the list, the job name taking whatever is left, and columns dropped
lowest-priority-first when the panel narrows. What survives to the narrowest
panel is the job id, its state and its time usage -- the three facts that decide
whether a job needs attention.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Static

from ..utils.colors import get_rich_color
from ..utils.formatting import align
from ..utils.slurm import (format_duration, format_size, is_running_state,
                           is_terminal_state, parse_duration, parse_size)
from .base import SWAP_ARMED_MARKER, gauge_bar, set_swap_armed_style


# Map common Slurm states to a colour + glyph for quick visual scanning, plus the
# short code shown in the ST column (squeue's own abbreviations).
_STATE_STYLE = {
    "RUNNING": ("green", "●", "R"),
    "PENDING": ("yellow", "○", "PD"),
    "COMPLETING": ("cyan", "◐", "CG"),
    "CONFIGURING": ("cyan", "◐", "CF"),
    "SUSPENDED": ("magenta", "‖", "S"),
    "COMPLETED": ("blue", "✓", "CD"),
    "CANCELLED": ("red", "✗", "CA"),
    "FAILED": ("red", "✗", "F"),
    "TIMEOUT": ("red", "⏱", "TO"),
    "NODE_FAIL": ("red", "✗", "NF"),
    "OUT_OF_MEMORY": ("red", "✗", "OOM"),
    "PREEMPTED": ("red", "✗", "PR"),
}

# (key, header, width, align, priority) -- lower priority drops first.
# Job id and state have no priority: a row you cannot identify, or whose state
# you cannot see, is not worth a row.
JOB_COLUMNS = (
    ("jobid",    "JOBID", 10, "left",  None),
    ("state",    "ST",     3, "left",  None),
    ("time",     "TIME",  16, "left",  6),
    ("nodelist", "NODE",  12, "left",  4),
    ("cpus",     "CPU",    4, "right", 2),
    ("mem",      "MEM",    6, "right", 3),
    ("gpus",     "GPU",    3, "right", 5),
    ("partition", "PART", 10, "left",  1),
)
COL_GAP = 1
# Narrower than this and the job name is unreadable, so it is dropped instead.
NAME_MIN_WIDTH = 8
# One button plus its margin, three times over (Focus / Output / Cancel): the
# strip the buttons occupy, which the header must reserve so its columns line up.
JOB_BUTTON_WIDTH = 3
JOB_BUTTONS = 3
JOB_BUTTON_STRIP = JOB_BUTTONS * (JOB_BUTTON_WIDTH + 1)
# States in which a job has not run yet, and so has written nothing. Every other
# state -- running, suspended, completing, finished -- has output worth reading;
# a job that failed is exactly when its log matters most.
NOT_STARTED_STATES = frozenset({"PENDING", "PD", "CONFIGURING", "CF", ""})

# Below this the time gauge is noise rather than information.
TIME_BAR_MIN_WIDTH = 10
# A job past this fraction of its time limit is close enough to being killed
# that the gauge says so in the warning colour.
TIME_WARN_FRACTION = 0.9


def _state_style(state: str):
    return _STATE_STYLE.get((state or "").upper(), ("white", "•", "?"))


def _fmt(value: str, dash: str = "—") -> str:
    """Display value or a dash placeholder for empties / N/A."""
    v = (value or "").strip()
    if not v or v.upper() in ("N/A", "(NULL)", "NONE", "0:00", "UNKNOWN"):
        return dash
    return v


def _job_value(job: dict, key: str, width: int | None = None) -> str:
    """Display string for one column of a job row.

    ``width`` lets a column offer a shorter form instead of being truncated: a
    time reading clipped mid-number ("15:10:00/1-00:0") is worse than one that
    drops the part the detail line repeats anyway.
    """
    if key == "jobid":
        return str(job.get("jobid") or "?")
    if key == "state":
        return _state_style(job.get("state", ""))[2]
    if key == "time":
        elapsed = parse_duration(job.get("elapsed"))
        limit = parse_duration(job.get("timelimit"))
        if elapsed is None:
            return "—"
        used = format_duration(elapsed)
        # Unlimited (or unreported) time limit: show the elapsed time alone
        # rather than implying a bound that does not exist.
        if not limit:
            return used
        both = f"{used}/{format_duration(limit)}"
        return both if width is None or len(both) <= width else used
    if key == "nodelist":
        return _fmt(job.get("nodelist", ""))
    if key == "cpus":
        return _fmt(job.get("cpus", ""))
    if key == "mem":
        size = parse_size(job.get("mem"))
        return format_size(size) if size is not None else _fmt(job.get("mem", ""))
    if key == "gpus":
        return _fmt(job.get("gpus", ""), "0")
    if key == "partition":
        return _fmt(job.get("partition", ""))
    return ""


def format_job_line(job: dict, width: int, header: bool = False) -> str:
    """Render one job (or the header) as exactly ``width`` cells of markup.

    Always returns ``width`` cells, since the row sits beside a fixed-width
    button strip and a short line would let the background show through.
    """
    width = int(width)
    if width <= 0:
        return ""

    def fixed_width(cols):
        return sum(w for _, _, w, _, _ in cols) + COL_GAP * len(cols)

    columns = list(JOB_COLUMNS)
    # 1. Drop optional columns, cheapest first, until the name has room.
    while fixed_width(columns) + NAME_MIN_WIDTH > width:
        droppable = [c for c in columns if c[4] is not None]
        if not droppable:
            break
        columns.remove(min(droppable, key=lambda c: c[4]))

    # 2. On a very narrow panel even JOBID + ST overflow. Squeeze from the right
    #    before giving up, so the row never bleeds into the buttons.
    overflow = fixed_width(columns) - width
    index = len(columns) - 1
    while overflow > 0 and index >= 0:
        key, label, col_width, alignment, priority = columns[index]
        take = min(overflow, max(0, col_width - 2))
        if take:
            columns[index] = (key, label, col_width - take, alignment, priority)
            overflow -= take
        index -= 1
    # 3. Still too narrow: drop from the right, JOBID last.
    while columns and fixed_width(columns) > width:
        columns.pop()

    name_width = width - fixed_width(columns)
    if name_width < NAME_MIN_WIDTH:
        name_width = 0

    accent = get_rich_color("accent", "#00afaf")
    state_color = _state_style(job.get("state", ""))[0]

    parts = []
    for key, label, col_width, alignment, _ in columns:
        text = label if header else _job_value(job, key, col_width)
        text = align(text[:col_width], col_width, alignment)
        if header:
            parts.append(f"[bold]{text}[/]")
        elif key == "jobid":
            parts.append(f"[bold]{text}[/]")
        elif key == "state":
            parts.append(f"[{state_color}]{text}[/]")
        elif key in ("cpus", "mem", "gpus", "partition"):
            parts.append(f"[dim]{text}[/]")
        else:
            parts.append(text)

    if name_width:
        raw = "NAME" if header else str(job.get("name") or "—")
        if len(raw) > name_width:
            raw = raw[: max(0, name_width - 1)] + "…"
        raw = align(raw, name_width, "left")
        parts.append(f"[bold]{raw}[/]" if header else f"[{accent}]{raw}[/]")

    line = (" " * COL_GAP).join(parts)
    plain_len = 0
    if parts:
        plain_len = (sum(w for _, _, w, _, _ in columns) + name_width
                     + COL_GAP * (len(parts) - 1))
    if plain_len < width:
        line += " " * (width - plain_len)
    return line


def _plausible_cpu_seconds(job: dict):
    """sstat's AveCPU in seconds, or None when the value cannot be real.

    Freshly started steps make ``sstat`` report an uninitialised counter --
    observed as ``213503982334-14:25:51``, which is 2^63 nanoseconds. Printing
    that verbatim is worse than printing nothing, so it is checked against the
    ceiling physics allows: CPU time cannot exceed wall time times the number of
    CPUs the job holds (doubled, to leave room for accounting slack).
    """
    seconds = parse_duration(job.get("live_cpu"))
    if seconds is None or seconds < 0:
        return None
    elapsed = parse_duration(job.get("elapsed"))
    try:
        cpus = int(str(job.get("cpus") or "").strip() or 0)
    except ValueError:
        cpus = 0
    if elapsed is not None and cpus > 0:
        return seconds if seconds <= elapsed * cpus * 2 + 60 else None
    # Without an allocation to compare against, fall back to a blunt ceiling:
    # no site runs a year-long job step.
    return seconds if seconds <= 365 * 86400 else None


def format_job_detail(job: dict, width: int) -> str:
    """The second line for one job: time gauge plus live usage, or ``""``.

    The gauge is drawn for *time*, not for CPU or memory, because time is the one
    quantity here with an unambiguous denominator -- the job's own limit -- and
    the one whose exhaustion kills the job. sstat's usage figures are per-step
    maxima with no comparable ceiling, so they are reported as values.
    """
    width = int(width)
    if width <= 0:
        return ""

    elapsed = parse_duration(job.get("elapsed"))
    limit = parse_duration(job.get("timelimit"))
    segments: list[tuple[str, int]] = []  # (markup, plain length)

    # Only a job that is actually consuming its allocation has time to show. A
    # pending job's limit has not started counting, so an empty gauge reading
    # "0% used" would suggest it had.
    if not (job.get("state") or "").upper().startswith(("RUN", "SUSP", "COMPL")):
        elapsed = None

    if elapsed is not None and limit:
        fraction = min(elapsed / limit, 1.0) if limit > 0 else 0.0
        remaining = max(limit - elapsed, 0)
        percent = f"{fraction * 100:.0f}%"
        left = format_duration(remaining)
        color = get_rich_color(
            "alert_warn" if fraction >= TIME_WARN_FRACTION else "accent",
            "#ffaf00" if fraction >= TIME_WARN_FRACTION else "#00afaf")

        def with_bar(label):
            bar_width = min(20, width - len(label) - 2)
            if bar_width < TIME_BAR_MIN_WIDTH:
                return None
            bar = gauge_bar(bar_width, fraction, color, track_color="#444444")
            return f"{bar} [dim]{label}[/]", bar_width + 1 + len(label)

        # Richest form that fits, rather than all-or-nothing: on a narrow panel a
        # bare "17%" still answers "how close is this job to being killed", which
        # is the question the line exists for.
        long_label = f"{percent} used · {left} left"
        # Note the order: the *numbers* outrank the gauge. Once both no longer
        # fit, "how much time is left" is worth more than a bar of the same fact.
        for candidate in (
            lambda: with_bar(long_label),
            lambda: (f"[dim]{long_label}[/]", len(long_label)),
            lambda: with_bar(percent),
            lambda: (f"[dim]{percent} · {left} left[/]", len(percent) + len(left) + 8),
            lambda: (f"[dim]{percent} used[/]", len(percent) + 5),
            lambda: (f"[dim]{percent}[/]", len(percent)),
        ):
            segment = candidate()
            if segment is not None and segment[1] <= width:
                segments.append(segment)
                break

    live = []
    cpu_seconds = _plausible_cpu_seconds(job)
    if cpu_seconds is not None:
        live.append(f"cpu-time {format_duration(cpu_seconds)}")
    rss = parse_size(job.get("live_rss"))
    if rss is not None:
        live.append(f"peak RSS {format_size(rss)}")
    if _fmt(job.get("live_tasks", "")) != "—":
        live.append(f"{job['live_tasks']} tasks")
    if live:
        text = " · ".join(live)
        segments.append((f"[dim]{text}[/]", len(text)))

    # Append segments while they fit; drop from the right otherwise, so a narrow
    # panel keeps the time gauge (the actionable half) over the usage figures.
    out, used = [], 0
    for markup, length in segments:
        extra = length + (3 if out else 0)
        if used + extra > width:
            break
        out.append(markup)
        used += extra
    return "   ".join(out)


class JobRow(Static):
    """One job on one row: table columns plus its Focus / Output / Cancel buttons.

    The buttons are real ``Button`` widgets squeezed to a single cell high, so
    they keep focus, hover and keyboard activation -- the same treatment the GPU
    process rows get.

    Which buttons are live follows what the job's state actually allows, and the
    three differ: Focus needs a running allocation to sample; Output needs only
    that the job has *started*, since a finished job's log is exactly the one you
    want; Cancel stays live for a queued job (that is when cancelling is cheapest)
    and goes dead only once the job reaches a state it cannot leave.

    Cancel is armed rather than immediate. A stray click on a signal button costs
    one process; a stray click here throws away hours of queueing and whatever
    the job had computed, so the first press turns the button red and the second
    (within :attr:`ARM_SECONDS`) actually calls ``scancel``. That keeps the
    control on one line -- no modal -- without making job loss a single keypress.
    """

    ARM_SECONDS = 4.0

    class CancelJob(Message):
        """Posted when the user confirms cancellation of ``jobid``."""

        def __init__(self, jobid: str) -> None:
            super().__init__()
            self.jobid = jobid

    class FocusJob(Message):
        """Posted when the user asks to focus the dashboard on ``jobid``."""

        def __init__(self, jobid: str) -> None:
            super().__init__()
            self.jobid = jobid

    class ShowOutput(Message):
        """Posted when the user asks to read ``jobid``'s stdout/stderr."""

        def __init__(self, jobid: str) -> None:
            super().__init__()
            self.jobid = jobid

    DEFAULT_CSS = """
    JobRow {
        height: 1;
        min-height: 1;
        padding: 0 1;
    }
    JobRow Horizontal {
        height: 1;
    }
    JobRow .job-info {
        width: 1fr;
        min-width: 0;
        height: 1;
        overflow: hidden hidden;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    /* Buttons default to a 3-row bordered box; strip all of it so the whole
       row is one cell high. */
    JobRow .job-btn {
        width: 3;
        min-width: 3;
        height: 1;
        min-height: 1;
        border: none;
        padding: 0;
        margin: 0 0 0 1;
        text-style: bold;
    }
    JobRow .focus-btn  { background: #005f87; color: white; }
    JobRow .output-btn { background: #4e4e8a; color: white; }
    JobRow .cancel-btn { background: #874000; color: white; }
    JobRow .cancel-btn.-armed { background: #c00; color: white; }
    /* A disabled button keeps its cell -- the columns above it still have to
       line up -- but stops looking like something to press. */
    JobRow .job-btn:disabled { background: #303030; color: #707070; text-style: none; }
    """

    def __init__(self, job: dict, focused: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._job = dict(job)
        self._focused_job = focused
        self._armed = False
        self._disarm_timer = None

    @property
    def jobid(self) -> str:
        return str(self._job.get("jobid") or "")

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", classes="job-info")
            for label, cls in (("F", "focus-btn"), ("O", "output-btn"),
                               ("C", "cancel-btn")):
                button = Button(label, classes=f"job-btn {cls}")
                # Textual ignores a click while the press animation is running,
                # which would silently swallow the second half of the two-press
                # confirmation. These buttons are one cell tall with no border, so
                # the animation is invisible anyway -- drop it.
                button.active_effect_duration = 0
                yield button

    def on_mount(self) -> None:
        self._render_info()
        self._refresh_buttons()

    def on_resize(self, event) -> None:
        # Column set depends on the width available, so re-render on every
        # resize rather than formatting once at compose time.
        self._render_info()

    def update_job(self, job: dict, focused: bool = False) -> None:
        """Refresh this row's values in place (no remount, so state survives)."""
        self._job = dict(job)
        self._focused_job = focused
        self._render_info()
        self._refresh_buttons()

    @property
    def running(self) -> bool:
        return is_running_state(self._job.get("state"))

    @property
    def started(self) -> bool:
        """True once the job has run at all — including after it has finished."""
        return (self._job.get("state") or "").strip().upper() not in NOT_STARTED_STATES

    def _render_info(self) -> None:
        try:
            info = self.query_one(".job-info", Static)
        except NoMatches:
            return
        width = info.content_size.width or (self.content_size.width - JOB_BUTTON_STRIP)
        info.update(format_job_line(self._job, max(0, width)))

    def _refresh_buttons(self) -> None:
        """Re-derive each button's enabled state and tooltip from the job.

        Called on every in-place update, because a job crosses from PENDING to
        RUNNING under a row that is never remounted -- so the row that came up
        with Focus greyed out has to light it up by itself.
        """
        jid = self.jobid or "?"
        try:
            focus_btn = self.query_one(".focus-btn", Button)
            output_btn = self.query_one(".output-btn", Button)
            cancel_btn = self.query_one(".cancel-btn", Button)
        except NoMatches:
            return
        running = self.running
        state = (self._job.get("state") or "").upper() or "this state"

        focus_btn.disabled = not running
        focus_btn.tooltip = (
            f"Only a running job can be focused (job {jid} is {state})" if not running
            else f"Already focused on job {jid}" if self._focused_job
            else f"Focus the dashboard on job {jid} (sample it on its own node)"
        )

        # Output outlives the job: a log is most worth reading once the job has
        # stopped, so this is gated on "has it started", not "is it running".
        output_btn.disabled = not self.started
        output_btn.tooltip = (
            f"Job {jid} has not started, so it has written no output yet"
            if output_btn.disabled else f"Read job {jid}'s stdout / stderr"
        )

        # A finished job has nothing left to cancel; a queued one very much does.
        cancel_btn.disabled = is_terminal_state(self._job.get("state"))
        if cancel_btn.disabled and self._armed:
            self._disarm()
        cancel_btn.tooltip = (
            f"Job {jid} has already ended ({state})" if cancel_btn.disabled
            else f"Press again to cancel job {jid}" if self._armed
            else f"scancel job {jid} — asks for confirmation"
        )

    @on(Button.Pressed, ".focus-btn")
    def _focus_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.jobid:
            self.post_message(self.FocusJob(self.jobid))

    @on(Button.Pressed, ".output-btn")
    def _output_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.jobid:
            self.post_message(self.ShowOutput(self.jobid))

    @on(Button.Pressed, ".cancel-btn")
    def _cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if not self.jobid:
            return
        if self._armed:
            self._disarm()
            self.post_message(self.CancelJob(self.jobid))
            return
        self._arm()

    def _arm(self) -> None:
        self._armed = True
        try:
            button = self.query_one(".cancel-btn", Button)
            button.add_class("-armed")
            button.label = "!"
        except NoMatches:
            pass
        self._refresh_buttons()
        # Auto-disarm: an armed destructive button left sitting there is a trap.
        self._disarm_timer = self.set_timer(self.ARM_SECONDS, self._disarm)

    def _disarm(self) -> None:
        if self._disarm_timer is not None:
            self._disarm_timer.stop()
            self._disarm_timer = None
        if not self._armed:
            return
        self._armed = False
        try:
            button = self.query_one(".cancel-btn", Button)
            button.remove_class("-armed")
            button.label = "C"
        except NoMatches:
            pass
        self._refresh_buttons()


class JobEntry(Vertical):
    """One job's row plus its detail line, kept together so both update as one."""

    DEFAULT_CSS = """
    JobEntry {
        height: auto;
        margin-bottom: 1;
    }
    JobEntry .job-detail {
        height: auto;
        padding: 0 1 0 2;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, job: dict, focused: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._job = dict(job)
        self._focused_job = focused

    @property
    def jobid(self) -> str:
        return str(self._job.get("jobid") or "")

    def compose(self) -> ComposeResult:
        yield JobRow(self._job, focused=self._focused_job)
        yield Static("", classes="job-detail", markup=True)

    def on_mount(self) -> None:
        self._render_detail()

    def on_resize(self, event) -> None:
        self._render_detail()

    def update_job(self, job: dict, focused: bool = False) -> None:
        self._job = dict(job)
        self._focused_job = focused
        try:
            self.query_one(JobRow).update_job(job, focused=focused)
        except NoMatches:
            pass
        self._render_detail()

    def _render_detail(self) -> None:
        try:
            detail = self.query_one(".job-detail", Static)
        except NoMatches:
            return
        width = detail.content_size.width or max(0, self.content_size.width - 3)
        lines = []
        body = format_job_detail(self._job, width)
        if body:
            lines.append(body)
        state = (self._job.get("state") or "").upper()
        reason = _fmt(self._job.get("reason", ""))
        # A reason is only news when the job is not running: for a running job
        # Slurm leaves it as "None" and the row already says RUNNING.
        if reason != "—" and not state.startswith("RUN"):
            lines.append(f"[yellow]{state or 'state?'}: {reason}[/]")
        detail.update("\n".join(lines))


def _clear(container) -> None:
    """Remove a container's children, one at a time.

    Matches how the GPU process list rebuilds its rows. The length check in
    :meth:`SlurmJobsWidget._sync_rows` is the safety net: removal and mounting are
    both deferred by Textual, so a rebuild that raced the previous one heals on
    the next tick instead of leaving duplicate rows.
    """
    for child in list(container.children):
        child.remove()


class SlurmJobsWidget(VerticalScroll):
    """Table of the user's Slurm jobs, one row each, with per-row buttons.

    Integrates with the dashboard grid like any other metric widget: it exposes
    ``title`` / ``border_title`` and an ``update_content``-style API
    (:meth:`update_jobs`). It does not plot.

    Everything the user has in the queue is listed, running and pending alike --
    there is nothing to pick and no mode to be in. A pending job is exactly what
    a job list is for on a busy cluster ("is it my turn yet, and why not"), and
    its row already carries the reason.

    Rows are rebuilt only when the *set* of jobs changes; otherwise values are
    updated in place, so a half-armed Cancel button and keyboard focus survive
    the refresh tick.
    """

    #: Rows mounted at once. A queue of several hundred jobs is a real thing on a
    #: shared cluster, and mounting a widget per job would cost more than the
    #: information is worth; the overflow is counted in a footer instead.
    MAX_ROWS = 50

    can_focus = True

    BINDINGS = [
        ("x", "hide_widget", "Hide"),
    ]

    DEFAULT_CSS = """
    SlurmJobsWidget {
        /* Job list may be longer than the panel (scrolls vertically), but rows are
           clipped rather than wrapped so nothing spills past the border. */
        overflow-y: auto;
        overflow-x: hidden;
    }
    SlurmJobsWidget > #slurm-jobs-note {
        width: 1fr;
        height: auto;
        padding: 0 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    SlurmJobsWidget > #slurm-jobs-header {
        height: 1;
        padding: 0 1;
    }
    SlurmJobsWidget #slurm-jobs-header Horizontal {
        height: 1;
    }
    SlurmJobsWidget .header-cols {
        width: 1fr;
        min-width: 0;
        height: 1;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    /* Same width as a row's button strip, so the headers sit over their buttons
       and the F/O/C letters get labelled. */
    SlurmJobsWidget .header-buttons {
        width: 12;
        min-width: 12;
        height: 1;
        text-align: left;
    }
    SlurmJobsWidget > #slurm-jobs-rows {
        width: 1fr;
        height: auto;
    }
    SlurmJobsWidget > #slurm-jobs-more {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    """

    def action_hide_widget(self) -> None:
        """Hide this panel from the dashboard (re-enable from Settings)."""
        try:
            self.app._hide_widget(self)
        except Exception:
            pass

    def __init__(self, title: str = "Slurm Jobs", id: str = None):
        super().__init__(id=id)
        self.title = title
        self.border_title = title
        self._note: str | None = "Looking for your jobs…"
        self._jobs: list = []
        self._focused_jobid: str | None = None
        self._row_ids: list[str] = []
        # Mirrors MetricWidget.set_swap_armed -- this panel is not one, but the
        # dashboard swap feature treats every grid child the same way.
        self._swap_armed = False

    def set_swap_armed(self, armed: bool) -> None:
        armed = bool(armed)
        if armed == self._swap_armed:
            return
        self._swap_armed = armed
        set_swap_armed_style(self, armed)
        self.border_title = f"{SWAP_ARMED_MARKER}{self.title}" if armed else self.title

    def compose(self) -> ComposeResult:
        yield Static("", id="slurm-jobs-note", markup=True)
        with Static(id="slurm-jobs-header"):
            with Horizontal():
                yield Static("", classes="header-cols")
                # Padded so each letter sits over its own button: one leading
                # margin cell, then three cells per button with the label centred.
                yield Static("[bold]  F   O   C[/]", classes="header-buttons")
        yield Vertical(id="slurm-jobs-rows")
        yield Static("", id="slurm-jobs-more", markup=True)

    def on_mount(self) -> None:
        self._render_jobs()

    def on_resize(self, event) -> None:
        self._render_header()

    def update_jobs(self, jobs: list | None, note: str | None = None,
                    focused_jobid: str | None = None) -> None:
        """Replace the displayed jobs.

        Args:
            jobs: list of normalized job dicts (see SlurmMonitor._collect).
            note: optional status line shown when there is nothing to display
                (e.g. "Slurm not available").
            focused_jobid: the job the dashboard is currently focused on, marked
                so the panel and the panels around it agree on what is shown.
        """
        self._jobs = list(jobs or [])
        self._note = note
        self._focused_jobid = focused_jobid
        self._render_jobs()

    # -- rendering ---------------------------------------------------------- #
    # Named _render_jobs, not _render: Widget._render is Textual's own private
    # hook for producing a widget's visual, and overriding it renders nothing.
    def _render_jobs(self) -> None:
        try:
            note = self.query_one("#slurm-jobs-note", Static)
            header = self.query_one("#slurm-jobs-header", Static)
            container = self.query_one("#slurm-jobs-rows", Vertical)
            more = self.query_one("#slurm-jobs-more", Static)
        except NoMatches:
            return  # not composed yet; on_mount will render

        if not self._jobs:
            note.update(f"[dim]{self._note or 'No jobs.'}[/]")
            note.display = True
            header.display = False
            more.display = False
            if self._row_ids:
                self._row_ids = []
                _clear(container)
            return

        note.display = False
        note.update("")
        header.display = True
        self._render_header()
        self._sync_rows(container)
        # Truncation is stated rather than silent: a list that stops at 50 without
        # saying so reads as "these are all my jobs".
        hidden = max(0, len(self._jobs) - self.MAX_ROWS)
        more.display = bool(hidden)
        if hidden:
            more.update(f"[dim]+{hidden} more job{'s' if hidden > 1 else ''} "
                        f"not shown (squeue --me lists them all)[/]")

    def _render_header(self) -> None:
        try:
            cols = self.query_one(".header-cols", Static)
        except NoMatches:
            return
        width = cols.content_size.width or max(
            0, self.content_size.width - JOB_BUTTON_STRIP - 2)
        cols.update(format_job_line({}, max(0, width), header=True))

    def _sync_rows(self, container: Vertical) -> None:
        """Update existing rows in place; remount only if the job set changed."""
        shown = self._jobs[: self.MAX_ROWS]
        ids = [str(job.get("jobid") or "") for job in shown]
        entries = list(container.query(JobEntry).results(JobEntry))
        if ids != self._row_ids or len(entries) != len(ids):
            self._row_ids = ids
            _clear(container)
            container.mount_all([
                JobEntry(job, focused=str(job.get("jobid")) == self._focused_jobid)
                for job in shown
            ])
            return
        for entry, job in zip(entries, shown):
            entry.update_job(job,
                             focused=str(job.get("jobid")) == self._focused_jobid)

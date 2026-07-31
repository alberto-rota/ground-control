"""Widget that displays a set of monitored Slurm jobs and their live status."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from ..utils.colors import get_rich_color


# Map common Slurm states to a colour + glyph for quick visual scanning.
_STATE_STYLE = {
    "RUNNING": ("green", "●"),
    "PENDING": ("yellow", "○"),
    "COMPLETING": ("cyan", "◐"),
    "CONFIGURING": ("cyan", "◐"),
    "SUSPENDED": ("magenta", "‖"),
    "COMPLETED": ("blue", "✓"),
    "CANCELLED": ("red", "✗"),
    "FAILED": ("red", "✗"),
    "TIMEOUT": ("red", "⏱"),
    "NODE_FAIL": ("red", "✗"),
    "OUT_OF_MEMORY": ("red", "✗"),
}


def _state_style(state: str):
    return _STATE_STYLE.get((state or "").upper(), ("white", "•"))


def _fmt(value: str, dash: str = "—") -> str:
    """Display value or a dash placeholder for empties / N/A."""
    v = (value or "").strip()
    if not v or v.upper() in ("N/A", "(NULL)", "NONE", "0:00", "UNKNOWN"):
        return dash if v.upper() != "NONE" else "—"
    return v


class SlurmJobsWidget(VerticalScroll):
    """Compact table of monitored Slurm jobs.

    Integrates with the dashboard grid like any other metric widget: it exposes
    ``title`` / ``border_title`` and an ``update_content``-style API
    (:meth:`update_jobs`). It does not plot — it renders a themed text table
    inside a scrollable child Static.
    """

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
    SlurmJobsWidget > #slurm-jobs-body {
        width: 1fr;
        height: auto;
        padding: 0 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
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
        self._note: str | None = "No jobs selected — press [bold]J[/] to choose."
        self._jobs: list = []

    def compose(self) -> ComposeResult:
        yield Static("", id="slurm-jobs-body", markup=True)

    def update_jobs(self, jobs: list | None, note: str | None = None) -> None:
        """Replace the displayed jobs.

        Args:
            jobs: list of normalized job dicts (see SlurmMonitor._collect).
            note: optional status line shown when there is nothing to display
                (e.g. "Slurm not available").
        """
        self._jobs = jobs or []
        self._note = note
        try:
            self.query_one("#slurm-jobs-body", Static).update(self._render_table())
        except Exception:
            pass

    def _render_table(self) -> str:
        if not self._jobs:
            return f"[dim]{self._note or 'No jobs.'}[/]"

        accent = get_rich_color("accent", "#00afaf")
        lines: list[str] = []
        for job in self._jobs:
            color, glyph = _state_style(job.get("state", ""))
            jid = job.get("jobid", "?")
            name = _fmt(job.get("name", ""), "")
            state = (job.get("state") or "UNKNOWN").upper()

            header = (
                f"[{color}]{glyph}[/] [bold]{jid}[/] "
                f"[{accent}]{name}[/]  [{color}]{state}[/]"
            )
            lines.append(header)

            # Resource / timing line.
            elapsed = _fmt(job.get("elapsed", ""))
            timelimit = _fmt(job.get("timelimit", ""))
            nodes = _fmt(job.get("nodes", ""))
            nodelist = _fmt(job.get("nodelist", ""))
            cpus = _fmt(job.get("cpus", ""))
            mem = _fmt(job.get("mem", ""))
            gpus = _fmt(job.get("gpus", ""))

            time_part = f"⏱ {elapsed}/{timelimit}"
            node_part = f"{nodes} node(s): {nodelist}" if nodelist != "—" else f"{nodes} node(s)"
            res_part = f"cpu {cpus} · mem {mem} · gpu {gpus}"
            lines.append(f"   [dim]{time_part}   {node_part}[/]")
            lines.append(f"   [dim]{res_part}[/]")

            # Live usage (running jobs with sstat data).
            live_cpu = _fmt(job.get("live_cpu", ""))
            live_rss = _fmt(job.get("live_rss", ""))
            if live_cpu != "—" or live_rss != "—":
                lines.append(f"   [dim]live: cpu {live_cpu} · maxRSS {live_rss}[/]")

            # Pending reason.
            reason = _fmt(job.get("reason", ""))
            if state.startswith("PEND") and reason != "—":
                lines.append(f"   [yellow]reason: {reason}[/]")

            lines.append("")  # blank separator
        return "\n".join(lines).rstrip("\n")

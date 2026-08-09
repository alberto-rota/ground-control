"""Reader for a Slurm job's stdout / stderr.

A job's log is the one thing about it Ground Control could not show. The metrics
say a job is using eight cores and 40 GB; only its output says whether it is on
epoch 3 or has been printing the same CUDA error for an hour.

Two decisions shape this file:

* The log is read from **here**, not from inside the job. Slurm writes the job's
  output to a path in the user's filesystem (``scontrol show job`` names it), and
  on any normal cluster that filesystem is shared -- so a plain ``open()`` on the
  login node sees the same bytes, with no job step and no ``srun``. Where that is
  *not* true (output on node-local scratch) the read fails with ENOENT, and the
  message says so rather than blaming the job.
* Only the tail is read (:data:`~ground_control.utils.slurm.OUTPUT_TAIL_BYTES`).
  The interesting end of a log is the recent end, and a training run's stdout can
  be gigabytes on a shared filesystem that everyone else is also using.

Output is rendered through ``Text.from_ansi``: job logs are full of progress bars
and coloured warnings, and the alternative to interpreting those escapes is
printing them as ``ESC[1;32m`` noise.
"""

from __future__ import annotations

import asyncio
import logging
import os

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from rich.text import Text

from ..utils.slurm import (OUTPUT_TAIL_BYTES, get_job_output_paths,
                           read_output_tail)

logger = logging.getLogger("ground-control.job-output")

# How often an open viewer re-reads the file. Slower than the dashboard's tick:
# this is a filesystem read of up to 64 KB on shared storage, and a log a human
# is reading does not need to be a second fresh.
REFRESH_SECONDS = 2.0


class JobOutputScreen(ModalScreen):
    """Modal showing the tail of one job's stdout (or stderr), following it live.

    ``Follow`` is on by default and keeps the view pinned to the end of the file,
    which is where a running job's news is. It only follows while the view *is* at
    the end: scroll up to read something and the refreshes stop moving under you,
    scroll back down and following resumes, exactly as a pager in tail mode
    behaves. ``f`` forces it back on (and jumps to the end) without scrolling.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("s", "toggle_stream", "stdout/stderr"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    DEFAULT_CSS = """
    JobOutputScreen {
        align: center middle;
    }
    #job-output-box {
        width: 90%;
        height: 85%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #job-output-title {
        height: auto;
        text-style: bold;
    }
    #job-output-path {
        height: auto;
        margin-bottom: 1;
    }
    #job-output-body {
        height: 1fr;
        border: none;
        background: transparent;
        /* A log line is a log line: wrapping it would misalign every table and
           progress bar in the file, so long lines scroll sideways instead. */
        overflow-x: auto;
    }
    #job-output-text {
        width: auto;
        height: auto;
        text-wrap: nowrap;
    }
    #job-output-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    #job-output-buttons Button {
        margin-left: 2;
    }
    """

    def __init__(self, jobid: str, job: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.jobid = str(jobid)
        self._job = dict(job or {})
        self._paths: dict = {}
        self._stream = "stdout"
        self._follow = True
        self._timer = None
        self._loading = True
        # Guards against two reads overlapping when the filesystem is slow enough
        # that a read outlives the refresh interval.
        self._reading = False

    # -- layout ------------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        with Vertical(id="job-output-box"):
            yield Static(self._title_text(), id="job-output-title", markup=True)
            yield Static("[dim]locating output file…[/]", id="job-output-path",
                         markup=True)
            with VerticalScroll(id="job-output-body"):
                yield Static("", id="job-output-text", markup=False)
            with Horizontal(id="job-output-buttons"):
                yield Button("stdout/stderr", id="job-output-stream")
                yield Button("Follow: on", variant="primary", id="job-output-follow")
                yield Button("Close", id="job-output-close")

    def on_mount(self) -> None:
        self._timer = self.set_interval(REFRESH_SECONDS, self._tick)
        self._start(self._load(first=True))

    def _start(self, coro) -> None:
        """Run one read as a Textual worker rather than a bare task.

        A worker is owned by this screen, so closing the viewer cancels a read
        that is still waiting on the filesystem. A bare ``asyncio.create_task``
        would outlive the screen it was updating.
        """
        self.run_worker(coro, group="job-output", exclusive=True)

    def _title_text(self) -> str:
        name = self._job.get("name") or ""
        state = (self._job.get("state") or "").upper()
        parts = [f"Output — job {self.jobid}"]
        if name:
            parts.append(name)
        if state:
            parts.append(state)
        return " · ".join(parts)

    # -- data --------------------------------------------------------------- #
    async def _load(self, first: bool = False) -> None:
        """Resolve the job's output paths once, then read the current tail."""
        loop = asyncio.get_event_loop()
        if not self._paths:
            try:
                self._paths = await loop.run_in_executor(
                    None, lambda: get_job_output_paths(self.jobid)) or {}
            except Exception as err:  # noqa: BLE001 - a viewer must not raise
                logger.info("could not resolve output paths for %s: %s",
                            self.jobid, err)
                self._paths = {}
            # An interactive job (salloc/srun --pty) has no output file at all:
            # its stdout went to the terminal it was launched from. Say that,
            # rather than reporting a missing file as an error.
            if first and not (self._paths.get("stdout") or self._paths.get("stderr")):
                self._set_path_line(
                    "[yellow]Slurm recorded no output file for this job.[/] "
                    "[dim]Interactive jobs (salloc, srun --pty) write straight to "
                    "the terminal that started them.[/]")
                self._set_body("")
                self._loading = False
                return
        await self._read()

    async def _read(self) -> None:
        if self._reading:
            return
        self._reading = True
        try:
            path = self._current_path()
            loop = asyncio.get_event_loop()
            try:
                text, error = await loop.run_in_executor(
                    None, lambda: read_output_tail(path))
            except Exception as err:  # noqa: BLE001
                text, error = "", f"could not read output: {err}"
            self._loading = False
            self._set_path_line(self._path_line(path, error))
            if error and not text:
                self._set_body("")
                return
            self._set_body(text)
        finally:
            self._reading = False

    def _current_path(self):
        return self._paths.get(self._stream)

    def _other_path(self):
        return self._paths.get("stderr" if self._stream == "stdout" else "stdout")

    def _path_line(self, path, error) -> str:
        if error:
            return f"[yellow]{error}[/]"
        size = None
        try:
            size = os.path.getsize(path)
        except OSError:
            pass
        bits = [f"[dim]{self._stream}:[/] {path}"]
        if size is not None:
            if size > OUTPUT_TAIL_BYTES:
                bits.append(f"[dim]showing the last {OUTPUT_TAIL_BYTES // 1024} KB "
                            f"of {size / 1024 / 1024:.1f} MB[/]")
            else:
                bits.append(f"[dim]{size / 1024:.1f} KB[/]")
        merged = self._other_path() == path
        if merged:
            # A job submitted without -e has both streams in one file; saying so
            # explains why the stdout/stderr toggle appears to do nothing.
            bits.append("[dim](stdout and stderr share this file)[/]")
        return "   ".join(bits)

    # -- rendering ---------------------------------------------------------- #
    def _set_path_line(self, markup: str) -> None:
        try:
            self.query_one("#job-output-path", Static).update(markup)
        except NoMatches:
            pass

    def _set_body(self, text: str) -> None:
        try:
            body = self.query_one("#job-output-text", Static)
            scroller = self.query_one("#job-output-body", VerticalScroll)
        except NoMatches:
            return
        # Read the scroll position *before* replacing the content: a user who has
        # scrolled up to read something is not at the end, and yanking them back
        # every two seconds would make the viewer unusable. Scrolling back down
        # resumes following by itself, the way a pager in tail mode behaves.
        at_end = scroller.is_vertical_scroll_end
        if not text:
            body.update(Text("(nothing to show yet)", style="dim"))
            return
        # from_ansi so the colours and progress bars a job prints render as
        # colours, not as literal escape sequences.
        body.update(Text.from_ansi(text.rstrip("\n")))
        if self._follow and at_end:
            # After the update, not with it: the scroll target depends on the new
            # content height, which Textual has not measured yet.
            self.call_after_refresh(lambda: scroller.scroll_end(animate=False))

    def _jump_to_end(self) -> None:
        try:
            scroller = self.query_one("#job-output-body", VerticalScroll)
        except NoMatches:
            return
        scroller.scroll_end(animate=False)

    async def _tick(self) -> None:
        if self._loading:
            return
        await self._load()

    # -- actions ------------------------------------------------------------ #
    def action_close(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.dismiss(None)

    def action_toggle_stream(self) -> None:
        other = "stderr" if self._stream == "stdout" else "stdout"
        if not self._paths.get(other):
            self.app.notify(f"This job has no separate {other} file.",
                            title="Job output")
            return
        self._stream = other
        self._start(self._read())

    def action_toggle_follow(self) -> None:
        self._set_follow(not self._follow)
        if self._follow:
            # Turning follow back on is also how a user says "take me to the end".
            self._jump_to_end()
            self._start(self._read())

    def _set_follow(self, follow: bool) -> None:
        self._follow = follow
        try:
            button = self.query_one("#job-output-follow", Button)
            button.label = f"Follow: {'on' if follow else 'off'}"
            button.variant = "primary" if follow else "default"
        except NoMatches:
            pass

    def action_refresh_now(self) -> None:
        self._start(self._read())

    @on(Button.Pressed, "#job-output-close")
    def _close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    @on(Button.Pressed, "#job-output-stream")
    def _stream_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_toggle_stream()

    @on(Button.Pressed, "#job-output-follow")
    def _follow_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_toggle_follow()

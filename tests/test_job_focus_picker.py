"""Headless tests for the focus picker and the job output viewer.

The picker's whole reason to exist is keystroke count: arrow to a job, press
enter, done. It used to be a checkbox list where space marked jobs and enter
confirmed a *different* action (listing them), which meant focusing a job took
space-then-enter-then-noticing-that-was-the-wrong-thing.
"""
import asyncio
import os

os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/gc-test-config")

from textual.app import App, ComposeResult
from textual.widgets import OptionList, Static

from ground_control.app import JobFocusScreen
from ground_control.widgets import job_output
from ground_control.widgets.job_output import JobOutputScreen

JOBS = [
    dict(jobid="100", name="train", state="RUNNING", partition="gpu", nodes="1",
         cpus="32", nodelist="a01", elapsed="1:00:00"),
    dict(jobid="200", name="eval", state="RUNNING", partition="gpu", nodes="1",
         cpus="8", nodelist="a02", elapsed="0:10:00"),
]


def _run(coro):
    """Drive a coroutine, leaving the thread with a usable event loop."""
    try:
        previous = asyncio.get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(
            previous if previous is not None and not previous.is_closed()
            else asyncio.new_event_loop())


async def _settle(pilot, ready, tries: int = 40):
    """Pause until ``ready()`` holds.

    The viewer's reads go through the executor, so the number of event-loop turns
    before the text lands is not something to hardcode.
    """
    for _ in range(tries):
        if ready():
            return True
        await pilot.pause()
    return ready()


class _Host(App):
    """Minimal app that pushes one screen and records how it was dismissed."""

    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.result = "not-dismissed"

    def compose(self) -> ComposeResult:
        yield Static("host")

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._done)

    def _done(self, result) -> None:
        self.result = result


# --------------------------------------------------------------------------- #
# Picker
# --------------------------------------------------------------------------- #
def test_enter_focuses_the_highlighted_job_without_checking_anything():
    async def scenario():
        app = _Host(JobFocusScreen(JOBS))
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            # One keypress to move, one to commit -- no space in between.
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        assert app.result == {"action": "focus", "job": JOBS[1]}

    _run(scenario())


def test_the_list_is_focused_on_open_so_the_arrows_just_work():
    async def scenario():
        app = _Host(JobFocusScreen(JOBS))
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            option_list = app.screen.query_one("#job-select-list", OptionList)
            assert option_list.has_focus
            assert option_list.highlighted == 0

    _run(scenario())


def test_the_picker_opens_on_the_job_already_focused():
    async def scenario():
        app = _Host(JobFocusScreen(JOBS, focused_jobid="200"))
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            # Re-opening the picker while focused should not make "enter" a
            # silent switch to a different job.
            assert app.screen.query_one("#job-select-list", OptionList).highlighted == 1

    _run(scenario())


def test_u_unfocuses_and_escape_cancels():
    async def unfocus():
        app = _Host(JobFocusScreen(JOBS, focused_jobid="100"))
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
        assert app.result == {"action": "unfocus"}

    async def cancel():
        app = _Host(JobFocusScreen(JOBS))
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        assert app.result is None

    _run(unfocus())
    _run(cancel())


def test_no_running_jobs_explains_itself_instead_of_showing_an_empty_list():
    async def scenario():
        app = _Host(JobFocusScreen([]))
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            text = str(app.screen.query_one("#job-select-empty").content)
            assert "no running jobs" in text
            # The panel still lists queued jobs, and saying so avoids reading
            # this as "gc cannot see my jobs".
            assert "queued" in text

    _run(scenario())


# --------------------------------------------------------------------------- #
# Output viewer
# --------------------------------------------------------------------------- #
def test_output_viewer_shows_the_tail_of_the_file(tmp_path, monkeypatch):
    log = tmp_path / "slurm-100.out"
    log.write_text("epoch 1 loss 0.5\nepoch 2 loss 0.4\n")
    monkeypatch.setattr(job_output, "get_job_output_paths",
                        lambda jobid: {"stdout": str(log), "stderr": str(log)})

    async def scenario():
        app = _Host(JobOutputScreen("100", JOBS[0]))
        async with app.run_test(size=(100, 24)) as pilot:
            assert await _settle(pilot, lambda: "epoch 2" in str(
                app.screen.query_one("#job-output-text").content))
            body = str(app.screen.query_one("#job-output-text").content)
            assert "epoch 2 loss 0.4" in body
            path_line = str(app.screen.query_one("#job-output-path").content)
            assert "slurm-100.out" in path_line
            # Submitted without -e, so both streams land in one file; the toggle
            # appearing to do nothing needs explaining.
            assert "share this file" in path_line

    _run(scenario())


def test_output_viewer_explains_a_job_with_no_output_file(monkeypatch):
    # An interactive job's stdout went to the terminal that launched it. That is
    # not a failure to report as one.
    monkeypatch.setattr(job_output, "get_job_output_paths",
                        lambda jobid: {"stdout": None, "stderr": None})

    async def scenario():
        app = _Host(JobOutputScreen("100", JOBS[0]))
        async with app.run_test(size=(100, 24)) as pilot:
            assert await _settle(pilot, lambda: "no output file" in str(
                app.screen.query_one("#job-output-path").content))
            path_line = str(app.screen.query_one("#job-output-path").content)
            assert "no output file" in path_line
            assert "Interactive" in path_line

    _run(scenario())


def test_output_viewer_reports_a_file_it_cannot_read(tmp_path, monkeypatch):
    missing = tmp_path / "not-written-yet.out"
    monkeypatch.setattr(job_output, "get_job_output_paths",
                        lambda jobid: {"stdout": str(missing), "stderr": None})

    async def scenario():
        app = _Host(JobOutputScreen("100", JOBS[0]))
        async with app.run_test(size=(100, 24)) as pilot:
            assert await _settle(pilot, lambda: "does not exist" in str(
                app.screen.query_one("#job-output-path").content))
            path_line = str(app.screen.query_one("#job-output-path").content)
            assert "does not exist yet" in path_line

    _run(scenario())


def test_output_viewer_renders_ansi_as_style_not_as_escape_codes(tmp_path, monkeypatch):
    log = tmp_path / "coloured.out"
    log.write_text("\x1b[32mPASS\x1b[0m all good\n")
    monkeypatch.setattr(job_output, "get_job_output_paths",
                        lambda jobid: {"stdout": str(log), "stderr": None})

    async def scenario():
        app = _Host(JobOutputScreen("100", JOBS[0]))
        async with app.run_test(size=(100, 24)) as pilot:
            assert await _settle(pilot, lambda: "PASS" in str(
                app.screen.query_one("#job-output-text").content))
            body = str(app.screen.query_one("#job-output-text").content)
            # Job logs are full of colour; printing the escapes verbatim is the
            # thing to avoid.
            assert "PASS all good" in body
            assert "\x1b" not in body and "[32m" not in body

    _run(scenario())

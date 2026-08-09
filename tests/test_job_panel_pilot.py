"""Headless tests for the Slurm panel's interactive behaviour.

Driven through Textual's ``run_test`` pilot. There is no pytest-asyncio here, so
each test drives its coroutine through :func:`_run`. ``XDG_CONFIG_HOME`` is
redirected in conftest so nothing touches the real config.
"""
import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Button

from ground_control.widgets.slurm_jobs import JobEntry, JobRow, SlurmJobsWidget

JOBS = [
    dict(jobid="3946056", name="twist", state="RUNNING", partition="rtxpro6k",
         nodes="1", cpus="32", nodelist="a2843", elapsed="15:10:00",
         timelimit="1-00:00:00", mem="120G", gpus="1", live_cpu="14:22:00",
         live_rss="18.4G", live_tasks="1", reason="None"),
    dict(jobid="3945189", name="COARSE", state="PENDING", partition="rtxpro6k",
         nodes="1", cpus="256", nodelist="", elapsed="0:00",
         timelimit="1-00:00:00", mem="", gpus="", reason="AssocGrpGRES"),
]


class PanelApp(App):
    """Hosts the panel alone, and records the messages it emits.

    The handlers are class-level on purpose: Textual resolves message handlers
    through the class MRO, so patching an instance attribute would silently never
    fire and the test would pass for the wrong reason.
    """

    def __init__(self):
        super().__init__()
        self.cancels: list = []
        self.focuses: list = []
        self.outputs: list = []

    def compose(self) -> ComposeResult:
        yield SlurmJobsWidget("Slurm Jobs", id="slurm_jobs")

    def on_job_row_cancel_job(self, message: JobRow.CancelJob) -> None:
        self.cancels.append(message.jobid)

    def on_job_row_focus_job(self, message: JobRow.FocusJob) -> None:
        self.focuses.append(message.jobid)

    def on_job_row_show_output(self, message: JobRow.ShowOutput) -> None:
        self.outputs.append(message.jobid)


def _run(coro):
    """Drive a coroutine, leaving the thread with a usable event loop.

    Not ``asyncio.run``: that closes the loop *and* clears the thread's current
    loop, after which other test modules constructing Textual widgets fail in
    ``get_event_loop()``. Tests must not sabotage the ones that follow them.
    """
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


def _entries(widget):
    return list(widget.query(JobEntry).results(JobEntry))


def test_cancel_needs_two_presses_and_reaches_the_app():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            await pilot.pause()
            row = _entries(panel)[0].query_one(JobRow)
            button = row.query_one(".cancel-btn", Button)

            await pilot.click(button)
            await pilot.pause()
            assert row._armed, "first press must arm, not cancel"
            assert str(button.label) == "!"
            assert app.cancels == []

            await pilot.click(button)
            await pilot.pause()
            assert app.cancels == ["3946056"]
            assert not row._armed, "confirming must disarm"

    _run(scenario())


def test_arming_expires_on_its_own():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            row = _entries(panel)[0].query_one(JobRow)
            button = row.query_one(".cancel-btn", Button)
            await pilot.click(button)
            await pilot.pause()
            assert row._armed
            # An armed destructive button left sitting there is a trap.
            await asyncio.sleep(JobRow.ARM_SECONDS + 0.7)
            await pilot.pause()
            assert not row._armed
            assert str(button.label) == "C"
            assert app.cancels == []

    _run(scenario())


def test_focus_button_posts_the_jobid():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            await pilot.click(_entries(panel)[0].query_one(".focus-btn", Button))
            await pilot.pause()
            assert app.focuses == ["3946056"]

    _run(scenario())


def test_output_button_posts_the_jobid():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            await pilot.click(_entries(panel)[0].query_one(".output-btn", Button))
            await pilot.pause()
            assert app.outputs == ["3946056"]

    _run(scenario())


def test_pending_job_cannot_be_focused_or_read():
    """A queued job has no allocation to sample and has written no output.

    The buttons are disabled rather than absent: the row still has to line up
    with every other row, and a greyed control explains itself where a missing
    one would just look like a rendering bug.
    """
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            pending = _entries(panel)[1].query_one(JobRow)
            focus_btn = pending.query_one(".focus-btn", Button)
            output_btn = pending.query_one(".output-btn", Button)
            assert focus_btn.disabled and output_btn.disabled
            # Cancelling a queued job is exactly when cancelling is cheapest.
            assert not pending.query_one(".cancel-btn", Button).disabled

            await pilot.click(focus_btn)
            await pilot.click(output_btn)
            await pilot.pause()
            assert app.focuses == [] and app.outputs == []

    _run(scenario())


def test_buttons_light_up_when_a_job_starts_running():
    """Rows update in place, so a PENDING row has to enable its own controls."""
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            row = _entries(panel)[1].query_one(JobRow)
            assert row.query_one(".focus-btn", Button).disabled

            started = [JOBS[0], dict(JOBS[1], state="RUNNING", nodelist="a2844")]
            panel.update_jobs(started)
            await pilot.pause()
            assert row is _entries(panel)[1].query_one(JobRow), "set unchanged"
            assert not row.query_one(".focus-btn", Button).disabled
            assert not row.query_one(".output-btn", Button).disabled

    _run(scenario())


def test_finished_job_cannot_be_cancelled():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs([dict(JOBS[0], state="COMPLETED")])
            await pilot.pause()
            row = _entries(panel)[0].query_one(JobRow)
            cancel = row.query_one(".cancel-btn", Button)
            assert cancel.disabled
            await pilot.click(cancel)
            await pilot.pause()
            assert app.cancels == []

    _run(scenario())


def test_refresh_updates_rows_in_place():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            row = _entries(panel)[0].query_one(JobRow)
            await pilot.click(row.query_one(".cancel-btn", Button))
            await pilot.pause()

            # A refresh tick arrives about once a second. Remounting rows there
            # would drop the armed state and any keyboard focus with it.
            panel.update_jobs([dict(job, elapsed="15:11:00") for job in JOBS])
            await pilot.pause()
            assert row is _entries(panel)[0].query_one(JobRow)
            assert row._armed

            # A changed job *set*, on the other hand, has to rebuild.
            panel.update_jobs(JOBS[:1])
            await pilot.pause()
            assert [e.jobid for e in _entries(panel)] == ["3946056"]

    _run(scenario())


def test_focused_job_is_marked_on_its_row():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            # Both running: the marker is about which one is focused, and a
            # pending row's Focus button says something else entirely.
            panel.update_jobs([JOBS[0], dict(JOBS[1], state="RUNNING")],
                              focused_jobid="3946056")
            await pilot.pause()
            first, second = _entries(panel)
            assert "Already focused" in (
                first.query_one(".focus-btn", Button).tooltip or "")
            assert "Focus the dashboard" in (
                second.query_one(".focus-btn", Button).tooltip or "")

    _run(scenario())


def test_empty_list_shows_the_note_and_no_rows():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            assert _entries(panel)
            panel.update_jobs([], note="Slurm not available on this system.")
            await pilot.pause()
            assert not _entries(panel)
            note = app.query_one("#slurm-jobs-note")
            assert "not available" in str(note.content)
            assert note.display

    _run(scenario())


def test_rows_render_without_wrapping_at_panel_width():
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs(JOBS)
            await pilot.pause()
            await pilot.pause()
            for entry in _entries(panel):
                info = entry.query_one(".job-info")
                text = str(info.content)
                assert "\n" not in text, "a wrapped row would break the table"
                assert text.strip(), "row rendered empty"

    _run(scenario())


def test_a_finished_jobs_output_is_still_readable():
    """The log of a job that failed is the log you most want to read."""
    async def scenario():
        app = PanelApp()
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.pause()
            panel = app.query_one(SlurmJobsWidget)
            panel.update_jobs([dict(JOBS[0], state="FAILED")])
            await pilot.pause()
            row = _entries(panel)[0].query_one(JobRow)
            output_btn = row.query_one(".output-btn", Button)
            assert not output_btn.disabled
            # Nothing left to sample inside it, though.
            assert row.query_one(".focus-btn", Button).disabled
            await pilot.click(output_btn)
            await pilot.pause()
            assert app.outputs == ["3946056"]

    _run(scenario())

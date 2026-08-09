"""Tests for entering and (especially) leaving Slurm job focus.

Leaving is the part with teeth. A focused job ends whenever it ends -- on its
own, at its time limit, by failing, by being cancelled -- and nothing tells the
dashboard. If that is not noticed, every panel keeps showing the job's last
reading with no indication that the job is gone, which is worse than showing
nothing: the numbers look live.

The app object is built with ``__new__`` and given only the attributes these
paths touch. Standing up the whole ``GroundControl`` would start collectors, read
the user's config and build a grid, none of which has anything to do with the
decision under test.
"""
import asyncio
import os

os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/gc-test-config")

from ground_control.app import GroundControl
from ground_control.utils import slurm as slurm_utils


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


class _FakeSampler:
    """Only what the focus paths ask of a sampler."""

    def __init__(self, failures=0):
        self.consecutive_failures = failures
        self.stopped = False
        self.interval = 1.0
        self.mode = "stream"

    def stop(self):
        self.stopped = True


def _app(jobid="4242", failures=0):
    app = GroundControl.__new__(GroundControl)
    app._job_sampler = _FakeSampler(failures)
    app._focused_job = {"jobid": jobid, "_node": "a01"}
    app._job_focus_probe_failures = 0
    app._job_liveness_checked_at = 0.0
    app._job_liveness_check_running = False
    app._slurm_monitor = slurm_utils.SlurmMonitor()
    app.exits = []
    app._exit_job_focus = lambda reason=None, severity="information": (
        app.exits.append((reason, severity)))
    return app


# --------------------------------------------------------------------------- #
# The liveness check is throttled, but never skipped
# --------------------------------------------------------------------------- #
def test_liveness_check_is_throttled(monkeypatch):
    app = _app()
    started = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro: (
        coro.close(), started.append(1))[0])

    app._check_focused_job_alive()
    assert len(started) == 1
    # Called from the UI tick, so an unthrottled check would mean a subprocess
    # per frame against the controller.
    app._job_liveness_check_running = False
    app._check_focused_job_alive()
    assert len(started) == 1


def test_a_probe_failure_streak_checks_immediately(monkeypatch):
    app = _app()
    started = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro: (
        coro.close(), started.append(1))[0])
    app._check_focused_job_alive()
    app._job_liveness_check_running = False
    # srun failing to create a step is the fast hint that the job is gone; it
    # must not have to wait out the throttle.
    app._check_focused_job_alive(immediate=True)
    assert len(started) == 2


def test_no_second_check_while_one_is_in_flight(monkeypatch):
    app = _app()
    started = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro: (
        coro.close(), started.append(1))[0])
    app._check_focused_job_alive(immediate=True)
    app._check_focused_job_alive(immediate=True)
    assert len(started) == 1


def test_no_check_once_focus_is_gone(monkeypatch):
    app = _app()
    app._job_sampler = None  # job_focus_active is False
    monkeypatch.setattr(asyncio, "create_task", lambda coro: (
        coro.close(), 1 / 0))
    app._check_focused_job_alive(immediate=True)  # must not raise


# --------------------------------------------------------------------------- #
# What happens when the job actually ends
# --------------------------------------------------------------------------- #
def test_focus_ends_and_names_the_final_state(monkeypatch):
    monkeypatch.setattr(slurm_utils, "get_job_liveness",
                        lambda jobid: (False, "COMPLETED"))
    app = _app()
    _run(app._drop_focus_if_job_ended())
    (reason, severity), = app.exits
    assert "4242" in reason and "COMPLETED" in reason
    assert severity == "information"
    assert app._job_liveness_check_running is False


def test_a_failed_job_is_reported_as_a_warning(monkeypatch):
    # "back on this host" is fine news for a job that finished and bad news for
    # one that died; the toast should not read the same way for both.
    monkeypatch.setattr(slurm_utils, "get_job_liveness",
                        lambda jobid: (False, "FAILED"))
    app = _app()
    _run(app._drop_focus_if_job_ended())
    (reason, severity), = app.exits
    assert "FAILED" in reason and severity == "warning"


def test_focus_survives_a_controller_that_will_not_answer(monkeypatch):
    monkeypatch.setattr(slurm_utils, "get_job_liveness", lambda jobid: (True, None))
    app = _app()
    _run(app._drop_focus_if_job_ended())
    assert app.exits == []


def test_focus_survives_an_exception_from_slurm(monkeypatch):
    def boom(jobid):
        raise OSError("controller unreachable")

    monkeypatch.setattr(slurm_utils, "get_job_liveness", boom)
    app = _app()
    _run(app._drop_focus_if_job_ended())
    assert app.exits == []
    # The flag has to be cleared even on the failure path, or one bad call stops
    # every later check and focus never ends.
    assert app._job_liveness_check_running is False


def test_ending_without_a_recorded_state_still_ends_focus(monkeypatch):
    monkeypatch.setattr(slurm_utils, "get_job_liveness", lambda jobid: (False, None))
    app = _app()
    _run(app._drop_focus_if_job_ended())
    (reason, _severity), = app.exits
    assert "no longer running" in reason


def test_the_check_does_not_unfocus_a_job_the_user_switched_away_from(monkeypatch):
    app = _app(jobid="4242")

    def switched(jobid):
        # The query runs on the executor and takes as long as the controller
        # takes; the user can focus a different job in that window. The answer
        # describes job 4242 and must not tear down whatever is focused now.
        assert jobid == "4242"
        app._focused_job = {"jobid": "9999"}
        return False, "COMPLETED"

    monkeypatch.setattr(slurm_utils, "get_job_liveness", switched)
    _run(app._drop_focus_if_job_ended())
    assert app.exits == []


def test_the_job_list_is_refreshed_after_focus_drops(monkeypatch):
    monkeypatch.setattr(slurm_utils, "get_job_liveness",
                        lambda jobid: (False, "TIMEOUT"))
    app = _app()
    app._slurm_monitor._cache = [{"jobid": "4242", "state": "RUNNING"}]
    app._slurm_monitor._last_poll = 1e12  # freshly polled: normally throttled
    _run(app._drop_focus_if_job_ended())
    # A list still showing the job as RUNNING right after we said it ended would
    # contradict the toast.
    assert app._slurm_monitor._last_poll == 0.0

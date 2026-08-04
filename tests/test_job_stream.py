"""Tests for the resident-collector job sampler (ground_control.utils.slurm).

The sampler is exercised with local stand-ins for ``srun`` -- short Python
one-liners that emit NDJSON, fail, or reject ``--stream`` -- so the reader,
restart and fallback logic is covered without a cluster.
"""
import json
import sys
import time

import pytest

from ground_control.utils import slurm


def _snapshot_line(index=0):
    return json.dumps({"schema_version": 1, "timestamp": time.time(),
                       "host": "node01", "metrics": {"cpu": {"percent": index}}})


def _fake_stream(lines, exit_code=0, delay=0.0):
    """A command that prints ``lines`` (one per line) then exits."""
    body = (f"import sys,time\n"
            f"time.sleep({delay})\n"
            f"for l in {lines!r}:\n"
            f"    sys.stdout.write(l + '\\n'); sys.stdout.flush()\n"
            f"sys.exit({exit_code})\n")
    return [sys.executable, "-c", body]


def _wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------- #
# Command construction
# --------------------------------------------------------------------------- #
def test_stream_command_joins_the_existing_allocation():
    cmd = slurm._stream_command("4242", node="a2843", interval=1.0)
    assert cmd[0] == "srun"
    # --overlap is the whole point: the collector must land in the job's cgroup
    # rather than queueing a second allocation.
    assert "--overlap" in cmd
    assert "--jobid=4242" in cmd
    assert "--ntasks=1" in cmd and "--nodes=1" in cmd
    assert "--nodelist=a2843" in cmd
    # Lines must arrive as they are written, not in blocks.
    assert "--unbuffered" in cmd


def test_stream_command_runs_the_module_not_the_console_script():
    cmd = slurm._stream_command("1", interval=2.5)
    assert cmd[cmd.index("-m"):cmd.index("-m") + 2] == ["-m", "ground_control"]
    assert sys.executable in cmd
    assert "gc" not in cmd  # a remote shell has none of the user's PATH


def test_stream_command_carries_cadence_and_lifetime_cap():
    cmd = slurm._stream_command("1", interval=2.5, max_seconds=120)
    assert "--stream" in cmd
    assert cmd[cmd.index("--interval") + 1] == "2.5"
    # The remote side must expire by itself if our stop signal never lands.
    assert cmd[cmd.index("--stream-max-seconds") + 1] == "120"


def test_stream_interval_has_a_floor():
    cmd = slurm._stream_command("1", interval=0.0)
    assert float(cmd[cmd.index("--interval") + 1]) >= 0.2


def test_stream_and_probe_share_the_srun_prefix():
    stream = slurm._stream_command("7", node="n1")
    probe = slurm._probe_command("7", node="n1")
    prefix_len = probe.index(sys.executable)
    assert stream[:prefix_len] == probe[:prefix_len]
    # ...and differ only in what they ask the remote gc to do.
    assert "--once" in probe and "--once" not in stream


def test_stream_command_exports_pythonpath():
    cmd = slurm._stream_command("1")
    assert any(part.startswith("--export=ALL,PYTHONPATH=") for part in cmd)


# --------------------------------------------------------------------------- #
# Line parsing
# --------------------------------------------------------------------------- #
def test_parse_stream_line_accepts_a_snapshot():
    parsed = slurm._parse_stream_line(_snapshot_line(3) + "\n")
    assert parsed["metrics"]["cpu"]["percent"] == 3


@pytest.mark.parametrize("line", [
    "",
    "\n",
    "srun: job step created",              # srun diagnostics on the same pipe
    "Traceback (most recent call last):",
    "{not json at all",
    "[1, 2, 3]",                           # valid JSON, wrong shape
    '{"schema_version": 1}',               # no metrics key
])
def test_parse_stream_line_rejects_everything_else(line):
    assert slurm._parse_stream_line(line) is None


# --------------------------------------------------------------------------- #
# Sampler behaviour
# --------------------------------------------------------------------------- #
def test_sampler_serves_streamed_samples(monkeypatch):
    lines = [_snapshot_line(i) for i in range(3)]
    monkeypatch.setattr(slurm, "_stream_command",
                        lambda *a, **k: _fake_stream(lines))
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    try:
        assert _wait_for(lambda: sampler.latest()[0] is not None)
        snapshot, age, error = sampler.latest()
        assert error is None
        assert age < 5.0
        assert sampler.mode == "stream"
        assert sampler.consecutive_failures == 0
    finally:
        sampler.stop()


def test_sampler_restarts_a_stream_that_ended_and_keeps_its_last_sample(monkeypatch):
    monkeypatch.setattr(slurm, "_stream_command",
                        lambda *a, **k: _fake_stream([_snapshot_line(1)]))
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    try:
        # Each fake stream exits after one line; the sampler must re-arm.
        assert _wait_for(lambda: sampler.restarts >= 2)
        snapshot, _age, error = sampler.latest()
        assert snapshot is not None
        # A stream that produced data and ended is normal (remote lifetime cap),
        # so it must not be reported as a failure.
        assert error is None and sampler.consecutive_failures == 0
    finally:
        sampler.stop()


def test_sampler_falls_back_to_probing_when_stream_is_unsupported(monkeypatch):
    """An older remote gc rejects --stream; that must not end monitoring."""
    monkeypatch.setattr(slurm, "_stream_command", lambda *a, **k: [
        sys.executable, "-c",
        "import sys; sys.stderr.write('Error: No such option: --stream\\n');"
        " sys.exit(2)"])
    monkeypatch.setattr(slurm, "_probe_command",
                        lambda *a, **k: _fake_stream([_snapshot_line(9)]))
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    try:
        assert _wait_for(lambda: sampler.mode == "probe", timeout=20)
        assert _wait_for(lambda: sampler.latest()[0] is not None, timeout=20)
        assert sampler.latest()[0]["metrics"]["cpu"]["percent"] == 9
    finally:
        sampler.stop()


def test_sampler_keeps_streaming_after_a_transient_failure(monkeypatch):
    """A step that could not be created is retried, not permanently downgraded."""
    monkeypatch.setattr(slurm, "_stream_command", lambda *a, **k: [
        sys.executable, "-c",
        "import sys; sys.stderr.write('srun: error: Unable to create step\\n');"
        " sys.exit(1)"])
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    try:
        assert _wait_for(lambda: sampler.consecutive_failures >= 2, timeout=20)
        snapshot, age, error = sampler.latest()
        assert snapshot is None and age == float("inf")
        # The reported error is what the remote side actually said.
        assert error and "Unable to create step" in error
        assert "Unable to create step" in " ".join(sampler.diagnostics())
        assert sampler.mode == "stream"
    finally:
        sampler.stop()


def test_sampler_diagnostics_are_bounded(monkeypatch):
    noise = [f"srun: warning {i}" for i in range(50)]
    monkeypatch.setattr(slurm, "_stream_command",
                        lambda *a, **k: _fake_stream(noise, exit_code=1))
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    try:
        assert _wait_for(lambda: sampler.diagnostics(), timeout=20)
        assert len(sampler.diagnostics()) <= slurm._STREAM_DIAG_LINES
    finally:
        sampler.stop()


def test_stop_is_prompt_and_leaves_no_reader_running(monkeypatch):
    """stop() runs on the UI thread, so it must never wait on srun."""
    monkeypatch.setattr(slurm, "_stream_command", lambda *a, **k: [
        sys.executable, "-c",
        "import sys, time\n"
        "sys.stdout.write('%s\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n" % _snapshot_line(1)])
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    assert _wait_for(lambda: sampler.latest()[0] is not None)
    started = time.time()
    sampler.stop()
    assert time.time() - started < 1.0, "stop() blocked the caller"
    assert sampler.stopped
    assert _wait_for(lambda: not any(
        t.name.startswith("gc-job-stream") and t.is_alive()
        for t in __import__("threading").enumerate()), timeout=15)


def test_sampler_replaces_a_stream_that_went_quiet(monkeypatch):
    """A stream can stall while srun stays alive; the reader would block forever."""
    monkeypatch.setattr(slurm, "_STREAM_MIN_STALL_TIMEOUT", 1)
    monkeypatch.setattr(slurm, "_STREAM_STALL_FACTOR", 1)
    monkeypatch.setattr(slurm, "_stream_command", lambda *a, **k: [
        sys.executable, "-c",
        "import sys, time\n"
        "sys.stdout.write('%s\\n'); sys.stdout.flush()\n"
        "time.sleep(300)\n" % _snapshot_line(1)])
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    try:
        assert _wait_for(lambda: sampler.latest()[0] is not None)
        # One sample, then silence: the watchdog must end it and the loop must
        # start a fresh stream rather than showing an ageing reading forever.
        assert _wait_for(lambda: sampler.restarts >= 2, timeout=20)
    finally:
        sampler.stop()


def test_sampler_gives_up_on_a_stream_that_never_speaks(monkeypatch):
    monkeypatch.setattr(slurm, "_STREAM_STARTUP_TIMEOUT", 1)
    monkeypatch.setattr(slurm, "_stream_command", lambda *a, **k: [
        sys.executable, "-c", "import time; time.sleep(300)"])
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    try:
        # No output at all: reported as a failure instead of hanging silently.
        assert _wait_for(lambda: sampler.consecutive_failures >= 1, timeout=20)
        assert sampler.latest()[0] is None
        assert sampler.latest()[2]
    finally:
        sampler.stop()


def test_sampler_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(slurm, "_stream_command",
                        lambda *a, **k: _fake_stream([_snapshot_line(1)]))
    sampler = slurm.JobFocusSampler("1", interval=0.3)
    sampler.start()
    sampler.start()  # a second start must not create a second reader
    try:
        assert sum(1 for t in __import__("threading").enumerate()
                   if t.name == "gc-job-stream-1") == 1
    finally:
        sampler.stop()

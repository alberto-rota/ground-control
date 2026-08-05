"""Tests for ground_control.utils.slurm pure parsers."""
from ground_control.utils import slurm


SQUEUE_LINE = "12345|train.sh|RUNNING|gpu|2|8|node[01-02]|1:23:45|2:00:00|None|alice"


def test_parse_squeue_basic():
    rows = slurm.parse_squeue(SQUEUE_LINE)
    assert len(rows) == 1
    r = rows[0]
    assert r["jobid"] == "12345"
    assert r["name"] == "train.sh"
    assert r["state"] == "RUNNING"
    assert r["partition"] == "gpu"
    assert r["nodes"] == "2"
    assert r["cpus"] == "8"
    assert r["user"] == "alice"


def test_parse_squeue_skips_blank_and_headerless_rows():
    out = "\n".join(["", SQUEUE_LINE, "   "])
    rows = slurm.parse_squeue(out)
    assert len(rows) == 1


def test_parse_squeue_pads_short_rows():
    # Missing trailing columns are padded, not dropped, as long as jobid exists.
    rows = slurm.parse_squeue("999|job")
    assert rows and rows[0]["jobid"] == "999"
    assert rows[0]["user"] == ""


def test_parse_scontrol_job_keyvalues():
    out = "JobId=12345 JobName=train.sh JobState=RUNNING NumNodes=2 NumCPUs=8 NodeList=node01"
    info = slurm.parse_scontrol_job(out)
    assert info["JobId"] == "12345"
    assert info["JobState"] == "RUNNING"
    assert info["NumNodes"] == "2"


def test_gpus_from_tres():
    assert slurm._gpus_from_tres("cpu=4,mem=16G,gres/gpu=2") == 2
    assert slurm._gpus_from_tres("cpu=4,mem=16G") is None
    assert slurm._gpus_from_tres("gres/gpu:a100=4") == 4
    assert slurm._gpus_from_tres("") is None


def test_job_alloc_from_scontrol_extracts_gpus_and_mem():
    info = slurm.parse_scontrol_job(
        "JobState=RUNNING RunTime=00:10:00 TimeLimit=01:00:00 "
        "NumNodes=1 NumCPUs=8 NodeList=node01 AllocTRES=cpu=8,mem=32G,gres/gpu=2"
    )
    alloc = slurm.job_alloc_from_scontrol(info)
    assert alloc["gpus"] == "2"
    assert alloc["mem"] == "32G"
    assert alloc["state"] == "RUNNING"
    assert alloc["cpus"] == "8"


def test_parse_sstat_picks_data_row():
    out = "JobID|AveCPU|MaxRSS|AveRSS|NTasks\n12345.0|00:05:00|1024K|512K|1"
    row = slurm.parse_sstat(out)
    assert row is not None
    assert row["MaxRSS"] == "1024K"
    assert row["AveCPU"] == "00:05:00"


def test_parse_sstat_empty():
    assert slurm.parse_sstat("") is None
    assert slurm.parse_sstat("only-a-header-line") is None


# --------------------------------------------------------------------------- #
# Running-only filtering
# --------------------------------------------------------------------------- #
def test_is_running_state():
    assert slurm.is_running_state("RUNNING")
    assert slurm.is_running_state("running")
    assert slurm.is_running_state("R")
    assert not slurm.is_running_state("PENDING")
    assert not slurm.is_running_state("")
    assert not slurm.is_running_state(None)
    # A job whose cgroup is being torn down has nothing useful left to sample.
    assert not slurm.is_running_state("COMPLETING")


def test_get_running_user_jobs_excludes_queued(monkeypatch):
    rows = slurm.parse_squeue("\n".join([
        "1|a|RUNNING|gpu|1|8|node01|0:10|1:00|None|alice",
        "2|b|PENDING|gpu|1|8||0:00|1:00|Resources|alice",
        "3|c|COMPLETING|gpu|1|8|node02|0:50|1:00|None|alice",
    ]))
    monkeypatch.setattr(slurm, "get_user_jobs", lambda user=None: rows)
    running = slurm.get_running_user_jobs()
    assert [j["jobid"] for j in running] == ["1"]


# --------------------------------------------------------------------------- #
# Nodelist reduction
# --------------------------------------------------------------------------- #
def test_first_node_forms():
    assert slurm.first_node("a2142") == "a2142"
    assert slurm.first_node("a[2142-2145]") == "a2142"
    assert slurm.first_node("node[01,05]") == "node01"
    assert slurm.first_node("a2142,a2143") == "a2142"
    # A comma inside brackets must not be mistaken for a list separator.
    assert slurm.first_node("n[01,02],m03") == "n01"


def test_first_node_missing():
    assert slurm.first_node("") is None
    assert slurm.first_node(None) is None
    assert slurm.first_node("(null)") is None


# --------------------------------------------------------------------------- #
# Probe command construction
# --------------------------------------------------------------------------- #
def test_probe_command_joins_existing_allocation():
    cmd = slurm._probe_command("12345", node="node01")
    assert cmd[0] == "srun"
    # --overlap is what makes this join the running job (and so inherit its
    # cgroup and CUDA_VISIBLE_DEVICES) rather than queue new work.
    assert "--overlap" in cmd
    assert "--jobid=12345" in cmd
    assert "--nodelist=node01" in cmd
    # Exactly one sample, not one per allocated CPU.
    assert "--ntasks=1" in cmd
    assert "--once" in cmd and "--json" in cmd
    # Addressed as a module through an absolute interpreter: a non-interactive
    # remote shell has none of the user's PATH, so `gc` would not resolve.
    assert "-m" in cmd and "ground_control" in cmd


def test_probe_command_without_node():
    cmd = slurm._probe_command("12345")
    assert not any(part.startswith("--nodelist") for part in cmd)


def test_probe_command_exports_pythonpath():
    # Needed so an editable / plain checkout is importable on the compute node.
    cmd = slurm._probe_command("1")
    assert any(part.startswith("--export=ALL,PYTHONPATH=") for part in cmd)


def test_probe_returns_none_on_command_failure(monkeypatch):
    monkeypatch.setattr(slurm, "_run", lambda *a, **k: None)
    assert slurm.probe_job_metrics("12345") is None


def test_probe_tolerates_srun_noise_before_json(monkeypatch):
    payload = '{"schema_version": 1, "metrics": {"cpu": null}}'
    monkeypatch.setattr(
        slurm, "_run",
        lambda *a, **k: "srun: job 1 has been allocated resources\n" + payload,
    )
    snapshot = slurm.probe_job_metrics("1")
    assert snapshot is not None and snapshot["schema_version"] == 1


def test_probe_rejects_unparsable_and_unexpected_output(monkeypatch):
    monkeypatch.setattr(slurm, "_run", lambda *a, **k: "{not json")
    assert slurm.probe_job_metrics("1") is None
    # Valid JSON but not a snapshot: must not be handed on as one.
    monkeypatch.setattr(slurm, "_run", lambda *a, **k: '{"unrelated": true}')
    assert slurm.probe_job_metrics("1") is None


# --------------------------------------------------------------------------- #
# The job list: everything the user has queued, from one squeue call
# --------------------------------------------------------------------------- #
ALL_JOBS_OUTPUT = "\n".join([
    # jobid|name|state|part|nodes|cpus|nodelist|elapsed|limit|reason|user|gres|mem
    "20|late|PENDING|gpu|1|8||0:00|1:00:00|Resources|alice|gres/gpu:a100:2|64G",
    "10|train|RUNNING|gpu|1|32|a01|1:00:00|8:00:00|None|alice|gres/gpu:a100:4|240000M",
    "5|old|RUNNING|cpu|2|16|a02|3:00:00|4:00:00|None|alice|N/A|16G",
])


def _monitor_with(monkeypatch, output, stats=None, record=None):
    monkeypatch.setattr(slurm, "slurm_available", lambda: True)
    monkeypatch.setattr(slurm, "_run", lambda cmd, timeout=None: output)

    def fake_stats(jobid):
        if record is not None:
            record.append(jobid)
        return stats

    monkeypatch.setattr(slurm, "get_job_live_stats", fake_stats)
    return slurm.SlurmMonitor()


def test_monitor_lists_every_job_with_no_selection(monkeypatch):
    monitor = _monitor_with(monkeypatch, ALL_JOBS_OUTPUT)
    jobs = monitor.poll()
    # Running first (that is what can be focused, read and watched), then by id.
    assert [j["jobid"] for j in jobs] == ["5", "10", "20"]
    assert [j["state"] for j in jobs] == ["RUNNING", "RUNNING", "PENDING"]


def test_monitor_takes_gpus_and_memory_from_squeue(monkeypatch):
    """No scontrol per job: with several hundred queued jobs that is the whole
    cost of the panel, and squeue already knows both numbers."""
    monitor = _monitor_with(monkeypatch, ALL_JOBS_OUTPUT)
    by_id = {j["jobid"]: j for j in monitor.poll()}
    assert by_id["10"]["gpus"] == "4"
    assert by_id["10"]["mem"] == "240000M"
    # A pending job's request is worth showing too -- it is why it is queued.
    assert by_id["20"]["gpus"] == "2"
    # No GPUs requested reads as blank, not as "0 allocated".
    assert by_id["5"]["gpus"] == ""


def test_monitor_only_sstats_running_jobs_and_caps_them(monkeypatch):
    asked = []
    monitor = _monitor_with(monkeypatch, ALL_JOBS_OUTPUT,
                            stats={"AveCPU": "1:00", "MaxRSS": "1G", "NTasks": "1"},
                            record=asked)
    monitor.detail_limit = 1
    jobs = monitor.poll()
    # One sstat call: the first running job. A pending job cannot be asked how
    # much CPU it is using, and the limit keeps a big queue off the tick.
    assert asked == ["5"]
    by_id = {j["jobid"]: j for j in jobs}
    assert by_id["5"]["live_rss"] == "1G"
    assert by_id["10"]["live_rss"] == ""
    assert by_id["20"]["live_rss"] == ""


def test_monitor_throttles_but_invalidate_forces_a_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(slurm, "slurm_available", lambda: True)
    monkeypatch.setattr(slurm, "get_job_live_stats", lambda jobid: None)

    def counting_run(cmd, timeout=None):
        calls.append(cmd)
        return ALL_JOBS_OUTPUT

    monkeypatch.setattr(slurm, "_run", counting_run)
    monitor = slurm.SlurmMonitor(min_interval=1000)
    monitor.poll()
    monitor.poll()
    assert len(calls) == 1, "the second poll must come from cache"
    # After a cancel, showing the pre-action list would read as the cancel failing.
    monitor.invalidate()
    monitor.poll()
    assert len(calls) == 2


def test_job_sort_key_orders_numerically_and_handles_arrays():
    jobs = [
        {"jobid": "1000", "state": "RUNNING"},
        {"jobid": "999", "state": "RUNNING"},
        {"jobid": "7_3", "state": "PENDING"},
        {"jobid": "8", "state": "RUNNING"},
    ]
    assert [j["jobid"] for j in sorted(jobs, key=slurm.job_sort_key)] == [
        "8", "999", "1000", "7_3"]


# --------------------------------------------------------------------------- #
# Liveness: what ends a focus session
# --------------------------------------------------------------------------- #
def _fake_status(monkeypatch, rc, out="", err=""):
    monkeypatch.setattr(slurm, "slurm_available", lambda: True)
    monkeypatch.setattr(slurm, "_run_status",
                        lambda cmd, timeout=None: (rc, out, err))


def test_liveness_running_job(monkeypatch):
    _fake_status(monkeypatch, 0, out="RUNNING\n")
    assert slurm.get_job_liveness("42") == (True, "RUNNING")


def test_liveness_job_still_queued_is_not_running(monkeypatch):
    _fake_status(monkeypatch, 0, out="PENDING\n")
    running, state = slurm.get_job_liveness("42")
    assert running is False and state == "PENDING"


def test_liveness_job_gone_from_the_queue_reports_its_final_state(monkeypatch):
    # squeue forgets a job shortly after it ends; sacct is what still knows how
    # it ended, which is the one thing worth saying when focus drops.
    _fake_status(monkeypatch, 1, err="slurm_load_jobs error: Invalid job id specified")
    monkeypatch.setattr(slurm, "final_state_from_sacct", lambda jobid: "FAILED")
    assert slurm.get_job_liveness("42") == (False, "FAILED")


def test_liveness_empty_queue_output_means_the_job_left(monkeypatch):
    _fake_status(monkeypatch, 0, out="\n")
    monkeypatch.setattr(slurm, "final_state_from_sacct", lambda jobid: "TIMEOUT")
    assert slurm.get_job_liveness("42") == (False, "TIMEOUT")


def test_liveness_unknown_when_the_controller_will_not_answer(monkeypatch):
    """A busy controller must never be read as "the job ended".

    Guessing wrong here tears down a working dashboard; guessing the other way
    costs one more check a few seconds later.
    """
    _fake_status(monkeypatch, None, err="Socket timed out on send/recv operation")
    assert slurm.get_job_liveness("42") == (True, None)


def test_liveness_ended_without_accounting_leaves_the_state_unnamed(monkeypatch):
    _fake_status(monkeypatch, 1, err="Invalid job id specified")
    monkeypatch.setattr(slurm, "final_state_from_sacct", lambda jobid: None)
    running, state = slurm.get_job_liveness("42")
    assert running is False
    assert state is None, "a guessed state is worse than no state"


def test_final_state_from_sacct_drops_the_cancelling_uid(monkeypatch):
    monkeypatch.setattr(slurm.shutil, "which", lambda name: "/usr/bin/sacct")
    monkeypatch.setattr(slurm, "_run",
                        lambda cmd, timeout=None: "CANCELLED by 213852\n")
    assert slurm.final_state_from_sacct("42") == "CANCELLED"


# --------------------------------------------------------------------------- #
# Job output
# --------------------------------------------------------------------------- #
SCONTROL_WITH_PATHS = (
    "JobId=42 JobName=train.sh JobState=RUNNING WorkDir=/home/alice/run "
    "StdIn=/dev/null StdOut=/home/alice/run/slurm-42.out "
    "StdErr=/home/alice/run/slurm-42.err Command=/home/alice/run/train.sh"
)


def test_output_paths_from_scontrol(monkeypatch):
    monkeypatch.setattr(slurm, "_run", lambda cmd, timeout=None: SCONTROL_WITH_PATHS)
    paths = slurm.get_job_output_paths("42")
    assert paths["stdout"] == "/home/alice/run/slurm-42.out"
    assert paths["stderr"] == "/home/alice/run/slurm-42.err"
    assert paths["workdir"] == "/home/alice/run"


def test_output_paths_resolve_against_the_jobs_workdir(monkeypatch):
    monkeypatch.setattr(slurm, "_run", lambda cmd, timeout=None: (
        "JobId=42 JobState=RUNNING WorkDir=/home/alice/run StdOut=out.log"))
    # A relative path is relative to the *job's* directory, not to wherever the
    # dashboard happens to be running.
    assert slurm.get_job_output_paths("42")["stdout"] == "/home/alice/run/out.log"


def test_output_paths_absent_for_an_interactive_job(monkeypatch):
    monkeypatch.setattr(slurm, "_run", lambda cmd, timeout=None: (
        "JobId=42 JobState=RUNNING WorkDir=/home/alice StdOut=(null) StdErr=(null)"))
    paths = slurm.get_job_output_paths("42")
    assert paths["stdout"] is None and paths["stderr"] is None


def test_read_output_tail_returns_the_end_of_the_file(tmp_path):
    log = tmp_path / "slurm-42.out"
    log.write_text("".join(f"line {i}\n" for i in range(1000)))
    text, error = slurm.read_output_tail(str(log), max_bytes=200)
    assert error is None
    assert text.endswith("line 999\n")
    assert len(text) <= 200
    # The seek lands mid-line; a half line shown as if it were whole is worse
    # than dropping it.
    assert all(line.startswith("line ") for line in text.splitlines())


def test_read_output_tail_returns_small_files_whole(tmp_path):
    log = tmp_path / "small.out"
    log.write_text("epoch 1\nepoch 2\n")
    assert slurm.read_output_tail(str(log)) == ("epoch 1\nepoch 2\n", None)


def test_read_output_tail_survives_undecodable_bytes(tmp_path):
    log = tmp_path / "binary.out"
    log.write_bytes(b"ok\n\xff\xfe\nmore\n")
    text, error = slurm.read_output_tail(str(log))
    assert error is None and "ok" in text and "more" in text


def test_read_output_tail_explains_a_missing_file(tmp_path):
    text, error = slurm.read_output_tail(str(tmp_path / "nope.out"))
    assert text == ""
    # Node-local scratch is a normal reason for this, and blaming the job would
    # be wrong.
    assert "does not exist yet" in error and "cannot see" in error


def test_read_output_tail_without_a_path():
    text, error = slurm.read_output_tail(None)
    assert text == "" and "no output file" in error

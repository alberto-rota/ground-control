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

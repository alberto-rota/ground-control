"""Which GPUs a session is allocated, across the Slurm setups seen in the wild.

The scenarios cannot all be reproduced on one machine, which is exactly why the
decision lives in a pure function: NVML's device count is the one fact taken
from the hardware, and every case below fixes it explicitly.
"""

from ground_control.utils.slurm import (
    expand_nodelist,
    job_id_from_cgroup,
    parse_job_gpu_indices,
)
from ground_control.utils.system_metrics import resolve_gpu_scope

ON_NODE = {"SLURMD_NODENAME": "gpu01"}


def indices(scope):
    f = scope["filter"]
    return None if f is None else ("none" if f == "none" else sorted(f["indices"]))


def test_no_variables_means_every_visible_gpu():
    assert indices(resolve_gpu_scope({}, device_count=4)) is None


def test_plain_cuda_visible_devices_is_respected():
    scope = resolve_gpu_scope({"CUDA_VISIBLE_DEVICES": "2"}, device_count=4)
    assert indices(scope) == [2] and scope["source"] == "CUDA_VISIBLE_DEVICES"


def test_unconstrained_node_with_relative_cuda_index_uses_slurm_physical_ids():
    """The reported case: NVML sees all 8 GPUs, the job holds GPU 3 only."""
    env = {**ON_NODE, "CUDA_VISIBLE_DEVICES": "0", "SLURM_STEP_GPUS": "3"}
    scope = resolve_gpu_scope(env, device_count=8)
    assert indices(scope) == [3]
    assert scope["source"] == "SLURM_STEP_GPUS" and "relative" in scope["note"]


def test_constrained_node_keeps_the_relative_index():
    """With a device cgroup NVML renumbers too, so CUDA's 0 is right."""
    env = {**ON_NODE, "CUDA_VISIBLE_DEVICES": "0", "SLURM_STEP_GPUS": "3"}
    assert indices(resolve_gpu_scope(env, device_count=1)) == [0]


def test_matching_cuda_and_slurm_indices_are_simply_applied():
    env = {**ON_NODE, "CUDA_VISIBLE_DEVICES": "1,3", "SLURM_JOB_GPUS": "1,3"}
    assert indices(resolve_gpu_scope(env, device_count=4)) == [1, 3]


def test_slurm_ids_beyond_the_visible_devices_mean_already_restricted():
    env = {**ON_NODE, "SLURM_JOB_GPUS": "5"}
    scope = resolve_gpu_scope(env, device_count=1)
    assert indices(scope) is None and "already restricted" in scope["note"]


def test_slurm_ids_on_an_unconstrained_node_filter():
    env = {**ON_NODE, "SLURM_JOB_GPUS": "5"}
    assert indices(resolve_gpu_scope(env, device_count=8)) == [5]


def test_step_gpus_win_over_job_gpus():
    env = {**ON_NODE, "SLURM_STEP_GPUS": "2", "SLURM_JOB_GPUS": "2,3"}
    assert indices(resolve_gpu_scope(env, device_count=8)) == [2]


def test_salloc_shell_on_the_login_node_ignores_the_compute_nodes_gpus():
    env = {"SLURM_JOB_GPUS": "1", "SLURM_JOB_NODELIST": "gpu[01-02]"}
    scope = resolve_gpu_scope(env, device_count=4, hostname="login1.cluster")
    assert indices(scope) is None and scope["source"] is None
    # ...but on a node of the job, without SLURMD_NODENAME, they apply.
    on_node = resolve_gpu_scope(env, device_count=4, hostname="gpu02.cluster")
    assert indices(on_node) == [1]


def test_cuda_none_sentinel_hides_everything():
    assert indices(resolve_gpu_scope({"CUDA_VISIBLE_DEVICES": "-1"}, 4)) == "none"


def test_uuid_specs_are_absolute():
    scope = resolve_gpu_scope({"CUDA_VISIBLE_DEVICES": "GPU-abc"}, device_count=4)
    assert scope["filter"]["uuids"] == {"gpu-abc"}


def test_controller_lookup_when_no_variable_is_set():
    """An ssh session adopted into a job inherits no SLURM_* variables."""
    scope = resolve_gpu_scope({}, device_count=8, job_lookup=lambda: ("77", [6]))
    assert indices(scope) == [6] and scope["source"] == "scontrol (job 77)"
    empty = resolve_gpu_scope({}, device_count=8, job_lookup=lambda: ("77", []))
    assert indices(empty) == "none"


def test_no_controller_lookup_from_an_salloc_shell_elsewhere():
    """The login node is not in the job: asking would block startup for nothing."""
    calls = []
    env = {"SLURM_JOB_ID": "77", "SLURM_JOB_NODELIST": "gpu[01-02]"}
    resolve_gpu_scope(env, device_count=2, hostname="login1",
                      job_lookup=lambda: calls.append(1))
    assert calls == []


def test_controller_failure_never_breaks_startup():
    def boom():
        raise RuntimeError("slurmctld timeout")
    assert indices(resolve_gpu_scope({}, device_count=8, job_lookup=boom)) is None


# ------------------------------------------------------------------- parsers

def test_expand_nodelist():
    assert expand_nodelist("gpu[01-03,7],cpu5") == ["gpu01", "gpu02", "gpu03", "gpu7", "cpu5"]
    assert expand_nodelist("(null)") == []


def test_parse_job_gpu_indices_per_node():
    output = """JobId=1 JobName=x
   NodeList=gpu[01-02]
     Nodes=gpu01 CPU_IDs=0-7 Mem=1000 GRES=gpu:a100:2(IDX:0,2)
     Nodes=gpu02 CPU_IDs=0-7 Mem=1000 GRES=gpu:a100:1(IDX:3)
"""
    assert parse_job_gpu_indices(output, "gpu01.cluster") == [0, 2]
    assert parse_job_gpu_indices(output, "gpu02") == [3]
    assert parse_job_gpu_indices(output, "login1") is None
    legacy = "     Nodes=n[1-4] CPU_IDs=0 Mem=0 GRES_IDX=gpu(IDX:0-1)"
    assert parse_job_gpu_indices(legacy, "n3") == [0, 1]


def test_job_id_from_cgroup():
    text = "0::/system.slice/slurmstepd.scope/job_4242/step_extern/user/task_0\n"
    assert job_id_from_cgroup(text) == "4242"
    assert job_id_from_cgroup("0::/user.slice/user-1000.slice\n") is None

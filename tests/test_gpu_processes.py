from ground_control.utils.system_metrics import _gpu_process_command_fields
from ground_control.widgets.gpu import (
    PROC_COLUMNS,
    _pid_width,
    _proc_command,
    format_process_line,
)


def test_gpu_process_python_module_keeps_module_name_when_command_is_short():
    command, script = _gpu_process_command_fields(
        ["python", "-m", "torch.distributed.run", "train.py"],
        process_name="python",
        raw_command="-m",
    )

    assert command == "python -m torch.distributed.run train.py"
    assert script == "-m torch.distributed.run"


def test_gpu_process_python_script_label_is_preserved():
    command, script = _gpu_process_command_fields(
        ["python", "-u", "/work/train.py"],
        process_name="python3",
        raw_command="python -u /work/train.py",
    )

    assert command == "python -u /work/train.py"
    assert script == "/work/train.py"


def test_gpu_process_argv_is_used_when_raw_command_is_unavailable():
    command, script = _gpu_process_command_fields(
        ["python", "-m", "my_package.worker"],
        process_name="python",
    )

    assert command == "python -m my_package.worker"
    assert script == "-m my_package.worker"


def test_gpu_process_display_repairs_legacy_option_only_script():
    assert (
        _proc_command(
            {
                "script": "-m",
                "command": "python -m torch.distributed.run train.py",
                "name": "python",
            }
        )
        == "python -m torch.distributed.run train.py"
    )


def test_gpu_process_header_prioritizes_command_over_gpu_memory():
    header = format_process_line({}, 80, header=True)

    assert "GPU MEM" not in header
    assert "CPU" not in header
    assert "HOST" not in header
    assert [column[1] for column in PROC_COLUMNS] == ["USER", "PID"]
    assert "COMMAND" in header
    assert next(column[2] for column in PROC_COLUMNS if column[0] == "pid") == 3
    assert all(column[0] != "gpu_memory" for column in PROC_COLUMNS)


def test_gpu_process_pid_width_matches_largest_pid():
    assert _pid_width([{"pid": 12}, {"pid": 1234567}]) == 7
    assert _pid_width([{"pid": 12}]) == 3


def test_gpu_process_signal_header_is_named_signal():
    from ground_control.widgets.gpu import GPUProcessList

    # The label is part of compose output; keep the assertion focused on the
    # user-facing name rather than the button implementation.
    assert GPUProcessList.SIGNAL_HEADER == "[bold] SIGNAL[/]"

"""Tests for widget behaviour when the sample comes from another machine.

In Slurm job focus the panels render a compute node's metrics while gc itself
runs on a login node. Anything a widget derives from *local* state is therefore
wrong in that mode, and has to be suppressed rather than silently mislabelled.
"""
import os

os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/gc-test-config")

from ground_control.widgets.cpu import CPUWidget
from ground_control.widgets.memory import MemoryWidget


class _Mem:
    """Stand-in for a psutil memory named tuple."""

    def __init__(self, total, used):
        self.total = total
        self.used = used
        self.percent = 100.0 * used / total if total else 0.0
        self.available = total - used


def test_remote_cpu_falls_back_to_all_cores():
    widget = CPUWidget("CPU", id="cpu_remote")
    widget._remote = True
    percentages = [10.0, 20.0, 30.0, 40.0]
    # Both locally-derived modes must degrade to the full core list: affinity
    # comes from this process, and "user" from a scan of local processes.
    for mode in ("affinity", "user"):
        values, indices = widget._get_display_for_mode(mode, percentages, 4)
        assert values == percentages
        assert indices == [0, 1, 2, 3]


def test_remote_cpu_does_not_scan_local_processes(monkeypatch):
    """The fallback must happen before any psutil scan, not after."""
    import psutil

    def explode(*args, **kwargs):
        raise AssertionError("process_iter must not run for a remote sample")

    monkeypatch.setattr(psutil, "process_iter", explode)
    widget = CPUWidget("CPU", id="cpu_remote2")
    widget._remote = True
    values, _indices = widget._get_display_for_mode("user", [1.0, 2.0], 2)
    assert values == [1.0, 2.0]


def test_local_cpu_still_uses_mode_specific_view(monkeypatch):
    widget = CPUWidget("CPU", id="cpu_local")
    widget._remote = False
    monkeypatch.setattr(widget, "_get_affinity_cpus", lambda: [1, 3])
    values, indices = widget._get_display_for_mode(
        "affinity", [10.0, 20.0, 30.0, 40.0], 4)
    assert indices == [1, 3]
    assert values == [20.0, 40.0]


def test_memory_display_title_preserves_suffix():
    """The memory panel writes live sizes into its border every render."""
    widget = MemoryWidget("Memory")
    widget.set_title_suffix(" — job 42 @ node01")
    widget.set_display_title("RAM [755 GB] SWAP [8 GB]")
    assert widget.border_title == "RAM [755 GB] SWAP [8 GB] — job 42 @ node01"
    # Identity key untouched, so the app can still find this panel.
    assert widget.title == "Memory"


def test_memory_display_title_preserves_alert_marker():
    from ground_control.utils.alerts import CRIT

    widget = MemoryWidget("Memory")
    widget.set_alert(CRIT)
    widget.set_display_title("RAM [755 GB] SWAP [8 GB]")
    assert widget.border_title.startswith("■ ")
    assert "RAM [755 GB]" in widget.border_title

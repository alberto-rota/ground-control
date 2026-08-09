"""Tests for ground_control.utils.formatting pure helpers."""
from ground_control.utils.formatting import (
    align,
    format_size,
    format_throughput,
    substitute_plot_timeframe,
)


def test_align_left_right_center_and_trim():
    assert align("ab", 5, "left") == "ab   "
    assert align("ab", 5, "right") == "   ab"
    assert align("abc", 3, "center") == "abc"
    # Trimming keeps the correct side
    assert align("abcdef", 3, "left") == "abc"
    assert align("abcdef", 3, "right") == "def"


def test_format_size_from_bytes():
    assert format_size(0) == "0 B"
    assert format_size(1024 ** 2) == "1 MB"
    assert format_size(1024 ** 3) == "1 GB"
    assert format_size(2 * 1024 ** 4) == "2 TB"


def test_format_size_in_gb():
    assert format_size(1, in_gb=True) == "1 GB"
    assert format_size(2048, in_gb=True) == "2 TB"
    assert format_size(0.5, in_gb=True) == "512 MB"


def test_format_throughput_units():
    assert format_throughput(0) == "0 MB/s"
    assert format_throughput(1) == "1 MB/s"
    assert format_throughput(2048) == "2 GB/s"
    assert format_throughput(0.5) == "512 KB/s"


def test_format_size_negative_clamped():
    assert format_size(-5, in_gb=True) == "0 B"


def _border(width: int) -> str:
    return "┌" + "─" * width + "┐\n└" + "─" * width + "┘"


def test_substitute_plot_timeframe_seconds_window():
    """A <=60s window should be labelled in seconds and keep the border length."""
    out = substitute_plot_timeframe(_border(6), 60)
    bottom = out.splitlines()[-1]
    assert bottom.startswith("└") and bottom.endswith("┘")
    assert len(bottom) == len("└" + "─" * 6 + "┘")
    assert "s" in bottom and "m" not in bottom


def test_substitute_plot_timeframe_minutes_window():
    """A >60s window (e.g. 240s at 2s/sample x 120) should be labelled in minutes."""
    out = substitute_plot_timeframe(_border(10), 240)
    bottom = out.splitlines()[-1]
    assert "m" in bottom
    # 240s -> 0m .. 2m .. 4m
    assert "4m" in bottom

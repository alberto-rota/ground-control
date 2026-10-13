"""Plotting a subset of a GPU's processes."""

from collections import deque

from ground_control.widgets.gpu import GPUWidget, process_sample, selected_series


def proc(pid, mem_gb, sm):
    return {"pid": pid, "gpu_memory_bytes": None if mem_gb is None else mem_gb * 1000**3,
            "sm_util_percent": sm}


def test_process_sample_uses_the_device_reading_units():
    sample = process_sample([proc(1, 2.5, 40), {"pid": None}, "junk"])
    assert sample == {1: (2.5, 40.0)}


def test_selected_series_sums_only_the_chosen_pids():
    history = [process_sample([proc(1, 2, 30), proc(2, 4, 50), proc(3, 8, 10)]),
               process_sample([proc(1, 2, 35), proc(3, 8, 5)])]
    ram, usage, available = selected_series(history, {1, 2})
    assert ram == [6.0, 2.0]          # pid 2 exited: it contributes nothing
    assert usage == [80.0, 35.0] and available


def test_usage_unavailable_only_when_no_selected_process_reports_it():
    history = [process_sample([proc(1, 2, None), proc(2, 1, None)])]
    assert selected_series(history, {1, 2})[2] is False
    history = [process_sample([proc(1, 2, None), proc(2, 1, 20)])]
    assert selected_series(history, {1, 2})[1:] == ([20.0], True)


def test_summed_usage_is_capped_at_100():
    history = [process_sample([proc(1, 1, 70), proc(2, 1, 60)])]
    assert selected_series(history, {1, 2})[1] == [100.0]


def test_widget_plots_the_selection_and_forgets_pids_that_left_the_window():
    widget = GPUWidget("GPU @ test", id="gpu_0", history_size=3)
    widget.first = False
    widget.rerender = lambda: None
    widget.update_content("g", 90, 10, 80, processes=[proc(1, 3, 40), proc(2, 5, 50)])
    widget.selected_pids = {1}
    ram, usage, _ = widget.plot_series()
    assert ram == [3.0] and usage == [40.0]
    assert widget.plot_series is not None
    # Pid 1 exits; once it is out of all three history slots it is dropped.
    for _ in range(3):
        widget.update_content("g", 90, 10, 80, processes=[proc(2, 5, 50)])
    assert widget.selected_pids == set()
    assert widget.plot_series()[0] == list(widget.gpu_ram_history)


def test_unavailable_usage_is_labelled_not_zero():
    from rich.text import Text

    widget = GPUWidget("GPU @ test", id="gpu_0")
    widget.max_val = 80
    bar = Text.from_markup(widget.create_center_bar(10, -1.0, 60)).plain
    assert "UNAV" in bar and "0.0 %" not in bar

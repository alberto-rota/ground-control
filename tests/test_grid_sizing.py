"""Tests for panel proportions: the pure weight arithmetic and the grid widget.

The arithmetic tests need no Textual at all. The widget tests drive a real
``ResizableGrid`` through Textual's pilot, since the drag path depends on actual
laid-out panel regions -- the thing a unit test of the maths cannot check.
"""
import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Static

from ground_control.utils.grid_sizing import (
    MAX_WEIGHT,
    MIN_WEIGHT,
    clamp_weight,
    drag_weights,
    is_default,
    normalize_weights,
    nudge_weight,
    weights_to_template,
)
from ground_control.widgets.resizable_grid import ResizableGrid


# ------------------------------------------------------------------ arithmetic

def test_clamp_holds_the_range_and_rounds():
    assert clamp_weight(1.0) == 1.0
    assert clamp_weight(99.0) == MAX_WEIGHT
    assert clamp_weight(-4.0) == MIN_WEIGHT
    assert clamp_weight(1.23456) == 1.235


def test_clamp_survives_junk():
    """A corrupt config must not take the dashboard down with it."""
    assert clamp_weight(None) == 1.0
    assert clamp_weight("wide") == 1.0
    assert clamp_weight(float("nan")) == 1.0


def test_normalize_pads_and_truncates_to_the_track_count():
    assert normalize_weights([2.0], 3) == [2.0, 1.0, 1.0]
    assert normalize_weights([2.0, 1.5, 1.0, 3.0], 2) == [2.0, 1.5]
    assert normalize_weights(None, 2) == [1.0, 1.0]
    assert normalize_weights([1.0], 0) == []


def test_template_rendering():
    assert weights_to_template([1.0, 1.4, 0.5]) == "1fr 1.4fr 0.5fr"


def test_is_default():
    assert is_default([1.0, 1.0])
    assert not is_default([1.0, 1.2])


def test_nudge_touches_only_its_own_track():
    assert nudge_weight([1.0, 1.0, 1.0], 1, 0.2) == [1.0, 1.2, 1.0]
    assert nudge_weight([1.0, 1.0], 5, 0.2) == [1.0, 1.0]


def test_nudge_saturates_rather_than_vanishing():
    """Holding the shrink key must leave the panel visible, not zero-width."""
    weights = [1.0, 1.0]
    for _ in range(50):
        weights = nudge_weight(weights, 0, -0.2)
    assert weights[0] == MIN_WEIGHT


def test_drag_conserves_the_pair_and_leaves_others_alone():
    # Three equal 30-cell columns; drag the first boundary 6 cells right.
    result = drag_weights([1.0, 1.0, 1.0], 0, (30, 30), 6, min_cells=12)
    assert round(result[0] + result[1], 3) == 2.0, "pair weight must be conserved"
    assert result[0] > result[1]
    assert result[2] == 1.0, "an untouched track must not move"


def test_drag_respects_the_minimum_cell():
    result = drag_weights([1.0, 1.0], 0, (30, 30), -100, min_cells=12)
    # 12 of 60 cells is a fifth of the pair's combined weight of 2, plus the
    # half-cell boundary bias -- so the floor lands on 12, never on 11.
    assert result == [0.417, 1.583]
    assert round(sum(result), 2) == 2.0


def test_drag_splits_when_the_pair_is_below_two_minimums():
    """Bounds that would cross must still yield a usable, near-even pair."""
    result = drag_weights([1.0, 1.0], 0, (5, 5), -100, min_cells=12)
    assert round(sum(result), 2) == 2.0
    assert abs(result[0] - result[1]) < 0.25


def test_drag_rejects_the_outer_edge_and_bad_input():
    assert drag_weights([1.0, 1.0], 1, (30, 30), 5, 12) == [1.0, 1.0]
    assert drag_weights([1.0, 1.0], 0, (0, 0), 5, 12) == [1.0, 1.0]
    assert drag_weights([1.0, 1.0], 0, None, 5, 12) == [1.0, 1.0]


# --------------------------------------------------------------- widget/pilot

class GridApp(App):
    """Four panels in a 2x2 ResizableGrid, sized like a small terminal."""

    # `height: 100%` mirrors MetricWidget: a panel fills its cell, which is what
    # makes a cell's bounds readable from its panel's region. An auto-height
    # Static would sit inside its cell and misreport where the boundary is.
    CSS = """
    ResizableGrid { width: 100%; height: 100%; }
    Static { border: round white; height: 100%; min-height: 0; }
    """

    def compose(self) -> ComposeResult:
        self.grid = ResizableGrid()
        with self.grid:
            for name in ("a", "b", "c", "d"):
                yield Static(name, id=name)

    def on_mount(self) -> None:
        self.grid.set_tracks(2, 2)


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
        asyncio.set_event_loop(previous)


def test_weights_change_the_panels_actual_widths():
    """The end-to-end check: a weight must move real cells on screen."""
    async def scenario():
        app = GridApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            before = app.query_one("#a", Static).region.width
            app.grid.nudge("columns", 0, 1.0)
            await pilot.pause()
            after = app.query_one("#a", Static).region.width
            assert after > before
            # The row-mate gave up the space; the row below is untouched.
            assert app.query_one("#b", Static).region.width < before
            assert app.query_one("#c", Static).region.width == after

            app.grid.reset_tracks()
            await pilot.pause()
            assert app.query_one("#a", Static).region.width == before

    _run(scenario())


def test_dragging_a_shared_border_resizes_both_panels():
    async def scenario():
        app = GridApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            panel_a = app.query_one("#a", Static)
            # Pilot offsets are relative to the widget they target, panel regions
            # are in screen coordinates; convert rather than relying on the grid
            # happening to sit at the screen origin.
            origin = app.grid.region.offset
            border_x = panel_a.region.right - 1 - origin.x
            border_y = panel_a.region.bottom - 1 - origin.y
            width_before = panel_a.region.width
            height_before = panel_a.region.height

            # Grab the corner: one drag, both axes.
            await pilot.mouse_down(app.grid, offset=(border_x, border_y))
            await pilot.pause()
            assert app.grid._drag is not None
            # hover is the pilot's MouseMove; the grid has captured the mouse,
            # so the event reaches it wherever the pointer nominally is.
            await pilot.hover(app.grid, offset=(border_x + 8, border_y + 3))
            await pilot.pause()
            await pilot.mouse_up(app.grid, offset=(border_x + 8, border_y + 3))
            await pilot.pause()

            assert app.grid._drag is None, "drag must end on mouse up"
            # Exact, not approximate: the border must land under the cursor.
            assert panel_a.region.width == width_before + 8
            assert panel_a.region.height == height_before + 3
            assert app.grid.column_weights[0] > app.grid.column_weights[1]
            assert app.grid.row_weights[0] > app.grid.row_weights[1]

    _run(scenario())


def test_clicking_a_panel_interior_starts_no_drag():
    """Only borders resize; a click in the middle of a panel is just a click."""
    async def scenario():
        app = GridApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            region = app.query_one("#a", Static).region
            await pilot.mouse_down(
                app.grid, offset=(region.x + region.width // 2,
                                  region.y + region.height // 2))
            await pilot.pause()
            assert app.grid._drag is None

    _run(scenario())


def test_single_track_axis_refuses_to_nudge():
    """Vertical layout has one column: there is nothing to take width from."""
    async def scenario():
        app = GridApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.grid.set_tracks(1, 4)
            await pilot.pause()
            assert app.grid.nudge("columns", 0, 0.2) is False
            assert app.grid.nudge("rows", 0, 0.2) is True

    _run(scenario())


def test_set_tracks_keeps_weights_across_a_changed_panel_count():
    """Hiding a panel must not throw away the proportions of the rest."""
    async def scenario():
        app = GridApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.grid.nudge("columns", 0, 0.5)
            app.grid.set_tracks(3, 2)
            await pilot.pause()
            assert app.grid.column_weights == [1.5, 1.0, 1.0]

    _run(scenario())

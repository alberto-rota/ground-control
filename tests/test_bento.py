"""Bento packing (pure) and the grid that lays panels out from it (pilot)."""

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Static

from ground_control.utils.bento import (
    MAX_COLUMNS,
    bento_placements,
    clamp_span,
    fill_holes,
    pack,
    parse_bento_config,
    resize_span,
    row_count,
    uniform_placements,
)
from ground_control.widgets.resizable_grid import ResizableGrid


def _cells(placements):
    """Every (column, row) covered, asserting no two placements overlap."""
    seen = {}
    for index, (c, r, w, h) in enumerate(placements):
        for x in range(c, c + w):
            for y in range(r, r + h):
                assert (x, y) not in seen, f"placements {seen[(x, y)]} and {index} overlap"
                seen[(x, y)] = index
    return seen


# ------------------------------------------------------------------ packing

def test_uniform_placements_fill_in_reading_order():
    assert uniform_placements(5, 2) == [
        (0, 0, 1, 1), (1, 0, 1, 1), (0, 1, 1, 1), (1, 1, 1, 1), (0, 2, 1, 1)]


def test_pack_wraps_a_wide_panel_instead_of_squashing_it():
    """Textual's own grid squashes a 2-wide cell placed in the last column."""
    placements = pack([(1, 1), (1, 1), (2, 1)], 3)
    assert placements[2] == (0, 1, 2, 1)


def test_pack_is_dense_a_later_small_panel_fills_an_earlier_hole():
    placements = pack([(1, 1), (2, 1), (2, 1), (1, 1)], 3)
    # (2,1) cannot fit after the first wide panel's row; the last 1x1 can.
    assert placements[3] == (2, 1, 1, 1)
    _cells(placements)


def test_fill_holes_leaves_a_fully_tiled_grid_with_nothing_moved():
    spans = [(2, 1), (1, 1), (1, 1), (1, 1), (1, 1), (2, 2)]
    packed = pack(spans, 4)
    filled = fill_holes(packed, 4)
    cells = _cells(filled)
    assert len(cells) == 4 * row_count(filled), "a bento should have no gaps"
    # Panels only grow: every top-left corner stays where packing put it.
    assert [p[:2] for p in filled] == [p[:2] for p in packed]
    assert all(f[2] >= p[2] and f[3] >= p[3] for f, p in zip(filled, packed))


def test_default_dashboard_shape_tiles_four_columns():
    # CPU, Memory, Temperature, Disk, Network, GPU, Slurm with default spans.
    spans = [(2, 1), (1, 1), (1, 1), (1, 1), (1, 1), (2, 2), (2, 1)]
    placements = bento_placements(spans, 4)
    assert len(_cells(placements)) == 4 * row_count(placements)


def test_spans_are_clamped_to_the_column_count():
    assert clamp_span((5, 9), 3) == (3, 4)
    assert clamp_span("junk", 3) == (1, 1)
    assert pack([(6, 1)], 2) == [(0, 0, 2, 1)]


def test_resize_span_reports_limits_as_none():
    assert resize_span((1, 1), "columns", 1, 4) == (2, 1)
    assert resize_span((1, 1), "rows", -1, 4) is None
    assert resize_span((4, 1), "columns", 1, 4) is None


def test_parse_bento_config_tolerates_garbage():
    assert parse_bento_config(None)[1] == {}
    columns, spans = parse_bento_config(
        {"columns": 99, "spans": {"Memory": [3, 2], "bad": "x", "short": [1]}})
    assert columns == MAX_COLUMNS
    assert spans == {"Memory": (3, 2)}


# -------------------------------------------------------------------- pilot

class BentoApp(App):
    CSS = """
    ResizableGrid { width: 100%; height: 100%; }
    Static { border: round white; height: 100%; min-height: 0; }
    """
    SPANS = {"a": (2, 1), "b": (1, 2), "c": (1, 1), "d": (1, 1)}

    def compose(self) -> ComposeResult:
        self.spans = dict(self.SPANS)  # per instance: tests change it
        self.grid = ResizableGrid()
        with self.grid:
            for name in "abcd":
                yield Static(name, id=name)

    def on_mount(self) -> None:
        self.grid.set_bento(3, lambda w: self.spans[w.id])


def _run(coro):
    """Drive a coroutine without leaving the thread's event loop closed."""
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


def test_panels_occupy_the_cells_their_placements_name():
    async def scenario():
        app = BentoApp()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            a, b, c, d = (app.query_one(f"#{n}") for n in "abcd")
            assert app.grid.placement_of(a) == (0, 0, 2, 1)
            assert app.grid.placement_of(b) == (2, 0, 1, 2)
            # a spans two of three equal columns, b two of two equal rows.
            assert a.region.width == 60 and b.region.height == 30
            assert c.region.x == 0 and d.region.x == 30
            assert c.region.y == a.region.bottom

    _run(scenario())


def test_span_change_is_rendered_immediately():
    """Regression: the grid's own arrangement cache made spans lag a step."""
    async def scenario():
        app = BentoApp()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            app.spans["c"] = (2, 1)
            app.grid.sync_tracks()
            await pilot.pause()
            c = app.query_one("#c")
            assert app.grid.placement_of(c) == (0, 1, 2, 1)
            assert c.region.width == 60

    _run(scenario())


def test_a_border_inside_a_wide_panel_is_not_a_drag_handle():
    """Column 0|1 is a boundary in row 1, but the middle of panel a in row 0."""
    async def scenario():
        app = BentoApp()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            origin = app.grid.region.offset
            a, c = app.query_one("#a"), app.query_one("#c")
            await pilot.mouse_down(app.grid, offset=(c.region.right - 1 - origin.x,
                                                     a.region.y + 3 - origin.y))
            await pilot.pause()
            assert app.grid._drag is None
            await pilot.mouse_up(app.grid)
            # The same x on row 1 is c's right border, which does resize.
            await pilot.mouse_down(app.grid, offset=(c.region.right - 1 - origin.x,
                                                     c.region.y + 3 - origin.y))
            await pilot.pause()
            assert app.grid._drag is not None and app.grid._drag.column_index == 0
            await pilot.mouse_up(app.grid)

    _run(scenario())


def test_nudging_every_track_of_an_axis_is_refused():
    """Equal growth of all tracks changes nothing on screen but creeps weights."""
    async def scenario():
        app = BentoApp()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            before = list(app.grid.column_weights)
            assert app.grid.nudge_tracks("columns", [0, 1, 2], 0.2) is False
            assert app.grid.column_weights == before
            assert app.grid.nudge_tracks("columns", [0, 1], 0.2) is True

    _run(scenario())

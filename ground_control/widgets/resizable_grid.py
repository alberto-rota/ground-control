"""The dashboard grid, with resizable rows and columns.

Textual ships no splitter widget, so the drag handling here is hand-rolled: the
grid has no gutters, which means the boundary between two cells *is* the two
adjacent panel borders, and a ``MouseDown`` on a border bubbles up from the panel
to this container. Both the keyboard shortcuts (in ``app.py``) and the drag end
up in the same place -- the ``fr`` weight lists in :mod:`utils.grid_sizing` --
so there is one source of truth for panel proportions and one thing to persist.

This widget owns the geometry; the app owns persistence and reacts to
:class:`ResizableGrid.TracksResized`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from textual.containers import Grid
from textual.message import Message

from ..utils.grid_sizing import (
    drag_weights,
    is_default,
    normalize_weights,
    nudge_weight,
    weights_to_template,
)


@dataclass
class _Drag:
    """State captured when a border drag starts.

    Both axes are optional and independent: grabbing a panel *corner* is simply
    a drag with a column boundary and a row boundary at once. Sizes and weights
    are snapshotted here because every move is computed from the drag origin, so
    a slow drag over many events cannot accumulate rounding drift.
    """

    origin_x: int
    origin_y: int
    column_index: int | None = None
    column_sizes: tuple[int, int] = (0, 0)
    column_weights: list[float] = field(default_factory=list)
    row_index: int | None = None
    row_sizes: tuple[int, int] = (0, 0)
    row_weights: list[float] = field(default_factory=list)


class ResizableGrid(Grid):
    """A ``Grid`` whose track proportions can be nudged or dragged."""

    # Floors for a dragged track. A panel narrower than this has no room left for
    # its bar and labels, and one shorter than this has no plot at all.
    MIN_CELL_WIDTH = 12
    MIN_CELL_HEIGHT = 4

    class TracksResized(Message):
        """The user changed track proportions; the app should persist them."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.column_weights: list[float] = []
        self.row_weights: list[float] = []
        self._drag: _Drag | None = None

    # ----------------------------------------------------------------- tracks

    def set_tracks(self, columns: int, rows: int,
                   column_weights=None, row_weights=None) -> None:
        """Set the track counts and their weights, then apply them.

        Called on every rebuild, so it also cancels any drag in flight: the panel
        set may have just changed underneath it and the snapshot is stale.
        """
        self._drag = None
        self.styles.grid_size_columns = max(1, columns)
        self.styles.grid_size_rows = max(1, rows)
        self.column_weights = normalize_weights(
            self.column_weights if column_weights is None else column_weights,
            max(1, columns))
        self.row_weights = normalize_weights(
            self.row_weights if row_weights is None else row_weights,
            max(1, rows))
        self.apply_tracks()

    def apply_tracks(self) -> None:
        """Write the current weights to the grid's row/column templates."""
        self.styles.grid_columns = weights_to_template(self.column_weights)
        self.styles.grid_rows = weights_to_template(self.row_weights)

    def _weights_for(self, orientation: str) -> list[float]:
        return self.column_weights if orientation == "columns" else self.row_weights

    def _store(self, orientation: str, weights: list[float]) -> None:
        if orientation == "columns":
            self.column_weights = weights
        else:
            self.row_weights = weights

    def nudge(self, orientation: str, index: int, delta: float) -> bool:
        """Grow/shrink one track by ``delta``. False if it could not apply."""
        weights = self._weights_for(orientation)
        if len(weights) < 2 or not (0 <= index < len(weights)):
            return False
        updated = nudge_weight(weights, index, delta)
        if updated == weights:
            return False  # already clamped at the limit
        self._store(orientation, updated)
        self.apply_tracks()
        return True

    def reset_tracks(self) -> bool:
        """Return every track to an equal share. False if already equal."""
        if is_default(self.column_weights) and is_default(self.row_weights):
            return False
        self.column_weights = normalize_weights([], len(self.column_weights))
        self.row_weights = normalize_weights([], len(self.row_weights))
        self.apply_tracks()
        return True

    # --------------------------------------------------------------- geometry

    def _cell_bounds(self) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
        """Map column/row index to its ``(start, end)`` in screen coordinates.

        Derived from the panels' actual regions rather than by re-deriving the
        layout arithmetic, so it cannot disagree with what is on screen. Index
        order matches ``GridLayout.arrange``, which lays out *displayed*
        children in order -- hidden panels occupy no cell.
        """
        columns: dict[int, tuple[int, int]] = {}
        rows: dict[int, tuple[int, int]] = {}
        column_count = max(1, int(self.styles.grid_size_columns or 1))
        try:
            children = [child for child in self.children if child.display]
            for index, child in enumerate(children):
                region = child.region
                if not region.area:
                    continue
                columns.setdefault(index % column_count, (region.x, region.right - 1))
                rows.setdefault(index // column_count, (region.y, region.bottom - 1))
        except Exception:
            # Regions are unavailable before the first layout; no geometry means
            # no drag, which is the right outcome rather than an error.
            return {}, {}
        return columns, rows

    @staticmethod
    def _boundary_at(bounds: dict[int, tuple[int, int]], position: int,
                     count: int) -> int | None:
        """Index of the track whose *trailing* border sits under ``position``.

        The grab zone is exactly two cells: the left/top panel's own border and
        its neighbour's, which are adjacent because the grid has no gutter. Only
        internal boundaries qualify -- the grid's outer edge resizes nothing.
        """
        for index in range(count - 1):
            if index in bounds and index + 1 in bounds:
                edge = bounds[index][1]
                if edge <= position <= edge + 1:
                    return index
        return None

    @staticmethod
    def _sizes(bounds: dict[int, tuple[int, int]], index: int) -> tuple[int, int]:
        start_a, end_a = bounds[index]
        start_b, end_b = bounds[index + 1]
        return (end_a - start_a + 1, end_b - start_b + 1)

    # ------------------------------------------------------------------ mouse

    def on_mouse_down(self, event) -> None:
        """Start a drag if the click landed on a boundary between two panels."""
        if event.button != 1:
            return
        columns, rows = self._cell_bounds()
        column_index = self._boundary_at(columns, event.screen_x, len(self.column_weights))
        row_index = self._boundary_at(rows, event.screen_y, len(self.row_weights))
        if column_index is None and row_index is None:
            return

        drag = _Drag(origin_x=event.screen_x, origin_y=event.screen_y)
        if column_index is not None:
            drag.column_index = column_index
            drag.column_sizes = self._sizes(columns, column_index)
            drag.column_weights = list(self.column_weights)
        if row_index is not None:
            drag.row_index = row_index
            drag.row_sizes = self._sizes(rows, row_index)
            drag.row_weights = list(self.row_weights)
        self._drag = drag
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event) -> None:
        drag = self._drag
        if drag is None:
            return
        if drag.column_index is not None:
            self.column_weights = drag_weights(
                drag.column_weights, drag.column_index, drag.column_sizes,
                event.screen_x - drag.origin_x, self.MIN_CELL_WIDTH)
        if drag.row_index is not None:
            self.row_weights = drag_weights(
                drag.row_weights, drag.row_index, drag.row_sizes,
                event.screen_y - drag.origin_y, self.MIN_CELL_HEIGHT)
        self.apply_tracks()
        event.stop()

    def on_mouse_up(self, event) -> None:
        if self._drag is None:
            return
        self._drag = None
        self.release_mouse()
        # Posted once per drag, not per move: the app's config save is debounced
        # anyway, and one message per mouse event is pure churn.
        self.post_message(self.TracksResized())
        event.stop()

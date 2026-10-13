"""The dashboard grid, with resizable rows and columns.

Textual ships no splitter widget, so the drag handling here is hand-rolled: the
grid has no gutters, which means the boundary between two cells *is* the two
adjacent panel borders, and a ``MouseDown`` on a border bubbles up from the panel
to this container. Both the keyboard shortcuts (in ``app.py``) and the drag end
up in the same place -- the ``fr`` weight lists in :mod:`utils.grid_sizing` --
so there is one source of truth for panel proportions and one thing to persist.

This widget owns the geometry; the app owns persistence and reacts to
:class:`ResizableGrid.TracksResized`.

Panels are positioned by :class:`PanelLayout` rather than Textual's own grid
layout, from placements computed in :mod:`utils.bento`. That is what makes the
bento mode possible (panels spanning several rows and columns, wrapped and
packed), and it means every mode -- uniform or bento -- knows exactly which
cells each panel covers, so resizing and hit-testing never have to guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Dict, List, Optional, Tuple

from textual._resolve import resolve
from textual.containers import Grid
from textual.css.scalar import Scalar
from textual.geometry import NULL_OFFSET, Region, Size
from textual.layout import ArrangeResult, Layout, WidgetPlacement
from textual.message import Message

from ..utils.bento import (
    DEFAULT_COLUMNS,
    Placement,
    Span,
    bento_placements,
    clamp_columns,
    row_count,
    uniform_placements,
)
from ..utils.grid_sizing import (
    drag_weights,
    is_default,
    normalize_weights,
    nudge_weight,
    weights_to_template,
)


class PanelLayout(Layout):
    """Lay panels out at the placements their :class:`ResizableGrid` computed.

    A trimmed-down ``GridLayout.arrange``: tracks are always ``fr`` weights with
    no gutter, and each child's cell rectangle comes from the grid instead of
    from Textual's forward-only placement cursor. Track sizes go through
    Textual's own ``resolve`` so a weight produces exactly the cells a grid
    template of the same weights would -- the drag arithmetic depends on that.
    """

    name = "panels"

    def __init__(self) -> None:
        # Last resolved tracks as (offset, length) in the container's content
        # area, kept for drag sizing: the same numbers the panels were placed by.
        self.columns: List[Tuple[int, int]] = []
        self.rows: List[Tuple[int, int]] = []

    def arrange(self, parent, children, size: Size, greedy: bool = True) -> ArrangeResult:
        parent.pre_layout(self)
        viewport = parent.app.viewport_size
        placements, column_count, rows_count = parent.compute_placements(children)

        def tracks(weights, count, total):
            scalars = [Scalar.parse(f"{w:g}fr") for w in normalize_weights(weights, count)]
            return resolve(scalars, total, 0, size, viewport)

        columns = tracks(parent.column_weights, column_count, size.width)
        rows = tracks(parent.row_weights, rows_count, size.height)
        self.columns, self.rows = columns, rows

        result: List[WidgetPlacement] = []
        for child in children:
            placement = placements.get(child)
            if placement is None:
                continue
            column, row, width, height = placement
            if column >= len(columns) or row >= len(rows):
                continue
            last_column = columns[min(len(columns) - 1, column + width - 1)]
            last_row = rows[min(len(rows) - 1, row + height - 1)]
            x, y = columns[column][0], rows[row][0]
            cell_size = Size(last_column[0] + last_column[1] - x,
                             last_row[0] + last_row[1] - y)
            box_width, box_height, margin = child._get_box_model(
                cell_size, viewport,
                Fraction(cell_size.width), Fraction(cell_size.height),
                constrain_width=True, greedy=greedy,
            )
            region = (
                Region(x, y, int(box_width + margin.width),
                       int(box_height + margin.height))
                .crop_size(cell_size)
                .shrink(margin)
            )
            styles = child.styles
            offset = (styles.offset.resolve(cell_size, viewport)
                      if styles.has_rule("offset") else NULL_OFFSET)
            result.append(WidgetPlacement(region, offset, margin, child))
        return result


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

    class PanelsSwapped(Message):
        """A mouse drag swapped two panels; the app should persist the order."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._panel_layout = PanelLayout()
        self.styles.layout = self._panel_layout
        self.column_weights: list[float] = []
        self.row_weights: list[float] = []
        # "uniform": one panel per cell in reading order, track counts set by
        # the caller (grid / horizontal / vertical). "bento": panels span cells
        # by `span_for(panel)` and are packed into `bento_columns`.
        self.mode = "uniform"
        self.bento_columns = DEFAULT_COLUMNS
        self.span_for: Optional[Callable[[object], Span]] = None
        # Placements from the most recent layout pass, by panel. Read by the
        # app (which cell is the focused panel in) and by hit-testing.
        self.placements: Dict[object, Placement] = {}
        self.track_counts: Tuple[int, int] = (1, 1)
        self._drag: _Drag | None = None
        # Swap-drag state, independent of the border-resize `_drag` above --
        # a mousedown that does *not* land on a resize boundary is a candidate
        # for dragging that panel onto another one instead. `_swap_candidate`
        # is set immediately (no capture yet, so a plain click still reaches
        # whatever is under the cursor unchanged); it is only promoted to
        # `_swap_drag` once the pointer actually moves onto a *different*
        # panel, which is also the point it becomes visible and captures the
        # mouse.
        self._swap_candidate = None
        self._swap_drag = None
        self._swap_hover = None

    # ----------------------------------------------------------------- tracks

    def set_tracks(self, columns: int, rows: int,
                   column_weights=None, row_weights=None) -> None:
        """Set the track counts and their weights, then apply them.

        Called on every rebuild, so it also cancels any drag in flight: the panel
        set may have just changed underneath it and the snapshot is stale.
        """
        self._cancel_interactions()
        self.mode = "uniform"
        self.styles.grid_size_columns = max(1, columns)
        self.styles.grid_size_rows = max(1, rows)
        self.column_weights = normalize_weights(
            self.column_weights if column_weights is None else column_weights,
            max(1, columns))
        self.row_weights = normalize_weights(
            self.row_weights if row_weights is None else row_weights,
            max(1, rows))
        self.track_counts = (max(1, columns), max(1, rows))
        self.apply_tracks()

    def set_bento(self, columns: int, span_for: Callable[[object], Span],
                  column_weights=None, row_weights=None) -> None:
        """Switch to the bento mode: spans per panel, packed into ``columns``.

        The row count is whatever the packing needs, so it is derived from the
        displayed panels rather than passed in. Saved weights are padded but
        never truncated here: the panel set is often still being mounted when
        this runs, and a transiently short grid must not eat the user's rows.
        """
        self._cancel_interactions()
        self.mode = "bento"
        self.bento_columns = clamp_columns(columns)
        self.span_for = span_for
        if column_weights is not None:
            self.column_weights = list(column_weights)
        if row_weights is not None:
            self.row_weights = list(row_weights)
        self.sync_tracks()

    def sync_tracks(self) -> None:
        """Re-derive track counts from the displayed panels and pad the weights.

        Call after anything that can change the packing: a panel shown, hidden,
        reordered or re-spanned. Uniform modes get their counts from the caller
        (``set_tracks``), so this only refreshes their placements.
        """
        _placements, columns, rows = self.compute_placements(self.displayed_panels())
        if self.mode == "bento":
            self.column_weights = normalize_weights(
                self.column_weights, max(columns, len(self.column_weights)))
            self.row_weights = normalize_weights(
                self.row_weights, max(rows, len(self.row_weights)))
            self.styles.grid_size_columns = columns
            self.styles.grid_size_rows = rows
        self.apply_tracks()

    def apply_tracks(self) -> None:
        """Write the current weights out and re-run layout.

        The templates are informational (``PanelLayout`` reads the weight lists
        directly) but keep the styles truthful for anything that inspects them.
        """
        columns, rows = self.track_counts
        self.styles.grid_columns = weights_to_template(
            normalize_weights(self.column_weights, columns))
        self.styles.grid_rows = weights_to_template(
            normalize_weights(self.row_weights, rows))
        # Weights and spans are this widget's own state, which Textual's
        # arrangement cache (keyed on size and child list) cannot see; and
        # refresh(layout=True) only clears the *ancestors'* caches. Without
        # this a span change can render one step behind.
        self._clear_arrangement_cache()
        self.refresh(layout=True)

    def _cancel_interactions(self) -> None:
        # The panel set may have just changed underneath a drag in flight, so
        # its snapshot of sizes and panels is stale.
        self._drag = None
        self._swap_candidate = None
        self._swap_drag = None
        self._swap_hover = None

    # ------------------------------------------------------------- placement

    def displayed_panels(self) -> list:
        """Children that occupy cells, in order -- hidden panels take none."""
        return [child for child in self.children if child.display]

    def compute_placements(self, children) -> Tuple[Dict[object, Placement], int, int]:
        """Placement per child plus the ``(columns, rows)`` track counts.

        Run on every layout pass (it is cheap: a handful of panels), so the
        placements can never go stale relative to what is on screen.
        """
        children = list(children)
        if self.mode == "bento":
            columns = self.bento_columns
            spans = [self._span(child) for child in children]
            cells = bento_placements(spans, columns)
            rows = row_count(cells)
        else:
            columns = max(1, int(self.styles.grid_size_columns or 1))
            cells = uniform_placements(len(children), columns)
            rows = max(int(self.styles.grid_size_rows or 1), row_count(cells))
        placements = dict(zip(children, cells))
        self.placements = placements
        self.track_counts = (columns, rows)
        return placements, columns, rows

    def _span(self, child) -> Span:
        if self.span_for is None:
            return (1, 1)
        try:
            return self.span_for(child)
        except Exception:  # noqa: BLE001 - a bad span must not break layout
            return (1, 1)

    def placement_of(self, panel) -> Optional[Placement]:
        """``(column, row, width, height)`` of a displayed panel, or None."""
        if panel not in self.placements:
            self.compute_placements(self.displayed_panels())
        return self.placements.get(panel)

    def _weights_for(self, orientation: str) -> list[float]:
        return self.column_weights if orientation == "columns" else self.row_weights

    def _store(self, orientation: str, weights: list[float]) -> None:
        if orientation == "columns":
            self.column_weights = weights
        else:
            self.row_weights = weights

    def _track_count(self, orientation: str) -> int:
        return self.track_counts[0 if orientation == "columns" else 1]

    def nudge(self, orientation: str, index: int, delta: float) -> bool:
        """Grow/shrink one track by ``delta``. False if it could not apply."""
        return self.nudge_tracks(orientation, [index], delta)

    def nudge_tracks(self, orientation: str, indices, delta: float) -> bool:
        """Grow/shrink several tracks -- every track a spanning panel covers.

        False if nothing changed: a single-track axis, or every track already
        at its clamp.
        """
        count = self._track_count(orientation)
        weights = normalize_weights(self._weights_for(orientation),
                                    max(count, len(self._weights_for(orientation))))
        indices = sorted({i for i in indices if 0 <= i < count})
        # Growing every track of an axis keeps the proportions identical: no
        # visible change, while the weights creep towards their cap.
        if count < 2 or not indices or len(indices) >= count:
            return False
        updated = weights
        for index in indices:
            updated = nudge_weight(updated, index, delta)
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

    # -------------------------------------------------------------- ordering

    def apply_order(self, ordered) -> None:
        """Reorder children to match ``ordered``, moving only what's out of place.

        Cell placement follows child order (``compute_placements``), so this is
        also how panel positions are changed -- both the saved-order restore
        on rebuild and a swap go through here. Fixes children left-to-right:
        once position ``idx`` holds the right widget it is never touched
        again, so moving the remaining widgets into place cannot disturb it.
        """
        current = list(self.children)
        for idx, widget in enumerate(ordered):
            if idx < len(current) and current[idx] is widget:
                continue
            self.move_child(widget, before=idx)
            current = list(self.children)

    def swap_children(self, a, b) -> bool:
        """Swap two children's grid positions. False if unusable or a no-op."""
        children = list(self.children)
        if a is b or a not in children or b not in children:
            return False
        i, j = children.index(a), children.index(b)
        children[i], children[j] = children[j], children[i]
        self.apply_order(children)
        return True

    def _panel_at(self, x: int, y: int):
        """The direct grid child whose screen region contains ``(x, y)``, or None.

        Mirrors the app's own focus-walk-up (``_focused_panel``): a hit test
        usually lands on something *inside* a panel (a bar, a button), so this
        walks up until it finds the grid's direct child.
        """
        try:
            widget, _ = self.screen.get_widget_at(x, y)
        except Exception:
            return None
        node = widget
        while node is not None:
            if node.parent is self:
                return node
            node = node.parent
        return None

    @staticmethod
    def _set_swap_armed(widget, armed: bool) -> None:
        """Duck-typed: any grid child may implement ``set_swap_armed``."""
        setter = getattr(widget, "set_swap_armed", None)
        if callable(setter):
            try:
                setter(armed)
            except Exception:  # noqa: BLE001 - a drag must never crash the app
                pass

    # --------------------------------------------------------------- geometry

    def _boundaries_at(self, panel, x: int, y: int) -> tuple[int | None, int | None]:
        """Track boundaries under ``(x, y)`` on ``panel``'s border, per axis.

        A boundary is grabbed on a panel's own border, using the placement it
        was laid out at -- not on a straight line across the grid, because in
        the bento mode a column boundary in one row can be the middle of a wide
        panel in the next. The grab zone is still two cells wide, since the
        neighbour's facing border is the next cell over (there is no gutter).
        Only internal boundaries qualify: the grid's outer edge resizes nothing.
        """
        placement = self.placements.get(panel)
        if placement is None:
            return None, None
        try:
            region = panel.region
        except Exception:
            # Regions are unavailable before the first layout; no geometry
            # means no drag, which is the right outcome rather than an error.
            return None, None
        if not region.area:
            return None, None
        column, row, width, height = placement
        columns, rows = self.track_counts
        column_index = row_index = None
        if x == region.right - 1 and column + width < columns:
            column_index = column + width - 1
        elif x == region.x and column > 0:
            column_index = column - 1
        if y == region.bottom - 1 and row + height < rows:
            row_index = row + height - 1
        elif y == region.y and row > 0:
            row_index = row - 1
        return column_index, row_index

    @staticmethod
    def _sizes(tracks: List[Tuple[int, int]], index: int) -> tuple[int, int]:
        """Cell sizes of tracks ``index`` and ``index + 1`` as last laid out."""
        if not (0 <= index < len(tracks) - 1):
            return (0, 0)
        return (tracks[index][1], tracks[index + 1][1])

    # ------------------------------------------------------------------ mouse

    def on_mouse_down(self, event) -> None:
        """Start a border-resize drag, or arm a same-panel drag as a swap candidate."""
        if event.button != 1:
            return
        panel = self._panel_at(event.screen_x, event.screen_y)
        column_index, row_index = self._boundaries_at(panel, event.screen_x, event.screen_y)
        if column_index is not None or row_index is not None:
            drag = _Drag(origin_x=event.screen_x, origin_y=event.screen_y)
            column_count, row_count_ = self.track_counts
            if column_index is not None:
                drag.column_index = column_index
                drag.column_sizes = self._sizes(self._panel_layout.columns, column_index)
                drag.column_weights = normalize_weights(
                    self.column_weights, max(column_count, len(self.column_weights)))
            if row_index is not None:
                drag.row_index = row_index
                drag.row_sizes = self._sizes(self._panel_layout.rows, row_index)
                drag.row_weights = normalize_weights(
                    self.row_weights, max(row_count_, len(self.row_weights)))
            self._drag = drag
            self.capture_mouse()
            event.stop()
            return

        # Not on a resize boundary: record the panel under the click as a swap
        # candidate, but deliberately do not capture or stop the event yet --
        # this may well just be a click on a button or a tab inside the panel,
        # and that must keep working exactly as if this handler didn't exist.
        self._swap_candidate = panel

    def on_mouse_move(self, event) -> None:
        drag = self._drag
        if drag is not None:
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
            return

        if self._swap_drag is None and self._swap_candidate is not None:
            # Still deciding: only a genuine drag onto a *different* panel
            # promotes this into a swap (and only then does it start stealing
            # events), so a click-and-slight-jitter on a button never does.
            hovered = self._panel_at(event.screen_x, event.screen_y)
            if hovered is None or hovered is self._swap_candidate:
                return
            self._swap_drag = self._swap_candidate
            self._swap_candidate = None
            self._set_swap_armed(self._swap_drag, True)
            self.capture_mouse()
            self._swap_hover = hovered
            self._set_swap_armed(hovered, True)
            event.stop()
            return

        if self._swap_drag is not None:
            hovered = self._panel_at(event.screen_x, event.screen_y)
            if hovered is not self._swap_hover:
                if self._swap_hover is not None and self._swap_hover is not self._swap_drag:
                    self._set_swap_armed(self._swap_hover, False)
                self._swap_hover = hovered if hovered is not self._swap_drag else None
                if self._swap_hover is not None:
                    self._set_swap_armed(self._swap_hover, True)
            event.stop()

    def on_mouse_up(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.release_mouse()
            # Posted once per drag, not per move: the app's config save is
            # debounced anyway, and one message per mouse event is pure churn.
            self.post_message(self.TracksResized())
            event.stop()
            return

        if self._swap_drag is not None:
            origin, target = self._swap_drag, self._swap_hover
            self._swap_drag = None
            self._swap_hover = None
            self._set_swap_armed(origin, False)
            if target is not None and target is not origin:
                self._set_swap_armed(target, False)
            self.release_mouse()
            if target is not None and target is not origin and self.swap_children(origin, target):
                self.post_message(self.PanelsSwapped())
            event.stop()
            return

        # A plain click that never became a drag: nothing was captured or
        # stopped, so let it fall through exactly as if this never ran.
        self._swap_candidate = None

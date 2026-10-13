"""Placement arithmetic for the dashboard grid, including the bento layout.

A *placement* is ``(column, row, width, height)`` in grid cells. The uniform
modes (grid / horizontal / vertical) place one panel per cell in reading order;
the bento mode gives every panel a *span* -- how many columns and rows it
covers -- and packs them into a fixed number of columns.

Textual's own ``GridLayout`` cannot express this: its placement cursor only
moves forward, so a two-wide panel that does not fit at the end of a row is
squashed into the last column rather than wrapped, and a hole left behind is
never revisited. The dashboard therefore lays panels out itself
(``widgets/resizable_grid.PanelLayout``) from the placements computed here.

Kept free of Textual so the packing can be unit-tested on its own.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

Placement = Tuple[int, int, int, int]
Span = Tuple[int, int]

MIN_COLUMNS = 1
MAX_COLUMNS = 8
DEFAULT_COLUMNS = 4
# Taller than this and a panel on an ordinary terminal is mostly empty plot.
MAX_ROW_SPAN = 4

# Starting shape per panel type (see ``GroundControl._get_widget_type``). The
# panels that carry the most per-row detail -- a GPU's plot and process list,
# the CPU's per-core view, the Slurm job table -- start larger; everything
# else starts as a single cell. Anything the user resizes overrides this.
DEFAULT_SPANS: Dict[str, Span] = {
    "cpu": (2, 1),
    "gpu": (2, 2),
    "slurm": (2, 1),
}


def clamp_columns(columns) -> int:
    try:
        value = int(columns)
    except (TypeError, ValueError):
        return DEFAULT_COLUMNS
    return min(max(value, MIN_COLUMNS), MAX_COLUMNS)


def clamp_span(span, columns: int) -> Span:
    """Coerce ``span`` to ``(width, height)`` that fits ``columns``.

    Malformed input (a hand-edited config) becomes ``(1, 1)`` rather than an
    error: a panel of the wrong size is a far smaller problem than a dashboard
    that refuses to start.
    """
    try:
        width, height = int(span[0]), int(span[1])
    except (TypeError, ValueError, IndexError, KeyError):
        width, height = 1, 1
    width = min(max(width, 1), max(1, columns))
    height = min(max(height, 1), MAX_ROW_SPAN)
    return width, height


def default_span(widget_type: str) -> Span:
    return DEFAULT_SPANS.get(widget_type, (1, 1))


def uniform_placements(count: int, columns: int) -> List[Placement]:
    """One panel per cell, in reading order: the grid/horizontal/vertical modes."""
    columns = max(1, int(columns))
    return [(i % columns, i // columns, 1, 1) for i in range(max(0, count))]


def _fits(occupied, column: int, row: int, width: int, height: int,
          columns: int) -> bool:
    if column + width > columns:
        return False
    return all((c, r) not in occupied
               for c in range(column, column + width)
               for r in range(row, row + height))


def pack(spans: Sequence[Span], columns: int) -> List[Placement]:
    """Place each span at the first free position, scanning in reading order.

    This is "dense" auto-placement: every panel restarts the scan from the top
    left, so a small panel later in the order fills a hole a wide one left
    behind. Order still decides precedence -- earlier panels claim the earlier
    cells -- which is why swapping two panels moves them.
    """
    columns = max(1, int(columns))
    occupied = set()
    placements: List[Placement] = []
    for span in spans:
        width, height = clamp_span(span, columns)
        row = 0
        while True:
            column = next((c for c in range(columns - width + 1)
                           if _fits(occupied, c, row, width, height, columns)), None)
            if column is not None:
                break
            row += 1
        for c in range(column, column + width):
            for r in range(row, row + height):
                occupied.add((c, r))
        placements.append((column, row, width, height))
    return placements


def row_count(placements: Sequence[Placement]) -> int:
    return max((row + height for _c, row, _w, height in placements), default=1)


def fill_holes(placements: Sequence[Placement], columns: int) -> List[Placement]:
    """Grow panels into empty cells so the bento has no gaps.

    Packing can leave holes -- a two-wide panel that did not fit at the end of
    a row leaves that cell empty, and the last row is rarely full. A bento with
    blank tiles reads as broken, so each panel is stretched right, then down,
    into whatever free cells border it. Only free cells are taken, so no panel
    moves; it only gets bigger. Cells no rectangle can absorb (rare) stay empty.

    This changes the *effective* spans only. The user's chosen spans are what
    is persisted, so a hole that disappears later (a panel shown again) gives
    the space back.
    """
    columns = max(1, int(columns))
    rows = row_count(placements)
    result = [list(p) for p in placements]
    occupied = set()
    for column, row, width, height in placements:
        for c in range(column, column + width):
            for r in range(row, row + height):
                occupied.add((c, r))

    changed = True
    while changed:
        changed = False
        for p in result:
            column, row, width, height = p
            # Right first: a hole at the end of a row belongs to the panel
            # beside it far more naturally than to the one above.
            while (column + width < columns
                   and all((column + width, r) not in occupied
                           for r in range(row, row + height))):
                for r in range(row, row + height):
                    occupied.add((column + width, r))
                width += 1
                changed = True
            while (row + height < rows
                   and all((c, row + height) not in occupied
                           for c in range(column, column + width))):
                for c in range(column, column + width):
                    occupied.add((c, row + height))
                height += 1
                changed = True
            p[2], p[3] = width, height
    return [tuple(p) for p in result]


def bento_placements(spans: Sequence[Span], columns: int) -> List[Placement]:
    """Pack ``spans`` into ``columns`` and fill the gaps: the bento layout."""
    return fill_holes(pack(spans, columns), columns)


def parse_bento_config(raw) -> Tuple[int, Dict[str, Span]]:
    """Validate the persisted ``bento`` config into ``(columns, spans)``.

    Spans are keyed by panel identity (``GroundControl._panel_identity``). They
    are clamped against ``MAX_COLUMNS`` rather than the configured column count,
    so lowering the column count and raising it again restores a wide panel.
    """
    if not isinstance(raw, dict):
        return DEFAULT_COLUMNS, {}
    columns = clamp_columns(raw.get("columns", DEFAULT_COLUMNS))
    spans: Dict[str, Span] = {}
    raw_spans = raw.get("spans")
    if isinstance(raw_spans, dict):
        for key, value in raw_spans.items():
            if isinstance(value, (list, tuple)) and len(value) == 2:
                spans[str(key)] = clamp_span(value, MAX_COLUMNS)
    return columns, spans


def resize_span(span: Span, axis: str, delta: int, columns: int) -> Optional[Span]:
    """``span`` grown/shrunk by ``delta`` along ``axis``; None if already at a limit."""
    width, height = span
    if axis == "columns":
        width += delta
    else:
        height += delta
    clamped = clamp_span((width, height), columns)
    if clamped == clamp_span(span, columns):
        return None
    return clamped

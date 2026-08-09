"""Per-track proportions for the dashboard grid.

A Textual ``Grid`` sizes its cells from ``grid-rows``/``grid-columns`` templates,
so panel proportions are really a list of ``fr`` weights per axis -- one weight
per row and one per column, *not* one per panel. Two panels sharing a row
therefore always share its height; that is a property of grid layout, not a
limitation of the code below.

Kept free of Textual (and of the app) so the arithmetic that decides how big a
panel gets is unit-testable on its own. Weights are plain floats where ``1.0``
is "an equal share"; the caller renders them with :func:`weights_to_template`.
"""

# A track may shrink to a quarter of its equal share and grow to six times it.
# The floor exists because an fr template has no notion of a minimum cell: with
# no clamp, holding the shrink key drives a panel to zero columns and its plot
# renders nothing.
MIN_WEIGHT = 0.25
MAX_WEIGHT = 6.0
DEFAULT_WEIGHT = 1.0
# Per keypress. Large enough that one press is visible on an 80-column terminal.
NUDGE_STEP = 0.2
# Half a cell, added to a dragged boundary's target size. Textual resolves an fr
# template by *flooring* accumulated exact fractions, and a weight like 1.2 is a
# hair under 6/5 in binary, so asking for 48 cells yields 47 and the border
# trails the cursor. Aiming half a cell past the target makes the floor land on
# it, with a comfortable margin against float noise either way.
_BOUNDARY_BIAS = 0.5


def clamp_weight(weight: float) -> float:
    """Clamp to the usable range and round, so configs and templates stay short.

    Rounding matters beyond tidiness: repeated drags otherwise accumulate float
    noise like ``1.0000000000000002fr`` into the config file.
    """
    try:
        value = float(weight)
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT
    if value != value:  # NaN, which would poison the whole template
        return DEFAULT_WEIGHT
    return round(min(max(value, MIN_WEIGHT), MAX_WEIGHT), 3)


def normalize_weights(weights, count: int) -> list[float]:
    """Coerce ``weights`` to exactly ``count`` valid weights.

    Padding and truncating rather than rejecting is deliberate: the number of
    tracks changes whenever a panel is hidden or a GPU appears, and the saved
    weights are still the user's best-known intent for the tracks that remain.
    """
    if count <= 0:
        return []
    if not isinstance(weights, (list, tuple)):
        weights = []
    result = [clamp_weight(w) for w in list(weights)[:count]]
    result.extend([DEFAULT_WEIGHT] * (count - len(result)))
    return result


def weights_to_template(weights) -> str:
    """Render weights as a Textual ``grid-rows``/``grid-columns`` template."""
    return " ".join(f"{w:g}fr" for w in weights)


def is_default(weights) -> bool:
    """True if every track is at its equal share (nothing to reset)."""
    return all(w == DEFAULT_WEIGHT for w in weights)


def nudge_weight(weights, index: int, delta: float) -> list[float]:
    """Grow or shrink one track, leaving the others' weights untouched.

    The other tracks keep their weights and so rescale proportionally, which is
    the whole mental model of the keyboard shortcuts: "this row is now 1.4x its
    equal share". Returns a new list; an out-of-range index changes nothing.
    """
    result = list(weights)
    if 0 <= index < len(result):
        result[index] = clamp_weight(result[index] + delta)
    return result


def drag_weights(weights, index: int, sizes, delta: int, min_cells: int) -> list[float]:
    """Move the boundary between tracks ``index`` and ``index + 1`` by ``delta``.

    This is the drag case, and it differs from a nudge on purpose: dragging a
    shared border is a promise that *only* the two panels either side of it
    move, so the pair's combined weight is conserved and every other track keeps
    its exact size.

    Args:
        weights: Current weights for this axis.
        index: Left/top track of the boundary being dragged.
        sizes: ``(cells_before, cells_after)`` -- the two tracks' current sizes
            in terminal cells, measured when the drag started.
        delta: Cells moved since the drag started (signed). Measured from the
            drag origin rather than the previous event so the result cannot
            drift over a long drag.
        min_cells: Smallest either track may become.

    Returns:
        A new weight list. Unchanged if the boundary or sizes are unusable.
    """
    result = list(weights)
    if not (0 <= index < len(result) - 1):
        return result
    try:
        size_a, size_b = (int(sizes[0]), int(sizes[1]))
    except (TypeError, ValueError, IndexError):
        return result
    total = size_a + size_b
    pair_weight = result[index] + result[index + 1]
    if total <= 0 or pair_weight <= 0:
        return result

    # When the pair is already smaller than two minimums, the clamp bounds would
    # cross; splitting what space there is beats refusing to move at all.
    floor = min(min_cells, total // 2)
    new_a = min(max(size_a + delta, floor), total - floor)

    weight_a = pair_weight * (new_a + _BOUNDARY_BIAS) / total
    result[index] = clamp_weight(weight_a)
    result[index + 1] = clamp_weight(pair_weight - weight_a)
    return result

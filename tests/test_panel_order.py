"""Tests for panel-order identity and the restore/persist logic in app.py.

`_panel_identity`, `_apply_panel_order` and `_sync_panel_order` only touch
`self.grid` (children + `apply_order`) and `self.panel_order`, so a minimal
stand-in for both is enough -- no need to boot a full GroundControl app
(heavy: psutil, GPU, Slurm) just to exercise this bookkeeping.
"""
from types import SimpleNamespace

from ground_control.app import GroundControl


class FakeGrid:
    """Applies a reorder the way ``ResizableGrid.apply_order`` would.

    The real implementation moves widgets into position one at a time via
    ``move_child(widget, before=idx)``; an ``ordered`` list that is not an
    exact permutation of ``children`` means an out-of-range index there, and
    a crash. This stand-in enforces the same invariant cheaply.
    """

    def __init__(self, children):
        self.children = list(children)

    def apply_order(self, ordered):
        assert len(ordered) == len(self.children), \
            "ordered must be a permutation, not grow or shrink the set"
        assert all(child in self.children for child in ordered), \
            "ordered must not invent widgets that were never children"
        assert len(set(id(w) for w in ordered)) == len(ordered), \
            "ordered must not repeat the same widget"
        self.children = list(ordered)


def _panel(title, widget_id=None):
    return SimpleNamespace(title=title, id=widget_id)


def test_panel_identity_prefers_id_when_present():
    """Title alone collides for real: duplicate mountpoints, identical GPUs."""
    widget = _panel("Disk @ /scratch", "disk_1_scratch")
    assert GroundControl._panel_identity(widget) == "id:disk_1_scratch"


def test_panel_identity_falls_back_to_title_when_no_id():
    """CPU/Memory/Temperature/Network have no id -- there is only ever one."""
    widget = _panel("Memory", None)
    assert GroundControl._panel_identity(widget) == "Memory"


def test_apply_panel_order_survives_duplicate_titles():
    """Regression: two panels sharing a title must not corrupt the merge.

    Before `_panel_identity` preferred id, two "Disk @ /scratch" panels (a
    bind-mounted duplicate mountpoint, seen on a real cluster) collapsed to
    one dict key, and the saved order produced more entries than there were
    children -- the grid was then asked to move a widget before an
    out-of-range index and the whole rebuild crashed.
    """
    a = _panel("Disk @ /scratch", "disk_0_scratch")
    b = _panel("Disk @ /tmp", "disk_1_tmp")
    c = _panel("Disk @ /scratch", "disk_2_scratch")  # duplicate title, distinct id
    app = SimpleNamespace(
        grid=FakeGrid([a, b, c]),
        panel_order=["id:disk_2_scratch", "id:disk_0_scratch", "id:disk_1_tmp"],
        _panel_identity=GroundControl._panel_identity,
    )
    GroundControl._apply_panel_order(app)
    assert app.grid.children == [c, a, b]


def test_apply_panel_order_appends_unmatched_panels():
    """A panel absent from the saved order (e.g. a newly appeared disk) is
    appended after the ones that matched, rather than dropped."""
    a = _panel("CPU")
    b = _panel("Memory")
    c = _panel("Disk @ /new", "disk_0_new")
    app = SimpleNamespace(
        grid=FakeGrid([a, b, c]),
        panel_order=["Memory", "CPU"],
        _panel_identity=GroundControl._panel_identity,
    )
    GroundControl._apply_panel_order(app)
    assert app.grid.children == [b, a, c]


def test_apply_panel_order_drops_saved_entries_with_no_match():
    """A saved title no longer present (e.g. a GPU that changed index) is
    ignored rather than raising."""
    a = _panel("CPU")
    b = _panel("Memory")
    app = SimpleNamespace(
        grid=FakeGrid([a, b]),
        panel_order=["GPU @ old card", "Memory", "CPU"],
        _panel_identity=GroundControl._panel_identity,
    )
    GroundControl._apply_panel_order(app)
    assert app.grid.children == [b, a]


def test_sync_panel_order_round_trips_through_identity():
    a = _panel("CPU")
    b = _panel("Memory")
    saved = {}
    app = SimpleNamespace(
        grid=FakeGrid([a, b]),
        panel_order=[],
        save_config=lambda: saved.setdefault("called", True),
        _panel_identity=GroundControl._panel_identity,
    )
    GroundControl._sync_panel_order(app)
    assert app.panel_order == ["CPU", "Memory"]
    assert saved.get("called") is True

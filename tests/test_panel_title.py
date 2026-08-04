"""Tests for panel border-title composition.

A panel's border title carries two independent annotations: the alert marker
(▲/■) and a suffix naming the machine being shown (Slurm job focus). They are
written by different code paths on different schedules, so they have to compose
rather than overwrite each other -- otherwise focusing a job that then breaches
a threshold makes the title flicker between the two every tick.
"""
import os

os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/gc-test-config")

from ground_control.utils.alerts import CRIT, OK, WARN
from ground_control.widgets.base import MetricWidget


def make_widget():
    # MetricWidget needs no running app for title bookkeeping; border_title is a
    # plain attribute until the widget is mounted.
    return MetricWidget(title="CPU", id="cpu_test")


def test_plain_title_has_no_marker_or_suffix():
    widget = make_widget()
    widget.set_alert(OK)
    widget._refresh_border_title()
    assert widget.border_title == "CPU"


def test_suffix_applied_without_alert():
    widget = make_widget()
    widget.set_title_suffix(" — job 42 @ node01")
    assert widget.border_title == "CPU — job 42 @ node01"
    # The identity key the app dispatches on must not change.
    assert widget.title == "CPU"


def test_alert_marker_preserves_suffix():
    widget = make_widget()
    widget.set_title_suffix(" — job 42 @ node01")
    widget.set_alert(CRIT)
    assert widget.border_title == "■ CPU — job 42 @ node01"
    assert widget.alert_level == CRIT


def test_suffix_applied_after_alert_keeps_marker():
    widget = make_widget()
    widget.set_alert(WARN)
    widget.set_title_suffix(" — job 42 @ node01")
    assert widget.border_title == "▲ CPU — job 42 @ node01"


def test_clearing_suffix_leaves_marker_intact():
    widget = make_widget()
    widget.set_alert(WARN)
    widget.set_title_suffix(" — job 42")
    widget.set_title_suffix("")
    assert widget.border_title == "▲ CPU"


def test_recovering_from_alert_leaves_suffix_intact():
    widget = make_widget()
    widget.set_title_suffix(" — job 42")
    widget.set_alert(CRIT)
    widget.set_alert(OK)  # no stickiness
    assert widget.border_title == "CPU — job 42"


def test_repeated_identical_suffix_is_a_noop():
    widget = make_widget()
    widget.set_title_suffix(" — job 42")
    before = widget.border_title
    widget.set_title_suffix(" — job 42")
    assert widget.border_title == before

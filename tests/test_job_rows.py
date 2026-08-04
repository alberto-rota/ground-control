"""Tests for the Slurm job row/table rendering and its supporting parsers.

Bar geometry is measured on markup-stripped text: the powerline tips are single
cells that a raw len() on the markup string would not count correctly.
"""
import pytest
from rich.text import Text

from ground_control.utils import slurm
from ground_control.widgets import slurm_jobs as sj


def plain(markup: str) -> str:
    return Text.from_markup(markup).plain


RUNNING = dict(
    jobid="3946056", name="twist_rtxpro6k", state="RUNNING", partition="rtxpro6k",
    nodes="1", cpus="32", nodelist="a2843", elapsed="15:10:00",
    timelimit="1-00:00:00", mem="120G", gpus="1", live_cpu="14:22:00",
    live_rss="18.4G", live_tasks="1", reason="None",
)
PENDING = dict(
    jobid="3945189", name="COARSE", state="PENDING", partition="rtxpro6k",
    nodes="1", cpus="256", nodelist="", elapsed="0:00", timelimit="1-00:00:00",
    mem="", gpus="", reason="AssocGrpGRES",
)


# --------------------------------------------------------------------------- #
# Durations
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("45", 45),
    ("12:34", 754),
    ("1:02:03", 3723),
    ("2-03:04:05", 2 * 86400 + 3 * 3600 + 4 * 60 + 5),
    ("2-03", 2 * 86400 + 3 * 3600),   # days with a bare hour count
    ("0:00", 0),
])
def test_parse_duration_handles_every_slurm_spelling(text, expected):
    assert slurm.parse_duration(text) == expected


@pytest.mark.parametrize("text", ["UNLIMITED", "N/A", "INVALID", "", None,
                                  "NOT_SET", "PARTITION_LIMIT", "nonsense"])
def test_parse_duration_returns_none_for_non_durations(text):
    # None, not 0: an unlimited limit has no progress to show, not none used.
    assert slurm.parse_duration(text) is None


def test_format_duration_matches_slurm_style():
    assert slurm.format_duration(45) == "0:45"
    assert slurm.format_duration(3723) == "1:02:03"
    assert slurm.format_duration(2 * 86400 + 3600) == "2-01:00:00"
    assert slurm.format_duration(None) == "—"


# --------------------------------------------------------------------------- #
# Sizes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected_gb", [
    ("120G", 120),
    ("4000M", 4000 / 1024),
    ("1234K", 1234 / 1024 ** 2),
    ("2Gc", 2),        # per-CPU suffix
    ("64Gn", 64),      # per-node suffix
    ("1T", 1024),
])
def test_parse_size_accepts_slurm_suffixes(text, expected_gb):
    assert slurm.parse_size(text) == pytest.approx(expected_gb * 1024 ** 3)


@pytest.mark.parametrize("text", ["", None, "N/A", "UNLIMITED", "nonsense"])
def test_parse_size_returns_none_when_there_is_no_number(text):
    assert slurm.parse_size(text) is None


def test_format_size_picks_a_readable_unit():
    assert slurm.format_size(120 * 1024 ** 3) == "120G"
    assert slurm.format_size(1.5 * 1024 ** 3) == "1.5G"
    assert slurm.format_size(512 * 1024) == "512K"
    assert slurm.format_size(None) == "—"


# --------------------------------------------------------------------------- #
# Row geometry
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("width", [120, 100, 84, 70, 60, 50, 40, 30, 20, 12, 6, 1])
def test_row_and_header_are_exactly_the_requested_width(width):
    # The row sits beside a fixed-width button strip, so a short line would let
    # the background show through and a long one would overlap the buttons.
    for job in (RUNNING, PENDING):
        assert len(plain(sj.format_job_line(job, width))) == width
    assert len(plain(sj.format_job_line({}, width, header=True))) == width


def test_zero_width_row_is_empty():
    assert sj.format_job_line(RUNNING, 0) == ""


def test_jobid_and_state_survive_the_narrowest_panel():
    line = plain(sj.format_job_line(RUNNING, 20))
    assert "3946056" in line
    assert "R" in line


def test_columns_drop_lowest_priority_first():
    wide = plain(sj.format_job_line(RUNNING, 100))
    assert "rtxpro6k" in wide and "a2843" in wide and "120G" in wide
    # Partition (priority 1) goes before the node list (4) and the GPU count (5).
    narrower = plain(sj.format_job_line(RUNNING, 70))
    assert "rtxpro6k" not in narrower
    assert "a2843" in narrower
    narrowest = plain(sj.format_job_line(RUNNING, 50))
    assert "a2843" not in narrowest
    assert narrowest.rstrip().endswith("twist_rtxpro6k")  # name kept


def test_time_column_drops_the_limit_rather_than_truncating_it():
    # "15:10:00/1-00:00:00" does not fit the column; a clipped number is worse
    # than showing elapsed alone (the detail line repeats the limit).
    line = plain(sj.format_job_line(RUNNING, 100))
    assert "15:10:00" in line
    assert "1-00:0 " not in line
    # A short limit does fit, and then both halves are shown.
    short = dict(RUNNING, elapsed="1:23:45", timelimit="8:00:00")
    assert "1:23:45/8:00:00" in plain(sj.format_job_line(short, 100))


def test_unlimited_time_limit_shows_elapsed_alone():
    job = dict(RUNNING, timelimit="UNLIMITED")
    line = plain(sj.format_job_line(job, 100))
    assert "15:10:00" in line and "/" not in line.split("15:10:00")[1][:2]


def test_missing_values_render_as_placeholders_not_blanks():
    line = plain(sj.format_job_line(PENDING, 100))
    assert "—" in line       # no node list yet
    assert " 0 " in line     # no GPUs requested


# --------------------------------------------------------------------------- #
# Detail line
# --------------------------------------------------------------------------- #
def test_detail_shows_time_gauge_and_live_usage():
    detail = plain(sj.format_job_detail(RUNNING, 100))
    assert "63% used" in detail
    assert "8:50:00 left" in detail
    assert "cpu-time 14:22:00" in detail
    assert "peak RSS 18G" in detail
    # The gauge is drawn, and the whole line still fits.
    assert "█" in detail and len(detail) <= 100


def test_detail_drops_usage_before_the_gauge_when_narrow():
    wide = plain(sj.format_job_detail(RUNNING, 100))
    narrow = plain(sj.format_job_detail(RUNNING, 40))
    assert "cpu-time" in wide and "cpu-time" not in narrow
    assert "% used" in narrow          # the actionable half survives
    assert len(narrow) <= 40


@pytest.mark.parametrize("width", [120, 100, 80, 60, 40, 30, 23, 18, 12, 8, 4, 2, 1])
def test_detail_degrades_instead_of_overflowing(width):
    detail = plain(sj.format_job_detail(RUNNING, width))
    assert len(detail) <= width
    # Down to four cells there is always *something* saying how close the job is
    # to its limit; all-or-nothing would leave a narrow panel blank.
    if width >= 4:
        assert "63%" in detail


def test_detail_prefers_the_numbers_over_the_gauge_when_squeezed():
    # At a width where the gauge and the full reading no longer both fit, "how
    # much time is left" is worth more than a bar of the same fact.
    assert plain(sj.format_job_detail(RUNNING, 30)) == "63% used · 8:50:00 left"


def test_detail_omits_the_gauge_for_a_job_that_has_not_started():
    # A pending job's limit has not started counting; an empty "0% used" gauge
    # would suggest it had.
    detail = plain(sj.format_job_detail(PENDING, 100))
    assert "used" not in detail
    assert detail == ""


def test_detail_gauge_switches_colour_near_the_time_limit():
    nearly_done = dict(RUNNING, elapsed="23:30:00", timelimit="1-00:00:00")
    markup = sj.format_job_detail(nearly_done, 100)
    assert "98% used" in plain(markup)
    # Colour is looked up from the palette, so assert on the key's value.
    from ground_control.utils.colors import get_rich_color
    assert get_rich_color("alert_warn", "#ffaf00") in markup


def test_detail_is_empty_when_there_is_nothing_to_say():
    assert sj.format_job_detail({"state": "RUNNING"}, 80) == ""
    assert sj.format_job_detail(RUNNING, 0) == ""


# --------------------------------------------------------------------------- #
# scancel
# --------------------------------------------------------------------------- #
class _Proc:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, stdout


def test_scancel_reports_success(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(slurm.shutil, "which", lambda _: "/usr/bin/scancel")
    monkeypatch.setattr(slurm.subprocess, "run", fake_run)
    ok, message = slurm.scancel_job("4242")
    assert ok and "4242" in message
    assert seen["cmd"] == ["scancel", "4242"]


def test_scancel_passes_slurms_own_error_through(monkeypatch):
    monkeypatch.setattr(slurm.shutil, "which", lambda _: "/usr/bin/scancel")
    monkeypatch.setattr(slurm.subprocess, "run", lambda cmd, **kw: _Proc(
        returncode=1, stderr="scancel: error: Kill job error on job id 4242: "
                             "Access/permission denied\n"))
    ok, message = slurm.scancel_job("4242")
    # The user needs to know *why*, and "permission denied" is the whole answer.
    assert not ok
    assert "Access/permission denied" in message
    assert not message.startswith("scancel: error:")


def test_scancel_without_slurm_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(slurm.shutil, "which", lambda _: None)
    ok, message = slurm.scancel_job("1")
    assert not ok and "scancel" in message


def test_scancel_rejects_an_empty_jobid():
    ok, message = slurm.scancel_job("")
    assert not ok and message


def test_scancel_can_send_a_signal_instead(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(slurm.shutil, "which", lambda _: "/usr/bin/scancel")
    monkeypatch.setattr(slurm.subprocess, "run", fake_run)
    ok, message = slurm.scancel_job("9", signal="TERM")
    assert ok and "signal TERM" in message
    assert seen["cmd"] == ["scancel", "--signal", "TERM", "9"]


def test_detail_rejects_sstats_uninitialised_cpu_counter():
    # A freshly started step reports 2^63 nanoseconds as AveCPU; printing that
    # verbatim ("213503982334-14:25:51") is worse than printing nothing.
    job = dict(RUNNING, live_cpu="213503982334-14:25:51", elapsed="4:36",
               cpus="256", live_rss="", live_tasks="1")
    detail = plain(sj.format_job_detail(job, 120))
    assert "cpu-time" not in detail
    assert "213503982334" not in detail
    assert "1 tasks" in detail          # the rest of the line survives


def test_detail_keeps_cpu_time_that_fits_the_allocation():
    job = dict(RUNNING, live_cpu="10:00:00", elapsed="1:00:00", cpus="32")
    assert "cpu-time 10:00:00" in plain(sj.format_job_detail(job, 120))


def test_detail_rejects_cpu_time_beyond_what_the_allocation_allows():
    # 100 CPU-hours cannot come out of one core running for one hour.
    job = dict(RUNNING, live_cpu="100:00:00", elapsed="1:00:00", cpus="1")
    assert "cpu-time" not in plain(sj.format_job_detail(job, 120))


def test_detail_falls_back_to_a_blunt_ceiling_without_an_allocation():
    fine = dict(RUNNING, live_cpu="10:00:00", elapsed="", cpus="")
    assert "cpu-time 10:00:00" in plain(sj.format_job_detail(fine, 120))
    absurd = dict(RUNNING, live_cpu="999-00:00:00", elapsed="", cpus="")
    assert "cpu-time" not in plain(sj.format_job_detail(absurd, 120))

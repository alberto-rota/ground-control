"""Network details: pure classifiers, table formatting, snapshot round trip."""

from rich.text import Text

from ground_control.utils.network_details import (
    EXPOSURE_ALL,
    EXPOSURE_LOOPBACK,
    exposure_of,
    merge_listeners,
    parse_proc_net_snmp,
    service_hint,
    summarize_listeners,
    tcp_health,
)
from ground_control.utils.snapshot import _network_section, metrics_from_snapshot
from ground_control.widgets.network import (
    format_link_lines,
    format_port_lines,
    primary_interface,
)


def plain(markup):
    return Text.from_markup(markup).plain


def test_exposure_classification():
    assert exposure_of("0.0.0.0") == EXPOSURE_ALL
    assert exposure_of("::") == EXPOSURE_ALL
    assert exposure_of("127.0.0.53") == EXPOSURE_LOOPBACK
    assert exposure_of("::1") == EXPOSURE_LOOPBACK
    assert exposure_of("::ffff:127.0.0.1") == EXPOSURE_LOOPBACK
    assert exposure_of("10.0.0.5", {"10.0.0.5": "ib0"}) == "ib0 only"
    assert exposure_of("10.0.0.6") == "10.0.0.6 only"


def test_service_hints_cover_the_ml_ports():
    assert service_hint(8888) == "jupyter" and service_hint(8891) == "jupyter"
    assert service_hint(6006) == "tensorboard"
    assert service_hint(22) == "ssh"


SNMP = """Ip: Forwarding DefaultTTL
Ip: 1 64
Tcp: RtoAlgorithm ActiveOpens CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts
Tcp: 1 10 7 1000 2000 30 0 4
"""


def test_proc_net_snmp_and_tcp_health():
    tcp = parse_proc_net_snmp(SNMP)["Tcp"]
    assert tcp["RetransSegs"] == 30 and tcp["CurrEstab"] == 7
    later = dict(tcp, OutSegs=3000, RetransSegs=50, OutRsts=6)
    health = tcp_health(tcp, later, 2.0)
    assert health["retransmit_percent"] == 2.0
    assert health["segments_out_per_s"] == 500.0
    assert health["resets_out_per_s"] == 1.0
    assert tcp_health(None, tcp, 1.0)["retransmit_percent"] is None


def test_counter_reset_never_yields_negative_rates():
    before = {"OutSegs": 1000, "RetransSegs": 50}
    after = {"OutSegs": 10, "RetransSegs": 0}
    health = tcp_health(before, after, 1.0)
    assert health["retransmits_per_s"] == 0.0


def test_forked_workers_collapse_to_one_listener():
    rows = [
        {"proto": "tcp", "address": "0.0.0.0", "port": 8000, "pid": 10,
         "exposure": EXPOSURE_ALL, "process": "gunicorn", "mine": True},
        {"proto": "tcp", "address": "0.0.0.0", "port": 8000, "pid": 11,
         "exposure": EXPOSURE_ALL, "process": "gunicorn", "mine": True},
        {"proto": "tcp", "address": "127.0.0.1", "port": 22, "pid": None,
         "exposure": EXPOSURE_LOOPBACK},
    ]
    merged = merge_listeners(rows)
    assert len(merged) == 2
    assert merged[0]["pids"] == [10, 11] and merged[0]["pid"] == 10
    assert summarize_listeners(merged) == {"listening": 2, "exposed": 1, "mine": 1}


LISTENING = [
    {"proto": "tcp", "address": "0.0.0.0", "port": 8888, "exposure": EXPOSURE_ALL,
     "pids": [4242], "pid": 4242, "process": "jupyter-lab", "username": "me",
     "mine": True, "service_hint": "jupyter"},
    {"proto": "tcp6", "address": "::1", "port": 631, "exposure": EXPOSURE_LOOPBACK,
     "pids": [], "pid": None, "process": None, "username": None, "mine": False,
     "service_hint": "ipp"},
]
INTERFACES = [
    {"name": "ib0", "kind": "infiniband", "is_up": True, "speed_mbit": 200000,
     "ipv4": ["10.1.0.4"], "rx_mbps": 812.0, "tx_mbps": 3.0},
    {"name": "docker0", "kind": "virtual", "is_up": False, "speed_mbit": None,
     "ipv4": ["172.17.0.1"], "rx_mbps": 0.0, "tx_mbps": 0.0},
]


def test_tables_are_exactly_width_cells_at_any_width():
    for width in (20, 33, 47, 80, 140):
        for line in format_port_lines(LISTENING, width) + format_link_lines(INTERFACES, width):
            assert len(plain(line)) == width, (width, plain(line))


def test_port_table_marks_exposure_and_names_the_service():
    lines = [plain(line) for line in format_port_lines(LISTENING, 80)]
    assert "PORT" in lines[0] and "SERVICE" in lines[0]
    assert "any ▲" in lines[1] and "jupyter-lab" in lines[1]
    assert "local" in lines[2] and "ipp?" in lines[2]


def test_primary_interface_skips_virtual_and_down_links():
    assert primary_interface(INTERFACES)["name"] == "ib0"
    assert primary_interface(INTERFACES[1:]) is None


def test_snapshot_round_trip_carries_details():
    details = {"interfaces": INTERFACES, "listening": LISTENING,
               "listening_summary": summarize_listeners(LISTENING),
               "connections": {"established": 3}, "tcp": {"retransmit_percent": 0.5},
               "sockets_age_seconds": 1.0}
    section = _network_section({"download_speed": 1.5, "upload_speed": 0.5}, details)
    assert section["download_mbps"] == 1.5 and section["listening"][0]["port"] == 8888
    rebuilt = metrics_from_snapshot({"metrics": {"network": section}})
    assert rebuilt["network"]["download_speed"] == 1.5
    assert rebuilt["network_details"]["listening"][0]["process"] == "jupyter-lab"


def test_old_snapshot_has_no_details_and_still_rebuilds_throughput():
    """A remote gc predating details must still drive the throughput plot."""
    rebuilt = metrics_from_snapshot(
        {"metrics": {"network": {"download_mbps": 2.0, "upload_mbps": 1.0}}})
    assert rebuilt["network"]["upload_speed"] == 1.0
    assert "network_details" not in rebuilt
    assert set(_network_section({}, None)) == {"download_mbps", "upload_mbps"}


def test_collector_returns_the_documented_shape():
    """Runs against this machine's real sockets; asserts shape, not values."""
    from ground_control.utils.system_metrics import SystemMetrics

    details = SystemMetrics.get_network_details(SystemMetrics.__new__(SystemMetrics))
    assert {"interfaces", "listening", "listening_summary", "connections", "tcp",
            "sockets_age_seconds"} <= set(details)
    assert all(i["name"] != "lo" for i in details["interfaces"])

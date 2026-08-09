"""Tests for rebuilding live metrics from a JSON snapshot.

``metrics_from_snapshot`` is what lets a remote host's reading -- a Slurm job
sampled on its compute node -- render through the same widgets and the same
alert evaluator as local metrics, so its output has to match the shape the
collectors produce, not merely be "close enough".
"""
from ground_control.utils.alerts import evaluate_snapshot, merge_thresholds
from ground_control.utils.snapshot import metrics_from_snapshot

_GB = 1024 ** 3


def make_snapshot(**overrides):
    metrics = {
        "cpu": {
            "name": "AMD EPYC 9745 [256 cores]",
            "cores": 4,
            "per_core_percent": [10.0, 20.0, 30.0, 40.0],
            "per_core_freq_mhz": [2400.0, 2400.0, 2400.0, 2400.0],
            "percent": 25.0,
            "freq_mhz": 2400.0,
            "memory_percent": 28.7,
            "telemetry": {"load_1": 8.0, "cgroup_quota_cores": 64.0},
        },
        "memory": {
            "total_gb": 100.0,
            "used_gb": 40.0,
            "available_gb": 60.0,
            "percent": 40.0,
            "swap": {"total_gb": 8.0, "used_gb": 1.0, "percent": 12.5},
            "meminfo": {"Cached": 1024, "Buffers": 512},
            "commit_ratio": 0.42,
        },
        "disk": {
            "mounts": [{
                "mountpoint": "/scratch",
                "total_gb": 200.0,
                "used_gb": 50.0,
                "free_gb": 150.0,
                "percent": 25.0,
                "read_mbps": 1.5,
                "write_mbps": 2.5,
            }],
            "read_mbps": 1.5,
            "write_mbps": 2.5,
        },
        "network": {"download_mbps": 3.0, "upload_mbps": 4.0},
        "gpu": [{
            "index": 0,
            "name": "NVIDIA RTX PRO 6000",
            "utilization_percent": 67,
            "memory_used_gb": 24.4,
            "memory_total_gb": 102.6,
            "memory_percent": 23.8,
            "process_count": 1,
            "processes": [{"pid": 3368355, "name": "python",
                           "gpu_memory": "22.69GiB", "username": "alice",
                           "command": "python train.py", "script": "train.py",
                           "cpu_percent": "99.0", "memory": "4.2%"}],
            "power_w": 277.2,
            "power_limit_w": 600.0,
            "temperature_c": 61.0,
            "fan_percent": 30,
            "sm_clock_mhz": 2100,
            "max_sm_clock_mhz": 2600,
            "memory_bandwidth_percent": 45,
            "performance_state": "P0",
            "throttle_reasons": ["SwPowerCap"],
            "throttle_severe": False,
        }],
        "temperature_c": {"Package id 0": 55.0},
    }
    metrics.update(overrides)
    return {"schema_version": 1, "host": "a2142", "metrics": metrics}


# --------------------------------------------------------------------------- #
# Shape fidelity
# --------------------------------------------------------------------------- #
def test_cpu_rebuilt_in_collector_shape():
    cpu = metrics_from_snapshot(make_snapshot())["cpu"]
    assert cpu["cpu_percentages"] == [10.0, 20.0, 30.0, 40.0]
    assert cpu["cpu_name"].startswith("AMD EPYC")
    assert cpu["mem_percent"] == 28.7
    assert cpu["cpu_telemetry"]["cgroup_quota_cores"] == 64.0
    # Frequencies are reached by attribute, as psutil returns them.
    assert [f.current for f in cpu["cpu_freqs"]] == [2400.0] * 4


def test_memory_rebuilt_as_attribute_object_in_bytes():
    memory = metrics_from_snapshot(make_snapshot())["memory"]
    info, swap = memory["memory_info"], memory["swap_info"]
    # Widgets divide .used/.total by 1024**3, so these must be bytes.
    assert info.total == int(100.0 * _GB)
    assert info.used == int(40.0 * _GB)
    assert info.percent == 40.0
    assert swap.total == int(8.0 * _GB)
    assert memory["meminfo"]["Cached"] == 1024
    assert memory["commit_ratio"] == 0.42


def test_disk_rebuilt_with_collector_keys():
    disk = metrics_from_snapshot(make_snapshot())["disk"]
    entry = disk["disks"][0]
    assert entry["mountpoint"] == "/scratch"
    assert entry["disk_total"] == int(200.0 * _GB)
    assert entry["disk_used"] == int(50.0 * _GB)
    assert entry["read_speed"] == 1.5
    assert entry["write_speed"] == 2.5


def test_network_rebuilt():
    network = metrics_from_snapshot(make_snapshot())["network"]
    assert network["download_speed"] == 3.0
    assert network["upload_speed"] == 4.0


def test_gpu_rebuilt_with_processes_and_telemetry():
    gpu = metrics_from_snapshot(make_snapshot())["gpu"][0]
    assert gpu["gpu_name"] == "NVIDIA RTX PRO 6000"
    assert gpu["gpu_util"] == 67.0
    assert gpu["mem_used"] == 24.4
    assert gpu["mem_total"] == 102.6
    assert gpu["processes"][0]["pid"] == 3368355
    assert gpu["processes"][0]["script"] == "train.py"
    # Telemetry keys keep the collector's names, since the widget reads the
    # raw per-GPU dict.
    assert gpu["power_w"] == 277.2
    assert gpu["mem_bw_percent"] == 45
    assert gpu["perf_state"] == "P0"
    assert gpu["throttle_reasons"] == ["SwPowerCap"]


def test_temperature_rebuilt():
    temps = metrics_from_snapshot(make_snapshot())["temperature"]
    assert temps == {"Package id 0": 55.0}


# --------------------------------------------------------------------------- #
# Degradation: older remote versions, partial payloads, junk
# --------------------------------------------------------------------------- #
def test_missing_detail_fields_degrade_quietly():
    """A remote gc predating the detail fields must not raise."""
    snapshot = make_snapshot()
    del snapshot["metrics"]["cpu"]["telemetry"]
    del snapshot["metrics"]["cpu"]["per_core_freq_mhz"]
    del snapshot["metrics"]["memory"]["meminfo"]
    del snapshot["metrics"]["gpu"][0]["processes"]
    result = metrics_from_snapshot(snapshot)
    assert result["cpu"]["cpu_telemetry"] == {}
    assert result["cpu"]["cpu_freqs"] == []
    assert "meminfo" not in result["memory"]
    # No process rows, rather than a crash.
    assert result["gpu"][0]["processes"] == []


def test_absent_family_is_omitted_not_faked():
    snapshot = make_snapshot(gpu=[], temperature_c=None)
    result = metrics_from_snapshot(snapshot)
    assert result["gpu"] == []
    assert "temperature" not in result


def test_null_gpu_utilisation_maps_to_unavailable_sentinel():
    # The widget treats a negative utilisation as "UNAV"; null must not become 0,
    # which would read as a genuinely idle GPU.
    snapshot = make_snapshot()
    snapshot["metrics"]["gpu"][0]["utilization_percent"] = None
    gpu = metrics_from_snapshot(snapshot)["gpu"][0]
    assert gpu["gpu_util"] < 0


def test_garbage_inputs_return_empty():
    assert metrics_from_snapshot(None) == {}
    assert metrics_from_snapshot({}) == {}
    assert metrics_from_snapshot({"metrics": None}) == {}
    assert metrics_from_snapshot("not a dict") == {}


# --------------------------------------------------------------------------- #
# The rebuilt shape must still drive alerting
# --------------------------------------------------------------------------- #
def test_rebuilt_metrics_are_accepted_by_alert_evaluator():
    metrics = metrics_from_snapshot(make_snapshot())
    targets, breaches = evaluate_snapshot(metrics, merge_thresholds(None))
    # The point is that evaluation runs over remote data and keys panels the
    # same way it does locally -- per-GPU and per-mount included.
    assert ("cpu", None) in targets
    assert ("gpu", 0) in targets
    assert ("disk", "/scratch") in targets
    assert isinstance(breaches, list)


def test_alerts_fire_on_remote_values():
    snapshot = make_snapshot()
    # Fill the job's memory and heat a card past their critical thresholds.
    snapshot["metrics"]["memory"]["percent"] = 99.0
    snapshot["metrics"]["gpu"][0]["temperature_c"] = 95.0
    metrics = metrics_from_snapshot(snapshot)
    _targets, breaches = evaluate_snapshot(metrics, merge_thresholds(None))
    assert breaches, "expected remote breaches to be reported"


# --------------------------------------------------------------------------- #
# Streaming (gc --stream)
# --------------------------------------------------------------------------- #
class _FakeCollector:
    """Minimal stand-in for SystemMetrics that counts how often it is read."""

    def __init__(self):
        self.reads = 0

    def get_cpu_metrics(self):
        self.reads += 1
        return {"cpu_percentages": [float(self.reads)], "cpu_freqs": [],
                "cpu_name": "fake", "mem_percent": 1.0}

    def get_memory_metrics(self):
        return {}

    def get_disk_metrics(self):
        return {"disks": []}

    def get_network_metrics(self):
        return {"download_speed": 0.0, "upload_speed": 0.0}

    def get_temperature_metrics(self):
        return None

    def get_gpu_metrics(self):
        return []


def test_iter_snapshots_primes_then_yields_repeatedly():
    from ground_control.utils.snapshot import iter_snapshots

    collector = _FakeCollector()
    stream = iter_snapshots(collector, interval=0.1, include_gpu=False)
    first = next(stream)
    # The counters are primed before the first yield, so a streamed reading is
    # meaningful immediately -- unlike a cold one-shot run, whose rates read 0.
    assert collector.reads >= 2
    second = next(stream)
    assert second["timestamp"] >= first["timestamp"]
    assert second["metrics"]["cpu"]["per_core_percent"] != \
        first["metrics"]["cpu"]["per_core_percent"]
    stream.close()


def test_iter_snapshots_stops_at_max_seconds():
    from ground_control.utils.snapshot import iter_snapshots

    # The remote collector runs inside someone's allocation, so it must expire on
    # its own even if no stop signal ever reaches it.
    produced = list(iter_snapshots(_FakeCollector(), interval=0.1,
                                   include_gpu=False, max_seconds=0.25))
    assert 1 <= len(produced) <= 6


def test_streamed_snapshot_round_trips_through_the_adapter():
    from ground_control.utils.snapshot import iter_snapshots, metrics_from_snapshot

    snapshot = next(iter_snapshots(_FakeCollector(), interval=0.1,
                                   include_gpu=False))
    rebuilt = metrics_from_snapshot(snapshot)
    # A streamed sample must feed the widgets through exactly the same path a
    # one-shot snapshot does.
    assert rebuilt["cpu"]["cpu_name"] == "fake"
    assert rebuilt["network"]["download_speed"] == 0.0

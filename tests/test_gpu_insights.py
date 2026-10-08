"""GPU diagnosis rules, on plain dicts in the snapshot's GPU shape."""

from ground_control.utils.gpu_insights import diagnose_gpu, diagnose_gpus


def gpu(**fields):
    base = {"index": 0, "name": "GPU", "throttle_reasons": [], "throttle_severe": False,
            "process_count": 1}
    base.update(fields)
    return base


def codes(reading):
    return [f["code"] for f in diagnose_gpu(reading)]


def test_input_starved_needs_all_three_signals():
    starved = gpu(utilization_percent=95, power_percent=25, memory_bandwidth_percent=4)
    assert codes(starved) == ["input_starved"]
    finding = diagnose_gpu(starved)[0]
    assert finding["gpu"] == 0 and finding["severity"] == "warn"
    assert finding["evidence"]["power_percent"] == 25
    # Busy bandwidth means the kernels are moving data: not starved.
    assert "input_starved" not in codes(gpu(utilization_percent=95, power_percent=25,
                                            memory_bandwidth_percent=60))


def test_healthy_busy():
    assert codes(gpu(utilization_percent=99, power_percent=90,
                     memory_bandwidth_percent=50)) == ["healthy_busy"]


def test_throttling_severity():
    severe = diagnose_gpu(gpu(throttle_severe=True, throttle_reasons=["thermal"]))
    assert severe[0]["code"] == "throttled" and severe[0]["severity"] == "warn"
    governed = diagnose_gpu(gpu(throttle_reasons=["power cap"]))
    assert governed[0]["code"] == "clock_governed" and governed[0]["severity"] == "info"


def test_throttled_or_hot_suppresses_healthy_verdict():
    reading = gpu(utilization_percent=99, power_percent=90, temperature_c=93)
    assert codes(reading) == ["hot"]
    assert diagnose_gpu(reading)[0]["severity"] == "crit"


def test_memory_findings():
    assert codes(gpu(memory_percent=97)) == ["vram_nearly_full"]
    assert codes(gpu(utilization_percent=0, memory_percent=40)) == ["idle_holding_memory"]
    assert codes(gpu(utilization_percent=0, memory_percent=40, process_count=0)) == []


def test_missing_fields_skip_rules():
    assert diagnose_gpu(gpu(utilization_percent=None, power_percent=None)) == []
    # High util with no power limit: say the verdict is impossible, not "fine".
    assert codes(gpu(utilization_percent=95, power_percent=None,
                     memory_bandwidth_percent=1)) == ["limited_telemetry"]
    assert diagnose_gpu("not a dict") == []


def test_diagnose_gpus_covers_every_device():
    findings = diagnose_gpus([gpu(index=0, memory_percent=99),
                              gpu(index=1, temperature_c=88)])
    assert [(f["gpu"], f["code"]) for f in findings] == [(0, "vram_nearly_full"), (1, "hot")]
    assert diagnose_gpus(None) == []

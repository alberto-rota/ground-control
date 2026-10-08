"""MCP stdio handshake, discovery, tools and prompts without real hardware."""

import io
import json
import sys

import pytest

from ground_control import mcp
from ground_control.mcp import McpServer, MetricsProvider, serve, summarize_series


def make_snapshot(cpu=70, util=80, power=20, bandwidth=5, status="warn", alerts=None):
    return {
        "schema_version": 1,
        "timestamp_iso": "2026-01-01T00:00:00+0000",
        "host": "test-host",
        "status": status,
        "alerts": [{"level": "warn", "metric": "cpu_percent", "scope": "cpu"}]
        if alerts is None else alerts,
        "metrics": {
            "cpu": {"percent": cpu, "per_core_percent": [60, 80]},
            "memory": {"percent": 50, "available_gb": 8},
            "disk": {"mounts": [{"mountpoint": "/", "percent": 80, "free_gb": 20}],
                     "read_mbps": 1.0, "write_mbps": 2.0},
            "network": {"download_mbps": 1, "upload_mbps": 2},
            "gpu": [
                {
                    "index": 0,
                    "name": "GPU",
                    "utilization_percent": util,
                    "memory_used_gb": 2,
                    "memory_total_gb": 8,
                    "memory_percent": 25.0,
                    "temperature_c": 60,
                    "power_percent": power,
                    "memory_bandwidth_percent": bandwidth,
                    "throttle_reasons": [],
                    "throttle_severe": False,
                    "process_count": 1,
                    "processes": [{"pid": 12}],
                }
            ],
            "temperature_c": {"cpu": 50.0},
        },
    }


class FakeProvider:
    def __init__(self, snapshots=None):
        self.calls = 0
        self.snapshots = snapshots

    def snapshot(self):
        self.calls += 1
        if self.snapshots:
            return self.snapshots[min(self.calls - 1, len(self.snapshots) - 1)]
        return make_snapshot()


def request(method, request_id, params=None):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def run(messages, provider=None, slurm_enabled=False):
    output = io.StringIO()
    serve(
        io.StringIO("".join(json.dumps(m) + "\n" for m in messages)),
        output,
        provider or FakeProvider(),
        slurm_enabled=slurm_enabled,
    )
    return [json.loads(line) for line in output.getvalue().splitlines()]


def call(name, request_id=1, **arguments):
    return request("tools/call", request_id, {"name": name, "arguments": arguments})


def payload(reply):
    return json.loads(reply["result"]["content"][0]["text"])


def test_stdio_handshake_discovery_and_calls():
    provider = FakeProvider()
    replies = run([
        request("initialize", 1, {"protocolVersion": "2024-11-05"}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request("tools/list", 2),
        request("tools/call", 3, {"name": "get_hardware_status"}),
        call("get_hardware_metrics", 4, section="gpu"),
        request("ping", 5),
    ], provider)
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2024-11-05"
    assert "prompts" in init["capabilities"] and "tools" in init["capabilities"]
    assert "login node" not in init["instructions"]
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names[:2] == ["get_hardware_status", "get_hardware_metrics"]
    assert {"sample_hardware", "diagnose_gpus", "get_alert_thresholds"} <= set(names)
    assert not any("slurm" in n for n in names)
    for tool in replies[1]["result"]["tools"]:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
    status = payload(replies[2])
    assert status["status"] == "warn" and status["cpu_percent"] == 70
    assert "processes" not in status["gpus"][0]
    assert status["gpus"][0]["power_percent"] == 20
    # 2024-11-05 predates structured content.
    assert "structuredContent" not in replies[2]["result"]
    details = payload(replies[3])
    assert list(details["metrics"]) == ["gpu"]
    assert details["metrics"]["gpu"][0]["processes"][0]["pid"] == 12
    assert provider.calls == 2


def test_structured_content_for_current_protocol():
    replies = run([
        request("initialize", 1, {"protocolVersion": "2025-06-18"}),
        request("tools/call", 2, {"name": "get_hardware_status"}),
    ])
    result = replies[1]["result"]
    assert result["structuredContent"] == json.loads(result["content"][0]["text"])


def test_invalid_arguments_are_tool_errors_naming_the_field():
    provider = FakeProvider()
    replies = run([
        call("get_hardware_metrics", 1, section=["cpu"]),
        call("get_hardware_metrics", 2, section="battery"),
        call("get_hardware_status", 3, section="cpu"),
        request("tools/call", 4, {"name": "get_hardware_metrics"}),
        call("sample_hardware", 5, duration_seconds=600),
        request("tools/call", 6, {"name": "no_such_tool"}),
    ], provider)
    errors = [r["result"] for r in replies[:3]] + [replies[4]["result"]]
    assert all(e["isError"] for e in errors)
    texts = [e["content"][0]["text"] for e in errors]
    assert "'section' must be a string" in texts[0]
    assert "battery" in texts[1]
    assert "unknown argument 'section'" in texts[2]
    assert "at most 60" in texts[3]
    assert "metrics" in payload(replies[3])
    assert replies[5]["error"]["code"] == -32602
    assert provider.calls == 1


def test_slurm_tools_hidden_without_slurm_and_rejected():
    replies = run([request("tools/list", 1), call("list_slurm_jobs", 2)])
    assert not any("slurm" in t["name"] for t in replies[0]["result"]["tools"])
    assert replies[1]["error"]["code"] == -32602


def test_summarize_series_aggregates_and_unions_alerts():
    crit = {"level": "crit", "metric": "cpu_percent", "scope": "cpu"}
    snaps = [make_snapshot(cpu=10, alerts=[]), make_snapshot(cpu=30, status="crit", alerts=[crit]),
             make_snapshot(cpu=20, status="ok", alerts=[])]
    snaps[1]["metrics"]["gpu"][0]["throttle_reasons"] = ["thermal"]
    summary = summarize_series(snaps)
    assert summary["status"] == "crit"
    assert summary["alerts_seen"] == [crit]
    assert summary["metrics"]["cpu"]["percent"] == {"min": 10, "mean": 20, "max": 30, "last": 20}
    gpu = summary["metrics"]["gpu"][0]
    assert gpu["utilization_percent"]["mean"] == 80
    assert gpu["throttle_reasons_seen"] == ["thermal"]
    assert summary["metrics"]["temperature_c"]["cpu"]["max"] == 50


def test_sample_hardware_reports_progress_and_window():
    notes = []
    sleeps = []
    server = McpServer(FakeProvider(), slurm_enabled=False,
                       notify=lambda method, params: notes.append((method, params)),
                       sleep=sleeps.append)
    reply = server.handle(request("tools/call", 1, {
        "name": "sample_hardware",
        "arguments": {"duration_seconds": 3, "interval_seconds": 1, "section": "cpu"},
        "_meta": {"progressToken": "tok"},
    }))
    value = reply["result"]["structuredContent"]
    assert value["window"]["samples"] == 4
    assert list(value["metrics"]) == ["cpu"]
    assert len(sleeps) == 3
    assert [n[1]["progress"] for n in notes] == [1, 2, 3, 4]
    assert all(n[0] == "notifications/progress" and n[1]["progressToken"] == "tok"
               for n in notes)


def test_diagnose_gpus_flags_input_starved_gpu():
    server = McpServer(FakeProvider(), slurm_enabled=False, sleep=lambda s: None)
    reply = server.handle(call("diagnose_gpus", 1, duration_seconds=2))
    value = reply["result"]["structuredContent"]
    assert value["basis"].startswith("mean over 3")
    assert [f["code"] for f in value["findings"]] == ["input_starved"]


def test_diagnose_gpus_without_gpus_hints_at_job_id(monkeypatch):
    snap = make_snapshot()
    snap["metrics"]["gpu"] = []
    server = McpServer(FakeProvider([snap]), slurm_enabled=True)
    value = server.handle(call("diagnose_gpus", 1, duration_seconds=0))["result"]["structuredContent"]
    assert value["findings"] == []
    assert "job_id" in value["note"]


def test_alert_thresholds_tool(monkeypatch):
    from ground_control import main

    monkeypatch.setattr(main, "_load_threshold_config", lambda: (
        {"cpu_percent": {"warn": 80, "crit": 95, "enabled": True}}, True, ["/snap"]))
    value = McpServer(FakeProvider(), slurm_enabled=False).handle(
        call("get_alert_thresholds"))["result"]["structuredContent"]
    assert value["thresholds"]["cpu_percent"]["direction"] == "above"
    assert value["disk_ignore_prefixes"] == ["/snap"]


@pytest.fixture
def fake_slurm(monkeypatch):
    from ground_control.utils import slurm

    state = {"liveness": (True, "RUNNING"), "probe": make_snapshot(), "probed": []}
    monkeypatch.setattr(slurm, "get_user_jobs", lambda user=None: [
        {"jobid": "20", "state": "PENDING", "elapsed": "0:00", "timelimit": "1:00:00",
         "gres": "N/A"},
        {"jobid": "10", "state": "RUNNING", "elapsed": "30:00", "timelimit": "1:00:00",
         "gres": "gres/gpu:a100:2", "nodelist": "node[1-2]"},
    ])
    monkeypatch.setattr(slurm, "get_job_detail", lambda jobid: {
        "state": "RUNNING", "elapsed": "00:30:00", "timelimit": "01:00:00",
        "nodelist": "node[1-2]"})
    monkeypatch.setattr(slurm, "get_job_liveness", lambda jobid: state["liveness"])
    monkeypatch.setattr(slurm, "get_job_live_stats", lambda jobid: {"AveCPU": "00:10:00"})

    def probe(jobid, node=None):
        state["probed"].append((jobid, node))
        return state["probe"]

    monkeypatch.setattr(slurm, "probe_job_metrics", probe)
    return state


def test_slurm_tools(fake_slurm):
    server = McpServer(FakeProvider(), slurm_enabled=True)
    names = [t["name"] for t in server.handle(request("tools/list", 1))["result"]["tools"]]
    assert {"list_slurm_jobs", "get_slurm_job", "get_slurm_job_metrics"} <= set(names)
    init = server.handle(request("initialize", 2, {}))["result"]
    assert "login node" in init["instructions"]

    jobs = server.handle(call("list_slurm_jobs", 3))["result"]["structuredContent"]
    assert [j["jobid"] for j in jobs["jobs"]] == ["10", "20"]
    assert jobs["jobs"][0]["gpus"] == 2 and jobs["jobs"][0]["time_used_percent"] == 50.0
    pending = server.handle(call("list_slurm_jobs", 4, state="pending"))["result"]
    assert [j["jobid"] for j in pending["structuredContent"]["jobs"]] == ["20"]

    job = server.handle(call("get_slurm_job", 5, job_id="10"))["result"]["structuredContent"]
    assert job["liveness"] == {"running": True, "state": "RUNNING"}
    assert job["usage"] == {"AveCPU": "00:10:00"} and job["time_used_percent"] == 50.0

    metrics = server.handle(call("get_slurm_job_metrics", 6, job_id="10", section="gpu"))
    value = metrics["result"]["structuredContent"]
    assert list(value["metrics"]) == ["gpu"] and value["job_id"] == "10"
    assert fake_slurm["probed"] == [("10", "node1")]

    diag = server.handle(call("diagnose_gpus", 7, job_id="10"))["result"]["structuredContent"]
    assert diag["basis"] == "single reading from inside job 10"
    assert diag["findings"][0]["code"] == "input_starved"


def test_slurm_job_errors(fake_slurm):
    server = McpServer(FakeProvider(), slurm_enabled=True)
    bad = server.handle(call("get_slurm_job", 1, job_id="10; rm -rf /"))["result"]
    assert bad["isError"] and "job_id" in bad["content"][0]["text"]

    fake_slurm["liveness"] = (False, "COMPLETED")
    done = server.handle(call("get_slurm_job_metrics", 2, job_id="10"))["result"]
    assert done["isError"] and "COMPLETED" in done["content"][0]["text"]
    assert fake_slurm["probed"] == []

    fake_slurm["liveness"] = (True, "RUNNING")
    fake_slurm["probe"] = None
    failed = server.handle(call("get_slurm_job_metrics", 3, job_id="10"))["result"]
    assert failed["isError"] and "Could not sample job 10" in failed["content"][0]["text"]


def test_prompts():
    server = McpServer(FakeProvider(), slurm_enabled=False)
    prompts = server.handle(request("prompts/list", 1))["result"]["prompts"]
    assert {p["name"] for p in prompts} == {
        "diagnose_slow_machine", "diagnose_gpu_job", "explain_alerts"}
    text = server.handle(request("prompts/get", 2, {"name": "diagnose_gpu_job"}))[
        "result"]["messages"][0]["content"]["text"]
    assert "diagnose_gpus" in text and "list_slurm_jobs" not in text
    # A job id is meaningless without Slurm.
    no_slurm = server.handle(request("prompts/get", 3, {
        "name": "diagnose_gpu_job", "arguments": {"job_id": "10"}}))
    assert no_slurm["error"]["code"] == -32602
    assert server.handle(request("prompts/get", 4, {"name": "nope"}))["error"]["code"] == -32602

    slurm_server = McpServer(FakeProvider(), slurm_enabled=True)
    text = slurm_server.handle(request("prompts/get", 5, {
        "name": "diagnose_gpu_job", "arguments": {"job_id": "10"}}))[
        "result"]["messages"][0]["content"]["text"]
    assert "get_slurm_job" in text and "'10'" in text


def test_stray_prints_do_not_corrupt_protocol(capsys):
    class NoisyProvider(FakeProvider):
        def snapshot(self):
            print("nvml says hello")
            return super().snapshot()

    replies = run([call("get_hardware_status", 1)], NoisyProvider())
    assert payload(replies[0])["status"] == "warn"
    assert "nvml says hello" in capsys.readouterr().err


def test_nan_readings_become_null():
    snap = make_snapshot(cpu=float("nan"))
    replies = run([call("get_hardware_status", 1)], FakeProvider([snap]))
    assert payload(replies[0])["cpu_percent"] is None


def test_collector_reused_reprimed_after_gap_and_config_applied(monkeypatch):
    from ground_control import main

    created = []
    primes = []
    now = [100.0]
    monkeypatch.setattr(
        main,
        "_load_threshold_config",
        lambda: ({"cpu_percent": {"warn": 80}}, False, ["/snap"]),
    )
    monkeypatch.setattr(
        "ground_control.utils.system_metrics.SystemMetrics",
        lambda all_gpus=False: created.append(all_gpus) or created,
    )
    monkeypatch.setattr(mcp, "sample_twice", lambda collector, interval: primes.append(now[0]))

    def fake_build(collector, thresholds, disk_ignore_prefixes):
        assert collector is created
        assert thresholds == {"cpu_percent": {"warn": 80}}
        assert disk_ignore_prefixes == ["/snap"]
        return {"alerts": [{"level": "crit"}], "status": "crit"}

    monkeypatch.setattr(mcp, "build_snapshot", fake_build)
    provider = MetricsProvider(all_gpus=True, clock=lambda: now[0])
    assert provider.snapshot() == {"alerts": [], "status": "ok"}
    now[0] += 2
    assert provider.snapshot() == {"alerts": [], "status": "ok"}
    assert created == [True] and primes == [100.0]
    # Rates after a long gap would average the whole gap: prime again.
    now[0] += 60
    provider.snapshot()
    assert primes == [100.0, 162.0]


def test_all_mounts_disables_ignore_list(monkeypatch):
    from ground_control import main

    monkeypatch.setattr(main, "_load_threshold_config", lambda: ({}, True, ["/snap"]))
    monkeypatch.setattr("ground_control.utils.system_metrics.SystemMetrics",
                        lambda all_gpus=False: object())
    monkeypatch.setattr(mcp, "sample_twice", lambda collector, interval: None)
    seen = []
    monkeypatch.setattr(mcp, "build_snapshot", lambda collector, thresholds, disk_ignore_prefixes:
                        seen.append(disk_ignore_prefixes) or {"alerts": [], "status": "ok"})
    MetricsProvider(all_mounts=True).snapshot()
    assert seen == [[]]


def test_parse_error_and_blank_lines():
    output = io.StringIO()
    serve(io.StringIO("\n{not json\n"), output, FakeProvider(), slurm_enabled=False)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(replies) == 1 and replies[0]["error"]["code"] == -32700
    assert sys.stdout is not None

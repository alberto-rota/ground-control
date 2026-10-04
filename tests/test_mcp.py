"""MCP stdio handshake, discovery and metrics calls without real hardware."""

import io
import json

from ground_control.mcp import MetricsProvider, serve


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {
            "schema_version": 1,
            "timestamp_iso": "2026-01-01T00:00:00+0000",
            "host": "test-host",
            "status": "warn",
            "alerts": [{"level": "warn"}],
            "metrics": {
                "cpu": {"percent": 70, "per_core_percent": [60, 80]},
                "memory": {"percent": 50, "available_gb": 8},
                "disk": {"mounts": [{"mountpoint": "/", "percent": 80, "free_gb": 20}]},
                "network": {"download_mbps": 1, "upload_mbps": 2},
                "gpu": [
                    {
                        "index": 0,
                        "name": "GPU",
                        "utilization_percent": 80,
                        "memory_used_gb": 2,
                        "memory_total_gb": 8,
                        "temperature_c": 60,
                        "processes": [{"pid": 12}],
                    }
                ],
                "temperature_c": None,
            },
        }


def request(method, request_id, params=None):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def test_stdio_handshake_discovery_and_calls():
    provider = FakeProvider()
    messages = [
        request("initialize", 1, {"protocolVersion": "2024-11-05"}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request("tools/list", 2),
        request("tools/call", 3, {"name": "get_hardware_status"}),
        request(
            "tools/call",
            4,
            {"name": "get_hardware_metrics", "arguments": {"section": "gpu"}},
        ),
        request("ping", 5),
    ]
    output = io.StringIO()
    serve(
        io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), output, provider
    )
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert [t["name"] for t in replies[1]["result"]["tools"]] == [
        "get_hardware_status",
        "get_hardware_metrics",
    ]
    status = json.loads(replies[2]["result"]["content"][0]["text"])
    assert status["status"] == "warn" and status["cpu_percent"] == 70
    assert "processes" not in status["gpus"][0]
    details = json.loads(replies[3]["result"]["content"][0]["text"])
    assert list(details["metrics"]) == ["gpu"]
    assert details["metrics"]["gpu"][0]["processes"][0]["pid"] == 12
    assert provider.calls == 2


def test_invalid_arguments_do_not_collect_or_kill_server():
    provider = FakeProvider()
    messages = [
        request(
            "tools/call",
            1,
            {"name": "get_hardware_metrics", "arguments": {"section": ["cpu"]}},
        ),
        request(
            "tools/call",
            2,
            {"name": "get_hardware_metrics", "arguments": {"section": "battery"}},
        ),
        request(
            "tools/call",
            3,
            {"name": "get_hardware_status", "arguments": {"section": "cpu"}},
        ),
        request("tools/call", 4, {"name": "get_hardware_metrics"}),
    ]
    output = io.StringIO()
    serve(
        io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), output, provider
    )
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert all(r["error"]["code"] == -32602 for r in replies[:3])
    assert "metrics" in json.loads(replies[3]["result"]["content"][0]["text"])
    assert provider.calls == 1


def test_collector_reused_and_config_applied(monkeypatch):
    from ground_control import main
    from ground_control import mcp

    created = []
    monkeypatch.setattr(
        main,
        "_load_threshold_config",
        lambda: ({"cpu_percent": {"warn": 80}}, False, ["/snap"]),
    )
    monkeypatch.setattr(
        "ground_control.utils.system_metrics.SystemMetrics",
        lambda: created.append(object()) or created[-1],
    )
    monkeypatch.setattr(mcp, "sample_twice", lambda collector, interval: None)

    def fake_build(collector, thresholds, disk_ignore_prefixes):
        assert collector is created[0]
        assert thresholds == {"cpu_percent": {"warn": 80}}
        assert disk_ignore_prefixes == ["/snap"]
        return {"alerts": [{"level": "crit"}], "status": "crit"}

    monkeypatch.setattr(mcp, "build_snapshot", fake_build)
    provider = MetricsProvider()
    assert provider.snapshot() == {"alerts": [], "status": "ok"}
    assert provider.snapshot() == {"alerts": [], "status": "ok"}
    assert len(created) == 1

"""Read-only MCP stdio server for local ground-control snapshots.

Keep the transport small and dependency-free: MCP stdio is newline-delimited
JSON-RPC. Nothing except protocol messages may be written to stdout.
"""

import json
import sys
from importlib.metadata import PackageNotFoundError, version as package_version

from .utils.snapshot import build_snapshot, sample_twice


PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {PROTOCOL_VERSION, "2025-03-26", "2024-11-05"}
SECTIONS = ("cpu", "memory", "disk", "network", "gpu", "temperature_c")

TOOLS = [
    {
        "name": "get_hardware_status",
        "description": "Get current health, alerts, and compact CPU, RAM, disk, network and GPU readings from this machine.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_hardware_metrics",
        "description": "Get a fresh, detailed hardware snapshot from this machine; optionally select one metric family. Rates are MB/s and GPU memory is GB. No Slurm remote job is sampled.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": list(SECTIONS),
                    "description": "Omit for all metric families.",
                }
            },
            "additionalProperties": False,
        },
    },
]


class MetricsProvider:
    """Keep one collector alive so subsequent rate samples use earlier counters."""

    def __init__(self):
        self.collector = None

    def snapshot(self):
        from .main import _load_threshold_config
        from .utils.system_metrics import SystemMetrics

        thresholds, alerts_enabled, ignore_prefixes = _load_threshold_config()
        if self.collector is None:
            collector = SystemMetrics()
            sample_twice(collector, interval=0.2)
            self.collector = collector
        snapshot = build_snapshot(
            self.collector, thresholds=thresholds, disk_ignore_prefixes=ignore_prefixes
        )
        if not alerts_enabled:
            snapshot["alerts"] = []
            snapshot["status"] = "ok"
        return snapshot


def compact_status(snapshot):
    """Summarize large per-core/per-process arrays without hiding alerts."""
    metrics = snapshot["metrics"]
    disk = metrics.get("disk") or {}
    network = metrics.get("network") or {}
    cpu = metrics.get("cpu") or {}
    memory = metrics.get("memory") or {}
    return {
        "schema_version": snapshot["schema_version"],
        "timestamp_iso": snapshot["timestamp_iso"],
        "host": snapshot["host"],
        "status": snapshot["status"],
        "alerts": snapshot["alerts"],
        "cpu_percent": cpu.get("percent"),
        "memory_percent": memory.get("percent"),
        "memory_available_gb": memory.get("available_gb"),
        "disks": [
            {
                "mountpoint": m.get("mountpoint"),
                "percent": m.get("percent"),
                "free_gb": m.get("free_gb"),
            }
            for m in disk.get("mounts", [])
        ],
        "network": network,
        "gpus": [
            {
                "index": g.get("index"),
                "name": g.get("name"),
                "utilization_percent": g.get("utilization_percent"),
                "memory_used_gb": g.get("memory_used_gb"),
                "memory_total_gb": g.get("memory_total_gb"),
                "temperature_c": g.get("temperature_c"),
            }
            for g in metrics.get("gpu") or []
        ],
        "temperature_c": metrics.get("temperature_c"),
    }


def _result(request_id, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": request_id}
    response["error" if error is not None else "result"] = (
        error if error is not None else result
    )
    return response


def _error(request_id, code, message):
    return _result(request_id, error={"code": code, "message": message})


def handle_request(message, provider):
    """Return a JSON-RPC response, or None for a client notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid JSON-RPC request")
    method = message.get("method")
    request_id = message.get("id")
    if not isinstance(method, str):
        return _error(request_id, -32600, "Missing method")
    if "id" not in message:
        return None
    params = message.get("params", {})
    if not isinstance(params, dict):
        return _error(request_id, -32602, "Parameters must be an object")

    if method == "initialize":
        version = params.get("protocolVersion")
        try:
            server_version = package_version("ground-control-tui")
        except PackageNotFoundError:
            server_version = "unknown"
        return _result(
            request_id,
            {
                "protocolVersion": version
                if isinstance(version, str) and version in SUPPORTED_VERSIONS
                else PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ground-control", "version": server_version},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name not in ("get_hardware_status", "get_hardware_metrics"):
            return _error(request_id, -32602, "Unknown tool")
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "Tool arguments must be an object")
        section = arguments.get("section")
        if (
            name == "get_hardware_status"
            and arguments
            or name == "get_hardware_metrics"
            and (
                set(arguments) - {"section"}
                or (
                    "section" in arguments
                    and (not isinstance(section, str) or section not in SECTIONS)
                )
            )
        ):
            return _error(request_id, -32602, "Invalid tool arguments")
        try:
            snapshot = provider.snapshot()
            if name == "get_hardware_status":
                value = compact_status(snapshot)
            elif section:
                value = {
                    key: snapshot[key]
                    for key in (
                        "schema_version",
                        "timestamp_iso",
                        "host",
                        "status",
                        "alerts",
                    )
                }
                value["metrics"] = {section: snapshot["metrics"][section]}
            else:
                value = snapshot
            return _result(
                request_id, {"content": [{"type": "text", "text": json.dumps(value)}]}
            )
        except Exception as exc:  # a failed reading must not terminate the server
            return _result(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": f"Could not collect metrics: {exc}"}
                    ],
                    "isError": True,
                },
            )
    return _error(request_id, -32601, "Method not found")


def serve(input_stream=None, output_stream=None, provider=None):
    """Process one JSON-RPC message per line until the client closes stdin."""
    input_stream = input_stream if input_stream is not None else sys.stdin
    output_stream = output_stream if output_stream is not None else sys.stdout
    provider = provider if provider is not None else MetricsProvider()
    for line in input_stream:
        try:
            message = json.loads(line)
            response = handle_request(message, provider)
        except (ValueError, TypeError):
            response = _error(None, -32700, "Parse error")
        if response is not None:
            output_stream.write(json.dumps(response, allow_nan=False) + "\n")
            output_stream.flush()

"""Read-only MCP stdio server for ground-control snapshots and Slurm jobs.

Keep the transport small and dependency-free: MCP stdio is newline-delimited
JSON-RPC. Nothing except protocol messages may be written to stdout -- which is
why ``serve`` moves everything else (Python prints *and* C-level writes from
NVML) onto stderr before it starts reading.

Every tool only reads. The Slurm tools are listed only when ``squeue`` is on
PATH, since a model offered a tool that can never succeed will keep trying it.
"""

import contextlib
import json
import math
import os
import re
import socket
import sys
import time
from importlib.metadata import PackageNotFoundError, version as package_version

from .utils import slurm
from .utils.alerts import METRIC_DIRECTIONS, worst
from .utils.gpu_insights import diagnose_gpus as gpu_findings
from .utils.snapshot import build_snapshot, sample_twice


PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {PROTOCOL_VERSION, "2025-03-26", "2024-11-05"}
# Protocol versions are ISO dates, so they compare correctly as strings.
STRUCTURED_CONTENT_SINCE = "2025-06-18"
SECTIONS = ("cpu", "memory", "disk", "network", "gpu", "temperature_c")
# Rates are deltas against the previous reading. After a gap this long they
# would describe the whole gap rather than now, so the counters are re-primed.
REPRIME_AFTER_SECONDS = 10.0
# Plain (123), array (123_4) and heterogeneous (123+0) job ids.
JOB_ID_PATTERN = r"^[0-9]+([_+][0-9]+)?$"
MAX_SAMPLES = 121


class ToolError(Exception):
    """A failure the model should see and can act on (returned as ``isError``)."""


class MetricsProvider:
    """Keep one collector alive so subsequent rate samples use earlier counters."""

    def __init__(self, all_gpus=False, all_mounts=False, clock=time.monotonic):
        self.collector = None
        self.all_gpus = all_gpus
        self.all_mounts = all_mounts
        self._clock = clock
        self._last_sample = None

    def snapshot(self):
        from .main import _load_threshold_config
        from .utils.system_metrics import SystemMetrics

        thresholds, alerts_enabled, ignore_prefixes = _load_threshold_config()
        if self.collector is None:
            self.collector = SystemMetrics(all_gpus=self.all_gpus)
            self._last_sample = None
        if (self._last_sample is None
                or self._clock() - self._last_sample > REPRIME_AFTER_SECONDS):
            sample_twice(self.collector, interval=0.2)
        snapshot = build_snapshot(
            self.collector,
            thresholds=thresholds,
            disk_ignore_prefixes=[] if self.all_mounts else ignore_prefixes,
        )
        self._last_sample = self._clock()
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
        "network": _compact_network(network),
        "gpu_scope": snapshot.get("gpu_scope"),
        "gpus": [
            {
                "index": g.get("index"),
                "name": g.get("name"),
                "utilization_percent": g.get("utilization_percent"),
                "memory_used_gb": g.get("memory_used_gb"),
                "memory_total_gb": g.get("memory_total_gb"),
                "temperature_c": g.get("temperature_c"),
                "power_percent": g.get("power_percent"),
                "memory_bandwidth_percent": g.get("memory_bandwidth_percent"),
                "throttle_severe": g.get("throttle_severe"),
            }
            for g in metrics.get("gpu") or []
        ],
        "temperature_c": metrics.get("temperature_c"),
    }


def _compact_network(network):
    """Rates plus one-line facts; the full socket list is get_network_details'."""
    compact = {key: network.get(key) for key in ("download_mbps", "upload_mbps")}
    if "listening_summary" in network:
        compact["listening"] = network.get("listening_summary")
        compact["tcp_retransmit_percent"] = (network.get("tcp") or {}).get(
            "retransmit_percent")
        compact["established_connections"] = (network.get("connections") or {}).get(
            "established")
    return compact


def _select_section(snapshot, section):
    """The snapshot itself, or its header plus one metric family."""
    if not section:
        return snapshot
    value = {
        key: snapshot.get(key)
        for key in ("schema_version", "timestamp_iso", "host", "status", "alerts")
    }
    if section == "gpu":
        value["gpu_scope"] = snapshot.get("gpu_scope")
    value["metrics"] = {section: (snapshot.get("metrics") or {}).get(section)}
    return value


# --------------------------------------------------------------------------- #
# Windowed sampling
# --------------------------------------------------------------------------- #
GPU_SERIES_FIELDS = (
    "utilization_percent", "memory_percent", "memory_used_gb", "power_w",
    "power_percent", "memory_bandwidth_percent", "temperature_c", "sm_clock_mhz",
)


def _stats(values):
    numbers = [float(v) for v in values
               if isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v)]
    if not numbers:
        return None
    return {
        "min": round(min(numbers), 2),
        "mean": round(sum(numbers) / len(numbers), 2),
        "max": round(max(numbers), 2),
        "last": round(numbers[-1], 2),
    }


def summarize_series(snapshots):
    """Collapse a run of snapshots into min/mean/max/last per reading.

    A single reading of GPU utilisation or network rate is a coin toss; the
    window is what answers "is this machine busy". Alerts are the union over
    the window, so a spike that came and went is still reported.
    """
    def metric(snap, *path):
        value = snap.get("metrics") or {}
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    seen_alerts = {}
    for snap in snapshots:
        for alert in snap.get("alerts") or []:
            key = (alert.get("metric"), alert.get("scope"))
            previous = seen_alerts.get(key)
            if previous is None or worst([previous.get("level"), alert.get("level")]) != previous.get("level"):
                seen_alerts[key] = alert

    gpus = {}
    for snap in snapshots:
        for gpu in metric(snap, "gpu") or []:
            if isinstance(gpu, dict):
                gpus.setdefault(gpu.get("index"), []).append(gpu)
    gpu_summary = []
    for index, readings in gpus.items():
        entry = {"index": index, "name": readings[-1].get("name")}
        for field in GPU_SERIES_FIELDS:
            entry[field] = _stats(r.get(field) for r in readings)
        reasons = []
        for r in readings:
            for reason in r.get("throttle_reasons") or []:
                if reason not in reasons:
                    reasons.append(reason)
        entry["throttle_reasons_seen"] = reasons
        entry["throttle_severe_seen"] = any(r.get("throttle_severe") for r in readings)
        entry["process_count"] = readings[-1].get("process_count")
        gpu_summary.append(entry)

    sensors = {}
    for snap in snapshots:
        for name, value in (metric(snap, "temperature_c") or {}).items():
            sensors.setdefault(name, []).append(value)

    mounts = {}
    for snap in snapshots:
        for mount in metric(snap, "disk", "mounts") or []:
            mounts.setdefault(mount.get("mountpoint"), []).append(mount)

    return {
        "status": worst([s.get("status") for s in snapshots]),
        "alerts_seen": list(seen_alerts.values()),
        "metrics": {
            "cpu": {"percent": _stats(metric(s, "cpu", "percent") for s in snapshots)},
            "memory": {
                "percent": _stats(metric(s, "memory", "percent") for s in snapshots),
                "available_gb": _stats(metric(s, "memory", "available_gb") for s in snapshots),
            },
            "disk": {
                "read_mbps": _stats(metric(s, "disk", "read_mbps") for s in snapshots),
                "write_mbps": _stats(metric(s, "disk", "write_mbps") for s in snapshots),
                "mounts": [
                    {
                        "mountpoint": point,
                        "percent": readings[-1].get("percent"),
                        "free_gb": readings[-1].get("free_gb"),
                    }
                    for point, readings in mounts.items()
                ],
            },
            "network": {
                "download_mbps": _stats(metric(s, "network", "download_mbps") for s in snapshots),
                "upload_mbps": _stats(metric(s, "network", "upload_mbps") for s in snapshots),
                "tcp_retransmit_percent": _stats(
                    (metric(s, "network", "tcp") or {}).get("retransmit_percent")
                    for s in snapshots),
            },
            "gpu": gpu_summary,
            "temperature_c": {name: _stats(values) for name, values in sensors.items()} or None,
        },
    }


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #
def _check_value(key, value, spec):
    kind = spec.get("type")
    if kind == "string":
        if not isinstance(value, str):
            return f"'{key}' must be a string"
        if "enum" in spec and value not in spec["enum"]:
            return f"'{key}' must be one of: {', '.join(spec['enum'])} (got {value!r})"
        if "pattern" in spec and not re.search(spec["pattern"], value):
            return f"'{key}' has an invalid format (got {value!r})"
    elif kind in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"'{key}' must be a {kind}"
        if kind == "integer" and not float(value).is_integer():
            return f"'{key}' must be a whole number"
        if "minimum" in spec and value < spec["minimum"]:
            return f"'{key}' must be at least {spec['minimum']}"
        if "maximum" in spec and value > spec["maximum"]:
            return f"'{key}' must be at most {spec['maximum']}"
    elif kind == "boolean" and not isinstance(value, bool):
        return f"'{key}' must be true or false"
    elif kind == "array":
        if not isinstance(value, list):
            return f"'{key}' must be an array"
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            return f"'{key}' may hold at most {spec['maxItems']} items"
        items = spec.get("items") or {}
        for index, item in enumerate(value):
            error = _check_value(f"{key}[{index}]", item, items)
            if error:
                return error
    return None


def validate_arguments(schema, args):
    """Check ``args`` against the JSON-schema subset our tools use.

    Returns an error message naming the offending field, or None. A message the
    model can read and correct beats a generic "invalid arguments".
    """
    if not isinstance(args, dict):
        return "arguments must be an object"
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required argument '{key}'"
    for key, value in args.items():
        spec = properties.get(key)
        if spec is None:
            if schema.get("additionalProperties", True) is False:
                accepted = ", ".join(properties) or "none"
                return f"unknown argument '{key}' (accepted: {accepted})"
            continue
        error = _check_value(key, value, spec)
        if error:
            return error
    return None


def _with_defaults(schema, args):
    merged = {
        key: spec["default"]
        for key, spec in schema.get("properties", {}).items()
        if "default" in spec
    }
    merged.update(args)
    return merged


def _scrub(value):
    """Replace non-finite floats with None: JSON has no NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
_SECTION_ARG = {
    "type": "string",
    "enum": list(SECTIONS),
    "description": "Omit for all metric families.",
}
_JOB_ID_ARG = {
    "type": "string",
    "pattern": JOB_ID_PATTERN,
    "description": "Slurm job id, e.g. \"123456\" or \"123456_7\" for an array task.",
}


def _schema(properties=None, required=None):
    schema = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


class Tool:
    def __init__(self, name, title, description, handler, properties=None,
                 required=None, needs_slurm=False):
        self.name = name
        self.title = title
        self.description = description
        self.handler = handler
        self.input_schema = _schema(properties, required)
        self.needs_slurm = needs_slurm

    def definition(self):
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                # Slurm tools talk to the cluster controller and compute nodes.
                "openWorldHint": self.needs_slurm,
            },
        }


def _tool_status(server, args, progress):
    return compact_status(server.provider.snapshot())


def _tool_metrics(server, args, progress):
    return _select_section(server.provider.snapshot(), args.get("section"))


def _tool_sample(server, args, progress):
    snapshots = server.sample_window(args["duration_seconds"],
                                     args["interval_seconds"], progress)
    summary = summarize_series(snapshots)
    section = args.get("section")
    if section:
        summary["metrics"] = {section: summary["metrics"][section]}
    return {
        "host": snapshots[-1].get("host"),
        "window": {
            "start_iso": snapshots[0].get("timestamp_iso"),
            "end_iso": snapshots[-1].get("timestamp_iso"),
            "samples": len(snapshots),
            "interval_seconds": args["interval_seconds"],
        },
        **summary,
    }


def _mean_gpus(summary_gpus):
    """Turn windowed GPU stats into the flat shape ``gpu_insights`` reads."""
    flat = []
    for entry in summary_gpus:
        gpu = {"index": entry["index"], "name": entry.get("name"),
               "process_count": entry.get("process_count"),
               "throttle_reasons": entry.get("throttle_reasons_seen") or [],
               "throttle_severe": entry.get("throttle_severe_seen", False)}
        for field in GPU_SERIES_FIELDS:
            stats = entry.get(field)
            gpu[field] = stats["mean"] if stats else None
        flat.append(gpu)
    return flat


def _tool_diagnose(server, args, progress):
    job_id = args.get("job_id")
    if job_id:
        if not server.slurm_enabled:
            raise ToolError("Slurm is not available on this host, so 'job_id' cannot be used.")
        snapshot = server.probe_job(job_id, progress)
        gpus = (snapshot.get("metrics") or {}).get("gpu") or []
        basis = f"single reading from inside job {job_id}"
        host = snapshot.get("host")
        scope = snapshot.get("gpu_scope")
    elif args["duration_seconds"] > 0:
        snapshots = server.sample_window(args["duration_seconds"], 1.0, progress)
        gpus = _mean_gpus(summarize_series(snapshots)["metrics"]["gpu"])
        basis = f"mean over {len(snapshots)} samples"
        host = snapshots[-1].get("host")
        scope = snapshots[-1].get("gpu_scope")
    else:
        snapshot = server.provider.snapshot()
        gpus = (snapshot.get("metrics") or {}).get("gpu") or []
        basis = "single reading"
        host = snapshot.get("host")
        scope = snapshot.get("gpu_scope")

    result = {
        "host": host,
        "basis": basis,
        "gpus": [
            {key: gpu.get(key) for key in ("index", "name", "utilization_percent",
                                           "memory_percent", "power_percent",
                                           "memory_bandwidth_percent", "temperature_c",
                                           "throttle_reasons")}
            for gpu in gpus
        ],
        "findings": gpu_findings(gpus),
        "gpu_scope": scope,
    }
    if not gpus:
        note = f"No NVIDIA GPUs are visible on {host}."
        if server.slurm_enabled and not job_id:
            note += (" This is probably a login node; pass 'job_id' to diagnose the"
                     " GPUs of a running Slurm job.")
        result["note"] = note
    return result


def _tool_thresholds(server, args, progress):
    from .main import _load_threshold_config

    thresholds, alerts_enabled, prefixes = _load_threshold_config()
    return {
        "alerts_enabled": bool(alerts_enabled),
        "thresholds": {
            metric: {**spec, "direction": METRIC_DIRECTIONS.get(metric)}
            for metric, spec in thresholds.items()
        },
        "disk_ignore_prefixes": [] if getattr(server.provider, "all_mounts", False) else prefixes,
        "note": ("direction 'above' alerts when the value exceeds the threshold, "
                 "'below' when it falls under it. Disabled thresholds never alert."),
    }


def _time_used_percent(elapsed, limit):
    if elapsed is None or not limit:
        return None
    return round(elapsed / limit * 100.0, 1)


def _job_row(job):
    row = dict(job)
    elapsed = slurm.parse_duration(job.get("elapsed"))
    limit = slurm.parse_duration(job.get("timelimit"))
    row["gpus"] = slurm.gpus_from_gres(job.get("gres"))
    row["elapsed_seconds"] = elapsed
    row["timelimit_seconds"] = limit
    row["time_used_percent"] = _time_used_percent(elapsed, limit)
    return row


def _tool_list_jobs(server, args, progress):
    jobs = slurm.get_user_jobs(args.get("user"))
    state = args["state"]
    if state == "running":
        jobs = [j for j in jobs if slurm.is_running_state(j.get("state"))]
    elif state == "pending":
        jobs = [j for j in jobs if (j.get("state") or "").upper() in ("PENDING", "PD")]
    jobs.sort(key=slurm.job_sort_key)
    return {
        "user": args.get("user") or "current user",
        "state_filter": state,
        "count": len(jobs),
        "jobs": [_job_row(j) for j in jobs],
    }


def _tool_get_job(server, args, progress):
    job_id = args["job_id"]
    detail = slurm.get_job_detail(job_id)
    running, state = slurm.get_job_liveness(job_id)
    if not detail and not running:
        liveness = {"running": False, "final_state": state,
                    "note": "The job is no longer in the queue."}
    elif state is None and running:
        liveness = {"running": None,
                    "note": "The controller did not answer; state unknown."}
    else:
        liveness = {"running": running, "state": state}
    result = {"job_id": job_id, "liveness": liveness, "detail": detail or None}
    if detail:
        result["time_used_percent"] = _time_used_percent(
            slurm.parse_duration(detail.get("elapsed")),
            slurm.parse_duration(detail.get("timelimit")))
    if slurm.is_running_state(state or (detail or {}).get("state")):
        result["usage"] = slurm.get_job_live_stats(job_id)
    if not detail and state is None and not running:
        result["note"] = "Slurm knows no job with this id (it may have ended long ago)."
    return result


def _tool_job_metrics(server, args, progress):
    snapshot = server.probe_job(args["job_id"], progress)
    value = _select_section(snapshot, args.get("section"))
    value = dict(value)
    value["job_id"] = args["job_id"]
    value["note"] = (f"Sampled inside the job on {snapshot.get('host')}; process "
                     "ids refer to that compute node, not to this host.")
    return value


def _process_rows(snapshots, gpu_index, pids):
    """Per-process GPU memory and SM % over ``snapshots``, per GPU.

    With one snapshot the stats collapse to a single reading. ``pids`` limits
    the rows *and* adds a ``selected`` total per GPU -- the same sum the
    dashboard plots when processes are selected in a GPU panel.
    """
    wanted = set(pids) if pids else None
    per_gpu = {}
    for snap in snapshots:
        for gpu in (snap.get("metrics") or {}).get("gpu") or []:
            if not isinstance(gpu, dict):
                continue
            if gpu_index is not None and gpu.get("index") != gpu_index:
                continue
            entry = per_gpu.setdefault(gpu.get("index"), {
                "index": gpu.get("index"), "name": gpu.get("name"),
                "device_util": [], "device_mem": [], "procs": {}, "selected": []})
            entry["device_util"].append(gpu.get("utilization_percent"))
            entry["device_mem"].append(gpu.get("memory_used_gb"))
            mem_sum = sm_sum = 0.0
            sm_seen = False
            for proc in gpu.get("processes") or []:
                pid = proc.get("pid")
                if wanted is not None and pid not in wanted:
                    continue
                row = entry["procs"].setdefault(pid, {"info": proc, "mem": [], "sm": []})
                row["info"] = proc
                mem = proc.get("gpu_memory_bytes")
                mem_gb = None if mem is None else mem / 1000**3
                row["mem"].append(mem_gb)
                row["sm"].append(proc.get("sm_util_percent"))
                mem_sum += mem_gb or 0.0
                if proc.get("sm_util_percent") is not None:
                    sm_sum += proc["sm_util_percent"]
                    sm_seen = True
            entry["selected"].append((mem_sum, sm_sum if sm_seen else None))

    result = []
    for entry in per_gpu.values():
        gpu = {
            "index": entry["index"],
            "name": entry["name"],
            "utilization_percent": _stats(entry["device_util"]),
            "memory_used_gb": _stats(entry["device_mem"]),
            "processes": [
                {
                    "pid": pid,
                    "name": row["info"].get("name"),
                    "username": row["info"].get("username"),
                    "command": row["info"].get("command"),
                    "cwd": row["info"].get("cwd"),
                    "gpu_memory_gb": _stats(row["mem"]),
                    "sm_util_percent": _stats(row["sm"]),
                    "samples_present": len(row["mem"]),
                }
                for pid, row in entry["procs"].items()
            ],
        }
        if wanted is not None:
            gpu["selected"] = {
                "pids": sorted(wanted),
                "gpu_memory_gb": _stats(m for m, _ in entry["selected"]),
                "sm_util_percent": _stats(s for _, s in entry["selected"]),
            }
        result.append(gpu)
    return result


def _tool_gpu_processes(server, args, progress):
    job_id = args.get("job_id")
    if job_id:
        if not server.slurm_enabled:
            raise ToolError("Slurm is not available on this host, so 'job_id' cannot be used.")
        snapshots = [server.probe_job(job_id, progress)]
        basis = f"single reading from inside job {job_id}"
    elif args["duration_seconds"] > 0:
        snapshots = server.sample_window(args["duration_seconds"], 1.0, progress)
        basis = f"{len(snapshots)} samples over {args['duration_seconds']}s"
    else:
        snapshots = [server.provider.snapshot()]
        basis = "single reading"
    gpus = _process_rows(snapshots, args.get("gpu_index"), args.get("pids"))
    result = {
        "host": snapshots[-1].get("host"),
        "basis": basis,
        "gpu_scope": snapshots[-1].get("gpu_scope"),
        "gpus": gpus,
        "note": ("sm_util_percent is NVML's per-process sample of SM activity; "
                 "summed over processes it need not equal the device utilisation. "
                 "Process ids refer to the sampled host. 'index' counts the GPUs "
                 "shown (0..n-1); gpu_scope.visible lists their physical NVML "
                 "indices, which differ when the host is scoped to an allocation."),
    }
    if args.get("pids") and not any(g["processes"] for g in gpus):
        result["note"] += " None of the requested pids holds a GPU context right now."
    return result


_LISTENING_FILTERS = ("all", "exposed", "mine")


def _tunnel_hint(row, host, login_host):
    """An ssh command that would reach a listening service from a laptop.

    Hints, not instructions: they assume the usual setup (ssh to the login node,
    compute nodes reachable from it) and say nothing about firewalls.
    """
    port = row.get("port")
    if not port or not str(row.get("proto") or "").startswith("tcp"):
        return None  # ssh -L forwards TCP only
    exposure = row.get("exposure")
    loopback = exposure == "localhost only"
    # Bound to one address: only that address answers, not localhost and not
    # necessarily the hostname. IPv6 literals need brackets in -L.
    address = row.get("address") or ""
    specific = not loopback and exposure != "all interfaces" and address
    target = (f"[{address}]" if ":" in address else address) if specific else None
    if login_host and login_host != host:
        if loopback:
            # Bound to the compute node's loopback: hop through the login node.
            return f"ssh -N -L {port}:localhost:{port} -J {login_host} {host}"
        return f"ssh -N -L {port}:{target or host}:{port} {login_host}"
    return f"ssh -N -L {port}:{target or 'localhost'}:{port} {host}"


def _tool_network_details(server, args, progress):
    job_id = args.get("job_id")
    if job_id:
        if not server.slurm_enabled:
            raise ToolError("Slurm is not available on this host, so 'job_id' cannot be used.")
        snapshot = server.probe_job(job_id, progress)
        login_host = socket.gethostname()
    else:
        snapshot = server.provider.snapshot()
        login_host = None
    host = snapshot.get("host")
    network = (snapshot.get("metrics") or {}).get("network") or {}
    if "listening" not in network:
        reason = ("the ground-control in the job may be too old" if job_id
                  else "the network details collector failed on this host (see its log)")
        raise ToolError(
            f"{host} did not report network details: {reason}. Throughput alone is in "
            "get_hardware_metrics(section='network').")
    listening = [dict(r) for r in network.get("listening") or [] if isinstance(r, dict)]
    which = args["listening"]
    if which == "exposed":
        listening = [r for r in listening if r.get("exposure") != "localhost only"]
    elif which == "mine":
        listening = [r for r in listening if r.get("mine")]
    for row in listening:
        if row.get("mine"):
            row["tunnel_hint"] = _tunnel_hint(row, host, login_host)
    result = {
        "host": host,
        "listening_filter": which,
        "listening": listening,
        "listening_summary": network.get("listening_summary"),
        "connections": network.get("connections"),
        "tcp": network.get("tcp"),
        "throughput_mbps": {"download": network.get("download_mbps"),
                            "upload": network.get("upload_mbps")},
        "note": ("exposure 'all interfaces' means reachable from the network unless a "
                 "firewall blocks it. Without root, other users' sockets have no pid or "
                 "process. service_hint is a guess from the port number."),
    }
    if args["include_interfaces"]:
        result["interfaces"] = network.get("interfaces")
    if job_id:
        result["job_id"] = job_id
    return result


TOOLS = [
    Tool(
        "get_hardware_status", "Hardware status",
        "Get current health, alerts, and compact CPU, RAM, disk, network and GPU "
        "readings from the machine running this server. One instantaneous reading: "
        "for utilisation questions prefer sample_hardware.",
        _tool_status,
    ),
    Tool(
        "get_hardware_metrics", "Hardware metrics",
        "Get a fresh, detailed hardware snapshot from this machine (per-core CPU, "
        "memory breakdown, every mount, per-GPU telemetry and processes); "
        "optionally select one metric family. Rates are MB/s and GPU memory is GB. "
        "No Slurm job is sampled.",
        _tool_metrics,
        {"section": _SECTION_ARG},
    ),
    Tool(
        "sample_hardware", "Sample hardware over time",
        "Sample this machine repeatedly over a short window and return "
        "min/mean/max/last per reading plus every alert seen. Use this instead of "
        "a single reading when asking whether the machine or a GPU is busy: GPU "
        "utilisation and network rates fluctuate from second to second. Blocks "
        "for duration_seconds.",
        _tool_sample,
        {
            "duration_seconds": {"type": "number", "minimum": 1, "maximum": 60,
                                 "default": 10, "description": "Window length."},
            "interval_seconds": {"type": "number", "minimum": 0.5, "maximum": 10,
                                 "default": 1, "description": "Gap between samples."},
            "section": _SECTION_ARG,
        },
    ),
    Tool(
        "diagnose_gpus", "Diagnose GPUs",
        "Interpret GPU readings and return findings: input-starved (high "
        "utilisation but low power and memory bandwidth -- a data-loading or "
        "CPU bottleneck), hardware throttling, VRAM nearly full, idle GPUs "
        "holding memory, overheating, or genuinely busy. NVML 'utilisation' only "
        "means a kernel was resident, so this is more reliable than reading it "
        "directly. Without job_id it averages the local GPUs over duration_seconds; "
        "with job_id it samples inside that running Slurm job (takes ~5 s).",
        _tool_diagnose,
        {
            "duration_seconds": {"type": "number", "minimum": 0, "maximum": 30,
                                 "default": 5,
                                 "description": "Local averaging window; 0 for one reading."},
            "job_id": _JOB_ID_ARG,
        },
    ),
    Tool(
        "get_gpu_processes", "GPU processes",
        "List the processes on each GPU with their own GPU memory and SM "
        "utilisation (NVML per-process samples), so GPU load can be attributed to "
        "a process. 'pids' restricts the list and adds their summed 'selected' "
        "load -- the same figure the dashboard plots when processes are selected "
        "in a GPU panel. duration_seconds > 0 returns min/mean/max/last over a "
        "window; job_id samples inside a running Slurm job instead (~5 s). Also "
        "reports gpu_scope: which GPUs this host counts as allocated, and why.",
        _tool_gpu_processes,
        {
            "pids": {"type": "array", "items": {"type": "integer", "minimum": 0},
                     "maxItems": 64,
                     "description": "Only these process ids (and their total)."},
            "gpu_index": {"type": "integer", "minimum": 0,
                          "description": "Only this GPU (the 'index' of get_hardware_metrics)."},
            "duration_seconds": {"type": "number", "minimum": 0, "maximum": 30,
                                 "default": 0,
                                 "description": "Sampling window; 0 for one reading."},
            "job_id": _JOB_ID_ARG,
        },
    ),
    Tool(
        "get_network_details", "Network details",
        "List listening TCP/UDP sockets (port, bind address, who can reach it -- "
        "'all interfaces' vs 'localhost only' --, owning process and user where "
        "visible, a service guess such as jupyter or tensorboard), network "
        "interfaces with addresses, link speed and per-interface rates, TCP "
        "connection counts and TCP retransmit percentage. The user's own services "
        "come with an ssh tunnel hint. With job_id it reads the job's compute node "
        "(~5 s), which is how to find the port of a notebook or dashboard "
        "running inside a Slurm job.",
        _tool_network_details,
        {
            "listening": {"type": "string", "enum": list(_LISTENING_FILTERS),
                          "default": "all",
                          "description": ("'exposed': not bound to loopback only; "
                                          "'mine': owned by the current user.")},
            "include_interfaces": {"type": "boolean", "default": True},
            "job_id": _JOB_ID_ARG,
        },
    ),
    Tool(
        "get_alert_thresholds", "Alert thresholds",
        "Return the alert thresholds in effect (warn/crit per metric, direction, "
        "enabled), whether alerting is on, and which mounts are ignored. Use it to "
        "explain why an alert fired or why one did not.",
        _tool_thresholds,
    ),
    Tool(
        "list_slurm_jobs", "List Slurm jobs",
        "List Slurm jobs (yours by default), running first, with state, partition, "
        "nodes, GPUs, memory, elapsed vs time limit and pending reason. An empty "
        "list can also mean the controller did not answer.",
        _tool_list_jobs,
        {
            "state": {"type": "string", "enum": ["all", "running", "pending"],
                      "default": "all"},
            "user": {"type": "string", "pattern": r"^[A-Za-z0-9._-]+$",
                     "description": "Another user's jobs; omit for your own."},
        },
        needs_slurm=True,
    ),
    Tool(
        "get_slurm_job", "Slurm job detail",
        "Get one Slurm job's allocation (nodes, CPUs, GPUs, memory, account, QOS, "
        "start/end time, workdir, command, exit code), whether it is still running "
        "(and its final state from accounting if it ended), and live CPU/RSS usage "
        "from sstat for a running job.",
        _tool_get_job,
        {"job_id": _JOB_ID_ARG},
        required=["job_id"],
        needs_slurm=True,
    ),
    Tool(
        "get_slurm_job_metrics", "Slurm job metrics",
        "Sample hardware from *inside* a running Slurm job's allocation (srun "
        "--overlap on its compute node), so CPU, memory and GPUs are the job's own. "
        "This is the only way to see a job's GPUs from a login node. Slow: each "
        "call creates a job step (~5 s); do not poll it in a tight loop. Same shape "
        "as get_hardware_metrics.",
        _tool_job_metrics,
        {"job_id": _JOB_ID_ARG, "section": _SECTION_ARG},
        required=["job_id"],
        needs_slurm=True,
    ),
]
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
PROMPTS = [
    {
        "name": "diagnose_slow_machine",
        "title": "Why is this machine slow?",
        "description": "Find the bottleneck on the machine running ground-control.",
        "arguments": [],
    },
    {
        "name": "diagnose_gpu_job",
        "title": "Diagnose GPU usage",
        "description": "Check whether a Slurm job's (or this machine's) GPUs are doing useful work.",
        "arguments": [
            {"name": "job_id", "description": "Slurm job id; omit for this machine's GPUs.",
             "required": False},
        ],
    },
    {
        "name": "reach_service",
        "title": "Reach a running service",
        "description": ("Find the port of a notebook, dashboard or server (e.g. Jupyter, "
                        "TensorBoard) on this machine or inside a Slurm job, and how to "
                        "tunnel to it."),
        "arguments": [
            {"name": "job_id", "description": "Slurm job id; omit for this machine.",
             "required": False},
        ],
    },
    {
        "name": "explain_alerts",
        "title": "Explain current alerts",
        "description": "Explain which alerts are active and what thresholds triggered them.",
        "arguments": [],
    },
]
PROMPTS_BY_NAME = {prompt["name"]: prompt for prompt in PROMPTS}


def _prompt_text(name, args, slurm_enabled):
    if name == "diagnose_slow_machine":
        return (
            "Find out why this machine feels slow, using the ground-control tools.\n"
            "1. Call get_hardware_status for the current alerts and headline numbers.\n"
            "2. Call sample_hardware with duration_seconds=10 so you judge sustained "
            "load rather than a single reading.\n"
            "3. Drill into whichever family looks saturated with get_hardware_metrics "
            "(per-core CPU and cgroup throttling under cpu.telemetry, swap and "
            "commit ratio under memory, per-mount I/O under disk, GPU processes under gpu).\n"
            "Name the single most likely bottleneck, quote the numbers that show it, "
            "and say what would confirm or rule it out."
        )
    if name == "diagnose_gpu_job":
        job_id = args.get("job_id")
        if job_id:
            return (
                f"Diagnose whether Slurm job {job_id} is using its GPUs well.\n"
                f"1. Call get_slurm_job with job_id={job_id!r} to confirm it is "
                "running and see what it was allocated and how much time remains.\n"
                f"2. Call diagnose_gpus with job_id={job_id!r}.\n"
                "3. If useful, call get_slurm_job_metrics for the job's per-GPU "
                "processes and CPU load (a starved GPU often sits next to a pegged CPU).\n"
                "Interpret power_percent and memory_bandwidth_percent, not just "
                "utilization_percent: NVML utilisation only means a kernel was "
                "resident. Conclude with a verdict and concrete next steps."
            )
        steps = (
            "Diagnose whether this machine's GPUs are doing useful work.\n"
            "1. Call diagnose_gpus with duration_seconds=10.\n"
            "2. Call get_hardware_metrics with section='gpu' to see which processes "
            "hold each GPU, and section='cpu' if any GPU looks input-starved.\n"
        )
        if slurm_enabled:
            steps += ("If no GPUs are visible this is likely a login node: call "
                      "list_slurm_jobs with state='running' and diagnose those jobs instead.\n")
        return steps + ("Interpret power_percent and memory_bandwidth_percent, not just "
                        "utilization_percent. Conclude with a verdict and next steps.")
    if name == "reach_service":
        job_id = args.get("job_id")
        where = (f"inside Slurm job {job_id}" if job_id else "on this machine")
        call_args = f"job_id={job_id!r}, listening='mine'" if job_id else "listening='mine'"
        return (
            f"Find the services I am running {where} and tell me how to reach them.\n"
            f"1. Call get_network_details with {call_args}.\n"
            "2. For each listener, name what it most likely is (process name first, "
            "service_hint second), its port, and whether it is bound to localhost only "
            "or to all interfaces.\n"
            "3. Give the tunnel_hint command for the one I most likely want, and warn "
            "if a service is exposed on all interfaces without needing to be.\n"
            "If nothing is listening, say so and suggest checking the program's log."
        )
    return (
        "Explain the current ground-control alerts.\n"
        "1. Call get_hardware_status for the active alerts.\n"
        "2. Call get_alert_thresholds to see the warn/crit limits and directions.\n"
        "For each alert say which metric breached which limit, by how much, and "
        "whether it needs action. If there are no alerts, say which readings are "
        "closest to a threshold."
    )


# --------------------------------------------------------------------------- #
# JSON-RPC
# --------------------------------------------------------------------------- #
def _result(request_id, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": request_id}
    response["error" if error is not None else "result"] = (
        error if error is not None else result
    )
    return response


def _error(request_id, code, message):
    return _result(request_id, error={"code": code, "message": message})


def _server_version():
    try:
        return package_version("ground-control-tui")
    except PackageNotFoundError:
        return "unknown"


class McpServer:
    """One client session: negotiated version, Slurm availability, notifications."""

    def __init__(self, provider, slurm_enabled=None, notify=None, sleep=time.sleep,
                 clock=time.monotonic):
        self.provider = provider
        self.slurm_enabled = (slurm.slurm_available() if slurm_enabled is None
                              else bool(slurm_enabled))
        self.notify = notify or (lambda method, params: None)
        self.protocol_version = PROTOCOL_VERSION
        self._sleep = sleep
        self._clock = clock

    # -- helpers used by tool handlers -------------------------------------- #
    def tools(self):
        return [t for t in TOOLS if self.slurm_enabled or not t.needs_slurm]

    def sample_window(self, duration, interval, progress):
        """Snapshots spaced ``interval`` apart, spanning about ``duration``."""
        count = min(int(duration // interval) + 1, MAX_SAMPLES)
        snapshots = []
        for n in range(count):
            started = self._clock()
            snapshots.append(self.provider.snapshot())
            progress(n + 1, count, f"sample {n + 1}/{count}")
            if n + 1 < count:
                self._sleep(max(interval - (self._clock() - started), 0.05))
        return snapshots

    def probe_job(self, job_id, progress):
        running, state = slurm.get_job_liveness(job_id)
        if not running:
            raise ToolError(
                f"Job {job_id} is not running (state: {state or 'ended, state unknown'}); "
                "only running jobs can be sampled. Use get_slurm_job for its detail.")
        detail = slurm.get_job_detail(job_id)
        node = slurm.first_node(detail.get("nodelist"))
        progress(0, 1, f"starting a job step on {node or 'the job'}")
        snapshot = slurm.probe_job_metrics(job_id, node=node)
        if snapshot is None:
            raise ToolError(
                f"Could not sample job {job_id}: the job step could not be created, or "
                "ground_control could not run on its compute node. Check that the job "
                "is still running and that `python -m ground_control --once` works there.")
        progress(1, 1, "sampled")
        return snapshot

    # -- dispatch ----------------------------------------------------------- #
    def handle(self, message):
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
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Parameters must be an object")

        if method == "initialize":
            return self._initialize(request_id, params)
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": [t.definition() for t in self.tools()]})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        if method == "prompts/list":
            return _result(request_id, {"prompts": PROMPTS})
        if method == "prompts/get":
            return self._get_prompt(request_id, params)
        return _error(request_id, -32601, "Method not found")

    def _initialize(self, request_id, params):
        version = params.get("protocolVersion")
        if isinstance(version, str) and version in SUPPORTED_VERSIONS:
            self.protocol_version = version
        instructions = (
            "ground-control reads hardware metrics from the machine this server runs "
            "on. Prefer sample_hardware or diagnose_gpus over a single reading for "
            "utilisation questions, since GPU and network figures fluctuate."
        )
        if self.slurm_enabled:
            instructions += (
                " Slurm is available: this host is probably a login node, so a job's "
                "CPUs and GPUs are not visible locally -- use list_slurm_jobs, "
                "get_slurm_job, get_slurm_job_metrics or diagnose_gpus(job_id) for them."
            )
        return _result(request_id, {
            "protocolVersion": self.protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": "ground-control", "title": "Ground Control",
                           "version": _server_version()},
            "instructions": instructions,
        })

    def _tool_result(self, request_id, value=None, error_text=None):
        if error_text is not None:
            return _result(request_id, {
                "content": [{"type": "text", "text": error_text}],
                "isError": True,
            })
        value = _scrub(value)
        result = {"content": [{"type": "text", "text": json.dumps(value)}]}
        if self.protocol_version >= STRUCTURED_CONTENT_SINCE:
            result["structuredContent"] = value
        return _result(request_id, result)

    def _progress_reporter(self, params):
        meta = params.get("_meta")
        token = meta.get("progressToken") if isinstance(meta, dict) else None
        if not isinstance(token, (str, int)) or isinstance(token, bool):
            return lambda done, total, message=None: None

        def report(done, total, message=None):
            payload = {"progressToken": token, "progress": done, "total": total}
            if message:
                payload["message"] = message
            self.notify("notifications/progress", payload)
        return report

    def _call_tool(self, request_id, params):
        tool = TOOLS_BY_NAME.get(params.get("name"))
        if tool is None or tool not in self.tools():
            return _error(request_id, -32602, f"Unknown tool: {params.get('name')}")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        error = validate_arguments(tool.input_schema, arguments)
        if error:
            return self._tool_result(request_id, error_text=f"Invalid arguments: {error}")
        arguments = _with_defaults(tool.input_schema, arguments)
        try:
            value = tool.handler(self, arguments, self._progress_reporter(params))
        except ToolError as exc:
            return self._tool_result(request_id, error_text=str(exc))
        except Exception as exc:  # a failed reading must not terminate the server
            return self._tool_result(request_id, error_text=f"Could not collect metrics: {exc}")
        return self._tool_result(request_id, value)

    def _get_prompt(self, request_id, params):
        prompt = PROMPTS_BY_NAME.get(params.get("name"))
        if prompt is None:
            return _error(request_id, -32602, f"Unknown prompt: {params.get('name')}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(request_id, -32602, "Prompt arguments must be an object")
        known = {a["name"] for a in prompt["arguments"]}
        unknown = set(args) - known
        if unknown:
            return _error(request_id, -32602, f"Unknown prompt argument: {sorted(unknown)[0]}")
        job_id = args.get("job_id")
        if job_id is not None:
            if not isinstance(job_id, str) or not re.search(JOB_ID_PATTERN, job_id):
                return _error(request_id, -32602, "job_id must be a Slurm job id")
            if not self.slurm_enabled:
                return _error(request_id, -32602, "Slurm is not available on this host")
        return _result(request_id, {
            "description": prompt["description"],
            "messages": [{
                "role": "user",
                "content": {"type": "text",
                            "text": _prompt_text(prompt["name"], args, self.slurm_enabled)},
            }],
        })


def handle_request(message, provider):
    """Handle one message in a fresh session (kept for callers of the old API)."""
    return McpServer(provider, slurm_enabled=False).handle(message)


def _protocol_stdout():
    """Claim fd 1 for the protocol and point everything else at stderr.

    ``redirect_stdout`` alone only catches Python-level prints; a C library
    (NVML, a CUDA runtime) writing to fd 1 would still corrupt the stream.
    """
    try:
        sys.stdout.flush()
        protocol_fd = os.dup(1)
        os.dup2(2, 1)
        return os.fdopen(protocol_fd, "w", encoding="utf-8", buffering=1)
    except (OSError, ValueError, AttributeError):
        return sys.stdout


def serve(input_stream=None, output_stream=None, provider=None, slurm_enabled=None):
    """Process one JSON-RPC message per line until the client closes stdin."""
    input_stream = input_stream if input_stream is not None else sys.stdin
    output_stream = output_stream if output_stream is not None else _protocol_stdout()
    provider = provider if provider is not None else MetricsProvider()

    def write(payload):
        try:
            line = json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            line = json.dumps(_error(payload.get("id"), -32603, f"Unserializable response: {exc}"))
        output_stream.write(line + "\n")
        output_stream.flush()

    def notify(method, params):
        write({"jsonrpc": "2.0", "method": method, "params": params})

    server = McpServer(provider, slurm_enabled=slurm_enabled, notify=notify)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            for line in input_stream:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    write(_error(None, -32700, "Parse error"))
                    continue
                response = server.handle(message)
                if response is not None:
                    write(response)
    except BrokenPipeError:
        return

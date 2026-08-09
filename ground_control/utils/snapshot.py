"""
Point-in-time metrics snapshot for scripting and health checks.

``gc --once`` renders one sample outside the TUI, either as a human summary or
as JSON. The JSON shape is an explicit, stable contract -- it is built field by
field here rather than dumping collector output, so internal changes to
``SystemMetrics`` cannot silently alter the published schema.
"""
import platform
import socket
import time
from typing import Dict, List, Optional

from .alerts import CRIT, OK, WARN, evaluate_snapshot, merge_thresholds, worst

# Bumped when the JSON shape changes incompatibly. Consumers should check it.
SCHEMA_VERSION = 1

# Nagios-style process exit codes, used by --check.
EXIT_OK, EXIT_WARN, EXIT_CRIT = 0, 1, 2

_GB = 1024 ** 3

# Kept in sync with app.DEFAULT_DISK_IGNORE_PREFIXES, duplicated here so this
# module stays importable without pulling in Textual.
FALLBACK_DISK_IGNORE_PREFIXES = ["/boot/efi", "/snap"]


def mount_ignored(mountpoint: str, prefixes) -> bool:
    """
    True when a mountpoint matches any ignore prefix.

    Shared by the TUI and the snapshot so both hide the same mounts. Without it
    a snapshot reports every read-only squashfs under /snap as 100% full and
    critically out of space, which is true and useless.
    """
    mp = (mountpoint or "").rstrip("/") or "/"
    for prefix in prefixes or []:
        p = str(prefix).rstrip("/") or "/"
        if mp == p or mp.startswith(p + "/"):
            return True
    return False


def filter_disk_metrics(disk: Optional[Dict], prefixes) -> Optional[Dict]:
    """Return ``disk`` with ignored mountpoints removed from its ``disks`` list."""
    if not isinstance(disk, dict) or not prefixes:
        return disk
    kept = [d for d in (disk.get("disks") or [])
            if not mount_ignored(d.get("mountpoint", ""), prefixes)]
    filtered = dict(disk)
    filtered["disks"] = kept
    return filtered


def collect_metrics_by_type(system_metrics, include_gpu: bool = True) -> Dict:
    """
    Gather one reading of every metric family.

    Each collector is called defensively: a family that raises is reported as
    None rather than taking down the whole snapshot, which matters on machines
    where (say) GPU or sensor access is restricted.
    """
    def safe(fn, default=None):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - one bad family must not kill the rest
            return default

    metrics = {
        "cpu": safe(system_metrics.get_cpu_metrics, {}),
        "memory": safe(system_metrics.get_memory_metrics, {}),
        "disk": safe(system_metrics.get_disk_metrics, {}),
        "network": safe(system_metrics.get_network_metrics, {}),
        "temperature": safe(system_metrics.get_temperature_metrics, None),
    }
    metrics["gpu"] = safe(system_metrics.get_gpu_metrics, []) if include_gpu else []
    return metrics


def _cpu_section(cpu: Optional[Dict]) -> Optional[Dict]:
    if not isinstance(cpu, dict):
        return None
    percentages = list(cpu.get("cpu_percentages") or [])
    section = {
        "name": cpu.get("cpu_name"),
        "cores": len(percentages),
        "per_core_percent": [round(p, 1) for p in percentages],
        "percent": round(sum(percentages) / len(percentages), 1) if percentages else None,
    }
    freqs = cpu.get("cpu_freqs") or []
    try:
        current = [f.current for f in freqs if getattr(f, "current", None)]
        section["freq_mhz"] = round(sum(current) / len(current), 1) if current else None
        # Per-core frequencies, kept so a consumer rebuilding a live view (see
        # metrics_from_snapshot) can show the same detail as a local reading.
        section["per_core_freq_mhz"] = [round(float(f.current), 1) for f in freqs
                                        if getattr(f, "current", None)] or None
    except (AttributeError, TypeError):
        section["freq_mhz"] = None
        section["per_core_freq_mhz"] = None
    # Telemetry is a flat dict of numbers (load average, context switches,
    # cgroup quota and throttling); pass it through as-is.
    telemetry = cpu.get("cpu_telemetry")
    section["telemetry"] = telemetry if isinstance(telemetry, dict) else None
    section["memory_percent"] = cpu.get("mem_percent")
    return section


def _memory_section(memory: Optional[Dict]) -> Optional[Dict]:
    if not isinstance(memory, dict):
        return None
    info, swap = memory.get("memory_info"), memory.get("swap_info")
    section: Dict = {}
    if info is not None:
        section.update({
            "total_gb": round(getattr(info, "total", 0) / _GB, 2),
            "used_gb": round(getattr(info, "used", 0) / _GB, 2),
            "available_gb": round(getattr(info, "available", 0) / _GB, 2),
            "percent": getattr(info, "percent", None),
        })
    if swap is not None:
        section["swap"] = {
            "total_gb": round(getattr(swap, "total", 0) / _GB, 2),
            "used_gb": round(getattr(swap, "used", 0) / _GB, 2),
            "percent": getattr(swap, "percent", None),
        }
    # /proc/meminfo-derived detail and the commit ratio, so a rebuilt view keeps
    # the cached/buffers/committed breakdown. top_processes is deliberately not
    # published: the collector fills it with placeholder data.
    meminfo = memory.get("meminfo")
    if isinstance(meminfo, dict):
        section["meminfo"] = {k: v for k, v in meminfo.items()
                              if isinstance(v, (int, float))}
    if memory.get("commit_ratio") is not None:
        try:
            section["commit_ratio"] = round(float(memory["commit_ratio"]), 4)
        except (TypeError, ValueError):
            pass
    return section or None


def _disk_section(disk: Optional[Dict]) -> Optional[Dict]:
    if not isinstance(disk, dict):
        return None
    mounts = []
    for entry in disk.get("disks") or []:
        total = entry.get("disk_total") or 0
        used = entry.get("disk_used") or 0
        mounts.append({
            "mountpoint": entry.get("mountpoint"),
            "total_gb": round(total / _GB, 2),
            "used_gb": round(used / _GB, 2),
            "free_gb": round((total - used) / _GB, 2),
            "percent": round(used / total * 100.0, 1) if total else None,
            "read_mbps": round(entry.get("read_speed") or 0.0, 3),
            "write_mbps": round(entry.get("write_speed") or 0.0, 3),
        })
    return {
        "mounts": mounts,
        "read_mbps": round(disk.get("read_speed") or 0.0, 3),
        "write_mbps": round(disk.get("write_speed") or 0.0, 3),
    }


def _gpu_section(gpus) -> List[Dict]:
    section = []
    for index, gpu in enumerate(gpus or []):
        if not isinstance(gpu, dict):
            continue
        used, total = gpu.get("mem_used"), gpu.get("mem_total")
        # The collector reports -1 for "could not read" (e.g. some MiG devices);
        # surface that as null rather than a bogus negative number.
        used = None if used is None or used < 0 else round(float(used), 3)
        total = None if total is None or total < 0 else round(float(total), 3)
        util = gpu.get("gpu_util")
        util = None if util is None or util < 0 else util
        def rounded(key, digits=1):
            value = gpu.get(key)
            return None if value is None else round(float(value), digits)

        power, limit = gpu.get("power_w"), gpu.get("power_limit_w")
        section.append({
            "index": index,
            "name": gpu.get("gpu_name"),
            "utilization_percent": util,
            "memory_used_gb": used,
            "memory_total_gb": total,
            "memory_percent": (round(used / total * 100.0, 1)
                               if used is not None and total else None),
            "process_count": len(gpu.get("processes") or []),
            # Full per-process rows, so a rebuilt view can show the same process
            # list as a local reading. Values are already strings/ints from the
            # collector. Note the pids are only meaningful on the sampled host.
            "processes": [
                {
                    "pid": proc.get("pid"),
                    "name": proc.get("name"),
                    "gpu_memory": proc.get("gpu_memory"),
                    "username": proc.get("username"),
                    "command": proc.get("command"),
                    "script": proc.get("script"),
                    "cpu_percent": proc.get("cpu_percent"),
                    "memory": proc.get("memory"),
                }
                for proc in (gpu.get("processes") or [])
                if isinstance(proc, dict)
            ],
            "power_w": rounded("power_w"),
            "power_limit_w": rounded("power_limit_w"),
            "power_percent": (round(power / limit * 100.0, 1)
                              if power is not None and limit else None),
            "temperature_c": rounded("temperature_c"),
            "fan_percent": rounded("fan_percent", 0),
            "sm_clock_mhz": rounded("sm_clock_mhz", 0),
            "max_sm_clock_mhz": rounded("max_sm_clock_mhz", 0),
            "memory_bandwidth_percent": rounded("mem_bw_percent", 0),
            "performance_state": gpu.get("perf_state"),
            # Empty list means "not throttled OR not reported" -- see
            # SystemMetrics._throttle_reasons; do not read health from it alone.
            "throttle_reasons": list(gpu.get("throttle_reasons") or []),
            "throttle_severe": bool(gpu.get("throttle_severe")),
        })
    return section


def build_snapshot(system_metrics, thresholds: Optional[Dict] = None,
                   include_gpu: bool = True,
                   disk_ignore_prefixes: Optional[List[str]] = None) -> Dict:
    """
    Build the full JSON-serialisable snapshot, alerts included.

    ``system_metrics`` should already have been primed with an earlier sample so
    that rate-based figures (network, disk I/O) are meaningful -- see
    :func:`sample_twice`.

    ``disk_ignore_prefixes`` defaults to the same mounts the TUI hides; pass an
    empty list to report every mount.
    """
    thresholds = merge_thresholds(thresholds)
    if disk_ignore_prefixes is None:
        disk_ignore_prefixes = FALLBACK_DISK_IGNORE_PREFIXES
    metrics = collect_metrics_by_type(system_metrics, include_gpu=include_gpu)
    metrics["disk"] = filter_disk_metrics(metrics.get("disk"), disk_ignore_prefixes)
    _, breaches = evaluate_snapshot(metrics, thresholds)

    network = metrics.get("network") or {}
    temperatures = metrics.get("temperature")

    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "status": worst([b.level for b in breaches]),
        "alerts": [b.as_dict() for b in breaches],
        "metrics": {
            "cpu": _cpu_section(metrics.get("cpu")),
            "memory": _memory_section(metrics.get("memory")),
            "disk": _disk_section(metrics.get("disk")),
            "network": {
                "download_mbps": round(network.get("download_speed") or 0.0, 3),
                "upload_mbps": round(network.get("upload_speed") or 0.0, 3),
            },
            "gpu": _gpu_section(metrics.get("gpu")),
            "temperature_c": ({k: round(v, 1) for k, v in temperatures.items()}
                              if isinstance(temperatures, dict) else None),
        },
    }


class _Fields:
    """Minimal attribute holder standing in for a psutil named tuple.

    Widgets and the alert evaluator reach into memory readings by attribute
    (``memory_info.used``), so a snapshot rebuilt from JSON has to offer the
    same access pattern rather than a plain dict.
    """

    __slots__ = ("total", "used", "available", "free", "percent", "current")

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        set_fields = {n: getattr(self, n) for n in self.__slots__
                      if getattr(self, n) is not None}
        return f"_Fields({set_fields})"


def metrics_from_snapshot(snapshot: Optional[Dict]) -> Dict:
    """Rebuild a ``metrics_by_type`` mapping from a JSON snapshot.

    This is the inverse of :func:`build_snapshot`, used to render a *remote*
    host's reading -- a Slurm job sampled on its compute node -- through the
    same widgets and the same alert evaluator as local metrics.

    The published schema is lossy by design, so this is a faithful rebuild of
    what it carries, not of everything the collector saw. Any family missing
    from the payload comes back as None, which callers already treat as "that
    collector produced nothing this tick". Older remote versions that predate
    the detail fields therefore degrade (no GPU process rows, no meminfo
    breakdown) instead of raising.
    """
    if not isinstance(snapshot, dict):
        return {}
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        return {}

    result: Dict = {}

    # -- cpu ---------------------------------------------------------------- #
    cpu = metrics.get("cpu")
    if isinstance(cpu, dict):
        percentages = [float(p) for p in (cpu.get("per_core_percent") or [])]
        freqs = [_Fields(current=float(f))
                 for f in (cpu.get("per_core_freq_mhz") or [])]
        telemetry = cpu.get("telemetry")
        result["cpu"] = {
            "cpu_percentages": percentages,
            "cpu_freqs": freqs,
            "cpu_name": cpu.get("name"),
            "mem_percent": cpu.get("memory_percent") or 0.0,
            "cpu_telemetry": telemetry if isinstance(telemetry, dict) else {},
        }

    # -- memory ------------------------------------------------------------- #
    memory = metrics.get("memory")
    if isinstance(memory, dict):
        def to_bytes(value):
            return int(float(value) * _GB) if value is not None else 0

        swap = memory.get("swap") or {}
        entry: Dict = {
            "memory_info": _Fields(
                total=to_bytes(memory.get("total_gb")),
                used=to_bytes(memory.get("used_gb")),
                available=to_bytes(memory.get("available_gb")),
                free=to_bytes(memory.get("available_gb")),
                percent=memory.get("percent"),
            ),
            "swap_info": _Fields(
                total=to_bytes(swap.get("total_gb")),
                used=to_bytes(swap.get("used_gb")),
                percent=swap.get("percent"),
            ),
        }
        if isinstance(memory.get("meminfo"), dict):
            entry["meminfo"] = memory["meminfo"]
        if memory.get("commit_ratio") is not None:
            entry["commit_ratio"] = memory["commit_ratio"]
        result["memory"] = entry

    # -- disk --------------------------------------------------------------- #
    disk = metrics.get("disk")
    if isinstance(disk, dict):
        disks = []
        for mount in disk.get("mounts") or []:
            if not isinstance(mount, dict):
                continue
            total_gb, used_gb = mount.get("total_gb"), mount.get("used_gb")
            disks.append({
                "mountpoint": mount.get("mountpoint"),
                "disk_total": int(float(total_gb) * _GB) if total_gb else 0,
                "disk_used": int(float(used_gb) * _GB) if used_gb else 0,
                "read_speed": mount.get("read_mbps") or 0.0,
                "write_speed": mount.get("write_mbps") or 0.0,
            })
        result["disk"] = {
            "disks": disks,
            "read_speed": disk.get("read_mbps") or 0.0,
            "write_speed": disk.get("write_mbps") or 0.0,
        }

    # -- network ------------------------------------------------------------ #
    network = metrics.get("network")
    if isinstance(network, dict):
        result["network"] = {
            "download_speed": network.get("download_mbps") or 0.0,
            "upload_speed": network.get("upload_mbps") or 0.0,
        }

    # -- gpu ---------------------------------------------------------------- #
    gpus = metrics.get("gpu")
    if isinstance(gpus, list):
        rebuilt = []
        for gpu in gpus:
            if not isinstance(gpu, dict):
                continue
            # The widget treats a negative utilisation as "unavailable"; null in
            # the payload means the same thing, so map it back to the sentinel.
            util = gpu.get("utilization_percent")
            rebuilt.append({
                "gpu_name": gpu.get("name") or f"GPU {gpu.get('index')}",
                "gpu_util": -1.0 if util is None else float(util),
                "mem_used": gpu.get("memory_used_gb") or 0.0,
                "mem_total": gpu.get("memory_total_gb") or 0.0,
                "processes": list(gpu.get("processes") or []),
                "power_w": gpu.get("power_w"),
                "power_limit_w": gpu.get("power_limit_w"),
                "temperature_c": gpu.get("temperature_c"),
                "fan_percent": gpu.get("fan_percent"),
                "sm_clock_mhz": gpu.get("sm_clock_mhz"),
                "max_sm_clock_mhz": gpu.get("max_sm_clock_mhz"),
                "mem_bw_percent": gpu.get("memory_bandwidth_percent"),
                "perf_state": gpu.get("performance_state"),
                "throttle_reasons": list(gpu.get("throttle_reasons") or []),
                "throttle_severe": bool(gpu.get("throttle_severe")),
            })
        result["gpu"] = rebuilt

    # -- temperature -------------------------------------------------------- #
    temperatures = metrics.get("temperature_c")
    if isinstance(temperatures, dict):
        result["temperature"] = {k: float(v) for k, v in temperatures.items()
                                 if isinstance(v, (int, float))}

    return result


def sample_twice(system_metrics, interval: float = 0.5,
                 include_gpu: bool = True) -> None:
    """
    Prime the rate counters.

    Network and disk throughput are deltas against the previous reading, so a
    cold collector reports 0. Taking a throwaway sample and sleeping makes the
    real one meaningful. Interval is clamped to a sane floor.
    """
    collect_metrics_by_type(system_metrics, include_gpu=include_gpu)
    time.sleep(max(interval, 0.05))


def iter_snapshots(system_metrics, thresholds: Optional[Dict] = None,
                   include_gpu: bool = True,
                   disk_ignore_prefixes: Optional[List[str]] = None,
                   interval: float = 1.0,
                   max_seconds: Optional[float] = None):
    """Yield one snapshot every ``interval`` seconds, indefinitely.

    This is what ``gc --stream`` runs, and the reason job monitoring is cheap:
    the collector stays resident on the machine being measured and keeps
    emitting, instead of paying process (and, over srun, job-step) startup for
    every single sample.

    The counters are primed once up front, so the *first* yielded snapshot
    already carries meaningful rates -- unlike a cold one-shot run. Sleeps are
    measured from the end of the previous collection, so a slow sampler reduces
    its own frequency rather than falling permanently behind.

    ``max_seconds`` bounds the total lifetime. It exists because this process
    typically runs inside somebody else's Slurm allocation: if the reader ever
    dies without its signal reaching us, the stream must still expire on its own
    rather than sitting in the job's cgroup forever.
    """
    interval = max(float(interval), 0.1)
    started = time.monotonic()
    # Prime the rate counters; short gap so the first real sample is not late.
    sample_twice(system_metrics, interval=min(interval, 0.5), include_gpu=include_gpu)
    while True:
        collected_at = time.monotonic()
        yield build_snapshot(system_metrics, thresholds=thresholds,
                             include_gpu=include_gpu,
                             disk_ignore_prefixes=disk_ignore_prefixes)
        if max_seconds is not None and (time.monotonic() - started) >= max_seconds:
            return
        time.sleep(max(interval - (time.monotonic() - collected_at), 0.05))


def exit_code_for(status: str) -> int:
    """Map an overall status to a Nagios-style exit code."""
    return {OK: EXIT_OK, WARN: EXIT_WARN, CRIT: EXIT_CRIT}.get(status, EXIT_OK)


def render_text(snapshot: Dict) -> str:
    """Render a snapshot as a compact human-readable report."""
    metrics = snapshot.get("metrics", {})
    marker = {OK: "  ", WARN: "! ", CRIT: "!!"}
    lines = [f"{snapshot.get('host')}  {snapshot.get('timestamp_iso')}  "
             f"[{snapshot.get('status', OK).upper()}]"]

    cpu = metrics.get("cpu")
    if cpu and cpu.get("percent") is not None:
        lines.append(f"CPU      {cpu['percent']:5.1f}%  ({cpu.get('cores')} cores)"
                     f"  {cpu.get('name') or ''}".rstrip())

    memory = metrics.get("memory")
    if memory and memory.get("percent") is not None:
        line = (f"Memory   {memory['percent']:5.1f}%  "
                f"{memory.get('used_gb')}/{memory.get('total_gb')} GB")
        swap = memory.get("swap") or {}
        if swap.get("total_gb"):
            line += f"   swap {swap.get('percent')}%"
        lines.append(line)

    disk = metrics.get("disk") or {}
    for mount in disk.get("mounts") or []:
        if mount.get("percent") is None:
            continue
        lines.append(f"Disk     {mount['percent']:5.1f}%  "
                     f"{mount.get('free_gb')} GB free   {mount.get('mountpoint')}")

    network = metrics.get("network") or {}
    lines.append(f"Network  {network.get('download_mbps', 0):.2f} MB/s down  "
                 f"{network.get('upload_mbps', 0):.2f} MB/s up")

    for gpu in metrics.get("gpu") or []:
        util = gpu.get("utilization_percent")
        util_text = f"{util:5.1f}%" if util is not None else "  UNAV"
        line = f"GPU {gpu.get('index')}    {util_text}"
        if gpu.get("memory_total_gb"):
            line += (f"  {gpu.get('memory_used_gb')}/{gpu.get('memory_total_gb')} GB"
                     f"  {gpu.get('name') or ''}")
        lines.append(line.rstrip())
        # Second line only when the card actually reports telemetry, so this
        # stays quiet on hardware that exposes nothing.
        detail = []
        if gpu.get("power_w") is not None:
            detail.append(f"{gpu['power_w']:.0f}W"
                          + (f"/{gpu['power_limit_w']:.0f}W ({gpu['power_percent']:.0f}%)"
                             if gpu.get("power_limit_w") else ""))
        if gpu.get("temperature_c") is not None:
            detail.append(f"{gpu['temperature_c']:.0f}C")
        if gpu.get("sm_clock_mhz") is not None:
            detail.append(f"{gpu['sm_clock_mhz']:.0f}"
                          + (f"/{gpu['max_sm_clock_mhz']:.0f}" if gpu.get("max_sm_clock_mhz") else "")
                          + " MHz")
        if gpu.get("memory_bandwidth_percent") is not None:
            detail.append(f"BW {gpu['memory_bandwidth_percent']:.0f}%")
        if gpu.get("throttle_reasons"):
            marker = "!!" if gpu.get("throttle_severe") else "!"
            detail.append(f"{marker} throttled: {', '.join(gpu['throttle_reasons'])}")
        if detail:
            lines.append("         " + "  ".join(detail))

    temperatures = metrics.get("temperature_c") or {}
    for sensor, value in temperatures.items():
        lines.append(f"Temp     {value:5.1f}C  {sensor}")

    alerts = snapshot.get("alerts") or []
    if alerts:
        lines.append("")
        lines.append("Alerts:")
        for alert in alerts:
            scope = f"{alert.get('scope')} " if alert.get("scope") else ""
            lines.append(f"  {marker.get(alert.get('level'), '  ')} "
                         f"{alert.get('level', '').upper():4} {scope}"
                         f"{alert.get('value')}{alert.get('unit')} "
                         f"(threshold {alert.get('threshold')})")
    return "\n".join(lines)

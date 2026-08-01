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
    except (AttributeError, TypeError):
        section["freq_mhz"] = None
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
        section.append({
            "index": index,
            "name": gpu.get("gpu_name"),
            "utilization_percent": util,
            "memory_used_gb": used,
            "memory_total_gb": total,
            "memory_percent": (round(used / total * 100.0, 1)
                               if used is not None and total else None),
            "process_count": len(gpu.get("processes") or []),
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

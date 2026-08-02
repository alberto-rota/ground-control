"""
Threshold evaluation for system metrics.

This module is deliberately free of Textual and psutil imports: it turns a
metrics snapshot into alert levels using plain data, so it can be unit-tested
and reused by the TUI, by ``gc --once`` and by anything added later.

A *threshold spec* is ``{"warn": float|None, "crit": float|None,
"enabled": bool}``. Direction is a property of the metric, not of the user's
configuration -- "CPU above 90%" and "disk free below 2 GB" are both bad, and
users should not have to encode that. ``METRIC_DIRECTIONS`` owns it.
"""
from typing import Dict, List, Optional, Tuple

# Alert levels, ordered from least to most severe. Compare with LEVEL_ORDER.
OK = "ok"
WARN = "warn"
CRIT = "crit"

LEVEL_ORDER = {OK: 0, WARN: 1, CRIT: 2}

# "above": larger values are worse (CPU%, temperature).
# "below": smaller values are worse (free disk space).
METRIC_DIRECTIONS: Dict[str, str] = {
    "cpu_percent": "above",
    "memory_percent": "above",
    "swap_percent": "above",
    "disk_percent": "above",
    "disk_free_gb": "below",
    "temperature_c": "above",
    "gpu_util_percent": "above",
    "gpu_memory_percent": "above",
    "gpu_temperature_c": "above",
    "net_download_mbps": "above",
    "net_upload_mbps": "above",
}

# Shipped defaults. Two deliberate choices:
#   * gpu_util_percent is disabled -- on a GPU box a pegged GPU is the goal,
#     not an incident. It exists so users who want it can flip enabled.
#   * network rates are disabled because a sensible ceiling is entirely
#     site-specific (a 1 Gb link and a 100 Gb link share no useful default).
DEFAULT_THRESHOLDS: Dict[str, Dict] = {
    "cpu_percent":        {"warn": 85.0,  "crit": 95.0,  "enabled": True},
    "memory_percent":     {"warn": 85.0,  "crit": 95.0,  "enabled": True},
    "swap_percent":       {"warn": 50.0,  "crit": 80.0,  "enabled": True},
    "disk_percent":       {"warn": 85.0,  "crit": 95.0,  "enabled": True},
    "disk_free_gb":       {"warn": 10.0,  "crit": 2.0,   "enabled": True},
    "temperature_c":      {"warn": 80.0,  "crit": 90.0,  "enabled": True},
    "gpu_util_percent":   {"warn": 95.0,  "crit": 99.0,  "enabled": False},
    "gpu_memory_percent": {"warn": 90.0,  "crit": 98.0,  "enabled": True},
    "gpu_temperature_c":  {"warn": 80.0,  "crit": 90.0,  "enabled": True},
    "net_download_mbps":  {"warn": None,  "crit": None,  "enabled": False},
    "net_upload_mbps":    {"warn": None,  "crit": None,  "enabled": False},
}


def worst(levels) -> str:
    """Return the most severe level in ``levels`` (OK when empty)."""
    return max(levels, key=lambda lv: LEVEL_ORDER.get(lv, 0), default=OK)


def merge_thresholds(configured: Optional[Dict]) -> Dict[str, Dict]:
    """
    Overlay user thresholds onto the defaults.

    Unknown metric names and malformed entries are ignored rather than raising,
    so a hand-edited config can never stop the app from starting.
    """
    merged = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}
    if not isinstance(configured, dict):
        return merged
    for metric, spec in configured.items():
        if metric not in merged or not isinstance(spec, dict):
            continue
        for field in ("warn", "crit"):
            if field in spec:
                value = spec[field]
                if value is None:
                    merged[metric][field] = None
                else:
                    try:
                        merged[metric][field] = float(value)
                    except (TypeError, ValueError):
                        pass
        if "enabled" in spec:
            merged[metric]["enabled"] = bool(spec["enabled"])
    return merged


def evaluate(metric: str, value, thresholds: Dict[str, Dict]) -> str:
    """
    Return the alert level for a single reading.

    Returns OK for a disabled metric, an unknown metric, a non-numeric value,
    or the sentinel -1 that the GPU collector uses for "unavailable" -- an
    unreadable sensor is not an alert.
    """
    spec = thresholds.get(metric)
    if not spec or not spec.get("enabled", False):
        return OK
    try:
        value = float(value)
    except (TypeError, ValueError):
        return OK
    if value < 0:
        return OK

    direction = METRIC_DIRECTIONS.get(metric, "above")
    crit, warn = spec.get("crit"), spec.get("warn")

    if direction == "above":
        if crit is not None and value >= crit:
            return CRIT
        if warn is not None and value >= warn:
            return WARN
    else:
        if crit is not None and value <= crit:
            return CRIT
        if warn is not None and value <= warn:
            return WARN
    return OK


class Breach:
    """One metric over its threshold, with enough context to render a message."""

    __slots__ = ("metric", "level", "value", "unit", "scope", "limit")

    def __init__(self, metric: str, level: str, value: float, unit: str,
                 scope: str = "", limit: Optional[float] = None):
        self.metric = metric
        self.level = level
        self.value = value
        self.unit = unit
        self.scope = scope
        self.limit = limit

    def label(self) -> str:
        """Short human string, e.g. ``/var 96% disk`` or ``CPU 97%``."""
        head = f"{self.scope} " if self.scope else ""
        value = f"{self.value:.0f}" if self.unit in ("%", "°C") else f"{self.value:.1f}"
        return f"{head}{value}{self.unit}"

    def as_dict(self) -> Dict:
        return {
            "metric": self.metric,
            "level": self.level,
            "value": round(self.value, 2),
            "unit": self.unit,
            "scope": self.scope,
            "threshold": self.limit,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Breach {self.metric} {self.scope} {self.level} {self.value}>"


def _check(metric: str, value, thresholds: Dict, unit: str, scope: str,
           out: List[Breach]) -> str:
    """Evaluate one reading, appending a Breach when it is not OK."""
    level = evaluate(metric, value, thresholds)
    if level != OK:
        spec = thresholds.get(metric, {})
        out.append(Breach(metric, level, float(value), unit, scope,
                          spec.get("crit") if level == CRIT else spec.get("warn")))
    return level


def evaluate_snapshot(metrics_by_type: Dict, thresholds: Dict[str, Dict]
                      ) -> Tuple[Dict, List[Breach]]:
    """
    Evaluate every metric in a snapshot.

    Args:
        metrics_by_type: Mapping of metric type -> collector output, using the
            same keys the app already dispatches on ("cpu", "memory", "disk",
            "network", "gpu", "temperature").
        thresholds: Merged threshold specs.

    Returns:
        ``(targets, breaches)`` where ``targets`` maps a widget-identifying key
        to that widget's worst level. Keys are ``("cpu", None)``,
        ``("memory", None)``, ``("network", None)``, ``("temperature", None)``,
        ``("disk", mountpoint)`` and ``("gpu", index)`` -- matching how the app
        identifies its panels.
    """
    targets: Dict[Tuple[str, Optional[object]], str] = {}
    breaches: List[Breach] = []

    cpu = metrics_by_type.get("cpu")
    if isinstance(cpu, dict):
        percentages = cpu.get("cpu_percentages") or []
        levels = []
        if percentages:
            # Aggregate, not per-core: one busy core on a 128-core box is normal.
            avg = sum(percentages) / len(percentages)
            levels.append(_check("cpu_percent", avg, thresholds, "%", "CPU", breaches))
        targets[("cpu", None)] = worst(levels)

    memory = metrics_by_type.get("memory")
    if isinstance(memory, dict):
        levels = []
        info, swap = memory.get("memory_info"), memory.get("swap_info")
        if info is not None and getattr(info, "percent", None) is not None:
            levels.append(_check("memory_percent", info.percent, thresholds,
                                 "%", "RAM", breaches))
        if swap is not None and getattr(swap, "total", 0):
            levels.append(_check("swap_percent", swap.percent, thresholds,
                                 "%", "swap", breaches))
        targets[("memory", None)] = worst(levels)

    disk = metrics_by_type.get("disk")
    if isinstance(disk, dict):
        for entry in disk.get("disks") or []:
            mount = entry.get("mountpoint", "?")
            total, used = entry.get("disk_total") or 0, entry.get("disk_used") or 0
            levels = []
            if total > 0:
                levels.append(_check("disk_percent", used / total * 100.0,
                                     thresholds, "%", mount, breaches))
                levels.append(_check("disk_free_gb", (total - used) / (1024 ** 3),
                                     thresholds, "GB free", mount, breaches))
            targets[("disk", mount)] = worst(levels)

    network = metrics_by_type.get("network")
    if isinstance(network, dict):
        levels = [
            _check("net_download_mbps", network.get("download_speed"), thresholds,
                   " MB/s down", "net", breaches),
            _check("net_upload_mbps", network.get("upload_speed"), thresholds,
                   " MB/s up", "net", breaches),
        ]
        targets[("network", None)] = worst(levels)

    gpus = metrics_by_type.get("gpu")
    if isinstance(gpus, list):
        for index, gpu in enumerate(gpus):
            if not isinstance(gpu, dict):
                continue
            name = gpu.get("gpu_name") or f"GPU {index}"
            levels = [_check("gpu_util_percent", gpu.get("gpu_util"), thresholds,
                             "%", name, breaches)]
            used, total = gpu.get("mem_used"), gpu.get("mem_total")
            try:
                if used is not None and total is not None and float(total) > 0 \
                        and float(used) >= 0:
                    levels.append(_check("gpu_memory_percent",
                                         float(used) / float(total) * 100.0,
                                         thresholds, "% vram", name, breaches))
            except (TypeError, ValueError):
                pass
            # The device's own thermal sensor, so a hot card lights up its own
            # panel instead of only the shared temperature panel.
            if gpu.get("temperature_c") is not None:
                levels.append(_check("gpu_temperature_c", gpu["temperature_c"],
                                     thresholds, "°C", name, breaches))
            targets[("gpu", index)] = worst(levels)

    temps = metrics_by_type.get("temperature")
    if isinstance(temps, dict):
        levels = []
        for sensor, reading in temps.items():
            metric = ("gpu_temperature_c" if "gpu" in str(sensor).lower()
                      else "temperature_c")
            levels.append(_check(metric, reading, thresholds, "°C", str(sensor),
                                 breaches))
        targets[("temperature", None)] = worst(levels)

    return targets, breaches

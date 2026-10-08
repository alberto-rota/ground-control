"""
Interpret GPU readings: what a snapshot's numbers *mean*, not just what they are.

Like ``alerts.py`` this is pure and import-light -- it works on the snapshot's
GPU dict (``snapshot._gpu_section`` shape), or on per-field means from a sampled
window, so it is shared by the MCP server and unit-testable without a GPU.

The central point is that NVML's ``utilization_percent`` only means "a kernel
was resident", not that the SMs were doing work. Power as a percent of its limit
and memory-bandwidth percent are what separate a busy card from an
input-starved training loop that merely *looks* busy.

A missing field (``None``) skips a rule rather than triggering it: consumer
cards report no power limit, MiG devices no utilization, and absence of a
reading is not evidence of anything.
"""
from typing import Dict, List, Optional

INFO = "info"
WARN = "warn"
CRIT = "crit"

# Utilization at or above this counts as "the GPU claims to be busy".
BUSY_UTIL_PERCENT = 80.0
# Below these, a "busy" GPU is not actually being worked: input-starved.
STARVED_POWER_PERCENT = 40.0
STARVED_BANDWIDTH_PERCENT = 15.0
# Power at or above this alongside high util reads as genuinely busy.
HEALTHY_POWER_PERCENT = 60.0
VRAM_FULL_PERCENT = 95.0
IDLE_UTIL_PERCENT = 2.0
IDLE_HELD_MEMORY_PERCENT = 10.0
HOT_TEMPERATURE_C = 85.0
CRITICAL_TEMPERATURE_C = 92.0


def _num(gpu: Dict, key: str) -> Optional[float]:
    value = gpu.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _finding(gpu: Dict, code: str, severity: str, message: str, **evidence) -> Dict:
    return {
        "gpu": gpu.get("index"),
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": {k: v for k, v in evidence.items() if v is not None},
    }


def diagnose_gpu(gpu: Dict) -> List[Dict]:
    """Return findings for one GPU, most actionable first."""
    if not isinstance(gpu, dict):
        return []
    findings = []
    util = _num(gpu, "utilization_percent")
    power = _num(gpu, "power_percent")
    bandwidth = _num(gpu, "memory_bandwidth_percent")
    memory = _num(gpu, "memory_percent")
    temperature = _num(gpu, "temperature_c")
    processes = gpu.get("process_count")
    if processes is None and isinstance(gpu.get("processes"), list):
        processes = len(gpu["processes"])

    if (util is not None and util >= BUSY_UTIL_PERCENT and power is not None
            and power < STARVED_POWER_PERCENT and bandwidth is not None
            and bandwidth < STARVED_BANDWIDTH_PERCENT):
        findings.append(_finding(
            gpu, "input_starved", WARN,
            "Utilization looks high but power draw and memory bandwidth are low: "
            "kernels are resident but the SMs are mostly waiting. Typical of a "
            "data-loading, host-to-device copy or CPU-bound bottleneck.",
            utilization_percent=util, power_percent=power,
            memory_bandwidth_percent=bandwidth))

    reasons = gpu.get("throttle_reasons") or []
    if gpu.get("throttle_severe"):
        findings.append(_finding(
            gpu, "throttled", WARN,
            "Clocks are being reduced by hardware distress (thermal or power "
            "brake); throughput is being lost right now.",
            throttle_reasons=list(reasons), temperature_c=temperature))
    elif reasons:
        findings.append(_finding(
            gpu, "clock_governed", INFO,
            "Clocks are governed by normal policy (e.g. power cap); this is "
            "expected under sustained load, not a fault.",
            throttle_reasons=list(reasons)))

    if memory is not None and memory >= VRAM_FULL_PERCENT:
        findings.append(_finding(
            gpu, "vram_nearly_full", WARN,
            "GPU memory is nearly exhausted; a larger batch or another process "
            "will fail with out-of-memory.",
            memory_percent=memory))

    if (util is not None and util <= IDLE_UTIL_PERCENT and memory is not None
            and memory >= IDLE_HELD_MEMORY_PERCENT and processes):
        findings.append(_finding(
            gpu, "idle_holding_memory", INFO,
            "The GPU is idle but processes are holding memory on it -- a stalled "
            "job, a notebook kernel, or a process waiting on something else.",
            utilization_percent=util, memory_percent=memory,
            process_count=processes))

    if temperature is not None and temperature >= HOT_TEMPERATURE_C:
        findings.append(_finding(
            gpu, "hot", CRIT if temperature >= CRITICAL_TEMPERATURE_C else WARN,
            "The GPU is running hot; expect thermal throttling.",
            temperature_c=temperature))

    if util is not None and util >= BUSY_UTIL_PERCENT and power is None:
        # Without a power limit (consumer cards, Grace-Blackwell) the starved and
        # busy rules cannot run. Say so, or an empty list reads as "healthy".
        findings.append(_finding(
            gpu, "limited_telemetry", INFO,
            "Utilization is high but this GPU reports no power limit, so a busy "
            "GPU cannot be told apart from an input-starved one. Check the "
            "process's own throughput or CPU load instead.",
            utilization_percent=util, power_w=_num(gpu, "power_w")))

    if (util is not None and util >= BUSY_UTIL_PERCENT and power is not None
            and power >= HEALTHY_POWER_PERCENT
            and not any(f["code"] in ("throttled", "hot") for f in findings)):
        findings.append(_finding(
            gpu, "healthy_busy", INFO,
            "High utilization backed by high power draw: the GPU is genuinely "
            "doing work.",
            utilization_percent=util, power_percent=power,
            memory_bandwidth_percent=bandwidth))

    return findings


def diagnose_gpus(gpus) -> List[Dict]:
    """Findings for every GPU in a snapshot's ``metrics.gpu`` list."""
    findings = []
    for gpu in gpus or []:
        findings.extend(diagnose_gpu(gpu))
    return findings

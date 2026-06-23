"""Slurm integration helpers for Ground Control.

Everything here is best-effort and defensive: Slurm may be absent, the
controller may be slow or unreachable, and individual commands may fail or
time out. Nothing in this module should ever raise to the caller — failures
degrade to ``None`` / empty results so the TUI keeps running.

The parsing functions (``parse_squeue``, ``parse_scontrol_job``,
``parse_sstat``) are pure and operate on raw command output, so they can be
unit-tested without a live Slurm cluster.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Dict, List, Optional

logger = logging.getLogger("ground-control.slurm")

# Default per-command timeout. slurmctld can be slow under load; keep this
# short so a hung controller never freezes the UI thread's executor slot.
_CMD_TIMEOUT = 6

# squeue output format. Order matters: it maps positionally onto SQUEUE_FIELDS.
# Specifiers chosen to be supported across Slurm versions.
SQUEUE_FORMAT = "%i|%j|%T|%P|%D|%C|%N|%M|%l|%r|%u"
SQUEUE_FIELDS = [
    "jobid", "name", "state", "partition", "nodes", "cpus",
    "nodelist", "elapsed", "timelimit", "reason", "user",
]


def slurm_available() -> bool:
    """True if the ``squeue`` client is on PATH."""
    return shutil.which("squeue") is not None


def _run(cmd: List[str], timeout: int = _CMD_TIMEOUT) -> Optional[str]:
    """Run a command, returning stdout on success or None on any failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as err:
        logger.info("slurm command failed (%s): %s", " ".join(cmd[:2]), err)
        return None
    except Exception as err:  # noqa: BLE001
        logger.info("slurm command error (%s): %s", " ".join(cmd[:2]), err)
        return None
    if proc.returncode != 0:
        logger.info("slurm command rc=%d (%s): %s",
                    proc.returncode, " ".join(cmd[:2]), (proc.stderr or "").strip()[:200])
        return None
    return proc.stdout


# --------------------------------------------------------------------------- #
# Pure parsers
# --------------------------------------------------------------------------- #
def parse_squeue(output: str, fields: List[str] = SQUEUE_FIELDS) -> List[Dict[str, str]]:
    """Parse delimited ``squeue`` output into a list of dicts.

    Each non-empty line is split on ``|`` and zipped with ``fields``. Lines
    with the wrong number of columns are skipped defensively.
    """
    jobs: List[Dict[str, str]] = []
    if not output:
        return jobs
    for line in output.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < len(fields):
            # Pad missing trailing columns rather than dropping the row.
            parts = parts + [""] * (len(fields) - len(parts))
        row = {f: parts[i].strip() for i, f in enumerate(fields)}
        if not row.get("jobid"):
            continue
        jobs.append(row)
    return jobs


def parse_scontrol_job(output: str) -> Dict[str, str]:
    """Parse ``scontrol show job <id>`` output into a flat key=value dict.

    scontrol emits whitespace-separated ``Key=Value`` tokens across several
    lines. Values themselves rarely contain spaces for the fields we use; the
    few that do (e.g. ``Command=``) are not relied upon here.
    """
    info: Dict[str, str] = {}
    if not output:
        return info
    for token in output.split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        if key and key not in info:
            info[key] = value
    return info


def _gpus_from_tres(tres: str) -> Optional[int]:
    """Extract the GPU count from a TRES string like 'cpu=4,mem=16G,gres/gpu=2'."""
    if not tres:
        return None
    total = 0
    found = False
    for item in tres.split(","):
        item = item.strip()
        # Match 'gres/gpu=N', 'gres/gpu:a100=N', or 'gpu=N'
        key, _, val = item.partition("=")
        key = key.lower()
        if key.startswith("gres/gpu") or key == "gpu":
            try:
                total += int(val)
                found = True
            except ValueError:
                continue
    return total if found else None


def job_alloc_from_scontrol(info: Dict[str, str]) -> Dict[str, Optional[str]]:
    """Derive normalized allocation fields from a parsed scontrol dict."""
    tres = info.get("AllocTRES") or info.get("TRES") or info.get("ReqTRES") or ""
    gpus = _gpus_from_tres(tres)
    # Memory: prefer explicit mem in TRES, else scontrol's mem fields.
    mem = None
    for item in tres.split(","):
        k, _, v = item.partition("=")
        if k.strip().lower() == "mem":
            mem = v.strip()
            break
    if mem is None:
        mem = info.get("MinMemoryNode") or info.get("Mem") or info.get("mem")
    return {
        "state": info.get("JobState"),
        "elapsed": info.get("RunTime"),
        "timelimit": info.get("TimeLimit"),
        "nodes": info.get("NumNodes"),
        "cpus": info.get("NumCPUs"),
        "nodelist": info.get("NodeList"),
        "gpus": str(gpus) if gpus is not None else None,
        "mem": mem,
        "reason": info.get("Reason"),
        "partition": info.get("Partition"),
    }


def parse_sstat(output: str) -> Optional[Dict[str, str]]:
    """Parse ``sstat -P -o ...`` output. Returns the first data row as a dict.

    Expects a header line followed by data lines (``-P`` => '|' separated).
    Returns None if there is no usable data.
    """
    if not output:
        return None
    lines = [l for l in output.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    header = [h.strip() for h in lines[0].split("|")]
    for data_line in lines[1:]:
        values = [v.strip() for v in data_line.split("|")]
        if len(values) < len(header):
            continue
        row = dict(zip(header, values))
        # Skip pseudo-steps with no real measurement.
        if any(row.get(k) for k in ("MaxRSS", "AveCPU", "AveRSS")):
            return row
    # Fall back to first row even if empty-ish.
    values = [v.strip() for v in lines[1].split("|")]
    return dict(zip(header, values)) if values else None


# --------------------------------------------------------------------------- #
# Live queries
# --------------------------------------------------------------------------- #
def get_user_jobs(user: Optional[str] = None) -> List[Dict[str, str]]:
    """Return the current user's jobs (for the selection picker)."""
    if not slurm_available():
        return []
    cmd = ["squeue", "--noheader", "-o", SQUEUE_FORMAT]
    if user:
        cmd += ["-u", user]
    else:
        cmd += ["--me"]
    out = _run(cmd)
    if out is None and not user:
        # Older squeue lacks --me; fall back to $USER.
        env_user = os.environ.get("USER") or os.environ.get("LOGNAME")
        if env_user:
            out = _run(["squeue", "--noheader", "-o", SQUEUE_FORMAT, "-u", env_user])
    return parse_squeue(out or "")


def get_jobs_by_id(jobids: List[str]) -> Dict[str, Dict[str, str]]:
    """Return a {jobid: squeue-row} map for the given job ids in one call."""
    if not jobids or not slurm_available():
        return {}
    out = _run(["squeue", "--noheader", "-o", SQUEUE_FORMAT, "-j", ",".join(jobids)])
    rows = parse_squeue(out or "")
    return {r["jobid"]: r for r in rows}


def get_job_detail(jobid: str) -> Dict[str, Optional[str]]:
    """Return normalized allocation detail for a job via scontrol."""
    out = _run(["scontrol", "show", "job", jobid])
    if not out:
        return {}
    return job_alloc_from_scontrol(parse_scontrol_job(out))


def get_job_live_stats(jobid: str) -> Optional[Dict[str, str]]:
    """Return live resource usage for a running job via sstat (best-effort)."""
    out = _run(["sstat", "-a", "-P", "-j", jobid,
                "-o", "JobID,AveCPU,MaxRSS,AveRSS,NTasks"])
    if not out:
        return None
    return parse_sstat(out)


class SlurmMonitor:
    """Holds the set of monitored job ids and provides throttled polling.

    ``poll()`` is safe to call every refresh tick: it returns cached data and
    only hits Slurm again once ``min_interval`` seconds have elapsed. This keeps
    subprocess pressure on slurmctld bounded regardless of the UI refresh rate.
    """

    def __init__(self, jobids: Optional[List[str]] = None, min_interval: float = 4.0):
        self.jobids: List[str] = list(jobids or [])
        self.min_interval = min_interval
        self._cache: List[Dict] = []
        self._last_poll: float = 0.0

    def set_jobs(self, jobids: List[str]) -> None:
        self.jobids = list(jobids)
        self._last_poll = 0.0  # force a refresh on next poll
        self._cache = []

    def cached(self) -> List[Dict]:
        """Return the last polled result without contacting Slurm."""
        return self._cache

    def poll(self, force: bool = False) -> List[Dict]:
        """Return monitored-job info, refreshing from Slurm at most every
        ``min_interval`` seconds."""
        now = time.time()
        if not force and self._cache and (now - self._last_poll) < self.min_interval:
            return self._cache
        self._last_poll = now
        if not self.jobids:
            self._cache = []
            return self._cache
        self._cache = self._collect()
        return self._cache

    def _collect(self) -> List[Dict]:
        squeue_map = get_jobs_by_id(self.jobids)
        results: List[Dict] = []
        for jid in self.jobids:
            row = dict(squeue_map.get(jid, {}))
            info: Dict = {
                "jobid": jid,
                "name": row.get("name", ""),
                "state": row.get("state", "UNKNOWN"),
                "partition": row.get("partition", ""),
                "nodes": row.get("nodes", ""),
                "cpus": row.get("cpus", ""),
                "nodelist": row.get("nodelist", ""),
                "elapsed": row.get("elapsed", ""),
                "timelimit": row.get("timelimit", ""),
                "reason": row.get("reason", ""),
                "gpus": "",
                "mem": "",
                "live_cpu": "",
                "live_rss": "",
            }
            # Enrich with scontrol allocation detail (gpus, mem, fill gaps).
            detail = get_job_detail(jid)
            for key in ("gpus", "mem", "nodelist", "cpus", "nodes",
                        "elapsed", "timelimit", "partition"):
                val = detail.get(key)
                if val and not info.get(key):
                    info[key] = val
            if detail.get("state") and (not info["state"] or info["state"] == "UNKNOWN"):
                info["state"] = detail["state"]
            if detail.get("reason") and not info.get("reason"):
                info["reason"] = detail["reason"]
            # Live usage only meaningful for running jobs.
            if (info.get("state") or "").upper().startswith("RUN"):
                stats = get_job_live_stats(jid)
                if stats:
                    info["live_cpu"] = stats.get("AveCPU", "") or ""
                    info["live_rss"] = stats.get("MaxRSS", "") or ""
            results.append(info)
        return results

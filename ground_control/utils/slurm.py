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

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("ground-control.slurm")

# Default per-command timeout. slurmctld can be slow under load; keep this
# short so a hung controller never freezes the UI thread's executor slot.
_CMD_TIMEOUT = 6

# Creating a job step and booting a Python interpreter on the compute node is
# far slower than any local collector -- measured at 2.5-3.5s on a busy cluster.
# The sampler runs off-thread, so this only bounds how long a wedged step is
# waited on before the panel falls back to its previous sample.
_PROBE_TIMEOUT = 45

# Lifetime cap for the resident remote collector. It lives inside the user's
# allocation, so it must expire by itself if our stop signal never lands (login
# node killed, network partition); the sampler simply starts a new one when the
# old one exits. An hour is long enough that restarts are invisible and short
# enough that an orphan cannot outlive a typical interactive session.
_STREAM_MAX_SECONDS = 3600

# How long to wait for the first line of a stream before deciding the step is
# not going to start. Covers step creation plus interpreter boot with headroom
# for a loaded controller.
_STREAM_STARTUP_TIMEOUT = 60

# A stream that has been delivering and then goes quiet for this long (whichever
# is larger) is treated as stalled and replaced. Generous, because a busy compute
# node can genuinely be slow to produce a sample -- restarting costs seconds of
# staleness, so it must not be triggered by ordinary jitter.
_STREAM_STALL_FACTOR = 15
_STREAM_MIN_STALL_TIMEOUT = 45

# Non-JSON lines from the remote side (srun diagnostics, tracebacks) worth
# keeping for the error message. Bounded: a chatty failure must not grow forever.
_STREAM_DIAG_LINES = 5

# Slurm states that mean "this job is on a node right now, with resources we
# can actually sample". COMPLETING is deliberately excluded: its cgroup is
# being torn down, so a probe races the teardown for no useful reading.
RUNNING_STATES = frozenset({"RUNNING", "R"})

# States a job cannot come back from. Used to decide that a row's Cancel button
# has nothing left to cancel, and that focus should be handed back.
TERMINAL_STATES = frozenset({
    "COMPLETED", "CD", "CANCELLED", "CA", "FAILED", "F", "TIMEOUT", "TO",
    "NODE_FAIL", "NF", "OUT_OF_MEMORY", "OOM", "PREEMPTED", "PR",
    "BOOT_FAIL", "BF", "DEADLINE", "DL", "SPECIAL_EXIT", "SE", "REVOKED", "RV",
})

# squeue output format. Order matters: it maps positionally onto SQUEUE_FIELDS.
# Specifiers chosen to be supported across Slurm versions.
#
# ``%b`` (TRES per node) and ``%m`` (minimum memory) are here so the *whole* job
# list carries its GPU count and memory size from the one squeue call. Those two
# numbers used to come from a per-job ``scontrol show job``, which was affordable
# while the panel showed a handful of hand-picked jobs and is not now that it
# shows everything the user has queued.
SQUEUE_FORMAT = "%i|%j|%T|%P|%D|%C|%N|%M|%l|%r|%u|%b|%m"
SQUEUE_FIELDS = [
    "jobid", "name", "state", "partition", "nodes", "cpus",
    "nodelist", "elapsed", "timelimit", "reason", "user",
    "gres", "min_memory",
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


def _run_status(cmd: List[str], timeout: int = _CMD_TIMEOUT) -> Tuple[Optional[int], str, str]:
    """Like :func:`_run`, but keeps the exit code and stderr.

    Needed where *why* a command failed changes the answer: "Invalid job id"
    means the job is gone, while a timeout means the controller is busy and we
    know nothing. ``rc`` is None when the command could not be run at all.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as err:  # noqa: BLE001 - includes Timeout/OSError/FileNotFound
        logger.info("slurm command failed (%s): %s", " ".join(cmd[:2]), err)
        return None, "", str(err)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


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


def gpus_from_gres(gres: str) -> Optional[int]:
    """GPU count from a squeue ``%b`` / gres string, or None when there is none.

    Slurm spells the same allocation several ways depending on version and on
    whether the job is running yet -- ``gres/gpu:a100:1``, ``gres:gpu:2``,
    ``gpu:4``, sometimes with an ``(IDX:0-3)`` suffix -- and writes ``N/A`` where
    a job requested no GPUs at all. A type name with no trailing count (``gpu:a100``)
    means one device.
    """
    text = (gres or "").strip()
    if not text or text.upper() in _UNSET_VALUES:
        return None
    total = 0
    found = False
    for item in text.split(","):
        # Drop the "(IDX:0-3)" device list some versions append.
        head = item.strip().partition("(")[0]
        parts = [p for p in head.replace("/", ":").split(":") if p]
        if not any(p.lower() == "gpu" for p in parts):
            continue
        total += int(parts[-1]) if parts[-1].isdigit() else 1
        found = True
    return total if found else None


def is_terminal_state(state: Optional[str]) -> bool:
    """True when a job has reached a state it cannot leave.

    Distinct from "not running": PENDING and CONFIGURING are not running either,
    but they still have a future (and are still worth cancelling).
    """
    return (state or "").strip().upper().split()[0] in TERMINAL_STATES if state else False


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
        # Context fields: what the job is and where it will end. Cheap (already
        # in the same scontrol output) and they answer the questions a job list
        # usually raises next.
        "account": info.get("Account"),
        "qos": info.get("QOS"),
        "start_time": info.get("StartTime"),
        "end_time": info.get("EndTime"),
        "workdir": info.get("WorkDir"),
        "command": info.get("Command"),
        "exit_code": info.get("ExitCode"),
    }


_UNSET_VALUES = frozenset({
    "", "N/A", "NA", "(NULL)", "NONE", "UNKNOWN", "UNLIMITED", "INVALID",
    "NOT_SET", "PARTITION_LIMIT",
})


def parse_duration(text: Optional[str]) -> Optional[float]:
    """Seconds from a Slurm duration, or None when there isn't a number in it.

    Slurm writes durations five different ways depending on magnitude and
    command -- ``45``, ``12:34``, ``1:02:03``, ``2-03:04:05``, ``2-03`` -- and
    also writes ``UNLIMITED`` / ``N/A`` / ``INVALID`` where a duration would go.
    None means "no finite duration", which is different from zero: a job with an
    unlimited time limit has no progress to show, not 100% of it used.
    """
    raw = (text or "").strip().upper()
    if raw in _UNSET_VALUES:
        return None
    days = 0.0
    if "-" in raw:
        day_part, _, raw = raw.partition("-")
        try:
            days = float(int(day_part))
        except ValueError:
            return None
    parts = raw.split(":") if raw else []
    try:
        values = [float(p) for p in parts] if parts else [0.0]
    except ValueError:
        return None
    if len(values) > 3:
        return None
    # Right-aligned: [s], [m, s], [h, m, s]. A bare "2-03" means 2 days 3 hours.
    if days and len(values) == 1:
        hours, minutes, seconds = values[0], 0.0, 0.0
    else:
        padded = [0.0] * (3 - len(values)) + values
        hours, minutes, seconds = padded
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def format_duration(seconds: Optional[float]) -> str:
    """Compact ``D-HH:MM:SS`` style rendering, matching Slurm's own output."""
    if seconds is None:
        return "—"
    total = int(max(seconds, 0))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_SIZE_UNITS = {"K": 1024.0, "M": 1024.0 ** 2, "G": 1024.0 ** 3,
               "T": 1024.0 ** 4, "P": 1024.0 ** 5}


def parse_size(text: Optional[str]) -> Optional[float]:
    """Bytes from a Slurm size like ``4000M``, ``64G``, ``1234K``, ``2Gc``.

    Slurm appends ``c``/``n`` to memory requests (per-CPU / per-node) and reports
    sstat sizes with a unit suffix; both are accepted. Returns None when the
    value carries no number, so callers can distinguish "unset" from zero.
    """
    raw = (text or "").strip().upper().rstrip("BC N")
    if not raw or raw in _UNSET_VALUES:
        return None
    multiplier = 1.0
    if raw[-1] in _SIZE_UNITS:
        multiplier = _SIZE_UNITS[raw[-1]]
        raw = raw[:-1]
    try:
        return float(raw) * multiplier
    except ValueError:
        return None


def format_size(num_bytes: Optional[float]) -> str:
    """Render bytes as the shortest sensible ``G``/``M``/``T`` string."""
    if num_bytes is None:
        return "—"
    value = float(num_bytes)
    for unit, scale in (("T", _SIZE_UNITS["T"]), ("G", _SIZE_UNITS["G"]),
                        ("M", _SIZE_UNITS["M"]), ("K", _SIZE_UNITS["K"])):
        if value >= scale:
            shown = value / scale
            return f"{shown:.0f}{unit}" if shown >= 10 else f"{shown:.1f}{unit}"
    return f"{value:.0f}B"


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


def is_running_state(state: Optional[str]) -> bool:
    """True when a squeue state string means the job is running on a node."""
    return (state or "").strip().upper() in RUNNING_STATES


def get_running_user_jobs(user: Optional[str] = None) -> List[Dict[str, str]]:
    """Return only the current user's *running* jobs.

    Filtering happens here rather than via ``squeue -t RUNNING`` so the caller
    still gets the same parsed row shape as :func:`get_user_jobs`, and so a
    Slurm version that dislikes the state filter cannot empty the picker.
    """
    return [job for job in get_user_jobs(user) if is_running_state(job.get("state"))]


def first_node(nodelist: Optional[str]) -> Optional[str]:
    """Best-effort first hostname from a Slurm nodelist expression.

    Handles the common bracket forms -- ``a2142``, ``a[2142-2145]``,
    ``node[01,05]``, ``a2142,a2143`` -- well enough to pin a probe to one node.
    Returns None when the expression cannot be reduced, in which case callers
    should let Slurm pick.
    """
    text = (nodelist or "").strip()
    if not text or text in ("(null)", "None"):
        return None
    # Take the first comma-separated element that is *outside* brackets.
    depth = 0
    head = ""
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            break
        head += ch
    if "[" not in head:
        return head or None
    prefix, _, rest = head.partition("[")
    rest = rest.rstrip("]")
    # First index in the range/list: "2142-2145" -> 2142, "01,05" -> 01
    first = rest.split(",")[0].split("-")[0].strip()
    if not first:
        return prefix or None
    return f"{prefix}{first}"


def _srun_prefix(jobid: str, node: Optional[str] = None) -> List[str]:
    """Build the ``srun`` invocation that lands a process inside a job.

    ``srun --overlap --jobid=N`` joins the *existing* allocation, so the sampled
    process lands inside the job's cgroup and inherits its
    ``CUDA_VISIBLE_DEVICES``. That is the whole point of this approach: the
    remote collector then scopes CPU, memory and GPUs to the job with no extra
    filtering logic on our side.
    """
    cmd = [
        "srun",
        "--overlap",             # join the running allocation, don't queue a new one
        f"--jobid={jobid}",
        "--ntasks=1",            # one collector, not one per allocated CPU
        "--nodes=1",
        "--quiet",               # keep srun's own chatter out of the payload
        "--unbuffered",          # forward each line as it is written, not in blocks
    ]
    if node:
        cmd.append(f"--nodelist={node}")
    # PYTHONPATH covers the editable / plain-checkout case, where the package is
    # importable from the source tree but not installed into site-packages.
    # It goes through srun's own --export rather than an `env` prefix: the
    # remote PATH is the user's, and a shadowing (or broken) `env` earlier on it
    # would otherwise be what actually gets executed.
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    existing = os.environ.get("PYTHONPATH")
    pythonpath = f"{package_root}:{existing}" if existing else package_root
    cmd.append(f"--export=ALL,PYTHONPATH={pythonpath}")
    return cmd


# The interpreter is addressed as ``sys.executable -m ground_control`` rather
# than by the ``gc`` console script: a non-interactive remote shell gets none of
# the user's PATH, so ``gc`` is typically not found, while the absolute
# interpreter path resolves fine over a shared home directory.
_REMOTE_MODULE = ["-m", "ground_control"]


def _stream_command(jobid: str, node: Optional[str] = None,
                    interval: float = 1.0,
                    max_seconds: float = _STREAM_MAX_SECONDS) -> List[str]:
    """Build the command that runs a *resident* collector inside the job.

    This is what makes job monitoring cheap. The expensive part of sampling a
    job was never the metrics -- it was creating a Slurm job step and booting a
    Python interpreter, seconds of it, for every single reading. Here that cost
    is paid once: ``gc --stream`` stays alive on the compute node and writes one
    JSON line per sample, so the login node's share of the work drops to reading
    a line and parsing it.

    ``max_seconds`` is a self-destruct timer for the remote process. It runs in
    the user's allocation, so it must expire on its own if our terminate signal
    somehow never reaches it.
    """
    return _srun_prefix(jobid, node) + [
        sys.executable, *_REMOTE_MODULE,
        "--stream",
        "--interval", str(max(float(interval), 0.2)),
        "--stream-max-seconds", str(int(max_seconds)),
    ]


def _probe_command(jobid: str, node: Optional[str] = None,
                   interval: float = 0.3) -> List[str]:
    """Build the one-shot ``srun`` command that samples a job once and exits.

    Kept as the fallback for a compute node whose ``ground_control`` predates
    ``--stream``: correct, just slow (a whole job step per sample).
    """
    return _srun_prefix(jobid, node) + [
        sys.executable, *_REMOTE_MODULE,
        "--once", "--json",
        "--interval", str(interval),
    ]


def probe_job_metrics(jobid: str, node: Optional[str] = None,
                      interval: float = 0.3,
                      timeout: int = _PROBE_TIMEOUT) -> Optional[Dict]:
    """Sample a running job's resources from inside its allocation.

    Returns the parsed ``gc --once --json`` snapshot, or None on any failure
    (step could not be created, remote interpreter missing, malformed output).
    """
    out = _run(_probe_command(jobid, node, interval), timeout=timeout)
    if not out:
        return None
    # srun can interleave its own diagnostics with the payload, so locate the
    # JSON object rather than assuming stdout is pure.
    start = out.find("{")
    if start < 0:
        logger.info("job probe %s returned no JSON payload", jobid)
        return None
    try:
        snapshot = json.loads(out[start:])
    except (ValueError, TypeError) as err:
        logger.info("job probe %s returned unparsable JSON: %s", jobid, err)
        return None
    if not isinstance(snapshot, dict) or "metrics" not in snapshot:
        logger.info("job probe %s returned an unexpected shape", jobid)
        return None
    return snapshot


class JobFocusSampler:
    """Keeps one running job's metrics flowing, sampled from inside the job.

    The design point: ``gc`` runs where the terminal is -- usually a shared,
    slow login node -- but *nothing* about collecting a job's metrics needs to
    happen there. So a resident ``gc --stream`` is started inside the job's
    allocation (:func:`_stream_command`) and this class does nothing but read its
    lines. All psutil/NVML work, all GPU enumeration, all process scanning
    happens on the compute node; the login node parses one JSON line per tick.

    That also fixes the latency. The earlier design ran a fresh ``srun ... --once``
    per sample, so every reading cost a job step plus an interpreter boot --
    seconds each, on a dashboard that ticks about once a second. Now that cost is
    paid once per stream, and samples arrive at the remote collector's own
    cadence.

    Reading still happens on a daemon thread and the UI still reads the last
    completed sample plus its age, because a stream can stall (node under load,
    step evicted) and a stale reading must be labelled, not presented as live.

    Falls back to one-shot probing if the remote ``ground_control`` is too old to
    understand ``--stream``.
    """

    def __init__(self, jobid: str, node: Optional[str] = None,
                 interval: float = 1.0):
        self.jobid = str(jobid)
        self.node = node
        # Remote sampling cadence. Matching the dashboard's own tick is now
        # affordable, since a sample no longer costs a job step.
        self.interval = max(float(interval), 0.2)
        self._snapshot: Optional[Dict] = None
        self._sampled_at: float = 0.0
        self._error: Optional[str] = None
        self._consecutive_failures = 0
        self._mode = "stream"
        self._restarts = 0
        self._stream_startup_failures = 0
        self._diagnostics: List[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"gc-job-stream-{self.jobid}", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the reader to finish and tear the remote collector down.

        Nothing here blocks: this is called from the UI thread (leaving job focus,
        or quitting), the reader thread may be sitting in ``readline``, and
        *reaping* ``srun`` takes as long as slurmctld takes to cancel the step.

        The split matters. The SIGTERM is sent **inline**, because it is a syscall
        that returns immediately and because this is also the last thing that
        happens on the way out of the app: handing the signal itself to a daemon
        thread risks the interpreter exiting before that thread is ever scheduled,
        which would leave an orphaned collector running inside the user's
        allocation with nobody reading it. Only the waiting -- and the SIGKILL
        fallback for a wedged srun -- goes to a throwaway thread.
        """
        self._stop.set()
        self._thread = None
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        _signal_terminate(proc)
        threading.Thread(target=_reap, args=(proc,),
                         name=f"gc-job-stop-{self.jobid}", daemon=True).start()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # -- data --------------------------------------------------------------- #
    def latest(self) -> Tuple[Optional[Dict], float, Optional[str]]:
        """Return ``(snapshot, age_seconds, error)`` without blocking.

        ``age_seconds`` is ``inf`` while no sample has landed yet, so callers
        can distinguish "still waiting for the first sample" from "stale".
        """
        with self._lock:
            snapshot, sampled_at, error = self._snapshot, self._sampled_at, self._error
        age = (time.time() - sampled_at) if sampled_at else float("inf")
        return snapshot, age, error

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    @property
    def mode(self) -> str:
        """``"stream"`` (resident collector) or ``"probe"`` (one srun per sample)."""
        with self._lock:
            return self._mode

    @property
    def restarts(self) -> int:
        """How many times the remote collector has been (re)started."""
        with self._lock:
            return self._restarts

    def diagnostics(self) -> List[str]:
        """Last few non-JSON lines from the remote side, for error reporting."""
        with self._lock:
            return list(self._diagnostics)

    # -- internals ---------------------------------------------------------- #
    def _record_sample(self, snapshot: Dict) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._sampled_at = time.time()
            self._error = None
            self._consecutive_failures = 0
            # A working stream clears the startup-failure tally, so failures
            # separated by hours of healthy streaming never add up to a fallback.
            self._stream_startup_failures = 0

    def _record_failure(self, error: str) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._error = error

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.mode == "stream":
                self._run_stream()
            else:
                self._run_probe()

    def _run_stream(self) -> None:
        """Run one resident collector, consuming its lines until it ends."""
        cmd = _stream_command(self.jobid, self.node, interval=self.interval)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                # Folded into stdout deliberately: a separate stderr pipe can
                # fill and deadlock the remote process, and every line is
                # validated as JSON anyway, so srun's diagnostics are harmless
                # here -- and worth keeping, since they explain failures.
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except (OSError, ValueError) as err:
            logger.info("job stream %s could not start: %s", self.jobid, err)
            self._record_failure(f"could not start srun: {err}")
            self._stop.wait(2.0)
            return

        with self._lock:
            self._proc = proc
            self._restarts += 1
        counter = {"samples": 0, "last": time.monotonic()}
        # Reading a pipe blocks indefinitely, so silence has to be policed from
        # outside: a step that never starts writes nothing at all, and a wedged
        # node can stop writing halfway through. Either way the fix is to end
        # this stream so the loop starts a fresh one.
        watchdog = threading.Thread(
            target=self._watch_stream, args=(proc, counter),
            name=f"gc-job-watch-{self.jobid}", daemon=True,
        )
        watchdog.start()
        try:
            for line in proc.stdout:  # blocks until a line, EOF or termination
                if self._stop.is_set():
                    break
                snapshot = _parse_stream_line(line)
                if snapshot is not None:
                    counter["samples"] += 1
                    counter["last"] = time.monotonic()
                    self._record_sample(snapshot)
                    continue
                text = line.strip()
                if text:
                    self._note_diagnostic(text)
        except Exception as err:  # noqa: BLE001 - the reader thread must never die
            logger.error("job stream %s read failed: %s", self.jobid, err)
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            _terminate(proc)
        samples = counter["samples"]

        if self._stop.is_set():
            return
        if samples:
            # The stream ended after producing data: normal for the remote
            # lifetime cap, so restart quietly and keep the last sample visible.
            logger.info("job stream %s ended after %d samples; restarting",
                        self.jobid, samples)
            self._stop.wait(1.0)
            return
        self._record_failure(self._describe_stream_failure())
        with self._lock:
            self._stream_startup_failures += 1
            failures = self._stream_startup_failures
        if failures >= 2 and self._looks_unsupported():
            with self._lock:
                self._mode = "probe"
            logger.info("job %s: remote gc does not support --stream; "
                        "falling back to one-shot probes", self.jobid)
            return
        self._stop.wait(min(2.0 * failures, 10.0))

    def _run_probe(self) -> None:
        """Legacy path: one ``srun ... --once`` per sample (seconds each)."""
        started = time.time()
        try:
            snapshot = probe_job_metrics(self.jobid, self.node)
        except Exception as err:  # noqa: BLE001 - thread must never die
            logger.error("job probe %s raised: %s", self.jobid, err)
            snapshot = None
        if self._stop.is_set():
            return
        if snapshot is not None:
            self._record_sample(snapshot)
        else:
            self._record_failure("probe failed")
        # Space probes from the *end* of the previous one so a slow cluster
        # reduces frequency instead of saturating slurmctld.
        elapsed = time.time() - started
        self._stop.wait(max(max(self.interval, 3.0) - elapsed, 0.5))

    def _watch_stream(self, proc: subprocess.Popen, counter: Dict) -> None:
        """End a stream that has gone quiet, so the loop can replace it.

        Two kinds of silence: a step that never produced a first sample (the
        allocation is gone, the node is not accepting steps) and one that
        produced samples and then stopped (node wedged, remote process killed).
        Both leave the reader blocked in ``readline`` forever and the panel
        showing an ageing sample, which is why they are policed from here.
        """
        stall_timeout = max(_STREAM_STALL_FACTOR * self.interval,
                            _STREAM_MIN_STALL_TIMEOUT)
        while not self._stop.is_set():
            if proc.poll() is not None:
                return  # ended on its own; nothing to police
            quiet_for = time.monotonic() - counter["last"]
            limit = _STREAM_STARTUP_TIMEOUT if not counter["samples"] else stall_timeout
            if quiet_for > limit:
                logger.info("job stream %s silent for %.0fs (%s); restarting",
                            self.jobid, quiet_for,
                            "never started" if not counter["samples"] else "stalled")
                _terminate(proc)
                return
            self._stop.wait(1.0)

    def _note_diagnostic(self, text: str) -> None:
        logger.info("job stream %s: %s", self.jobid, text[:300])
        with self._lock:
            self._diagnostics.append(text[:300])
            del self._diagnostics[:-_STREAM_DIAG_LINES]

    def _describe_stream_failure(self) -> str:
        diagnostics = self.diagnostics()
        return diagnostics[-1] if diagnostics else "no output from srun"

    def _looks_unsupported(self) -> bool:
        """True when the remote failure reads like an unrecognised ``--stream``.

        Only then is falling back to one-shot probing the right answer; a step
        that could not be created, or a node that is simply busy, is retried as a
        stream instead of being permanently downgraded.
        """
        haystack = " ".join(self.diagnostics()).lower()
        return any(marker in haystack for marker in (
            "no such option", "unrecognized", "unrecognised",
            "unexpected extra argument", "usage: gc", "no such file or directory",
        ))


def _parse_stream_line(line: str) -> Optional[Dict]:
    """Parse one NDJSON line from a remote collector, or None if it isn't one."""
    text = (line or "").strip()
    if not text.startswith("{"):
        return None
    try:
        snapshot = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(snapshot, dict) or "metrics" not in snapshot:
        return None
    return snapshot


def _signal_terminate(proc: Optional[subprocess.Popen]) -> None:
    """Ask a remote collector's ``srun`` to stop, without waiting for it.

    SIGTERM to ``srun`` is what cancels the job step, and therefore what actually
    stops the process running inside the job. Sending it returns immediately, so
    this is safe to call from the UI thread.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001 - teardown must never raise
        pass


def _reap(proc: Optional[subprocess.Popen]) -> None:
    """Wait for a terminated ``srun`` to go, escalating to SIGKILL if it will not.

    This is the part that blocks: srun has to tell the controller to cancel the
    step, which is not instant. Always call it off the UI thread.
    """
    if proc is None:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _terminate(proc: Optional[subprocess.Popen]) -> None:
    """Blocking teardown: signal, then reap. For the sampler's own thread."""
    _signal_terminate(proc)
    _reap(proc)


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


# squeue's wording when the id is not a job it knows about -- which, for a job
# that has finished, is how "it is over" arrives. Matched on stderr rather than
# on the exit code, because a busy controller also exits non-zero and that means
# the opposite: we know nothing yet.
_JOB_GONE_MARKERS = ("invalid job id", "invalid job array id")


def final_state_from_sacct(jobid: str) -> Optional[str]:
    """The state Slurm's accounting database recorded for a finished job.

    ``squeue`` forgets a job shortly after it ends, so this is what turns "gone"
    into something worth telling the user: COMPLETED and FAILED both end a focus
    session, but only one of them is bad news. ``-X`` asks for the allocation
    only, so a job's steps do not each contribute a row.
    """
    jobid = str(jobid or "").strip()
    if not jobid or not shutil.which("sacct"):
        return None
    out = _run(["sacct", "--noheader", "-P", "-X", "-o", "State", "-j", jobid])
    if not out:
        return None
    for line in out.splitlines():
        state = line.strip()
        if state:
            # "CANCELLED by 213852" -> "CANCELLED": the uid adds nothing here.
            return state.split()[0].upper()
    return None


def get_job_liveness(jobid: str) -> Tuple[bool, Optional[str]]:
    """``(still_running, state)`` for one job, erring towards "still running".

    This is what job focus hangs off, so the bias matters: a wrong "it ended"
    tears down a working dashboard, while a wrong "still running" costs one more
    check a few seconds later. So a controller that times out or refuses to answer
    returns ``(True, None)`` -- *unknown*, not *finished* -- and only squeue
    saying the job is gone, or naming a non-running state, ends a focus session.
    """
    jobid = str(jobid or "").strip()
    if not jobid or not slurm_available():
        return True, None
    rc, out, err = _run_status(["squeue", "--noheader", "-o", "%T", "-j", jobid])
    if rc == 0:
        for line in out.splitlines():
            state = line.strip().upper()
            if state:
                return is_running_state(state), state
        # Exit 0 with no rows: the job has left the queue.
        # State stays None when accounting has nothing to say: "it ended" is the
        # part we are sure of, and naming a state we guessed at would be worse
        # than saying only that.
        return False, final_state_from_sacct(jobid)
    if any(marker in err.lower() for marker in _JOB_GONE_MARKERS):
        # State stays None when accounting has nothing to say: "it ended" is the
        # part we are sure of, and naming a state we guessed at would be worse
        # than saying only that.
        return False, final_state_from_sacct(jobid)
    logger.info("job %s liveness unknown (rc=%s): %s", jobid, rc, err.strip()[:200])
    return True, None


# How much of a job's output file to show. The interesting end of a log is the
# recent end, and reading a multi-gigabyte training log over a shared filesystem
# to display 40 lines of it is not a thing to do on a keypress.
OUTPUT_TAIL_BYTES = 64 * 1024


def get_job_output_paths(jobid: str) -> Dict[str, Optional[str]]:
    """Where a job's stdout/stderr are going, per ``scontrol``.

    Returns ``{"stdout", "stderr", "workdir", "command"}`` with unset values as
    None. Slurm has already expanded the ``%j``/``%x`` patterns by the time
    scontrol reports them, so these are real paths -- though not necessarily
    reachable from here: a job writing to node-local scratch has an output file
    this host cannot see, which is why the reader reports *why* it failed.

    A path containing spaces is not recoverable from scontrol's key=value
    output; that is a limitation of the format, not something worth guessing at.
    """
    jobid = str(jobid or "").strip()
    if not jobid:
        return {}
    out = _run(["scontrol", "show", "job", jobid])
    if not out:
        return {}
    info = parse_scontrol_job(out)

    def clean(value: Optional[str]) -> Optional[str]:
        text = (value or "").strip()
        if not text or text.upper() in _UNSET_VALUES or text == "(null)":
            return None
        return text

    workdir = clean(info.get("WorkDir"))
    result: Dict[str, Optional[str]] = {
        "workdir": workdir,
        "command": clean(info.get("Command")),
    }
    for key, field in (("stdout", "StdOut"), ("stderr", "StdErr")):
        path = clean(info.get(field))
        # A relative path is relative to the job's working directory, not ours.
        if path and not os.path.isabs(path) and workdir:
            path = os.path.join(workdir, path)
        result[key] = path
    return result


def read_output_tail(path: Optional[str],
                     max_bytes: int = OUTPUT_TAIL_BYTES) -> Tuple[str, Optional[str]]:
    """Read the last ``max_bytes`` of a file: ``(text, error)``.

    Both halves can be useful at once: a file that shrank (a rerun truncating
    its log) still returns what was read. Errors are returned rather than raised
    because every one of them is a normal thing to hit -- the job has not written
    anything yet, the file lives on scratch this node cannot mount, another
    user's job is not ours to read.
    """
    if not path:
        return "", "no output file recorded for this job"
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                # The seek almost certainly landed mid-line; drop that fragment
                # rather than showing half a line as if it were whole.
                handle.readline()
            data = handle.read()
    except FileNotFoundError:
        return "", (f"{path} does not exist yet — the job may not have written "
                    "anything, or the path is on storage this node cannot see")
    except PermissionError:
        return "", f"not permitted to read {path}"
    except OSError as err:
        return "", f"could not read {path}: {err}"
    # Job output is arbitrary bytes: undecodable sequences are replaced rather
    # than allowed to fail the read, and ANSI escapes are left in place for the
    # viewer to interpret.
    return data.decode("utf-8", errors="replace"), None


def scancel_job(jobid: str, signal: Optional[str] = None) -> Tuple[bool, str]:
    """Cancel (or signal) a job, returning ``(ok, message)``.

    Unlike everything else here this *changes* cluster state, so the outcome is
    reported rather than swallowed: the caller needs to tell the user whether
    their job is actually gone. Slurm's own stderr is passed through, since
    "Access/permission denied" and "Invalid job id specified" are exactly the
    messages worth showing.
    """
    jobid = str(jobid or "").strip()
    if not jobid:
        return False, "no job id"
    if not shutil.which("scancel"):
        return False, "scancel is not on PATH"
    cmd = ["scancel"]
    if signal:
        cmd += ["--signal", str(signal)]
    cmd.append(jobid)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "scancel timed out (controller busy?)"
    except (OSError, ValueError) as err:
        return False, f"scancel failed: {err}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"scancel exited {proc.returncode}"
        # Slurm prefixes its own name; drop it so the toast reads cleanly.
        return False, message.replace("scancel: error: ", "").strip()
    what = f"signal {signal} sent to" if signal else "cancelled"
    logger.info("scancel: job %s %s", jobid, what)
    return True, f"Job {jobid} {what}."


# Ranking for the job list: what the user is most likely to act on first.
# Running jobs are what can be focused, watched and read; a pending job's only
# news is its reason, and a finishing one is already out of reach.
_STATE_RANK = {
    "RUNNING": 0, "R": 0,
    "SUSPENDED": 1, "S": 1,
    "CONFIGURING": 2, "CF": 2, "COMPLETING": 2, "CG": 2,
    "PENDING": 3, "PD": 3,
}


def job_sort_key(job: Dict[str, str]):
    """Sort running jobs first, then by job id -- oldest (lowest id) first.

    Numeric where possible so ``999`` sorts before ``1000``, with array ids
    (``123_4``) reduced to their base. The string fallback keeps a site with
    non-numeric ids in a stable order rather than raising.
    """
    state = (job.get("state") or "").strip().upper()
    raw = str(job.get("jobid") or "")
    base = raw.split("_")[0].split(".")[0]
    numeric = (0, int(base)) if base.isdigit() else (1, 0)
    return (_STATE_RANK.get(state, 4), numeric, raw)


class SlurmMonitor:
    """Throttled polling of the user's own job queue.

    The panel shows *all* of the user's jobs -- queued and running -- so there is
    nothing to select and no state to hold beyond the cache: one ``squeue`` call
    describes the whole list, including each job's GPU count and memory
    (``SQUEUE_FORMAT``), and that is all a row needs.

    Only *running* jobs are enriched with ``sstat``, and only the first
    ``detail_limit`` of them. That is the one call here that costs per job, it
    answers a question a queued job cannot even be asked (how much CPU time and
    memory the job is actually using), and a user with two hundred queued jobs
    must not turn a refresh tick into two hundred subprocesses.

    ``poll()`` is safe to call every refresh tick: it returns cached data and
    only hits Slurm again once ``min_interval`` seconds have elapsed, so
    subprocess pressure on slurmctld stays bounded regardless of the UI rate.
    """

    #: Running jobs given the full sstat treatment per poll.
    DETAIL_LIMIT = 8

    def __init__(self, min_interval: float = 4.0, detail_limit: int = DETAIL_LIMIT):
        self.min_interval = min_interval
        self.detail_limit = detail_limit
        self._cache: List[Dict] = []
        self._last_poll: float = 0.0

    def cached(self) -> List[Dict]:
        """Return the last polled result without contacting Slurm."""
        return self._cache

    def invalidate(self) -> None:
        """Drop the throttle so the next ``poll()`` contacts Slurm.

        Used after an action that changes the queue (a cancel), where showing the
        pre-action list for another few seconds would read as the action failing.
        """
        self._last_poll = 0.0

    def poll(self, force: bool = False) -> List[Dict]:
        """Return the user's jobs, refreshing at most every ``min_interval``."""
        now = time.time()
        if not force and self._cache and (now - self._last_poll) < self.min_interval:
            return self._cache
        self._last_poll = now
        self._cache = self._collect()
        return self._cache

    def _collect(self) -> List[Dict]:
        rows = sorted(get_user_jobs(), key=job_sort_key)
        results: List[Dict] = []
        detailed = 0
        for row in rows:
            jid = str(row.get("jobid") or "")
            gpus = gpus_from_gres(row.get("gres", ""))
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
                "user": row.get("user", ""),
                "gpus": str(gpus) if gpus is not None else "",
                "mem": row.get("min_memory", ""),
                "live_cpu": "",
                "live_rss": "",
                "live_tasks": "",
            }
            if is_running_state(info["state"]) and detailed < self.detail_limit:
                detailed += 1
                stats = get_job_live_stats(jid)
                if stats:
                    info["live_cpu"] = stats.get("AveCPU", "") or ""
                    info["live_rss"] = stats.get("MaxRSS", "") or ""
                    info["live_tasks"] = stats.get("NTasks", "") or ""
            results.append(info)
        return results

#!/usr/bin/env python3
"""
Synthetic-but-plausible system load, for recording Ground Control demos.

An idle machine makes a dull screenshot: flat lines, empty bars, nothing to
look at. This script drives CPU, memory, disk, network and GPU with signals
that *look* like real work -- quasi-periodic envelopes with noise and the odd
burst -- so every plot in the TUI has something to say.

Every signal is the sum of two sine waves whose periods are deliberately
non-harmonic (23s and 7.3s), plus noise and occasional spikes. The result never
repeats exactly inside a recording, which is what stops a 15-second GIF from
looking like a looping test pattern.

    ./loadgen.py                       # 'wave' profile, runs until Ctrl-C
    ./loadgen.py --profile burst       # heavy, trips alert thresholds
    ./loadgen.py --profile calm        # light, for screenshots of idle-ish systems
    ./loadgen.py --duration 120        # stop by itself after 2 minutes
    ./loadgen.py --only cpu,gpu        # just those generators

Nothing here needs root, everything is cleaned up on exit, and the disk
generator confines itself to one file under --workdir.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Two non-harmonic periods: their sum never repeats inside a short recording.
SLOW_PERIOD = 23.0
FAST_PERIOD = 7.3


# --------------------------------------------------------------------------- #
# Signal generation
# --------------------------------------------------------------------------- #

class Signal:
    """
    A bounded, wandering value in [0, 1].

    ``phase`` shifts the whole waveform, which is how the CPU generator makes a
    wave *travel* across the per-core bars: core N gets phase N/ncores.
    """

    def __init__(self, base: float, amp: float, noise: float = 0.06,
                 phase: float = 0.0, spike_chance: float = 0.0,
                 spike_size: float = 0.4, rng: random.Random | None = None):
        self.base = base
        self.amp = amp
        self.noise = noise
        self.phase = phase
        self.spike_chance = spike_chance
        self.spike_size = spike_size
        self.rng = rng or random.Random()
        self._spike = 0.0

    def at(self, t: float) -> float:
        slow = math.sin(2 * math.pi * (t / SLOW_PERIOD - self.phase))
        fast = math.sin(2 * math.pi * (t / FAST_PERIOD - self.phase * 1.7))
        value = self.base + self.amp * (0.72 * slow + 0.28 * fast)
        value += self.rng.uniform(-self.noise, self.noise)

        # Spikes decay rather than snapping back: a vertical cliff on both sides
        # of a peak reads as a glitch, a decay reads as a burst of work.
        if self.spike_chance and self.rng.random() < self.spike_chance:
            self._spike = self.spike_size
        value += self._spike
        self._spike *= 0.82
        if self._spike < 0.01:
            self._spike = 0.0

        return min(1.0, max(0.0, value))


PROFILES = {
    # base, amp, noise, spike_chance  -- per generator
    "calm": {
        "cpu": (0.14, 0.10, 0.03, 0.004),
        "mem": (0.10, 0.05, 0.01, 0.0),
        "disk": (0.10, 0.08, 0.04, 0.01),
        "net": (0.12, 0.10, 0.05, 0.02),
        "gpu": (0.18, 0.12, 0.04, 0.01),
    },
    "wave": {
        "cpu": (0.46, 0.34, 0.06, 0.010),
        "mem": (0.30, 0.18, 0.02, 0.0),
        "disk": (0.40, 0.30, 0.08, 0.03),
        "net": (0.38, 0.30, 0.10, 0.05),
        "gpu": (0.55, 0.35, 0.06, 0.02),
    },
    "burst": {
        "cpu": (0.74, 0.24, 0.07, 0.030),
        "mem": (0.52, 0.22, 0.02, 0.0),
        "disk": (0.62, 0.32, 0.10, 0.06),
        "net": (0.60, 0.34, 0.12, 0.08),
        "gpu": (0.80, 0.20, 0.05, 0.04),
    },
}


# Per-generator scale factors, filled in from --intensity before any worker is
# forked so the children inherit them. Recordings dial the whole thing back a
# little to leave the machine headroom for the screen-capture pipeline, and dial
# CPU back further still -- on a small chassis the CPU is what heats the
# motherboard sensor towards its own warning threshold, which makes every panel
# in the recording look like an incident.
INTENSITY: dict[str, float] = {}


def intensity_of(name: str) -> float:
    return INTENSITY.get(name, 1.0)


def parse_intensity(spec: str) -> dict[str, float]:
    """
    Parse ``--intensity``: either ``0.8`` or ``0.8,cpu=0.6,gpu=1.0``.

    A bare number sets every generator; ``name=value`` overrides one.
    """
    scales = {name: 1.0 for name in ALL_GENERATORS}
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if "=" in part:
            name, _, value = part.partition("=")
            name = name.strip()
            if name not in scales:
                raise ValueError(f"unknown generator {name!r} in --intensity")
            scales[name] = float(value)
        else:
            scales = {name: float(part) for name in scales}
    return scales


def make_signal(profile: str, name: str, phase: float = 0.0,
                seed: int = 0) -> Signal:
    base, amp, noise, spike = PROFILES[profile][name]
    scale = intensity_of(name)
    return Signal(base=base * scale, amp=amp * scale, noise=noise,
                  phase=phase, spike_chance=spike, rng=random.Random(seed))


# --------------------------------------------------------------------------- #
# CPU: one worker per core, phase-shifted, so the bars ripple
# --------------------------------------------------------------------------- #

SLICE = 0.05  # duty-cycle window; short enough that psutil sees a smooth average

# How much of a full cycle the per-core phases are spread over.
#
# Worth understanding before changing: at 1.0 the cores are spread evenly around
# the whole circle, which makes the heatmap ripple beautifully -- and flattens
# the *aggregate* CPU trace to a straight line, because the sines cancel. At 0.0
# every core moves together, so the aggregate has full amplitude and the heatmap
# has no pattern at all. Half a cycle keeps a strong visible gradient across the
# heatmap while leaving the aggregate about 64% of its amplitude.
PHASE_SPREAD = 0.5


def cpu_worker(core: int, ncores: int, profile: str, seed: int,
               t0: float, stop_at: float) -> None:
    """Busy-wait for duty*SLICE, sleep the rest. Pinned to one core."""
    try:
        os.sched_setaffinity(0, {core})
    except (AttributeError, OSError):
        pass  # affinity is a nicety; the wave still works without it

    sig = make_signal(profile, "cpu",
                      phase=PHASE_SPREAD * core / max(ncores, 1),
                      seed=seed + core)
    while time.monotonic() < stop_at:
        duty = sig.at(time.monotonic() - t0)
        busy_until = time.monotonic() + SLICE * duty
        x = 1.000001
        while time.monotonic() < busy_until:
            # A few thousand FLOPs between clock reads: enough work that the
            # loop is compute-bound, few enough that we do not overshoot.
            for _ in range(400):
                x = x * 1.0000001 + 1e-9
        idle = SLICE * (1.0 - duty)
        if idle > 0.001:
            time.sleep(idle)


def start_cpu(profile: str, seed: int, t0: float, stop_at: float,
              cores: int | None) -> list[int]:
    """Fork one pinned worker per core. Returns their pids."""
    ncores = cores or os.cpu_count() or 4
    pids = []
    for core in range(ncores):
        pid = os.fork()
        if pid == 0:  # child
            try:
                cpu_worker(core, ncores, profile, seed, t0, stop_at)
            except KeyboardInterrupt:
                pass
            finally:
                os._exit(0)
        pids.append(pid)
    return pids


# --------------------------------------------------------------------------- #
# Memory: breathe between a floor and a ceiling
# --------------------------------------------------------------------------- #

BLOCK = 64 * 1024 * 1024  # 64 MiB per block: coarse enough to be cheap to churn


def mem_worker(profile: str, seed: int, t0: float, stop_at: float,
               max_gb: float) -> None:
    sig = make_signal(profile, "mem", seed=seed + 991)
    blocks: list[bytearray] = []
    max_blocks = max(1, int(max_gb * 1024 / 64))

    while time.monotonic() < stop_at:
        want = int(round(sig.at(time.monotonic() - t0) * max_blocks))
        while len(blocks) < want:
            b = bytearray(BLOCK)
            # Touch every page, otherwise Linux hands out zero pages lazily and
            # RSS -- which is what the widget plots -- never moves.
            b[::4096] = b"\x01" * len(b[::4096])
            blocks.append(b)
        while len(blocks) > want:
            blocks.pop()
        time.sleep(0.35)


# --------------------------------------------------------------------------- #
# Disk: write, drop the cache, read back
# --------------------------------------------------------------------------- #

CHUNK = 4 * 1024 * 1024


def disk_worker(profile: str, seed: int, t0: float, stop_at: float,
                workdir: Path, file_mb: int) -> None:
    """
    Generate real block-layer I/O.

    Reading back a file you just wrote is served from page cache and shows up
    as exactly zero disk reads, which is why every chunk is followed by
    ``posix_fadvise(DONTNEED)``: it evicts our own clean pages, so the read
    pass has to go to the device.
    """
    sig = make_signal(profile, "disk", seed=seed + 313)
    path = workdir / "gc-demo-io.bin"
    payload = os.urandom(CHUNK)
    peak_mb_s = 900.0  # NVMe-ish ceiling; the signal scales down from here

    try:
        while time.monotonic() < stop_at:
            # --- write pass ---
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            try:
                written = 0
                while written < file_mb * 1024 * 1024 and time.monotonic() < stop_at:
                    os.write(fd, payload)
                    written += CHUNK
                    os.fsync(fd)
                    _pace(sig, t0, peak_mb_s, CHUNK)
                _fadvise_dontneed(fd)
            finally:
                os.close(fd)

            if time.monotonic() >= stop_at:
                break

            # --- read pass ---
            fd = os.open(path, os.O_RDONLY)
            try:
                while time.monotonic() < stop_at:
                    data = os.read(fd, CHUNK)
                    if not data:
                        break
                    _fadvise_dontneed(fd)
                    _pace(sig, t0, peak_mb_s, len(data))
            finally:
                os.close(fd)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _pace(sig: Signal, t0: float, peak_mb_s: float, nbytes: int) -> None:
    """Sleep so the achieved throughput tracks the signal."""
    rate = max(0.02, sig.at(time.monotonic() - t0)) * peak_mb_s
    target = (nbytes / (1024 * 1024)) / rate
    if target > 0.001:
        time.sleep(min(target, 0.5))


def _fadvise_dontneed(fd: int) -> None:
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass


# --------------------------------------------------------------------------- #
# Network: loopback, shaped like a download
# --------------------------------------------------------------------------- #

def net_worker(profile: str, seed: int, t0: float, stop_at: float) -> None:
    """
    Push shaped traffic over loopback.

    Note this is inherently symmetric: bytes on ``lo`` count once as sent and
    once as received, so the widget's upload and download traces mirror each
    other. Any genuine traffic the machine is already doing rides on top and
    breaks the symmetry -- which is why recordings still look plausible.
    """
    sig_down = make_signal(profile, "net", seed=seed + 77)
    sig_up = make_signal(profile, "net", phase=0.4, seed=seed + 78)
    peak_mb_s = 260.0

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]

    payload = os.urandom(256 * 1024)
    stop = threading.Event()

    def sink() -> None:
        conn, _ = listener.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while not stop.is_set():
                if not conn.recv(1 << 20):
                    break
                # Ack with a smaller counter-flow so the two traces differ in
                # shape even though their totals must match.
                rate = max(0.02, sig_up.at(time.monotonic() - t0)) * peak_mb_s
                conn.sendall(payload[: int(32 * 1024 * min(1.0, rate / 60))] or b"\x00")
        except OSError:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=sink, daemon=True)
    thread.start()

    client = socket.create_connection(("127.0.0.1", port))
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    drain = threading.Thread(target=_drain, args=(client, stop), daemon=True)
    drain.start()

    try:
        while time.monotonic() < stop_at:
            client.sendall(payload)
            _pace(sig_down, t0, peak_mb_s, len(payload))
    except OSError:
        pass
    finally:
        stop.set()
        client.close()
        listener.close()


def _drain(sock: socket.socket, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            if not sock.recv(1 << 20):
                break
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# GPU: a modulated CUDA kernel, compiled on demand
# --------------------------------------------------------------------------- #

def build_gpu_binary(workdir: Path) -> Path | None:
    """Compile gpu_wave.cu. Returns None if there is no CUDA toolchain."""
    src = HERE / "gpu_wave.cu"
    if not src.exists():
        return None
    nvcc = shutil.which("nvcc") or "/usr/local/cuda/bin/nvcc"
    if not Path(nvcc).exists():
        print("loadgen: no nvcc, skipping GPU load", file=sys.stderr)
        return None
    out = workdir / "gpu_wave"
    proc = subprocess.run([nvcc, "-O2", "-o", str(out), str(src)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"loadgen: nvcc failed, skipping GPU load\n{proc.stderr}",
              file=sys.stderr)
        return None
    return out


def start_gpu(binary: Path, profile: str, duration: float, vram_gb: float,
              workers: int) -> list[subprocess.Popen]:
    """
    Launch ``workers`` instances of the CUDA load.

    Each gets its own copy of the binary under a distinct name, because the GPU
    widget's process rows show the command -- and a list of three
    identically-named PIDs demonstrates nothing about the layout.

    Two deliberate choices. The phases are only slightly offset, not spread
    around the circle: NVML's utilization is "was a kernel resident", so
    workers spread evenly would keep *something* resident at all times and peg
    the reading flat near 100%. And the VRAM is split unevenly, so the process
    rows differ in the column that matters most about them.
    """
    base, amp, _noise, _spike = PROFILES[profile]["gpu"]
    scale = intensity_of("gpu")
    base, amp = base * scale, amp * scale
    workers = max(1, workers)
    weights = _vram_weights(workers)
    procs = []
    for i in range(workers):
        exe = binary
        if i:  # worker 0 keeps the plain name
            exe = binary.with_name(f"{binary.name}-{i}")
            if not exe.exists():
                shutil.copy2(binary, exe)
        procs.append(subprocess.Popen(
            [str(exe), f"--base={base}", f"--amp={amp}",
             f"--seconds={duration:.0f}",
             f"--vram-gb={vram_gb * weights[i]:.2f}",
             f"--phase={0.06 * i:.3f}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return procs


def _vram_weights(workers: int) -> list[float]:
    """Uneven split summing to 1: a big job, a medium one, and small ones."""
    raw = [1.0 / (i + 1.4) for i in range(workers)]
    total = sum(raw)
    return [r / total for r in raw]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

ALL_GENERATORS = ("cpu", "mem", "disk", "net", "gpu")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", choices=sorted(PROFILES), default="wave")
    p.add_argument("--duration", type=float, default=0.0,
                   help="seconds to run; 0 means until killed (default)")
    p.add_argument("--only", default=",".join(ALL_GENERATORS),
                   help=f"comma-separated subset of {','.join(ALL_GENERATORS)}")
    p.add_argument("--seed", type=int, default=7,
                   help="makes the waveforms reproducible across recordings")
    p.add_argument("--intensity", default="1.0",
                   help="scale the generators: '0.8' for all, or "
                        "'0.8,cpu=0.6' to override one")
    p.add_argument("--cores", type=int, default=None,
                   help="how many cores to drive (default: all)")
    p.add_argument("--max-mem-gb", type=float, default=16.0)
    p.add_argument("--vram-gb", type=float, default=8.0,
                   help="total VRAM to hold, split across --gpu-workers")
    p.add_argument("--gpu-workers", type=int, default=3,
                   help="phased CUDA processes, so the GPU process rows have rows")
    p.add_argument("--disk-file-mb", type=int, default=1536)
    p.add_argument("--workdir", default=None,
                   help="scratch dir for the disk file and CUDA binary")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    global INTENSITY
    try:
        INTENSITY = parse_intensity(args.intensity)
    except ValueError as exc:
        print(f"loadgen: bad --intensity: {exc}", file=sys.stderr)
        return 2

    wanted = {g.strip() for g in args.only.split(",") if g.strip()}
    unknown = wanted - set(ALL_GENERATORS)
    if unknown:
        print(f"loadgen: unknown generator(s): {', '.join(sorted(unknown))}",
              file=sys.stderr)
        return 2

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="gc-loadgen-"))
    workdir.mkdir(parents=True, exist_ok=True)
    owns_workdir = args.workdir is None

    t0 = time.monotonic()
    # An open-ended run still needs a horizon for the workers' loop conditions;
    # a day is far past any recording and the parent kills them long before.
    stop_at = t0 + (args.duration if args.duration > 0 else 86400.0)

    forked: list[int] = []
    popens: list[subprocess.Popen] = []

    def spawn(fn, *fn_args) -> None:
        pid = os.fork()
        if pid == 0:
            try:
                fn(*fn_args)
            except (KeyboardInterrupt, BrokenPipeError):
                pass
            except Exception as exc:  # noqa: BLE001 - one dead generator is survivable
                print(f"loadgen worker {fn.__name__}: {exc}", file=sys.stderr)
            finally:
                os._exit(0)
        forked.append(pid)

    if "cpu" in wanted:
        forked += start_cpu(args.profile, args.seed, t0, stop_at, args.cores)
    if "mem" in wanted:
        spawn(mem_worker, args.profile, args.seed, t0, stop_at, args.max_mem_gb)
    if "disk" in wanted:
        spawn(disk_worker, args.profile, args.seed, t0, stop_at, workdir,
              args.disk_file_mb)
    if "net" in wanted:
        spawn(net_worker, args.profile, args.seed, t0, stop_at)
    if "gpu" in wanted:
        binary = build_gpu_binary(workdir)
        if binary:
            popens += start_gpu(binary, args.profile,
                                stop_at - time.monotonic(), args.vram_gb,
                                args.gpu_workers)

    active = ",".join(sorted(wanted))
    print(f"loadgen: profile={args.profile} generators={active} "
          f"pids={len(forked) + len(popens)} workdir={workdir}", flush=True)

    stopping = threading.Event()

    def handle(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    try:
        while not stopping.is_set() and time.monotonic() < stop_at:
            time.sleep(0.2)
    finally:
        for proc in popens:
            proc.terminate()
        for pid in forked:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        for pid in forked:
            while time.monotonic() < deadline:
                try:
                    if os.waitpid(pid, os.WNOHANG)[0] == pid:
                        break
                except ChildProcessError:
                    break
                time.sleep(0.05)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for proc in popens:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        print("loadgen: stopped", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())

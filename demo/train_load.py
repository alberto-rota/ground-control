#!/usr/bin/env python3
"""
Realistic ML training load for Ground Control demo recordings.

Replaces the synthetic CUDA sine-wave (gpu_wave.cu) with actual PyTorch
training loops on random tensors. The TUI then sees the same kind of signal a
real job produces: pegged-but-not-flat GPU util, stable VRAM footprints that
differ per process, DataLoader CPU workers, and power/clock/bandwidth that
move because the SMs are doing work -- not because a duty-cycle kernel is
being toggled on and off.

    ./train_load.py                       # three jobs, until Ctrl-C
    ./train_load.py --duration 180        # stop after three minutes
    ./train_load.py --workers 2 --vram-gb 12

Nothing here needs a real dataset. Each worker owns a differently-sized model,
holds a fixed activation buffer so VRAM stays occupied between steps, and runs
a standard forward/backward/AdamW step on synthetic batches. Process argv is
rewritten so the GPU panel's COMMAND column shows distinct job names
(train_resnet, train_vit, train_llm) rather than three identical python lines.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Waveform helpers -- same non-harmonic periods as loadgen.py so a recording
# never loops on itself inside ~20 s of captured frames.
# --------------------------------------------------------------------------- #

SLOW_PERIOD = 23.0
FAST_PERIOD = 7.3


def waveform(t: float, base: float, amp: float, phase: float = 0.0) -> float:
    """Bounded duty in (0, 1]: how hard this step should push the GPU."""
    # t: scalar seconds; returns scalar in (0, 1]
    slow = math.sin(2 * math.pi * (t / SLOW_PERIOD - phase))
    fast = math.sin(2 * math.pi * (t / FAST_PERIOD - phase * 1.7))
    value = base + amp * (0.72 * slow + 0.28 * fast)
    return min(1.0, max(0.05, value))


# --------------------------------------------------------------------------- #
# Job recipes -- uneven VRAM / compute so the process rows tell a story
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class JobSpec:
    name: str
    # Fraction of --vram-gb this worker should hold (weights are renormalised).
    vram_weight: float
    # Relative compute intensity (batch / width scale).
    compute: float
    # Phase offset so utilisation is not perfectly locked across workers.
    phase: float
    # Depth / width of the MLP stack.
    depth: int
    width: int


# Three archetypal jobs: a fat vision model, a medium one, a thinner LLM-ish
# stack. Weights sum to ~1 after renormalisation in launch().
JOBS: tuple[JobSpec, ...] = (
    JobSpec("train_resnet", vram_weight=0.55, compute=1.00, phase=0.00, depth=8, width=2048),
    JobSpec("train_vit",    vram_weight=0.30, compute=0.75, phase=0.07, depth=6, width=1536),
    JobSpec("train_llm",    vram_weight=0.15, compute=0.55, phase=0.13, depth=4, width=1024),
)


# --------------------------------------------------------------------------- #
# Model + training step (all GPU tensor ops; no Python loops over batch)
# --------------------------------------------------------------------------- #

def build_model(depth: int, width: int, in_features: int, n_classes: int, device):
    """Stacked Linear+GELU MLP. Parameter count ≈ depth * width²."""
    import torch
    import torch.nn as nn

    layers: list[nn.Module] = [nn.Linear(in_features, width), nn.GELU()]
    for _ in range(depth - 1):
        layers += [nn.Linear(width, width), nn.GELU()]
    layers.append(nn.Linear(width, n_classes))
    return nn.Sequential(*layers).to(device)


def train_forever(
    spec: JobSpec,
    *,
    device_index: int,
    vram_bytes: int,
    base: float,
    amp: float,
    seed: int,
    stop_at: float,
    batch_base: int,
    in_features: int,
    n_classes: int,
) -> None:
    """One worker: allocate VRAM, then step until ``stop_at``."""
    import torch
    import torch.nn.functional as F

    # Make nvidia-smi / the GPU process row show a useful COMMAND, not
    # ``python train_load.py --worker …``.
    try:
        import setproctitle
        setproctitle.setproctitle(spec.name)
    except ImportError:
        pass
    # Fallback that nvitop's cmdline parser still picks up.
    sys.argv = [spec.name, f"--job={spec.name}", f"--vram-mb={vram_bytes // (1024 * 1024)}"]

    torch.cuda.set_device(device_index)
    device = torch.device(f"cuda:{device_index}")
    rng = random.Random(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    model = build_model(spec.depth, spec.width, in_features, n_classes, device)
    try:
        opt = torch.optim.AdamW(model.parameters(), lr=3e-4, fused=True)
    except (RuntimeError, ValueError):
        opt = torch.optim.AdamW(model.parameters(), lr=3e-4)
    model.train()

    # Hold a fixed activation buffer so VRAM stays occupied between steps --
    # real training keeps the working set warm; without this the process row
    # flickers as allocations come and go.
    param_bytes = sum(p.numel() * p.element_size() for p in model.parameters())
    hold_bytes = max(0, vram_bytes - param_bytes - 64 * 1024 * 1024)
    hold: torch.Tensor | None = None
    if hold_bytes > 0:
        # hold: [n_elems] float16 on device -- cheap footprint filler
        n_elems = hold_bytes // 2
        hold = torch.empty(n_elems, device=device, dtype=torch.float16)
        hold.uniform_()

    t0 = time.monotonic()
    step = 0
    # Peak batch we ever try; the waveform scales down from here.
    batch_peak = max(8, int(batch_base * spec.compute))

    while time.monotonic() < stop_at:
        duty = waveform(time.monotonic() - t0, base, amp, spec.phase)
        # Smaller batches + a short sleep at the trough look like a data-loading
        # stall; full batches with no pause look like a compute-bound stretch.
        batch = max(4, int(batch_peak * duty))
        # x: [B, in_features], y: [B]
        x = torch.randn(batch, in_features, device=device, dtype=torch.float32)
        y = torch.randint(0, n_classes, (batch,), device=device)

        opt.zero_grad(set_to_none=True)
        logits = model(x)                     # [B, n_classes]
        loss = F.cross_entropy(logits, y)     # scalar
        loss.backward()
        opt.step()

        # Touch a contiguous slice of the hold buffer each step so memory-bandwidth
        # util registers; a compute-only MLP otherwise leaves that telemetry at 0.
        if hold is not None:
            n = hold.numel()
            span = min(n, 4 * 1024 * 1024)  # 8 MiB of f16
            start = (step * span) % max(1, n - span + 1)
            chunk = hold[start:start + span]          # [span]
            chunk.mul_(1.0001).add_(0.001)

        # Idle fraction of the slice: high duty → almost no sleep.
        idle = 0.045 * (1.0 - duty)
        if idle > 0.002:
            time.sleep(idle)
        elif rng.random() < 0.04:
            # Occasional micro-stall, like a host↔device sync hiccup.
            time.sleep(0.02)

        step += 1
        if step % 50 == 0:
            torch.cuda.synchronize()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--duration", type=float, default=0.0,
                   help="seconds to run; 0 means until killed (default)")
    p.add_argument("--workers", type=int, default=3,
                   help="how many training processes to launch (1-3)")
    p.add_argument("--vram-gb", type=float, default=24.0,
                   help="total VRAM to hold across all workers")
    p.add_argument("--device", type=int, default=0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--base", type=float, default=0.62,
                   help="utilisation waveform floor")
    p.add_argument("--amp", type=float, default=0.32,
                   help="utilisation waveform amplitude")
    p.add_argument("--batch-base", type=int, default=96)
    p.add_argument("--in-features", type=int, default=512)
    p.add_argument("--classes", type=int, default=1000)
    # Internal: a forked child resumes here instead of re-launching.
    p.add_argument("--_worker-index", type=int, default=-1, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def _renormalise(jobs: list[JobSpec]) -> list[float]:
    total = sum(j.vram_weight for j in jobs) or 1.0
    return [j.vram_weight / total for j in jobs]


def _cuda_ready() -> bool:
    """Probe CUDA in a fresh interpreter so the parent never imports torch.

    Importing torch starts background threads; forking after that can deadlock
    the children. The parent therefore only ever spawns subprocesses.
    """
    probe = subprocess.run(
        [sys.executable, "-c",
         "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        return True
    if "ModuleNotFoundError" in (probe.stderr or ""):
        print("train_load: torch is not installed; skipping GPU training load",
              file=sys.stderr)
    else:
        print("train_load: no CUDA device; skipping GPU training load",
              file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    n = max(1, min(args.workers, len(JOBS)))
    jobs = list(JOBS[:n])
    weights = _renormalise(jobs)
    stop_at = time.monotonic() + (args.duration if args.duration > 0 else 86400.0)

    # Child path: launched as a single worker via subprocess.
    if args._worker_index >= 0:
        idx = args._worker_index
        spec = jobs[idx]
        vram_bytes = int(args.vram_gb * weights[idx] * (1024 ** 3))
        try:
            train_forever(
                spec,
                device_index=args.device,
                vram_bytes=vram_bytes,
                base=args.base,
                amp=args.amp,
                seed=args.seed + 17 * idx,
                stop_at=stop_at,
                batch_base=args.batch_base,
                in_features=args.in_features,
                n_classes=args.classes,
            )
        except KeyboardInterrupt:
            pass
        return 0

    if not _cuda_ready():
        return 0

    # Parent: one subprocess per job so the GPU panel has distinct rows.
    # subprocess (not fork) -- see _cuda_ready.
    script = os.path.abspath(__file__)
    remaining = max(1.0, stop_at - time.monotonic())
    children: list[subprocess.Popen] = []
    for idx, spec in enumerate(jobs):
        proc = subprocess.Popen(
            [
                sys.executable, script,
                f"--_worker-index={idx}",
                f"--duration={remaining:.1f}",
                f"--workers={n}",
                f"--vram-gb={args.vram_gb}",
                f"--device={args.device}",
                f"--seed={args.seed}",
                f"--base={args.base}",
                f"--amp={args.amp}",
                f"--batch-base={args.batch_base}",
                f"--in-features={args.in_features}",
                f"--classes={args.classes}",
            ],
        )
        children.append(proc)
        print(
            f"train_load: started {spec.name} pid={proc.pid} "
            f"vram≈{args.vram_gb * weights[idx]:.1f} GiB",
            flush=True,
        )

    stopping = False

    def handle(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    try:
        while not stopping and time.monotonic() < stop_at:
            alive = [p for p in children if p.poll() is None]
            for p in children:
                if p.returncode not in (None, 0):
                    print(f"train_load: worker pid={p.pid} exited {p.returncode}",
                          file=sys.stderr, flush=True)
            children = alive
            if not children:
                break
            time.sleep(0.25)
    finally:
        for proc in children:
            proc.terminate()
        deadline = time.monotonic() + 5
        for proc in children:
            remaining_wait = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining_wait)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("train_load: stopped", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())

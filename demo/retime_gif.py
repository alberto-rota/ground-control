#!/usr/bin/env python3
"""
Stretch a GIF's frame delays so it lasts as long as its tape intended.

Some recordings lose frames no matter how much headroom the machine has. A
theme change in Ground Control repaints every cell on screen, and while the app
is busy doing that, vhs's screenshot loop gets nothing -- it does not wait, it
just captures fewer frames. Because it writes the survivors at a fixed delay,
the result is a GIF that is *short*, and therefore plays too fast: seven themes
flick past in fourteen seconds instead of twenty-five.

Nothing can recover the missing frames. What can be fixed is the pacing, by
scaling every remaining delay by the same factor -- each theme then holds on
screen for as long as the tape asked, at a lower effective frame rate.

    ./retime_gif.py assets/themes.gif 25.0

Requires gifsicle. Refuses to *shorten* a GIF, since that would only ever be
papering over a tape that asks for less than it shows.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# gifsicle prints "delay 0.04s" per frame; GIF stores centiseconds.
DELAY_RE = re.compile(r"delay ([0-9.]+)")

# Below 2cs (50fps) most browsers substitute their own minimum, so a scaled
# delay that lands there would not be honoured anyway.
MIN_CENTISECONDS = 2


def frame_delays(gif: Path) -> list[float]:
    """Per-frame delays in seconds, in order."""
    out = subprocess.run(["gifsicle", "--info", str(gif)],
                         capture_output=True, text=True, check=True).stdout
    return [float(m) for m in DELAY_RE.findall(out)]


def retime(gif: Path, target_seconds: float) -> tuple[bool, str]:
    """
    Scale ``gif``'s delays so it lasts ``target_seconds``. Returns (changed, note).
    """
    if not shutil.which("gifsicle"):
        return False, "gifsicle not installed"

    delays = frame_delays(gif)
    if not delays:
        return False, "no frames found"

    current = sum(delays)
    if current <= 0:
        return False, "zero-length gif"
    if current >= target_seconds:
        return False, f"already {current:.1f}s, target {target_seconds:.1f}s"

    scale = target_seconds / current

    # gifsicle takes per-frame delays as `'#N' --delay CS`. Scaling each one
    # rather than setting a single uniform delay matters: optimisation already
    # merged runs of identical frames into one long-delay frame each, and
    # flattening those would destroy the pacing this is trying to fix.
    args = ["gifsicle", str(gif)]
    for i, delay in enumerate(delays):
        centiseconds = max(MIN_CENTISECONDS, round(delay * 100 * scale))
        args += [f"#{i}", "--delay", str(centiseconds)]
    args += ["-o", str(gif) + ".retimed"]

    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        Path(str(gif) + ".retimed").unlink(missing_ok=True)
        return False, f"gifsicle failed: {proc.stderr.strip()}"

    Path(str(gif) + ".retimed").replace(gif)
    return True, (f"{current:.1f}s -> {sum(frame_delays(gif)):.1f}s "
                  f"({len(delays)} frames, x{scale:.2f})")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("gif", type=Path)
    p.add_argument("target_seconds", type=float)
    args = p.parse_args(argv)

    if not args.gif.is_file():
        print(f"retime_gif: no such file: {args.gif}", file=sys.stderr)
        return 2

    changed, note = retime(args.gif, args.target_seconds)
    print(f"retime_gif: {args.gif.name}: {note}")
    return 0 if changed else 1


if __name__ == "__main__":
    sys.exit(main())

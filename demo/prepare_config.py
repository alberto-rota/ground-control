#!/usr/bin/env python3
"""
Build a throwaway Ground Control config for one demo recording.

Recordings must not depend on -- or scribble on -- the config of whoever runs
them, so every tape gets its own ``XDG_CONFIG_HOME``. This writes a complete
config.json into it: the requested theme's palette, layout, refresh rate,
history size, and optionally alert thresholds low enough that the real load on
the machine actually trips them.

    XDG_CONFIG_HOME=/tmp/demo ./prepare_config.py --theme tokyo-night
    XDG_CONFIG_HOME=/tmp/demo ./prepare_config.py --theme neon --alerts-tuned

It also emits, on stdout, a VHS ``Set Theme`` block whose background and
foreground are taken straight from the same theme file -- so the terminal
around the TUI matches the TUI exactly and there is no seam at the edge of
the frame.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Import the real thing rather than reimplementing it: if the theme format
# changes, the demos follow along instead of quietly drifting.
from ground_control.main import get_default_config
from ground_control.utils.colors import (
    apply_theme,
    get_available_themes,
    load_theme,
)

# Thresholds tuned so that ordinary demo load crosses them. The defaults are
# deliberately conservative (they are meant for real incidents), which is
# exactly wrong for a 15-second recording of the alerting feature.
TUNED_THRESHOLDS = {
    "cpu_percent": {"warn": 45.0, "crit": 72.0, "enabled": True},
    "memory_percent": {"warn": 9.0, "crit": 15.0, "enabled": True},
    "disk_percent": {"warn": 70.0, "crit": 92.0, "enabled": True},
    "temperature_c": {"warn": 55.0, "crit": 66.0, "enabled": True},
    # Shipped disabled on purpose -- a pegged GPU is normally the goal, not an
    # incident -- but a demo of alerting wants the GPU panel to light up too.
    "gpu_util_percent": {"warn": 50.0, "crit": 82.0, "enabled": True},
    "gpu_temperature_c": {"warn": 50.0, "crit": 57.0, "enabled": True},
}


def build_vhs_theme(colors: dict) -> str:
    """
    Render a VHS ``Set Theme`` line from a Ground Control palette.

    Only background/foreground/cursor/selection really matter -- the app paints
    everything inside the frame with true-colour escapes -- but the ANSI slots
    are filled from the palette too, so anything the shell prints before the
    TUI starts is in the same key.
    """
    bg = colors.get("background", "#000000")
    fg = colors.get("text", "#ffffff")
    theme = {
        "background": bg,
        "foreground": fg,
        "cursor": colors.get("accent", fg),
        "selection": colors.get("selection_highlight", colors.get("accent", fg)),
        "black": colors.get("surface", bg),
        "red": colors.get("high_value", "#ff5555"),
        "green": colors.get("network_upload", "#50fa7b"),
        "yellow": colors.get("alert_warn", "#f1fa8c"),
        "blue": colors.get("accent", "#6272a4"),
        "magenta": colors.get("disk_read", "#bd93f9"),
        "cyan": colors.get("disk_free", "#8be9fd"),
        "white": colors.get("white", fg),
        "brightBlack": colors.get("tab_inactive_fg", "#6272a4"),
        "brightRed": colors.get("alert_crit", "#ff6e6e"),
        "brightGreen": colors.get("temp_normal", "#69ff94"),
        "brightYellow": colors.get("temp_warm", "#ffffa5"),
        "brightBlue": colors.get("border", "#d6acff"),
        "brightMagenta": colors.get("memory_ram", "#ff92df"),
        "brightCyan": colors.get("memory_swap", "#a4ffff"),
        "brightWhite": colors.get("text", fg),
    }
    return "Set Theme " + json.dumps(theme)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--theme", default="tokyo-night")
    p.add_argument("--layout", default="grid",
                   choices=("grid", "horizontal", "vertical"))
    p.add_argument("--refresh", type=float, default=0.5,
                   help="refresh_rate in seconds (fast looks better on film)")
    p.add_argument("--history", type=int, default=120,
                   help="history_size in seconds")
    p.add_argument("--alerts-tuned", action="store_true",
                   help="lower the thresholds so demo load actually breaches them")
    p.add_argument("--no-alerts", action="store_true",
                   help="turn alerting off, for panels that should look calm")
    p.add_argument("--sticky", type=float, default=30.0,
                   help="alert_sticky_seconds")
    p.add_argument("--print-vhs-theme", action="store_true",
                   help="also write the VHS Set Theme line to stdout")
    args = p.parse_args(argv)

    if "XDG_CONFIG_HOME" not in os.environ:
        print("prepare_config: refusing to run without XDG_CONFIG_HOME set "
              "(it would overwrite your real config)", file=sys.stderr)
        return 2

    available = get_available_themes()
    if args.theme not in available:
        print(f"prepare_config: unknown theme {args.theme!r}; have: "
              f"{', '.join(available)}", file=sys.stderr)
        return 2

    from platformdirs import user_config_dir
    config_dir = Path(user_config_dir("ground-control"))
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    config = get_default_config()
    config["layout"] = args.layout
    config["refresh_rate"] = args.refresh
    config["history_size"] = args.history
    config["alerts_enabled"] = not args.no_alerts
    config["alert_sticky_seconds"] = args.sticky
    if args.alerts_tuned:
        config["thresholds"].update(
            {k: dict(v) for k, v in TUNED_THRESHOLDS.items()})

    config_file.write_text(json.dumps(config, indent=4))

    # apply_theme writes the palette into the config we just created and
    # records which theme it came from, so the Settings tab highlights it.
    if not apply_theme(args.theme):
        print(f"prepare_config: failed to apply theme {args.theme!r}",
              file=sys.stderr)
        return 1

    if args.print_vhs_theme:
        print(build_vhs_theme(load_theme(args.theme) or {}))

    return 0


if __name__ == "__main__":
    sys.exit(main())

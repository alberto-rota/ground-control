# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install for development
pip install -e .

# Run the app
gc                           # launch TUI
gc --cpu --ram --net         # show only specific widgets
gc -g --gpu-index 0,1        # show GPU widgets for GPU 0 and 1
gc --debug                   # surface exceptions instead of catching them

# Config and themes
gc config                    # open config in $EDITOR
gc config --reset            # reset to defaults
gc theme --list              # list themes
gc theme --monokai           # apply theme (requires restart)

# Build for distribution
python -m build              # produces dist/ via setuptools
```

No automated test suite — manual testing with `gc` and `gc --debug`.  
No linter or formatter is configured.

## Architecture

**Entry point**: `ground_control/main.py` — Click CLI (`gc`/`groundcontrol`). Parses widget-filter flags into an `allowed_types` set (or `None` for all), then instantiates and runs `GroundControl`.

**Main app**: `ground_control/app.py` — `GroundControl(App)`, a ~1700-line Textual application. Manages three layout modes (grid/horizontal/vertical), composes widgets conditionally based on `allowed_types`, and loads/saves config on startup/quit.

**Widgets**: `ground_control/widgets/` — each widget subclasses `MetricWidget` (base.py), which handles Plotext → ANSI → Rich markup conversion (`ansi2rich()`), plot sizing on resize, and history deques. Widgets implement `compose()` for Textual layout and `update_metric(system_metrics)` to pull fresh data.

**Metrics**: `ground_control/utils/system_metrics.py` — `SystemMetrics` collects all data via psutil, nvidia-ml-py, and nvitop. Tracks previous I/O counters for delta calculations. GPU devices are lazily initialized; temperature sensors are discovered once on init.

**Config**: persisted at `~/.config/ground-control/config.json` (via `platformdirs`). Stores widget visibility, layout, refresh rate, history size, and the full color palette. `get_default_config()` in `main.py` defines the schema.

**Themes**: `ground_control/themes/*.json` (20 themes). Each JSON file defines the same 54 per-widget color keys; the CLI flags (`gc theme --<name>`) and the Settings theme list are both built from the directory listing, so adding a JSON file is all that is needed for a new theme. `utils/colors.py` merges theme → config → hardcoded defaults. `gc theme --name` writes the theme colors into the config file; the app reads colors from config on next launch.

**GPU support**: NVIDIA only, via nvitop for device enumeration and nvidia-ml-py for metrics. MiG devices are detected but utilization may be unavailable ("Usage UNAV").

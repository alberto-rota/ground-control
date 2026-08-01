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

**Themes**: builtins live in `ground_control/themes/*.json` (20 themes); user-created themes in `~/.config/ground-control/themes/*.json`, which shadow builtins of the same name (`save_theme` refuses builtin names, so this only happens if a file is placed there by hand). Each JSON file defines the same 54 color keys — `COLOR_GROUPS` in `utils/colors.py` is the canonical grouped list. The CLI flags (`gc theme --<name>`) and the Settings theme list are both built from the directory listings, so adding a JSON file is all that is needed for a new theme. `utils/colors.py` merges theme → config → hardcoded defaults.

`apply_theme(name)` copies the theme's colors into the config *and* records `config["theme"]`/`config["theme_modified"]`, so an edited palette still knows which theme it came from; `get_active_theme()` returns `(name, modified)` and falls back to palette matching for configs written before those keys existed.

**Editing colors**: the Settings tab has a per-key color editor (`#color-key-list` + `#color-hex-input`) that writes single keys, and a save row that writes the live palette to a named user theme (`save_theme`/`delete_theme`). Enter on a color row opens `ColorPickerScreen`. Every path funnels through `GroundControl.apply_color_live(key, hex)` → `set_color()`, `load_colors()`, `_generate_css()`, `_refresh_stylesheet()`; plots then pick up the change on their next tick, since widgets re-read the config through `get_rich_color()` on every render. CLI equivalents: `gc theme <name>`, `gc theme --save-as NAME`, `gc theme --delete NAME`.

**Color picker** (`widgets/color_picker.py`): Textual ships no color-picker or slider widget, so `PaletteGrid` (hue × shade swatch grid, arrow/click navigation) and `HsvSliders` (H/S/V steppers, shift for ×10) are built on `Static` + bindings and `textual.color.Color`. Both post messages with a `control` property so `@on(..., "#id")` can match them. `ColorPickerScreen` is a `ModalScreen` laying out key list | palette + steppers | live preview; it applies changes immediately (no Apply button — Revert restores the value the key had when the screen opened).

**Picker preview**: the preview pane mounts a *real* metric widget outside the grid. `preview_group_for_key()` maps a color key to a metric type, `_build_preview_widget()` constructs it with the title/id that `_dispatch_widget_update` matches on (disk by title, GPU by `gpu_<index>` id), and `update_metrics` feeds it alongside the grid widgets — so its required metric type is added to `required_types`. `refresh_color_preview()` reuses `_last_metrics_by_type` so recoloring costs no I/O. Preview failures are deliberately kept out of `_failed_widget_titles`, so a broken preview can never disable the real widget of the same type.

**GPU support**: NVIDIA only, via nvitop for device enumeration and nvidia-ml-py for metrics. MiG devices are detected but utilization may be unavailable ("Usage UNAV").

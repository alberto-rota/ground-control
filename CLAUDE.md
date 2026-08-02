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

**Alerting** (`utils/alerts.py`): pure, import-light threshold evaluation — no Textual, no psutil — so it is unit-testable and shared by the TUI and `gc --once`. A threshold spec is `{"warn", "crit", "enabled"}`; *direction* is a property of the metric (`METRIC_DIRECTIONS`), not of user config, so "CPU above 90%" and "disk free below 2 GB" are both expressed as plain numbers. `evaluate_snapshot()` returns `(targets, breaches)` where targets are keyed `("cpu", None)`, `("disk", mountpoint)`, `("gpu", index)` — matching how `app._alert_target_key()` identifies panels. Defaults live in `DEFAULT_THRESHOLDS`; `merge_thresholds()` overlays user config and ignores malformed entries rather than raising. Two deliberate defaults: `gpu_util_percent` is **disabled** (a pegged GPU is the goal, not an incident) and network rates are disabled (no sensible site-independent ceiling).

`MetricWidget.set_alert(level, sticky_seconds)` paints the border via the `alert_warn`/`alert_crit` palette keys and prefixes the title with `▲`/`■` — a marker, not colour alone, so the state survives monochrome terminals. Stickiness keeps a breach visible after recovery (escalation still wins immediately) so a spike that happened while the user was on another tab is not missed. Note `styles.clear_rule("border")` is a **no-op** in Textual — border is stored as four per-edge rules, so clearing must iterate `border_top/right/bottom/left`. Config keys: `alerts_enabled`, `alert_sticky_seconds`, `thresholds`; `a` toggles alerting at runtime.

**Snapshot / scripting** (`utils/snapshot.py`): `gc --once` renders one sample outside Textual — `--json` for the machine-readable form, `--check` for Nagios-style exit codes (0/1/2, and 3 on collector failure), `--interval` for the priming gap. Rate metrics are deltas, so `sample_twice()` primes the counters first or throughput reads as 0. The JSON shape is built field by field against `SCHEMA_VERSION` rather than dumping collector output, so internal metric changes cannot silently alter the published contract. `mount_ignored()`/`filter_disk_metrics()` are shared with the TUI's `disk_ignore_prefixes` — without them a snapshot reports every read-only squashfs under `/snap` as 100% full and critically out of space (`--all-mounts` opts back in).

**GPU support**: NVIDIA only, via nvitop for device enumeration and nvidia-ml-py for metrics. MiG devices are detected but utilization may be unavailable ("Usage UNAV").

**GPU telemetry**: `_collect_one_gpu` returns power, temperature, fan, SM clock vs max clock, memory-*bandwidth* utilization, encoder/decoder, performance state and clock-throttle reasons alongside the core util/VRAM figures. Every one of these is best-effort — `SystemMetrics._num()` normalizes NVML's `NA` sentinel to `None` (consumer cards report no power limit, Grace-Blackwell no discrete memory clock), and both the widget and the JSON simply omit or null what is missing rather than printing `N/A` noise.

Throttle reasons come from NVML directly (`_throttle_reasons`), since nvitop exposes no wrapper; the getter is looked up under both the old `...ClocksThrottleReasons` and new `...ClocksEventReasons` spellings. `_THROTTLE_BITS` marks thermal/power-brake/HW slowdown as **severe** (throughput is being lost now) and power-cap/sync-boost as ordinary governed behaviour; `GpuIdle` is deliberately excluded, as an idle GPU is not a throttled one. An empty reason list means "not throttled *or* not reported" — never infer health from it alone.

`GPUWidget.create_telemetry_line()` renders these under the split bar, dropping segments from the least-important end so a narrow panel keeps the throttle state and power draw; the whole row is hidden below `TELEMETRY_MIN_PANEL_HEIGHT` so the plot keeps the space. Two readings are worth understanding: **power as a percent of its limit** and **memory-bandwidth percent** — a card at 100% "utilization" drawing 15% of its power cap with near-zero bandwidth is an input-starved training loop, not a busy GPU. NVML's `gpu_utilization` only means "a kernel was resident", not that the SMs were doing work.

Per-device temperature also feeds `evaluate_snapshot`, so a hot card lights up its own panel rather than only the shared temperature panel.

**GPU process rows**: one process is one row, buttons included. `format_process_line()` lays out fixed-width columns (`PROC_COLUMNS`) so values align down the list, giving the command whatever is left; when the panel narrows it drops columns lowest-priority-first (HOST → CPU → USER), then squeezes PID/GPU MEM, which are never dropped outright — a row you cannot identify and whose memory cost you cannot see is not worth a row. It always returns exactly `width` cells, since it sits beside a fixed-width button strip. The signal buttons are real `Button`s forced to one cell (`border: none; padding: 0; height: 1`) so they keep hover, focus and keyboard activation; single-letter labels K/T/I are explained by the header row and by per-button tooltips naming the signal and PID.

**Widget failure handling**: a panel is disabled (`_failed_widget_titles`, `display:none`) only after `WIDGET_FAILURE_LIMIT` *consecutive* failed renders, tracked in `_widget_failure_streaks` and cleared by any successful tick. Metric sources legitimately blink — a process exiting between `process_iter()` and `cpu_num()`, a GPU vanishing for a tick — and a single transient exception used to hide a panel for the rest of the session. When touching psutil in a widget, catch `psutil.Error` (covers `NoSuchProcess`/`ZombieProcess`/`AccessDenied`), not just `PermissionError`.

**Bar style**: every horizontal bar in the app goes through `MetricWidget.build_gauge_bar(width, fraction, color, grow, track_color)`. One vocabulary: `█` body, a powerline tip (`` / ``) on the growing end, `─` for the unfilled track. The tip *replaces* the last filled cell rather than being appended, so the result is always exactly `width` cells and a full bar still reads as full. `grow="left"` mirrors it for halves that meet at a centre separator (memory's RAM half, network's download half). `build_split_bar` (GPU, network), `create_gradient_bar`, and the memory/disk/temperature bars are all thin wrappers over it — add new bars the same way rather than hand-rolling block strings.

Metric panels do **not** use plotext legends: the bars underneath already name each series in its plot colour, so a legend would duplicate them while eating plot rows. It is also a correctness matter — plotext's legend renderer raises `IndexError` at some panel geometries, which previously disabled `MemoryWidget` outright.

**Testing**: no suite in-repo. Headless testing works via Textual's `app.run_test()` pilot, and `XDG_CONFIG_HOME` redirects config/themes/logs away from the real `~/.config/ground-control` — always set it, since `load_colors()` writes the config as a side effect of reading it. When asserting bar geometry, strip markup with `rich.text.Text.from_markup(...).plain` before measuring: the powerline tips are single cells that a raw `len()` on the markup string will not count correctly.

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
gc --squeue                  # add the Slurm panel and pick jobs at startup

# Scripting / collectors
gc --once --json             # one snapshot, then exit
gc --once --check            # Nagios-style exit codes
gc --stream                  # one JSON snapshot per line, forever (see Job focus)

# Config and themes
gc config                    # open config in $EDITOR
gc config --reset            # reset to defaults
gc theme --list              # list themes
gc theme --monokai           # apply theme (requires restart)

# Tests
pytest tests                 # pure parsers, formatters, and headless pilot tests

# Build for distribution
python -m build              # produces dist/ via setuptools
```

No linter or formatter is configured. `gc --debug` surfaces exceptions instead of catching them.

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

`gc --stream` is the same snapshot repeated: `iter_snapshots()` primes once and then yields one compact JSON object per line, flushed immediately, so a reader can consume it with `readline`. It is the collector half of job focus (below) and is why a remote sample costs a line of JSON instead of a job step. Termination is deliberately over-determined because it usually runs inside somebody's allocation: SIGTERM/SIGINT/SIGHUP exit cleanly, a closed stdout exits cleanly, and `--stream-max-seconds` expires the process even if no signal ever arrives.

**Slurm** (`utils/slurm.py`, `widgets/slurm_jobs.py`): every command is best-effort — Slurm may be absent, `slurmctld` slow, a job gone between two calls — so nothing here raises to the caller; failures degrade to `None`/empty and the TUI keeps running. The parsers (`parse_squeue`, `parse_scontrol_job`, `parse_sstat`) are pure and unit-tested without a cluster. `SlurmMonitor` throttles its own polling (`min_interval`), so it is safe to call from every refresh tick.

`J` lists your **running** jobs only — a queued job holds no resources, so there is nothing to sample or focus on yet (`get_running_user_jobs`; `COMPLETING` is excluded too, since its cgroup is already being torn down). The picker offers two different things, because they answer different questions: `enter` lists the *checked* jobs in the `SlurmJobsWidget` panel (metadata, allocation, `sstat` usage), while `f` **focuses** the *highlighted* one.

**Job focus** — the important thing to understand is that `gc` normally runs on a **login node** while the job runs on a compute node, so job-scoped monitoring cannot be done by filtering local readings: psutil and NVML would describe the wrong machine, and a login node typically reports zero GPUs. Instead the collector is *moved into the job*: `JobFocusSampler` starts `srun --overlap --jobid=N <python> -m ground_control --stream` (`_stream_command`) and the login node only reads the NDJSON lines it writes. `--overlap` joins the *existing* allocation, so that process lands inside the job's cgroup and inherits its `CUDA_VISIBLE_DEVICES` — which means the CPU/memory/GPU scoping `SystemMetrics` already does for its own environment becomes exactly the scoping we want, with no extra filtering. `metrics_from_snapshot()` (`utils/snapshot.py`) is the inverse of `build_snapshot` and turns each line back into the `metrics_by_type` shape, so remote data flows through the same widgets *and* the same `evaluate_snapshot` alerting as local data.

Why streaming rather than one `--once` per sample: the expensive part was never the metrics, it was **creating a job step and booting an interpreter** — measured at 5.5s per sample on this cluster, against a dashboard that ticks once a second. A resident collector pays that once (first line in ~1.4s, then one line per `--interval`), and the login node's share of the work drops to `json.loads`. That is what makes focus usable from a loaded login node at all.

Details that are easy to get wrong:

- The stream is not assumed to be permanent. It ends when the remote lifetime cap (`--stream-max-seconds`, an hour) expires or the node hiccups, and the sampler quietly re-arms; a stream that produced samples and ended is **not** a failure and must not be counted as one. Sampling still happens off the UI thread, and the UI reads the last completed sample plus its **age**, shown in the panel title (`(starting…)`, `(stale Ns)`) rather than passing an old reading off as live. `_job_sample_stale_after()` derives that threshold from the sampler's cadence and mode, since the one-shot fallback is legitimately slower.
- `JobFocusSampler.stop()` must not block: it is called from the UI thread while the reader thread sits in `readline`, so terminating `srun` (which is what actually ends the remote step) is handed to a throwaway thread. Blocking there froze the whole app on unfocus.
- stderr is folded into stdout deliberately. A separate stderr pipe can fill and deadlock the remote process, every line is validated as JSON anyway, and srun's diagnostics are worth keeping — `diagnostics()` feeds them back into the "could not start" message, because "Access/permission denied" is fixable and "see Logs" is not.
- A remote `gc` too old to know `--stream` falls back to one-shot `--once` probing (`_probe_command`), but only when the failure *reads* like a rejected flag (`_looks_unsupported`). A step that could not be created is retried as a stream instead of being permanently downgraded.
- The panel *set* depends on the job's hardware, which is unknown until the first sample returns — hence `_layout_metrics()` and the rebuild in `_rebuild_when_sample_arrives()`. Only `border_title` is retitled with the job/node; `widget.title` is an identity key (disk panels are matched by it, failure streaks tracked by it) and renaming it breaks dispatch.
- GPU process pids belong to the **compute node**. `os.kill` on them locally would signal whatever unrelated login-node process holds that number, so the K/T/I buttons are disabled in focus mode *and* `ProcessRow._send_signal` refuses outright — a disabled button alone is not a safety guarantee.

The remote command is addressed as `sys.executable -m ground_control`, not `gc`: a non-interactive remote shell inherits none of the user's PATH, so the console script usually is not found. `PYTHONPATH` is passed via srun's own `--export` rather than an `env` prefix, because the remote PATH is the user's and a shadowing (or broken) `env` earlier on it is what would actually execute. Focus ends automatically if the job stops running (confirmed off-thread, since the check is a subprocess), and the extra detail fields the rebuild needs (`telemetry`, `meminfo`, per-GPU `processes`) are additive — an older remote `gc` degrades to fewer rows instead of raising.

**Slurm jobs panel** (`widgets/slurm_jobs.py`): one job is one row, buttons included — the same shape as the GPU process list, and laid out the same way (`format_job_line`, `JOB_COLUMNS`, columns dropped lowest-priority-first, always exactly `width` cells). Job id and state are never dropped; the `TIME` column offers a shorter form rather than being truncated mid-number. A second `dim` line per job (`format_job_detail`) carries the time-limit gauge and `sstat` usage — the gauge is drawn for **time** because that is the one quantity here with an unambiguous denominator and the one whose exhaustion kills the job, and it is omitted entirely for a job that has not started (an empty "0% used" would suggest it had). Bars go through the module-level `gauge_bar()` in `widgets/base.py`, which `MetricWidget.build_gauge_bar` now delegates to, so a non-`MetricWidget` panel draws with the same vocabulary.

Rows are updated **in place**; only a changed job *set* remounts them. A refresh tick that rebuilt the rows would drop keyboard focus and the half-armed Cancel button with it.

`F` on a row focuses that job; `C` cancels it, but **arms first** — one press turns the button red, a second within `JobRow.ARM_SECONDS` calls `scancel`, and it auto-disarms because an armed destructive button left sitting there is a trap. A stray click on a signal button costs one process; a stray click here throws away a queued allocation and everything the job had computed. `scancel_job()` is the one Slurm helper that reports failure instead of swallowing it (the user needs to know *whether their job is gone*), and it runs off the UI thread. Both row buttons set `active_effect_duration = 0`: Textual ignores a click while the press animation runs, which would otherwise swallow the second half of the confirmation.

**GPU support**: NVIDIA only, via nvitop for device enumeration and nvidia-ml-py for metrics. MiG devices are detected but utilization may be unavailable ("Usage UNAV").

**GPU telemetry**: `_collect_one_gpu` returns power, temperature, fan, SM clock vs max clock, memory-*bandwidth* utilization, encoder/decoder, performance state and clock-throttle reasons alongside the core util/VRAM figures. Every one of these is best-effort — `SystemMetrics._num()` normalizes NVML's `NA` sentinel to `None` (consumer cards report no power limit, Grace-Blackwell no discrete memory clock), and both the widget and the JSON simply omit or null what is missing rather than printing `N/A` noise.

Throttle reasons come from NVML directly (`_throttle_reasons`), since nvitop exposes no wrapper; the getter is looked up under both the old `...ClocksThrottleReasons` and new `...ClocksEventReasons` spellings. `_THROTTLE_BITS` marks thermal/power-brake/HW slowdown as **severe** (throughput is being lost now) and power-cap/sync-boost as ordinary governed behaviour; `GpuIdle` is deliberately excluded, as an idle GPU is not a throttled one. An empty reason list means "not throttled *or* not reported" — never infer health from it alone.

`GPUWidget.create_telemetry_line()` renders these under the split bar, dropping segments from the least-important end so a narrow panel keeps the throttle state and power draw; the whole row is hidden below `TELEMETRY_MIN_PANEL_HEIGHT` so the plot keeps the space. Two readings are worth understanding: **power as a percent of its limit** and **memory-bandwidth percent** — a card at 100% "utilization" drawing 15% of its power cap with near-zero bandwidth is an input-starved training loop, not a busy GPU. NVML's `gpu_utilization` only means "a kernel was resident", not that the SMs were doing work.

Per-device temperature also feeds `evaluate_snapshot`, so a hot card lights up its own panel rather than only the shared temperature panel.

**GPU process rows**: one process is one row, buttons included. `format_process_line()` lays out fixed-width columns (`PROC_COLUMNS`) so values align down the list, giving the command whatever is left; when the panel narrows it drops columns lowest-priority-first (HOST → CPU → USER), then squeezes PID/GPU MEM, which are never dropped outright — a row you cannot identify and whose memory cost you cannot see is not worth a row. It always returns exactly `width` cells, since it sits beside a fixed-width button strip. The signal buttons are real `Button`s forced to one cell (`border: none; padding: 0; height: 1`) so they keep hover, focus and keyboard activation; single-letter labels K/T/I are explained by the header row and by per-button tooltips naming the signal and PID.

**Panel proportions** (`utils/grid_sizing.py`, `widgets/resizable_grid.py`): panels are cells of one `ResizableGrid`, so their sizes are `fr` weight *lists per axis* — one weight per row and one per column, never one per panel. Two panels sharing a grid row therefore always share its height; that is grid layout, not a missing feature. `ctrl+←→`/`ctrl+↑↓` nudge the focused panel's column/row (`nudge_weight` touches only that track, so the others rescale proportionally — "this row is now 1.4× its share"), while dragging a shared border runs `drag_weights`, which *conserves the pair's combined weight* so only the two panels either side move. `z` resets. Dragging a corner is just a drag with a column boundary and a row boundary at once.

Details worth knowing before touching this:

- The grid has no gutter, so the boundary between two cells **is** the two adjacent panel borders, and the grab zone is exactly those two columns. `MouseDown` bubbles up from the panel to the grid, which then does capture-and-release-on-mouse-up exactly as Textual's own `ScrollBar` does — there is no splitter widget in Textual to inherit from.
- `_cell_bounds()` reads track geometry from the panels' **actual regions** rather than re-deriving the layout arithmetic, so it cannot disagree with the screen. This relies on a panel filling its cell (`MetricWidget` sets `height: 100%`); an auto-height panel would sit inside its cell and misreport where the boundary is.
- Cell index order follows `GridLayout.arrange`: *displayed* children fill cells in order, so a hidden panel shifts every panel after it and occupies no cell. `_focused_cell()` mirrors that, and walks up from `app.focused` because focus is usually on something inside a panel (a signal button, a job row).
- `drag_weights` aims **half a cell past** the target (`_BOUNDARY_BIAS`). Textual resolves an `fr` template by flooring accumulated exact `Fraction`s, and a weight like `1.2` is a hair under `6/5` in binary — without the bias, asking for 48 cells yields 47 and the border trails the cursor.
- Weights are clamped (`MIN_WEIGHT`) because an `fr` template has no minimum-cell concept: unclamped, holding the shrink key drives a panel to zero width. Drags clamp in cells instead (`MIN_CELL_WIDTH`/`MIN_CELL_HEIGHT`), and split evenly rather than refusing when the pair is already below two minimums.
- Persisted as `grid_weights` **per layout mode**, since a mode change re-tracks the grid entirely (one row horizontal, one column vertical) and weights carry no meaning across modes. Lengths are *not* validated at load: the track counts depend on how many panels the machine has, which is unknown that early, so `normalize_weights` pads/truncates against the real counts in `set_tracks`. Padding rather than discarding is deliberate — a hidden panel changes the count, and the saved weights are still the user's intent for the tracks that remain.

**Widget failure handling**: a panel is disabled (`_failed_widget_titles`, `display:none`) only after `WIDGET_FAILURE_LIMIT` *consecutive* failed renders, tracked in `_widget_failure_streaks` and cleared by any successful tick. Metric sources legitimately blink — a process exiting between `process_iter()` and `cpu_num()`, a GPU vanishing for a tick — and a single transient exception used to hide a panel for the rest of the session. When touching psutil in a widget, catch `psutil.Error` (covers `NoSuchProcess`/`ZombieProcess`/`AccessDenied`), not just `PermissionError`.

**Bar style**: every horizontal bar in the app goes through `MetricWidget.build_gauge_bar(width, fraction, color, grow, track_color)`. One vocabulary: `█` body, a powerline tip (`` / ``) on the growing end, `─` for the unfilled track. The tip *replaces* the last filled cell rather than being appended, so the result is always exactly `width` cells and a full bar still reads as full. `grow="left"` mirrors it for halves that meet at a centre separator (memory's RAM half, network's download half). `build_split_bar` (GPU, network), `create_gradient_bar`, and the memory/disk/temperature bars are all thin wrappers over it — add new bars the same way rather than hand-rolling block strings.

Metric panels do **not** use plotext legends: the bars underneath already name each series in its plot colour, so a legend would duplicate them while eating plot rows. It is also a correctness matter — plotext's legend renderer raises `IndexError` at some panel geometries, which previously disabled `MemoryWidget` outright.

**Testing**: `pytest tests` (pytest only, no pytest-asyncio — the pilot tests drive their own loop via a local `_run` helper that restores the thread's event loop afterwards, or the widget constructors in later modules fail). `tests/conftest.py` redirects `XDG_CONFIG_HOME` at *import* time, not in a fixture, because test modules import ground_control before fixtures run and `load_colors()` writes the config as a side effect of reading it. Headless UI testing works via Textual's `app.run_test()` pilot. When asserting bar geometry, strip markup with `rich.text.Text.from_markup(...).plain` before measuring: the powerline tips are single cells that a raw `len()` on the markup string will not count correctly.

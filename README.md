# 🚀 Ground Control - The Ultimate Terminal System Monitor

![Ground Control](https://github.com/alberto-rota/ground-control/blob/main/assets/hero.gif?raw=true)

[![PyPI version](https://badge.fury.io/py/groundcontrol.svg)](https://badge.fury.io/py/groundcontrol)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.6+](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)

**Ground Control** is a sleek, real-time terminal-based system monitor built with [Textual](https://textual.textualize.io/), [Plotext](https://github.com/piccolomo/plotext) and the [nvitop API](https://terminaltrove.com/nvitop/). It provides a powerful, aesthetic, customizable interface for tracking CPU, memory, disk, network, GPU usage, and system temperatures — all in a visually appealing and responsive TUI.

**Ground Control** works optimally with [TMUX](https://github.com/tmux/tmux/wiki), install it [here](https://github.com/tmux/tmux/wiki/Installing)!

We tested **Ground Control** with the *Windows Terminal* app, *Tabby* and the *VSCode integrated terminal*. Monospaced fonts are preferred — a [Nerd Font](https://www.nerdfonts.com/) gets you the powerline tips on every gauge bar.

## 🌟 Features

### 📊 Real-Time System Monitoring
- **CPU Usage**: Per-core heatmap with load, pressure-stall, frequency and context-switch telemetry.
- **Memory Utilization**: RAM and SWAP on one dual plot, with a centre bar showing used vs free.
- **Temperature Monitoring**: Per-sensor traces with caution and warning lines.
- **Disk I/O**: Read and write throughput per mount, plus capacity.
- **Network Traffic**: Live upload/download rates and a split bar.
- **GPU Metrics**: NVIDIA utilization, VRAM, power, clocks, throttle reasons, and per-process rows.

### 🎨 20 Built-In Themes
- Cycle them live with a single key, or edit any of the 56 colour keys in a **built-in colour picker** with a live preview.
- Save your palette as a custom theme.

### 🔔 Threshold Alerts
- Panels that breach a threshold get a coloured border and a `▲` / `■` marker.
- Configurable per metric, and shared with the scripting mode.

### 🤖 Scriptable
- `gc --once` prints one sample and exits — human-readable, `--json`, or `--check` for Nagios-style exit codes.

### 🖥️ Responsive Layout
- **Automatic resizing** to fit your terminal window.
- **Multiple layouts**: Grid, Horizontal, and Vertical.
- **Customizable widgets**: Show only the metrics you need with granular control.

---

## 🛠️ Installation

### 🔹 Install via PyPI
```sh
pip install ground-control-tui
```

### 🔹 Install from Source
```sh
git clone https://github.com/alberto-rota/ground-control
cd ground-control
pip install -e .
```

---

## 🚀 Getting Started

### 🔹 Run Ground Control
Once installed, simply launch Ground Control with:
```sh
groundcontrol
```
or
```sh
gc
```

### 🔹 Show Only What You Need
Any combination of widget flags restricts the dashboard to those panels:

```sh
gc --cpu --ram              # CPU and memory only
gc -g --gpu-index 0,1       # GPUs 0 and 1
gc --disk --net             # storage and network
gc --debug                  # surface exceptions instead of catching them
```

| Flag | Panel |
|------|-------|
| `-c`, `--cpu` | CPU |
| `-r`, `--ram` | Memory |
| `-d`, `--disk` | Disk (one panel per mount) |
| `-n`, `--net` | Network |
| `-t`, `--temp` | Temperature |
| `-g`, `--gpu` | GPU (all of them; `--gpu-index 0,1` to filter) |

### 🔹 Available Layouts

Press `g`, `h` or `v` to switch between the grid, a single row, and a single column — instantly, without restarting.

![Layouts](https://github.com/alberto-rota/ground-control/blob/main/assets/layouts.gif?raw=true)

**Grid** is the default and fits everything on a normal terminal. **Horizontal** suits a wide TMUX split.

**Vertical** is for a narrow one — give it the height and every panel still gets a real plot:

<img src="https://github.com/alberto-rota/ground-control/blob/main/assets/vertical.gif?raw=true" alt="Vertical layout" width="420">

---

## 🖥️ Widget Breakdown

Each panel in Ground Control represents a different system metric.

### 🔹 **CPU Usage**
- History plot of total load, with a **per-core heatmap** underneath — one cell per core, packed into as many rows as the panel height allows
- Telemetry line: average and peak core load, I/O wait, load average, pressure stall, current/max frequency, user vs system time, context switches, and running process count
- Three views, on `1` / `2` / `3` when the panel is focused: **All cores**, **Affinity** (just the cores this process may run on), and **My processes**

![CPU widget](https://github.com/alberto-rota/ground-control/blob/main/assets/cpu.gif?raw=true)

### 🔹 **Memory Utilization**
- Dual plot: RAM above the axis, SWAP below, both in GB
- Centre bar meeting in the middle, with used and free on each side
- Title shows total RAM and SWAP capacity

![Memory widget](https://github.com/alberto-rota/ground-control/blob/main/assets/memory.gif?raw=true)

### 🔹 **Disk I/O**
- Dual plot: read above the axis, write below, in MB/s
- Capacity bar showing used vs free
- One panel per mounted disk or partition; boot/EFI and `/snap` mounts are filtered out by default (configurable in Settings)

![Disk widget](https://github.com/alberto-rota/ground-control/blob/main/assets/disk.gif?raw=true)

### 🔹 **Network Traffic**
- Dual plot: upload above the axis, download below
- Split bar with the current rate on each side

![Network widget](https://github.com/alberto-rota/ground-control/blob/main/assets/network.gif?raw=true)

### 🔹 **Temperature Monitoring**
- Multi-line plot tracking each sensor over time, prioritising CPU, GPU and motherboard
- Caution and warning lines drawn across the plot
- Right-hand column lists every sensor, each bar in the same colour as its line in the plot above

![Temperature widget](https://github.com/alberto-rota/ground-control/blob/main/assets/temperature.gif?raw=true)

### 🔹 **GPU Metrics (NVIDIA Only)**
- Dual plot: utilization % above the axis, VRAM GB below
- Split bar, and a telemetry line with **power draw against its limit**, temperature, SM clock vs maximum, memory-bandwidth utilization, performance state and clock-throttle reasons
- A **Processes** tab (`2` when the panel is focused) listing what is on the card, with per-row buttons to send `SIGKILL` / `SIGTERM` / `SIGINT`

![GPU widget](https://github.com/alberto-rota/ground-control/blob/main/assets/gpu.gif?raw=true)

Two readings there are worth understanding. NVML's "utilization" only means *a kernel was resident* — not that the SMs did any work. A card at 100% utilization drawing 15% of its power cap with near-zero memory bandwidth is an input-starved training loop, not a busy GPU.

Everything on that line is best-effort: consumer cards report no power limit, Grace-Blackwell reports no discrete memory clock. Missing values are omitted rather than printed as noise.

---

## 🎨 Themes

20 themes ship with Ground Control. Press `t` to cycle through them live — plots, bars, borders and the header all repaint:

![Themes](https://github.com/alberto-rota/ground-control/blob/main/assets/themes.gif?raw=true)

Or pick one from the command line:

```sh
gc theme --list             # list everything available
gc theme --tokyo-night      # apply by flag
gc theme gruvbox            # ...or by name
```

Built-ins: `ayu-light`, `catppuccin-latte`, `catppuccin-mocha`, `classic`, `dracula`, `everforest`, `github-light`, `gruvbox`, `gruvbox-light`, `kanagawa`, `light`, `light-pastel`, `monokai`, `neon`, `nord`, `one-dark`, `rose-pine`, `solarized-dark`, `solarized-light`, `tokyo-night`.

### 🔹 **Editing colours**

Every theme is 56 colour keys, and all of them are editable from the Settings tab. Highlight one and press `Enter` for the colour picker: a hue × shade swatch grid, H/S/V steppers, and a **live preview built from a real metric widget** being fed real data — so you see the colour where you will actually use it.

![Colour picker](https://github.com/alberto-rota/ground-control/blob/main/assets/colorpicker.gif?raw=true)

Changes apply immediately; `Ctrl+Z` reverts the key to what it was when the picker opened. Save the result as your own theme:

```sh
gc theme --save-as my-theme
gc theme --delete my-theme
```

Custom themes live in `~/.config/ground-control/themes/` and appear alongside the built-ins everywhere.

---

## 🔔 Threshold Alerts

Any panel that crosses a threshold takes on the alert colour and gains a marker in its title — `▲` for a warning, `■` for critical. The marker matters as much as the colour: it survives a monochrome terminal, where colour alone would not.

![Alerts](https://github.com/alberto-rota/ground-control/blob/main/assets/alerts.gif?raw=true)

Press `a` to toggle alerting at runtime. A breach stays visible for a while after recovery (`alert_sticky_seconds`), so a spike that happened while you were on another tab is not missed.

Direction is a property of the metric, not something you configure: "CPU above 90%" and "disk free below 2 GB" are both written as plain numbers. Two defaults are deliberate — **GPU utilization alerting is off**, because on a GPU box a pegged card is the goal rather than an incident, and **network rate alerting is off**, because a 1 Gb link and a 100 Gb link share no useful default.

Edit them under `thresholds` in the config file:

```json
"thresholds": {
    "cpu_percent":     { "warn": 85.0, "crit": 95.0, "enabled": true },
    "disk_free_gb":    { "warn": 10.0, "crit": 2.0,  "enabled": true },
    "gpu_util_percent":{ "warn": 95.0, "crit": 99.0, "enabled": false }
}
```

---

## 🤖 Scripting: `gc --once`

Ground Control can take a single sample and exit, entirely outside the TUI — safe for cron, CI and health checks.

![Snapshot mode](https://github.com/alberto-rota/ground-control/blob/main/assets/snapshot.gif?raw=true)

*(That recording runs with deliberately low thresholds, so every output has something to report. With the shipped defaults a healthy machine prints `[OK]`, an empty `alerts` array, and exit `0`.)*

```sh
gc --once                    # one human-readable sample
gc --once --json             # ...as JSON, with a versioned schema
gc --once --check            # ...as an exit code
gc --once --interval 2       # widen the gap used to compute rate metrics
gc --once --all-mounts       # include mounts normally hidden
```

`--check` follows the Nagios convention, so it drops into an existing monitoring setup unchanged:

| Exit code | Meaning |
|-----------|---------|
| `0` | everything within thresholds |
| `1` | at least one warning |
| `2` | at least one critical breach |
| `3` | the collector itself failed |

Errors go to stderr, so stdout is either valid JSON or empty — piping into `jq` never sees a half-written document.

---

## 🛠️ Configuring Ground Control

### 🔹 **The Settings tab**
Press `s` for Settings. It opens with the widget list focused, so hiding a panel is two keys:

![Settings](https://github.com/alberto-rota/ground-control/blob/main/assets/settings.gif?raw=true)

From there you can toggle individual panels, switch layout, set the refresh rate (500 ms to 1 minute) and history window (30 s to 10 minutes), choose which mounts the disk panels ignore, pick a theme, and edit colours.

### 🔹 **Persistent Configuration**
Everything is saved to `~/.config/ground-control/config.json` when you quit, and restored next launch. Edit it directly with:

```sh
gc config              # open in $EDITOR
gc config --path       # print the path
gc config --reset      # back to defaults
```

### 🔹 **Keyboard Shortcuts**

Press `?` in the app for this list at any time.

| Key | Action |
|-----|--------|
| `d` / `s` / `l` | Dashboard / Settings / Logs |
| `g` / `h` / `v` | Grid / Horizontal / Vertical layout |
| `space` | Cycle layout |
| `r` | Refresh now |
| `+` / `-` | Faster / slower refresh |
| `t` | Cycle theme |
| `a` | Toggle threshold alerts |
| `]` / `[` / `tab` | Focus next / previous panel |
| `x` | Hide the focused panel |
| `?` | Keyboard shortcuts |
| `q` | Quit |

When a panel is focused:

| Key | Action |
|-----|--------|
| `1` `2` `3` | CPU: All cores / Affinity / My processes |
| `1` / `2` | GPU: Plot / Processes |

In Settings → Colors:

| Key | Action |
|-----|--------|
| `enter` | Open the colour picker |
| `ctrl+z` | Revert the colour being edited |

---

## ⛔ Current Known Limitations/Bugs
- In heavy-duty HPC systems, with multiple disks, cores and GPUs to be monitored, metric collection and plotting might get bottlenecked and groundcontrol might run slow. Consider **directly editing the config file with a text editor** to avoid this.
- GPU usage is monitored only for CUDA-enabled hardware. Ground Control detects MiG devices but in some cases it cannot detect their utilization. You'll see *Usage UNAV* in the GPU Widget if this is the case.
- Temperature monitoring availability depends on system sensors and may not be available on all platforms.
- An empty throttle-reason list on the GPU panel means "not throttled *or* not reported" — never infer health from it alone.

## 🎬 Regenerating the demo GIFs
Every animation in this README is scripted. See [`demo/`](demo/) — `demo/record.sh` renders all of them with [VHS](https://github.com/charmbracelet/vhs), driving the app against a synthetic load generator so the plots have something to show.

## 👨‍💻 Contributing
Pull requests and contributions are welcome! To contribute:
1. Fork the repo.
2. Create a feature branch.
3. Submit a PR with your changes.

Visit the [Issue Section](https://github.com/alberto-rota/ground-control/issues) to start!

## 📜 License
This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.

## 📧 Author
**Alberto Rota**
📩 Email: alberto_rota@outlook.com
🐙 GitHub: [@alberto-rota](https://github.com/alberto-rota)

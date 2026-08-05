# 🚀 Ground Control - The Ultimate Terminal System Monitor

![Ground Control Banner](https://github.com/alberto-rota/ground-control/blob/main/assets/dashboard.gif?raw=true)

[![PyPI version](https://badge.fury.io/py/groundcontrol.svg)](https://badge.fury.io/py/groundcontrol)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.6+](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)

**Ground Control** is a sleek, real-time terminal-based system monitor built with [Textual](https://textual.textualize.io/), [Plotext](https://github.com/piccolomo/plotext) and the [nvitop API](https://terminaltrove.com/nvitop/). It provides a powerful, aesthetic, customizable interface for tracking CPU, memory, disk, network, GPU usage, and system temperatures — all in a visually appealing and responsive TUI.

**Ground Control** works optimally with [TMUX](https://github.com/tmux/tmux/wiki), install it [here](https://github.com/tmux/tmux/wiki/Installing)!

We tested **Ground Control** with the *Windows Terminal* app, *Tabby* and the *VSCode integrated terminal*. Monospaced fonts are preferred.  

## 🌟 Features

### 📊 Real-Time System Monitoring
- **CPU Usage**: Per-core load tracking with frequency stats and detailed performance metrics.
- **Memory Utilization**: RAM usage with dynamic visualization and memory statistics.
- **Temperature Monitoring**: Real-time system temperature tracking with thermal status indicators.
- **Disk I/O**: Monitor read/write speeds and disk usage with comprehensive storage metrics.
- **Network Traffic**: Live upload/download speeds with bandwidth utilization graphs.
- **GPU Metrics**: Real-time NVIDIA GPU monitoring with utilization and memory tracking (if available).

### 🖥️ Responsive Layout
- **Automatic resizing** to fit your terminal window.
- **Multiple layouts**: Grid, Horizontal, and Vertical.
- **Customizable widgets**: Show only the metrics you need with granular control.

### 🎛️ Interactive Controls
- **Keyboard shortcuts** for quick navigation.
- **Toggle between different layouts** instantly.
- **Customize displayed metrics** via a built-in selection panel with individual widget control.

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

### 🔹 Available Layouts

### Grid Layout
A structured layout displaying all widgets neatly in a grid. When you first launch **Ground Control**, it will show this layout. The recording below is `gc` running inside a Slurm job with four GPUs.
![Grid Layout](https://github.com/alberto-rota/ground-control/blob/main/assets/dashboard.gif?raw=true)

### Horizontal Layout
All widgets aligned in a single row. If you like working with wide shell spaces, split a TMUX session horizontally and use this layout!
![Horizontal Layout](https://github.com/alberto-rota/ground-control/blob/main/assets/horizontal.gif?raw=true)

#### Vertical Layout
A column-based layout, ideal for narrow shell spaces. If you like working with tall shell spaces, split a TMUX session verticall and use this layout!
![Vertical Layout](https://github.com/alberto-rota/ground-control/blob/main/assets/vertical.gif?raw=true)

### 🖥️ Widget Breakdown
Each panel in Ground Control represents a different system metric:

### 🔹 **CPU Usage**
- Shows per-core CPU usage as horizontal bars (0-100%)
- Displays each core's utilization in a compact bar chart format
- Updates in real-time with color-coded bars showing load intensity

![CPU widget](https://github.com/alberto-rota/ground-control/blob/main/assets/cpu.gif?raw=true)

### 🔹 **Memory Utilization**
- Dual plot showing RAM (positive axis) and SWAP (negative axis) usage in GB
- Center bar with color-coded sections showing used/free RAM and SWAP
- Title displays total RAM and SWAP capacity in GB

![Memory widget](https://github.com/alberto-rota/ground-control/blob/main/assets/memory.gif?raw=true)

### 🔹 **Temperature Monitoring**
- Multi-line plot tracking temperature over time in °C for up to 4 key sensors
- Color-coded warning thresholds at 60°C (orange) and 80°C (red)
- Right panel shows current temperatures with dynamic color bars based on heat levels
- Prioritizes CPU, GPU, and motherboard sensors

![Temperature widget](https://github.com/alberto-rota/ground-control/blob/main/assets/temperature.gif?raw=true)

### 🔹 **Disk I/O**
- Dual plot showing read (positive axis) and write (negative axis) speeds for each mounted disk/partition
- Shows disk usage with color-coded bar for used/free space in GB
- Updates in real-time with throughput history
- Each mounted disk/partition gets its own widget (except boot/EFI partitions)
- Automatically detects and displays all mounted disks and partitions

![Disk widget](https://github.com/alberto-rota/ground-control/blob/main/assets/disk.gif?raw=true)

### 🔹 **Network Traffic**
- Dual plot showing upload (positive axis) and download (negative axis) speeds
- Shows current transfer rates with color-coded indicators
- Tracks cumulative data transfer amounts

![Network widget](https://github.com/alberto-rota/ground-control/blob/main/assets/network.gif?raw=true)

### 🔹 **GPU Metrics (NVIDIA Only)**
- Dual plot showing GPU usage % (positive axis) and memory usage GB (negative axis)
- Center bar displays current GPU memory usage (GB) and utilization (%)
- A telemetry line underneath reports power draw against its limit, temperature, SM clock, memory-bandwidth utilization and any clock-throttle reason
- Shows "Usage UNAV" when GPU utilization cannot be detected

![GPU widget](https://github.com/alberto-rota/ground-control/blob/main/assets/gpu.gif?raw=true)

### 🔹 **Slurm Jobs**
Shown automatically wherever `squeue` is on `PATH` — no flag needed (`gc --slurm` shows *only* this panel, like the other widget filters).

- Lists **all of your jobs**, running and pending, one row each: id, state, elapsed/limit time, node, CPUs, memory, GPUs, partition and name. Narrow panels drop the least important columns instead of wrapping.
- A second line per job carries the time-limit gauge (how close the job is to being killed) and live `sstat` usage for running jobs.
- Three buttons per row:
  - **F** — *focus*: point every panel at that job. Ground Control starts a collector **inside the job's allocation**, so CPU, memory, GPU and process panels show the compute node's view of the job instead of the login node's. Focus ends by itself when the job does, naming its final state (`COMPLETED`, `FAILED`, `TIMEOUT`, `CANCELLED`).
  - **O** — *output*: read the job's `stdout`/`stderr`, tailing the last 64 KB and following it live. ANSI colours in the log are rendered, not printed.
  - **C** — *cancel*: `scancel` the job. Press once to arm (the button turns red), again within four seconds to confirm.

`F` from anywhere opens a list of your running jobs: arrow to one and press **enter** to focus it, `u` to stop focusing.

## 🛠️ Configuring Ground Control
Ground Control offers extensive customization options to tailor your monitoring experience. You might not want to see all the widgets all at once, or you may want to focus on specific system metrics.

### 🔹 **Settings Tab**
The settings panel can be accessed by pressing `s` or clicking the `Settings` tab. It lets you:

- **Toggle widgets**: Enable/disable individual widgets (CPU, Memory, Temperature, each Disk, Network, each GPU) by clicking their checkboxes
- **Refresh rate**: Choose update intervals from 500ms to 1 minute
- **History window**: Set the data history length from 30 seconds to 10 minutes
- **Hide mounts**: Keep uninteresting filesystems out of the dashboard with the disk ignore prefixes
- **Pick a theme**: Choose one of the built-in palettes, edit any individual color, and save the result as your own theme
- **Save preferences**: All settings are automatically saved to `~/.config/ground-control/config.json`

The config file stores:
- Widget visibility settings for each widget
- Current layout (grid/horizontal/vertical) 
- Refresh rate in seconds
- History size in seconds

### 🔹 **Layout Management**
You can switch between different layouts instantly:
- Press `g` or click `Grid Layout` for the structured grid view
- Press `h` or click `Horizontal Layout` for single-row alignment
- Press `v` or click `Vertical Layout` for column-based display

![Settings tab](https://github.com/alberto-rota/ground-control/blob/main/assets/settings.gif?raw=true)

### 🔹 **Persistent Configuration**
All your customizations are automatically saved when you quit Ground Control. When you launch it again, you'll see the same layout and widget configuration you previously selected, ensuring a consistent monitoring experience.

### 🔹 **Keyboard Shortcuts**
All available keyboard shortcuts are listed here:
| Key  | Action |
|------|--------|
| `h`  | Switch to Horizontal Layout |
| `v`  | Switch to Vertical Layout |
| `g`  | Switch to Grid Layout |
| `d`  | Show the Dashboard |
| `s`  | Show the Settings tab |
| `l`  | Show the Logs tab |
| `r`  | Refresh now |
| `+` / `-` | Refresh faster / slower |
| `F`  | Focus a Slurm job (arrow + enter) / return to this host |
| `?`  | List every shortcut, including the ones not in the footer |
| `q`  | Quit Ground Control |

The footer of the app always shows the keys available in the current context, and `?` opens the full list.

---

**Ground Control** saves user preferences in a configuration file located at:
`
~/.config/ground-control/config.json
`.
Modify this file in your default text editor with
```sh
groundcontrol config
```
or 

```sh
gc config
```

## ⛔ Current Known Limitations/Bugs
- In heavy-duty HPC systems, with multiple disks, cores and GPUs to be monitored, metric collection and plotting might get bottlenecked and groundcontrol might run slow. Consider **directly editing the config file with a text editor** to avoid 
- GPU usage is monitored only for CUDA-enabled hardware. Ground Control detects MiG devices but in some cases it cannot detect their utilization. You'll see *Usage UNAV* in the GPU Widget if this is the case
- Temperature monitoring availability depends on system sensors and may not be available on all platforms

## 👨‍💻 Contributing
Pull requests and contributions are welcome! To contribute:
1. Fork the repo.
2. Create a feature branch.
3. Submit a PR with your changes.

Visit the [Issue Section](https://github.com/alberto-rota/ground-control/issues) to start!

Every animation in this README is generated from a [vhs](https://github.com/charmbracelet/vhs) tape in [`tapes/`](tapes) — one tape per asset, re-recordable with `vhs tapes/<name>.tape`. See [`tapes/README.md`](tapes/README.md) if you change the UI and need to refresh them.

## 📜 License
This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.

## 📧 Author
**Alberto Rota**  
📩 Email: alberto_rota@outlook.com  
🐙 GitHub: [@alberto-rota](https://github.com/alberto-rota)

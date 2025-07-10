# 🚀 Ground Control - The Ultimate Terminal System Monitor

![Ground Control Banner](https://github.com/alberto-rota/ground-control/blob/main/assets/horiz.png?raw=true)

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
A structured layout displaying all widgets neatly in a grid. When you first launch **Ground Control**, it will show this layout.
![Grid Layout](https://github.com/alberto-rota/ground-control/blob/main/assets/grid.png?raw=true)

### Horizontal Layout
All widgets aligned in a single row. If you like working with wide shell spaces, split a TMUX session horizontally and use this layout!
![Horizontal Layout](https://github.com/alberto-rota/ground-control/blob/main/assets/horiz.png?raw=true)

#### Vertical Layout
A column-based layout, ideal for narrow shell spaces. If you like working with tall shell spaces, split a TMUX session verticall and use this layout!
![Vertical Layout](https://github.com/alberto-rota/ground-control/blob/main/assets/tmux.png?raw=true)

### 🖥️ Widget Breakdown
Each panel in Ground Control represents a different system metric:

### 🔹 **CPU Usage**
- Shows per-core CPU usage as horizontal bars (0-100%)
- Displays each core's utilization in a compact bar chart format
- Updates in real-time with color-coded bars showing load intensity

<img src="https://github.com/alberto-rota/ground-control/blob/main/assets/cpus.png?raw=true" alt="CPU_widget" width="600">

### 🔹 **Memory Utilization**
- Dual plot showing RAM (positive axis) and SWAP (negative axis) usage in GB
- Center bar with color-coded sections showing used/free RAM and SWAP
- Title displays total RAM and SWAP capacity in GB

<img src="https://github.com/alberto-rota/ground-control/blob/main/assets/ram.png?raw=true" alt="RAM_widget" width="600">

### 🔹 **Temperature Monitoring**
- Multi-line plot tracking temperature over time in °C for up to 4 key sensors
- Color-coded warning thresholds at 60°C (orange) and 80°C (red)
- Right panel shows current temperatures with dynamic color bars based on heat levels
- Prioritizes CPU, GPU, and motherboard sensors

<img src="https://github.com/alberto-rota/ground-control/blob/main/assets/temperature.png?raw=true" alt="Temperature_widget" width="600">

### 🔹 **Disk I/O**
- Dual plot showing read (positive axis) and write (negative axis) speeds for each mounted disk/partition
- Shows disk usage with color-coded bar for used/free space in GB
- Updates in real-time with throughput history
- Each mounted disk/partition gets its own widget (except boot/EFI partitions)
- Automatically detects and displays all mounted disks and partitions

<img src="https://github.com/alberto-rota/ground-control/blob/main/assets/disk.png?raw=true" alt="Disk_widget" width="600">

### 🔹 **Network Traffic**
- Dual plot showing upload (positive axis) and download (negative axis) speeds
- Shows current transfer rates with color-coded indicators
- Tracks cumulative data transfer amounts

<img src="https://github.com/alberto-rota/ground-control/blob/main/assets/network.png?raw=true" alt="Network_widget" width="600">

### 🔹 **GPU Metrics (NVIDIA Only)**
- Dual plot showing GPU usage % (positive axis) and memory usage GB (negative axis)
- Center bar displays current GPU memory usage (GB) and utilization (%)
- Shows "Usage UNAV" when GPU utilization cannot be detected

<img src="https://github.com/alberto-rota/ground-control/blob/main/assets/gpu.png?raw=true" alt="GPU_widget" width="600">

## 🛠️ Configuring Ground Control
Ground Control offers extensive customization options to tailor your monitoring experience. You might not want to see all the widgets all at once, or you may want to focus on specific system metrics.

### 🔹 **Widget Selection Panel**
The configuration panel can be accessed by pressing `c` or clicking the `Configure` button. This opens a panel that allows you to:

- **Toggle widgets**: Enable/disable individual widgets (CPU, Memory, Temperature, Disk, Network, GPU) by clicking their checkboxes
- **Refresh rate**: Choose update intervals from 500ms to 1 minute using the refresh rate buttons
- **History size**: Set the data history length from 30 seconds to 10 minutes using the history buttons
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

![Config_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/config.png?raw=true)

### 🔹 **Persistent Configuration**
All your customizations are automatically saved when you quit Ground Control. When you launch it again, you'll see the same layout and widget configuration you previously selected, ensuring a consistent monitoring experience.

### 🔹 **Keyboard Shortcuts**
All available keyboard shortcuts are listed here:
| Key  | Action |
|------|--------|
| `h`  | Switch to Horizontal Layout |
| `v`  | Switch to Vertical Layout |
| `g`  | Switch to Grid Layout |
| `c`  | Show/Hide the configuration panel |
| `q`  | Quit Ground Control |

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

## 📜 License
This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.

## 📧 Author
**Alberto Rota**  
📩 Email: alberto_rota@outlook.com  
🐙 GitHub: [@alberto-rota](https://github.com/alberto-rota)

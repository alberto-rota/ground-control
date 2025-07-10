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
pip install groundcontrol
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

Or run as a Python module:
```sh
python -m ground_control
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
- Shows real-time per-core CPU usage with detailed performance metrics.
- Displays CPU frequency information and load averages.
- Visual representation of CPU utilization across all cores.

![CPU_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/cpus.png?raw=true)

### 🔹 **Memory Utilization**
- Comprehensive RAM usage monitoring with detailed memory statistics.
- Visual progress bars showing memory consumption.
- Available, used, and cached memory information.

![RAM_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/ram.png?raw=true)

### 🔹 **Temperature Monitoring**
- Real-time system temperature tracking for CPU and other sensors.
- Temperature trend visualization with thermal status indicators.
- Helps monitor system thermal performance and potential overheating.

![Temperature_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/temperature.png?raw=true)

### 🔹 **Disk I/O**
- Monitors read/write speeds with real-time throughput graphs.
- Displays disk usage and available storage space.
- Comprehensive storage metrics in an easy-to-read format.

![Disk_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/disk.png?raw=true)

### 🔹 **Network Traffic**
- Tracks real-time upload/download speeds with bandwidth visualization.
- Network activity graphs showing traffic patterns.
- Cumulative data transfer statistics.

![Network_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/network.png?raw=true)

### 🔹 **GPU Metrics (NVIDIA Only)**
- Displays GPU utilization and memory usage with detailed performance metrics.
- Supports multiple GPUs with live tracking and temperature monitoring.
- Power consumption and clock frequency information.

![GPU_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/gpu.png?raw=true)

## 🛠️ Configuring Ground Control
Ground Control offers extensive customization options to tailor your monitoring experience. You might not want to see all the widgets all at once, or you may want to focus on specific system metrics.

### 🔹 **Widget Selection Panel**
To access the configuration panel, press `c` or click the `Configure` button. This will open a comprehensive selection panel where you can:

- **Toggle individual widgets** on/off (CPU, Memory, Temperature, Disk, Network, GPU)
- **Customize widget arrangement** based on your monitoring needs
- **Preview changes** in real-time before applying them
- **Reset to default** configuration if needed

Press `c` again to hide the configuration panel and return to monitoring.

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

## ⛔ Current Known Limitations/Bugs
- GPU usage is monitored only for CUDA-enabled hardware. Ground Control detects MiG devices but in some cases it cannot detect their utilization. You'll see *Usage UNAV* in the GPU Widget if this is the case
- Disk I/O is currently reported from `psutil.disk_io_counters()` and `psutil.disk_usage('/')`. This measurements do not account for partitions / unmounted disks / more-than-disk configuration. See [Issue #4](https://github.com/alberto-rota/ground-control/issues/4)
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
📩 Email: alberto1.rota@polimi.it  
🐙 GitHub: [@alberto-rota](https://github.com/alberto-rota)

## 🚀 Stay Updated
For the latest features and updates, visit the [GitHub repository](https://github.com/alberto-rota/ground-control).

# 🚀 Ground Control - The Ultimate Terminal System Monitor

![Ground Control Banner](https://github.com/alberto-rota/ground-control/blob/main/assets/horiz.png?raw=true)

[![PyPI version](https://badge.fury.io/py/ground-control-tui.svg)](https://badge.fury.io/py/ground-control-tui)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.6+](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)

**Ground Control** is a sleek, real-time terminal-based system monitor built with [Textual](https://textual.textualize.io/), [Plotext](https://github.com/piccolomo/plotext) and the [nvitop API](https://terminaltrove.com/nvitop/). It provides a powerful, aesthetic, customizable interface for tracking CPU, memory, disk, network, and GPU usage — all in a visually appealing and responsive TUI.

**Ground Control** works optimally with [TMUX](https://github.com/tmux/tmux/wiki), install it [here](https://github.com/tmux/tmux/wiki/Installing)!
## 🌟 Features

### 📊 Real-Time System Monitoring
- **CPU Usage**: Per-core load tracking with frequency stats.
- **Memory Utilization**: RAM usage with dynamic visualization.
- **Disk I/O**: Monitor read/write speeds and disk usage.
- **Network Traffic**: Live upload/download speeds.
- **GPU Metrics**: Real-time NVIDIA GPU monitoring (if available).

### 🖥️ Responsive Layout
- **Automatic resizing** to fit your terminal window.
- **Multiple layouts**: Grid, Horizontal, and Vertical.
- **Customizable widgets**: Show only the metrics you need.

### 🎛️ Interactive Controls
- **Keyboard shortcuts** for quick navigation.
- **Toggle between different layouts** instantly.
- **Customize displayed metrics** via a built-in selection panel.

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

### 🔹 **CPU & Memory**
- Shows real-time per-core CPU usage.
- Displays RAM usage with a visual bar.

![CPU_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/cpus.png?raw=true)

### 🔹 **Disk I/O**
- Monitors read/write speeds.
- Displays disk usage in an easy-to-read format.

![CPU_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/disk.png?raw=true)


### 🔹 **Network Traffic**
- Tracks real-time upload/download speeds.
- Visualizes network activity.

![CPU_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/network.png?raw=true)


### 🔹 **GPU Metrics (NVIDIA Only)**
- Displays GPU utilization and memory usage.
- Supports multiple GPUs with live tracking.

![CPU_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/gpu.png?raw=true)


## 🛠️ Configuring Ground Control
You might not want to see all the widgets all at once. To select which widgets you want Ground Control to show, press `c` or the `Configure` button. You'll see a selection panel that yuu can use to toggle the widgets shown. 
Press `c` again to hide the configuration panel

You can switch to a grid, horizontal or verical layout with `g`, `h` or `v` or use the `Grid Layout`, `Horizontal Layout` or `Vertical Layout` buttons.


![Config_widget](https://github.com/alberto-rota/ground-control/blob/main/assets/config.png?raw=true)

This configurations are saved when you quit Ground Control, so when you launch it again you will see the same layout!

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

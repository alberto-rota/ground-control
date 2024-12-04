# Ground Control 🚀

[![PyPI version](https://badge.fury.io/py/ground-control-tui.svg)](https://badge.fury.io/py/ground-control-tui)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.6+](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)

A modern, responsive Terminal User Interface (TUI) for real-time system monitoring, built with [Textual](https://textual.textualize.io/).

![Ground Control Demo](https://raw.githubusercontent.com/alberto-rota/ground-control/main/docs/demo.gif)

## Features

- **Real-time System Monitoring**
  - CPU usage per core with frequency tracking
  - Memory utilization
  - Disk I/O with read/write speeds
  - Network traffic monitoring
  - GPU metrics (if NVIDIA GPU is available)

- **Responsive Design**
  - Automatically adjusts layout based on terminal size
  - Supports horizontal, vertical, and grid layouts
  - Dynamic resizing with smooth transitions

- **Process Management**
  - View top-level user processes
  - Monitor system resource usage per process

## Installation

You can install Ground Control directly from PyPI:

```bash
pip install ground-control-tui
```

or install from source:

```bash
git clone https://github.com/alberto-rota/ground-control
cd ground-control
pip install -e .
```

## Quick Start

After installation, simply run:

```bash
groundcontrol
```

Or use it as a Python module:

```bash
python -m ground_control
```

## Usage

### Keyboard Controls

- `h`: Switch to horizontal layout
- `v`: Switch to vertical layout
- `g`: Switch to grid layout
- `a`: Toggle automatic layout
- `q`: Quit the application

### Layout Modes

Ground Control offers three layout modes:

1. **Grid Layout (Default)**: 2x2 grid arrangement
2. **Horizontal Layout**: All panels in a row
3. **Vertical Layout**: All panels in a column

The layout automatically adjusts based on your terminal size, but you can override this using the keyboard controls.

### Process Monitoring

To view top-level processes for the current user:

```bash
python -m ground_control.cli.process_list
```

## Requirements

- Python 3.6 or higher
- psutil
- textual
- plotext
- pynvml (optional, for NVIDIA GPU monitoring)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Author

**Alberto Rota**  
Email: alberto1.rota@polimi.it  
GitHub: [@alberto-rota](https://github.com/alberto-rota)

## Acknowledgments

- Built with [Textual](https://textual.textualize.io/)
- System metrics provided by [psutil](https://github.com/giampaolo/psutil)
- GPU monitoring via [pynvml](https://github.com/nvidia/nvidia-ml-py)

---

For more information and updates, visit the [GitHub repository](https://github.com/alberto-rota/ground-control).
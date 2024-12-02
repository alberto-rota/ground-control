import time
import psutil
from collections import deque
from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import Header, Static
from textual.message import Message
import plotext as plt
import re
try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except:
    NVML_AVAILABLE = False

def ansi2rich(text: str) -> str:
    """Replace ANSI color sequences with Rich markup."""
    color_map = {
        '12': 'blue',
        '10': 'green'
    }
    
    for ansi_code, rich_color in color_map.items():
        pattern = fr'\x1b\[38;5;{ansi_code}m(.*?)\x1b\[0m'
        text = re.sub(pattern, fr'[{rich_color}]\1[/]', text)
    return text

class MetricWidget(Static):
    """Base widget for system metrics with plot."""
    DEFAULT_CSS = """
    MetricWidget {
        height: 100%;
        border: solid green;
        background: $surface;
        layout: vertical;
    }
    
    .metric-title {
        text-align: left;
        height: 1;
    }
    
    .metric-value {
        text-align: left;
        height: 1;
    }
    .cpu-metric-value {
        text-align: left;
    }
    
    .metric-plot {
        height: 1fr;
    }
    """

    def __init__(self, title: str, color: str = "blue", history_size: int = 120):
        super().__init__()
        self.title = title
        self.color = color
        self.history = deque(maxlen=history_size)
        self.plot_width = 0
        self.plot_height = 0

    def on_resize(self, event: Message) -> None:
        """Handle resize events to update plot dimensions."""
        self.plot_width = event.size.width - 3
        self.plot_height = event.size.height - 3
        self.refresh()

    def get_plot(self, y_min=0, y_max=100) -> str:
        if not self.history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width+1)
        plt.theme("pro")
        plt.plot(list(self.history), marker="braille")
        plt.ylim(y_min, y_max)
        plt.xfrequency(0)
        plt.yfrequency(3)
        return ansi2rich(plt.build()).replace("\x1b[0m","").replace("[blue]",f"[{self.color}]")
    
    def create_gradient_bar(self, value: float, width: int = 20, color: str = None) -> str:
        """Creates a gradient bar with custom base color."""
        filled = int((width * value) / 100)
        if filled > width * 0.8:
            color = "bright_red"
        empty = width - filled
        
        if filled == 0:
            return "─" * width
        
        # For low values, use the base color at reduced intensity
        if value < 20:
            return f"[{self.color if color is None else color}]{'█' * filled}[/]{'─' * empty}"
        
        # For higher values, create a gradient from base color to bright_red
        light_section = filled // 2
        dark_section = filled - light_section
        
        bar = (f"[{self.color if color is None else color}]{'█' * filled}[/]"
               f"{'─' * empty}")
        
        return bar

    def format_metric_line(self, label: str, value: float, suffix: str = "%") -> str:
        """Creates a consistent metric line with label, bar, and value."""
        bar = self.create_gradient_bar(value)
        return f"{label:<4}{bar}{value:>7.1f}{suffix}"

class CPUWidget(MetricWidget):
    """CPU usage display widget."""
    DEFAULT_CSS = """
    CPUWidget {
        height: 100%;
        border: solid green;
        background: $surface;
        layout: vertical;
    }
    
    .metric-title {
        text-align: left;
    }
    
    .cpu-metric-value {
        height: 1fr;
    }
    """
    
    def compose(self) -> ComposeResult:
        #yield Static(self.title, classes="metric-title")
        yield Static("", id="cpu-content", classes="cpu-metric-value")

    def update_content(self, cpu_percentages):
        bar_width = self.size.width - 16
        
        lines = []
        for idx, percent in enumerate(cpu_percentages):
            core_name = f"Core {idx}: "
            percentage = f"{percent:5.1f}%"
            # Alternate between different shades of green for better distinction
            color = "green" if idx % 2 == 0 else "dark_green"
            bar = self.create_gradient_bar(percent, bar_width,color="dodger_blue1")

            line = f"{core_name:<4}{bar}{percentage:>7}"
            lines.append(line)
            
        self.query_one("#cpu-content").update("\n".join(lines))

class HistoryWidget(MetricWidget):
    """Widget for metrics with history plot."""
    
    def compose(self) -> ComposeResult:
        yield Static("", id="current-value", classes="metric-value")
        yield Static("", id="history-plot", classes="metric-plot")

    def update_content(self, value: float, suffix: str = "%", y_min=0, y_max=100, label="RAM:"):
        self.history.append(value)
        label = self.title
        bar_width = self.size.width - 10
        valuet = f"{value:.1f}% "
        bar = self.create_gradient_bar(value, bar_width,color=self.color)
        self.query_one("#current-value").update(f"{label:<4}{bar}{valuet:>7}")
        self.query_one("#history-plot").update(self.get_plot(y_min, y_max))

class DiskIOWidget(MetricWidget):
    """Widget for disk I/O with dual plots."""
    def __init__(self, title: str, history_size: int = 120):
        super().__init__(title, "magenta", history_size)
        self.read_history = deque(maxlen=history_size)
        self.write_history = deque(maxlen=history_size)
        self.max_io = 100

    def compose(self) -> ComposeResult:
        yield Static("", id="current-value", classes="metric-value")
        yield Static("", id="history-plot", classes="metric-plot")

    def create_center_bar(self, read_speed: float, write_speed: float, total_width: int = 40) -> str:
        half_width = total_width // 2-3
        read_percent = min((read_speed / self.max_io) * 100, 100)
        write_percent = min((write_speed / self.max_io) * 100, 100)
        
        read_blocks = int((half_width * read_percent) / 100)
        write_blocks = int((half_width * write_percent) / 100)
        
        left_bar = f"{'─' * (half_width - read_blocks)}[cyan]{'█' * read_blocks}[/]"
        right_bar = f"[magenta]{'█' * write_blocks}[/]{'─' * (half_width - write_blocks)}"
        
        return f"DISK "+f"{read_speed:6.1f} MB/s {left_bar}│{right_bar} {write_speed:6.1f} MB/s"

    def get_dual_plot(self) -> str:
        if not self.read_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width+1)
        plt.theme("pro")
        plt.plot(list(self.read_history), marker="braille", label="Read")
        plt.plot(list(self.write_history), marker="braille", label="Write")
        plt.yfrequency(3)
        plt.xfrequency(0)
        return ansi2rich(plt.build()).replace("\x1b[0m","").replace("[blue]","[magenta]").replace("[green]","[cyan]")

    def update_content(self, read_speed: float, write_speed: float):
        self.read_history.append(read_speed)
        self.write_history.append(write_speed)
        self.query_one("#current-value").update(
            self.create_center_bar(read_speed, write_speed)
        )
        self.query_one("#history-plot").update(self.get_dual_plot())

class GPUWidget(MetricWidget):
    """Widget for GPU metrics with dual plots."""
    def __init__(self, title: str, history_size: int = 120):
        super().__init__(title, "yellow", history_size)
        self.util_history = deque(maxlen=history_size)
        self.mem_history = deque(maxlen=history_size)

    def compose(self) -> ComposeResult:
        # yield Static("GPU Stats", classes="metric-title")
        yield Static("", id="gpu-util-value", classes="metric-value")
        yield Static("", id="gpu-util-plot", classes="metric-plot")
        yield Static("", id="gpu-mem-value", classes="metric-value")
        yield Static("", id="gpu-mem-plot", classes="metric-plot")

    def update_content(self, gpu_util: float, mem_used: float, mem_total: float):
        self.util_history.append(gpu_util)
        mem_percent = (mem_used / mem_total) * 100
        self.mem_history.append(mem_percent)
        mem_used = f"{mem_used / 1024:.1f}GB "  # Convert to GB
        gpu_utilt = f"{gpu_util:.1f}% "
        # Update utilization with yellow theme
        bar_width = self.size.width - 10
        bar = self.create_gradient_bar(gpu_util, bar_width, color="green")
        self.query_one("#gpu-util-value").update(f"{'GUTL':<4}{bar}{gpu_utilt:>7}% ")
        self.query_one("#gpu-util-plot").update(self.get_plot(
            data=self.util_history, 
            height=self.plot_height // 2-1,
            color="green"
        ))

        # Update memory with orange theme
        bar_width = self.size.width - 10
        bar = self.create_gradient_bar(mem_percent, bar_width, color="cyan")
        self.query_one("#gpu-mem-value").update(f"{'GMEM':<4}{bar}{mem_used:>7}")
        self.query_one("#gpu-mem-plot").update(self.get_plot(
            data=self.mem_history, 
            height=self.plot_height // 2,
            color="cyan"
        ))

    def get_plot(self, data: deque, height: int, color: str = None, y_min: float = 0, y_max: float = 100) -> str:
        if not data:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=height, width=self.plot_width+1)
        plt.theme("pro")
        plt.plot(list(data), marker="braille")
        plt.ylim(y_min, y_max)
        plt.yfrequency(3)
        plt.xfrequency(0)
        return ansi2rich(plt.build()).replace("\x1b[0m","").replace("[blue]",f"[{color}]")

class VMMonitor(App):
    """Main system monitor application with dynamic layout."""
    
    # Define layout breakpoints
    HORIZONTAL_RATIO_THRESHOLD = 6  # Width/Height ratio for horizontal layout
    VERTICAL_RATIO_THRESHOLD = 1    # Width/Height ratio for vertical layout
    
    CSS = """
    Grid {
        grid-gutter: 0;
        padding: 0;
    }
    Grid.default {
        grid-size: 2 2;
    }
    
    Grid.horizontal {
        grid-size: 4 1;
    }
    
    Grid.vertical {
        grid-size: 1 4;
    }
    
    """
    
    BINDINGS = [
        ("h", "set_horizontal", "Horizontal Layout"),
        ("v", "set_vertical", "Vertical Layout"),
        ("g", "set_grid", "Grid Layout"),
        ("a", "toggle_auto", "Toggle Auto Layout"),
    ]

    def __init__(self):
        super().__init__()
        self.prev_read_bytes = 0
        self.prev_write_bytes = 0
        self.prev_time = time.time()
        self.current_layout = "horizontal"
        self.auto_layout = True

    def get_layout_class(self, width: int, height: int) -> str:
        """Determine the appropriate layout based on terminal dimensions."""
        ratio = width / height if height > 0 else 0
        if ratio >= self.HORIZONTAL_RATIO_THRESHOLD:
            return "horizontal"
        elif ratio <= self.VERTICAL_RATIO_THRESHOLD:
            return "vertical"
        else:
            return "default"

    def compose(self) -> ComposeResult:
        """Create the initial layout."""
        yield Header()
        with Grid():
            yield CPUWidget("CPU Cores")
            yield HistoryWidget("RAM",color="yellow")
            yield DiskIOWidget("Disk")
            if NVML_AVAILABLE:
                yield GPUWidget("GPU")
            else:
                yield Static("GPU Not Available")

    def action_set_horizontal(self) -> None:
        """Switch to horizontal layout."""
        self.auto_layout = False
        self.set_layout("horizontal")

    def action_set_vertical(self) -> None:
        """Switch to vertical layout."""
        self.auto_layout = False
        self.set_layout("vertical")

    def action_set_grid(self) -> None:
        """Switch to grid layout."""
        self.auto_layout = False
        self.set_layout("default")

    def action_toggle_auto(self) -> None:
        """Toggle automatic layout adjustment."""
        self.auto_layout = not self.auto_layout
        if self.auto_layout:
            self.update_layout()

    def set_layout(self, new_layout: str) -> None:
        """Apply the specified layout."""
        if new_layout != self.current_layout:
            grid = self.query_one(Grid)
            grid.remove_class(self.current_layout)
            grid.add_class(new_layout)
            self.current_layout = new_layout

    async def on_mount(self) -> None:
        """Initialize the app and start update intervals."""
        self.set_interval(1.0, self.update_metrics)
        self.update_layout()

    def on_resize(self, event: Message) -> None:
        """Handle terminal resize events."""
        if self.auto_layout:
            self.update_layout()

    def update_layout(self) -> None:
        """Update the grid layout based on terminal dimensions."""
        if not self.is_mounted:
            return
            
        if self.auto_layout:
            width = self.size.width
            height = self.size.height
            new_layout = self.get_layout_class(width, height)
            self.set_layout(new_layout)

    def update_metrics(self):
        """Update all system metrics."""
        # CPU Update
        cpu_widget = self.query_one(CPUWidget)
        cpu_percentages = psutil.cpu_percent(percpu=True)
        cpu_widget.update_content(cpu_percentages)

        # RAM Update
        ram_widget = self.query_one("HistoryWidget")
        mem = psutil.virtual_memory()
        ram_widget.update_content(mem.percent)

        # Disk I/O Update
        current_time = time.time()
        io_counters = psutil.disk_io_counters()
        time_delta = current_time - self.prev_time
        
        read_speed = (io_counters.read_bytes - self.prev_read_bytes) / (1024**2) / time_delta
        write_speed = (io_counters.write_bytes - self.prev_write_bytes) / (1024**2) / time_delta
        
        disk_widget = self.query_one(DiskIOWidget)
        disk_widget.update_content(read_speed, write_speed)

        self.prev_read_bytes = io_counters.read_bytes
        self.prev_write_bytes = io_counters.write_bytes
        self.prev_time = current_time

        # GPU Update if available
        if NVML_AVAILABLE:
            gpu_widget = self.query_one(GPUWidget)
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            meminfo = pynvml.nvmlDeviceGetMemoryInfo(handle)
            
            gpu_widget.update_content(
                gpu_util=util.gpu,
                mem_used=meminfo.used / (1024**3),
                mem_total=meminfo.total / (1024**3)
            )

def main():
    app = VMMonitor()
    app.run()
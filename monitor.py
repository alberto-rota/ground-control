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

def blueout(text: str) -> str:
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

    def __init__(self, title: str, history_size: int = 120):  # 120 seconds = 2 minutes
        super().__init__()
        self.title = title
        self.history = deque(maxlen=history_size)
        self.plot_width = 0
        self.plot_height = 0

    def on_resize(self, event: Message) -> None:
        """Handle resize events to update plot dimensions."""
        self.plot_width = event.size.width - 3  # Account for padding
        self.plot_height = event.size.height - 3  # Account for title and value bars
        self.refresh()

    def get_plot(self, y_min=0, y_max=100) -> str:
        if not self.history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width)
        plt.theme("pro")
        plt.plot(list(self.history), marker="braille")
        plt.ylim(y_min, y_max)
        plt.xfrequency(0)
        plt.yfrequency(3)
        return blueout(plt.build()).replace("\x1b[0m","")

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
        height: 1;
    }
    
    .cpu-metric-value {
        height: 1fr;
    }
    """
    
    def compose(self) -> ComposeResult:
        yield Static("Core Usage", classes="metric-title")
        yield Static("", id="cpu-content", classes="cpu-metric-value")

    def get_color_for_percentage(self, percent: float) -> str:
        """Returns a color from green to red based on percentage."""
        if percent < 25:
            return "green"
        elif percent < 50:
            return "bright_green"
        elif percent < 75:
            return "yellow"
        elif percent < 90:
            return "orange"
        return "red"

    def create_bar(self, percent: float, width: int) -> str:
        """Creates a color-coded bar of specified width."""
        filled = int((width * percent) / 100)
        empty = width - filled
        color = self.get_color_for_percentage(percent)
        return f"[{color}]{'█' * filled}{' ' * empty}|[/]"

    def update_content(self, cpu_percentages):
        # Calculate the available width for the bar
        # Assuming max core name width of 8 ("Core XX: ") and percentage width of 6 ("100.0%")
        bar_width = self.size.width - 16  # Adjust based on padding and fixed text width
        
        lines = []
        for idx, percent in enumerate(cpu_percentages):
            core_name = f"Core {idx}: "
            percentage = f"{percent:5.1f}%"
            bar = self.create_bar(percent, bar_width)
            
            # Construct the line with proper spacing
            line = f"{core_name:<8}{bar}{percentage:>7}"
            lines.append(line)
            
        self.query_one("#cpu-content").update("\n".join(lines))

    def on_resize(self, event: Message) -> None:
        """Handle resize events by refreshing the display."""
        super().on_resize(event)
        # Force refresh of the bars to match new width
        if hasattr(self, 'last_percentages'):
            self.update_content(self.last_percentages)

class HistoryWidget(MetricWidget):
    """Widget for metrics with history plot."""
    
    def compose(self) -> ComposeResult:
        yield Static("", id="current-value", classes="metric-value")
        yield Static("", id="history-plot", classes="metric-plot")

    def update_content(self, value: float, suffix: str = "%", y_min=0, y_max=100):
        self.history.append(value)
        content = "".join(
            f"{'█' * int(value/5)}{' ' * (20-int(value/5))}"
        )
        self.query_one("#current-value").update(f"RAM:  {content} {value:.1f}{suffix}")
        self.query_one("#history-plot").update(self.get_plot(y_min, y_max))
        

class DiskIOWidget(MetricWidget):
    """Widget for disk I/O with dual plots."""
    def __init__(self, title: str, history_size: int = 120):
        super().__init__(title, history_size)
        self.read_history = deque(maxlen=history_size)
        self.write_history = deque(maxlen=history_size)
        self.max_io = 100  # Initial max I/O value in MB/s

    def compose(self) -> ComposeResult:
        yield Static("", id="current-value", classes="metric-value")
        yield Static("", id="history-plot", classes="metric-plot")

    def get_dual_plot(self) -> str:
        if not self.read_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width)
        plt.theme("pro")
        plt.plot(list(self.read_history), marker="braille", label="Read")
        plt.plot(list(self.write_history), marker="braille", label="Write")
        current_max = max(max(self.read_history or [0]), max(self.write_history or [0]))
        self.max_io = max(self.max_io, current_max * 1.2)  # Add 20% margin
        plt.ylim(0, self.max_io)
        plt.yfrequency(3)
        plt.xfrequency(0)
        return blueout(plt.build()).replace("\x1b[0m","")

    def update_content(self, read_speed: float, write_speed: float):
        self.read_history.append(read_speed)
        self.write_history.append(write_speed)
        self.query_one("#current-value").update(
            f"Read: {read_speed:.1f} MB/s | Write: {write_speed:.1f} MB/s"
        )
        self.query_one("#history-plot").update(self.get_dual_plot())

class SystemMonitorApp(App):
    """Main system monitor application."""
    CSS = """
    Grid {
        grid-size: 2 2;
        grid-gutter: 0;
        padding: 0;
    }
    """

    def __init__(self):
        super().__init__()
        self.prev_read_bytes = 0
        self.prev_write_bytes = 0
        self.prev_time = time.time()

    def compose(self) -> ComposeResult:
        yield Header()
        with Grid():
            yield CPUWidget("CPU Usage")
            yield HistoryWidget("RAM Usage")
            yield DiskIOWidget("Disk I/O")
            if NVML_AVAILABLE:
                yield HistoryWidget("GPU Usage")
            else:
                yield Static("GPU Not Available")

    async def on_mount(self) -> None:
        self.set_interval(1.0, self.update_metrics)

    def update_metrics(self):
        # Update CPU
        cpu_widget = self.query_one(CPUWidget)
        cpu_percentages = psutil.cpu_percent(percpu=True)
        cpu_widget.update_content(cpu_percentages)

        # Update RAM
        ram_widget = self.query_one("HistoryWidget")
        mem = psutil.virtual_memory()
        ram_widget.update_content(mem.percent)

        # Update Disk I/O
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

        # Update GPU if available
        if NVML_AVAILABLE:
            gpu_widget = list(self.query(HistoryWidget))[-1]  # Last HistoryWidget is GPU
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            gpu_widget.update_content(util.gpu)

if __name__ == "__main__":
    app = SystemMonitorApp()
    app.run()
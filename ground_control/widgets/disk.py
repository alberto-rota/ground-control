from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from utils.formatting import ansi2rich

class DiskIOWidget(MetricWidget):
    """Widget for disk I/O with dual plots and disk usage bar."""
    def __init__(self, title: str, history_size: int = 120):
        super().__init__(title, "magenta", history_size)
        self.read_history = deque(maxlen=history_size)
        self.write_history = deque(maxlen=history_size)
        self.max_io = 100
        self.disk_total = 0
        self.disk_used = 0
        self.first = True

    def compose(self) -> ComposeResult:
        yield Static("", id="current-value", classes="metric-value")
        yield Static("", id="history-plot", classes="metric-plot")
        yield Static("", id="disk-usage", classes="metric-value")

    def create_center_bar(self, read_speed: float, write_speed: float, total_width: int) -> str:
        half_width = total_width // 2
        read_percent = min((read_speed / self.max_io) * 100, 100)
        write_percent = min((write_speed / self.max_io) * 100, 100)
        
        read_blocks = int((half_width * read_percent) / 100)
        write_blocks = int((half_width * write_percent) / 100)
        
        left_bar = f"{'─' * (half_width - read_blocks)}[magenta]{''}{'█' * (read_blocks-1)}[/]" if read_blocks >= 1 else f"{'─' * half_width}"
        right_bar = f"[cyan]{'█' * (write_blocks-1)}{''}[/]{'─' * (half_width - write_blocks)}" if write_blocks >=1 else f"{'─' * half_width}"
        
        return f"DISK {read_speed:6.1f} MB/s {left_bar}│{right_bar} {write_speed:6.1f} MB/s"

    def create_usage_bar(self, total_width: int = 40) -> str:
        if self.disk_total == 0:
            return "No disk usage data..."
        
        usage_percent = (self.disk_used / self.disk_total) * 100
        available = self.disk_total - self.disk_used

        usable_width = total_width - 16
        used_blocks = int((usable_width * usage_percent) / 100)
        free_blocks = usable_width - used_blocks

        usage_bar = f"[magenta]{'█' * used_blocks}[/][green]{'█' * free_blocks}[/]"

        used_gb = self.disk_used / (1024 ** 3)
        available_gb = available / (1024 ** 3)

        return f"USED {used_gb:5.1f}GB {usage_bar} {available_gb:5.1f}GB FREE"

    def get_dual_plot(self) -> str:
        if not self.read_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height-1, width=self.plot_width+1)
        plt.theme("pro")
        plt.plot(list(self.read_history), marker="braille", label="Read")
        plt.plot(list(self.write_history), marker="braille", label="Write")
        plt.yfrequency(3)
        plt.xfrequency(0)
        return ansi2rich(plt.build()).replace("\x1b[0m","").replace("[blue]","[magenta]").replace("[green]","[cyan]")

    def update_content(self, read_speed: float, write_speed: float, disk_used: int = None, disk_total: int = None):
        if self.first:
            self.first = False
            return
        self.read_history.append(read_speed)
        self.write_history.append(write_speed)
        
        if disk_used is not None and disk_total is not None:
            self.disk_used = disk_used
            self.disk_total = disk_total

        total_width = self.size.width - len("DISK ") - len(f"{read_speed:6.1f} MB/s ") - len(f"{write_speed:6.1f} MB/s") - 2
        self.query_one("#current-value").update(
            self.create_center_bar(read_speed, write_speed, total_width=total_width)
        )
        self.query_one("#history-plot").update(self.get_dual_plot())
        self.query_one("#disk-usage").update(self.create_usage_bar())

from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from ground_control.utils.formatting import ansi2rich

class DiskIOWidget(MetricWidget):
    """Widget for disk I/O with dual plots and disk usage bar."""
    def __init__(self, title: str, id:str = None, history_size: int = 120):
        super().__init__(title=title, color="magenta", history_size=history_size, id=id)
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

        usage_bar = f"[magenta]{'█' * used_blocks}[/][cyan]{'█' * free_blocks}[/]"

        used_gb = self.disk_used / (1024 ** 3)
        available_gb = available / (1024 ** 3)

        return f"USED {used_gb:5.1f}GB {usage_bar} {available_gb:5.1f}GB FREE"

    def get_dual_plot(self) -> str:
        if not self.read_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height-2, width=self.plot_width+1)
        plt.theme("pro")
        
        # Create negative values for download operations
        negative_downloads = [-x for x in self.read_history]
        
        # Find the maximum value between uploads and downloads to set symmetric y-axis limits
        max_value = max(
            max(self.write_history, default=0),
            max(negative_downloads, key=abs, default=0)
        )
        
        # Add some padding to the max value
        y_limit = max_value * 1.1
        
        # Set y-axis limits symmetrically around zero
        # plt.ylim(-y_limit, y_limit)
        
        # Plot upload values above zero (positive)
        plt.plot(list(self.write_history), marker="braille", label="Write")
        
        # Plot download values below zero (negative)
        plt.plot(negative_downloads, marker="braille", label="Read")
        
        # Add a zero line
        plt.hline(0.0)
        
        plt.yfrequency(2)  # Increased to show more y-axis labels
        plt.xfrequency(0)
        # plt.title(len(self.read_history))
        # Customize y-axis labels to show absolute values
        # plt.ylabels([f"{abs(x):.0f}" for x in plt.yticks(return_values=True)])
        
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
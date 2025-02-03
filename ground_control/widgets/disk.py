from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.containers import Horizontal
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align

def rotate_text(text: str) -> str:
    # Rotate by printing one character per line.
    return "\n".join(list(text))

class DiskIOWidget(MetricWidget):
    """Widget for disk I/O with dual plots and vertical read/write bar."""
    def __init__(self, title: str, id: str = None, history_size: int = 120):
        super().__init__(title=title, color="magenta", history_size=history_size, id=id)
        self.read_history = deque(maxlen=history_size)
        self.write_history = deque(maxlen=history_size)
        self.max_io = 100
        self.disk_total = 0
        self.disk_used = 0
        self.first = True
        self.title = "Disk I/O"
        self.border_title = self.title

    def compose(self) -> ComposeResult:
        # Arrange the plot and read/write bar side by side.
        with Horizontal():
            yield Static("", id="history-plot", classes="metric-plot")
            yield Static("", id="current-value", classes="metric-value-vertical")
        yield Static("", id="disk-usage")

    def create_readwrite_bar(self, read_speed: float, write_speed: float, total_width: int) -> str:
        read_speed_withunits = align(f"{read_speed:.1f} MB/s", 12, "right")
        write_speed_withunits = align(f"{write_speed:.1f} MB/s", 12, "left")
        aval_width = total_width
        half_width = aval_width // 2
        read_percent = min((read_speed / self.max_io) * 100, 100)
        write_percent = min((write_speed / self.max_io) * 100, 100)
        
        read_blocks = int((half_width * read_percent) / 100)
        write_blocks = int((half_width * write_percent) / 100)
        
        left_bar = (f"{'─' * (half_width - read_blocks)}"
                    f"[magenta]{''}{'█' * (read_blocks-1)}[/]") if read_blocks >= 1 else f"{'─' * half_width}"
        right_bar = (f"[cyan]{'█' * (write_blocks-1)}{''}[/]{'─' * (half_width - write_blocks)}") if write_blocks >= 1 else f"{'─' * half_width}"
        
        return f"DSK  {read_speed_withunits} {left_bar}│{right_bar} {write_speed_withunits}"

    def create_disk_usage_bar(self, disk_used: float, disk_total: float, total_width: int = 40) -> str:
        if disk_total == 0:
            return "No disk usage data..."
        
        usage_percent = (disk_used / disk_total) * 100
        available = disk_total - disk_used

        usable_width = total_width - 2
        used_blocks = int((usable_width * usage_percent) / 100)
        free_blocks = usable_width - used_blocks

        usage_bar = f"[magenta]{'█' * used_blocks}[/][cyan]{'█' * free_blocks}[/]"

        used_gb = disk_used / (1024 ** 3)
        available_gb = available / (1024 ** 3)
        used_gb_txt = align(f"{used_gb:.1f} GB USED", total_width // 2 - 2, "left")
        free_gb_txt = align(f"FREE: {available_gb:.1f} GB ", total_width // 2 - 2, "right")
        return f' [magenta]{used_gb_txt}[/]    [cyan]{free_gb_txt}[/]\n {usage_bar}'

    def get_dual_plot(self) -> str:
        if not self.read_history:
            positive_downloads = [0] * 10
            negative_downloads = [0] * 10

        plt.clear_figure()
        plt.plot_size(height=self.plot_height-1, width=self.plot_width)
        plt.theme("pro")
        
        positive_downloads = [x + 0.1 for x in self.read_history]
        negative_downloads = [-x - 0.1 for x in self.write_history]
        
        max_value = int(max(
            max(positive_downloads, default=0),
            max(self.read_history, default=0)
        ))
        min_value = int(min( 
            min(negative_downloads, default=0),
            min(self.write_history, default=0)
        ))
        max_value = max(max_value, 1)
        min_value = abs(min(min_value, -1))
        
        limit = max(max_value, min_value)
        plt.ylim(-limit, limit)
        plt.plot(positive_downloads, marker="braille", label="Read")
        plt.plot(negative_downloads, marker="braille", label="Write")
        plt.hline(0.0)
        plt.yfrequency(5)
        plt.xfrequency(0)
        return ansi2rich(plt.build()).replace("\x1b[0m", "").replace("[blue]", "[blue]").replace("[green]", "[magenta]")

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

        # Update dual plot.
        self.query_one("#history-plot").update(self.get_dual_plot())
        
        # Create horizontal bar, then rotate it to display vertically.
        horizontal_bar = self.create_readwrite_bar(read_speed, write_speed, total_width=total_width)
        vertical_bar = rotate_text(horizontal_bar)
        self.query_one("#current-value").update(vertical_bar)
        
        self.query_one("#disk-usage").update    (self.create_disk_usage_bar(disk_used, disk_total, self.plot_width + 1))

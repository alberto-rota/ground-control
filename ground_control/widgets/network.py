from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from utils.formatting import ansi2rich

class NetworkIOWidget(MetricWidget):
    """Widget for network I/O with dual plots."""
    def __init__(self, title: str, history_size: int = 120):
        super().__init__(title, "blue", history_size)
        self.download_history = deque(maxlen=history_size)
        self.upload_history = deque(maxlen=history_size)
        self.max_net = 100
        self.first = True

    def compose(self) -> ComposeResult:
        yield Static("", id="current-value", classes="metric-value")
        yield Static("", id="history-plot", classes="metric-plot")

    def create_center_bar(self, download_speed: float, upload_speed: float, total_width: int) -> str:
        half_width = total_width // 2
        download_percent = min((download_speed / self.max_net) * 100, 100)
        upload_percent = min((upload_speed / self.max_net) * 100, 100)
        
        download_blocks = int((half_width * download_percent) / 100)
        upload_blocks = int((half_width * upload_percent) / 100)
        
        left_bar = f"{'─' * (half_width - download_blocks)}[blue]{''}{'█' * (download_blocks-1)}[/]" if download_blocks >= 1 else f"{'─' * half_width}"
        right_bar = f"[green]{'█' * (upload_blocks-1)}{''}[/]{'─' * (half_width - upload_blocks)}" if upload_blocks >=1 else f"{'─' * half_width}"
        
        return f"NET  {download_speed:6.1f} MB/s {left_bar}│{right_bar} {upload_speed:6.1f} MB/s"

    def get_dual_plot(self) -> str:
        if not self.download_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width+1)
        plt.theme("pro")
        plt.plot(list(self.download_history), marker="braille", label="Download")
        plt.plot(list(self.upload_history), marker="braille", label="Upload")
        plt.yfrequency(3)
        plt.xfrequency(0)
        return ansi2rich(plt.build()).replace("\x1b[0m","").replace("[blue]","[blue]").replace("[green]","[green]")

    def update_content(self, download_speed: float, upload_speed: float):
        if self.first:
            self.first = False
            return
        self.download_history.append(download_speed)
        self.upload_history.append(upload_speed)
        
        total_width = self.size.width - len("NET  ") - len(f"{download_speed:6.1f} MB/s ") - len(f"{upload_speed:6.1f} MB/s") - 2
        self.query_one("#current-value").update(
            self.create_center_bar(download_speed, upload_speed, total_width=total_width)
        )
        self.query_one("#history-plot").update(self.get_dual_plot())

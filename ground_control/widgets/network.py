from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from base import MetricWidget
import plotext as plt
from ground_control.utils.formatting import ansi2rich

class NetworkIOWidget(MetricWidget):
    """Widget for network I/O with dual plots."""
    def __init__(self, title: str, id:str = None, color:str = "blue",  history_size: int = 120):
        super().__init__(title=title, color="blue", history_size=history_size, id=id)
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
        
        # Create negative values for download operations
        negative_downloads = [-x for x in self.download_history]
        
        # Find the maximum value between uploads and downloads to set symmetric y-axis limits
        max_value = max(
            max(self.upload_history, default=0),
            max(negative_downloads, key=abs, default=0)
        )
        
        # Add some padding to the max value
        y_limit = max_value * 1.1
        
        # Set y-axis limits symmetrically around zero
        plt.ylim(-y_limit, y_limit)
        
        # Plot upload values above zero (positive)
        plt.plot(list(self.upload_history), marker="braille", label="Upload")
        
        # Plot download values below zero (negative)
        plt.plot(negative_downloads, marker="braille", label="Download")
        
        # Add a zero line
        plt.hline(0.0)
        
        plt.yfrequency(5)  # Increased to show more y-axis labels
        plt.xfrequency(0)
        
        # Customize y-axis labels to show absolute values
        # plt.ylabels([f"{abs(x):.0f}" for x in plt.yticks(return_values=True)])
        
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
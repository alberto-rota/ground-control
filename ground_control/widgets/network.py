from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align


class NetworkIOWidget(MetricWidget):
    """Widget for network I/O with dual plots."""

    def __init__(
        self, title: str, id: str = None, color: str = "blue", history_size: int = 120
    ):
        super().__init__(title=title, color="blue", history_size=history_size, id=id)
        self.download_history = deque(maxlen=history_size)
        self.upload_history = deque(maxlen=history_size)
        self.max_net = 100
        self.first = True
        self.title = title
        self.border_title = title #f"{title}"  # [blue]MB/s[/]"

    def compose(self) -> ComposeResult:
        yield Static("", id="history-plot", classes="metric-plot")
        yield Static("", id="current-value", classes="metric-value")

    def create_center_bar(
        self, read_speed: float, write_speed: float, total_width: int
    ) -> str:
        read_speed_withunits = align(f"{read_speed:.1f} MB/s", 12, "right")
        write_speed_withunits = align(f"{write_speed:.1f} MB/s", 12, "left")
        aval_width = (
            total_width  # s- len(read_speed_withunits) - len(write_speed_withunits) - 2
        )
        half_width = aval_width // 2
        read_percent = min((read_speed / self.max_net) * 100, 100)
        write_percent = min((write_speed / self.max_net) * 100, 100)

        read_blocks = int((half_width * read_percent) / 100)
        write_blocks = int((half_width * write_percent) / 100)

        left_bar = (
            f"{'─' * (half_width - read_blocks)}[green]{''}{'█' * (read_blocks-1)}[/]"
            if read_blocks >= 1
            else f"{'─' * half_width}"
        )
        right_bar = (
            f"[dark_orange]{'█' * (write_blocks-1)}{''}[/]{'─' * (half_width - write_blocks)}"
            if write_blocks >= 1
            else f"{'─' * half_width}"
        )

        return f"{read_speed_withunits} {left_bar}│{right_bar} {write_speed_withunits}"

    def get_dual_plot(self) -> str:
        if not self.download_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width)
        plt.theme("pro")

        # Create negative values for download operations
        negative_downloads = [-x - 0.1 for x in self.download_history]
        positive_downloads = [x + 0.1 for x in self.upload_history]

        # Find the maximum value between uploads and downloads to set symmetric y-axis limits
        max_value = max(
            max(self.upload_history, default=0),
            max(negative_downloads, key=abs, default=0),
        )

        # Add some padding to the max value
        y_limit = max_value
        if y_limit < 10:
            y_limit = 10
        self.max_net = y_limit

        # Set y-axis limits symmetrically around zero
        plt.ylim(-y_limit, y_limit)
        # Create custom y-axis ticks with MB/s labels
        num_ticks = min(
            5, self.plot_height - 1
        )  # Don't use too many ticks in small plots
        tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

        y_ticks = []
        y_labels = []

        for i in range(num_ticks):
            value = -y_limit + i * tick_step
            y_ticks.append(value)
            # Add MB/s to positive values (read speed) and negative values (write speed)
            if value == 0:
                y_labels.append("0")
            elif value > 0:
                y_labels.append(f"{value:.1f}↑")  # Up arrow for read
            else:
                y_labels.append(f"{abs(value):.1f}↓")  # Down arrow for write

        plt.yticks(y_ticks, y_labels)

        # Plot upload values above zero (positive)
        plt.plot(positive_downloads, marker="braille", label="Upload")

        # Plot download values below zero (negative)
        plt.plot(negative_downloads, marker="braille", label="Download")

        # Add a zero line
        plt.hline(0.0)

        plt.yfrequency(5)  # Increased to show more y-axis labels
        plt.xfrequency(0)

        # Customize y-axis labels to show absolute values
        # plt.ylabels([f"{abs(x):.0f}" for x in plt.yticks(return_values=True)])
        return (
            ansi2rich(plt.build())
            .replace("\x1b[0m", "")
            .replace("[blue]", "[dark_orange]")
            .replace("[green]", "[green]")
            .replace("──────┐","─MB/s─┐")
        )

    def update_content(self, download_speed: float, upload_speed: float):
        if self.first:
            self.first = False
            return
        self.download_history.append(download_speed)
        self.upload_history.append(upload_speed)

        total_width = (
            self.size.width
            - len("")
            - len(f"{download_speed:6.1f} MB/s ")
            - len(f"{upload_speed:6.1f} MB/s")
            - 2
        )
        self.query_one("#current-value").update(
            self.create_center_bar(
                download_speed, upload_speed, total_width=total_width
            )
        )
        self.query_one("#history-plot").update(self.get_dual_plot())

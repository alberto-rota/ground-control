import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.network")


class NetworkIOWidget(MetricWidget):
    """Widget for network I/O with dual plots."""

    def __init__(
        self, title: str, id: str = None, color: str = "blue", history_size: int = 120
    ):
        super().__init__(title=title, color=get_rich_color("default_plot", "#0080FF"), history_size=history_size, id=id)
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

        download_color = get_rich_color("network_download", "#FF8C00")
        upload_color = get_rich_color("network_upload", "#00FF00")
        left_bar = (
            f"{'─' * (half_width - read_blocks)}[{upload_color}]{''}{'█' * (read_blocks-1)}[/]"
            if read_blocks >= 1
            else f"{'─' * half_width}"
        )
        right_bar = (
            f"[{download_color}]{'█' * (write_blocks-1)}{''}[/]{'─' * (half_width - write_blocks)}"
            if write_blocks >= 1
            else f"{'─' * half_width}"
        )

        return f"{read_speed_withunits} {left_bar}│{right_bar} {write_speed_withunits}"

    # Plotext needs minimum height for plot + legend; smaller values trigger IndexError in build()
    _PLOT_MIN_HEIGHT = 6
    _PLOT_MIN_WIDTH = 12

    def get_dual_plot(self) -> str:
        if not self.download_history:
            return "No data yet..."

        # Enforce minima so plotext's legend build doesn't index out of range
        raw_height = max(1, getattr(self, "plot_height", 10))
        raw_width = max(10, getattr(self, "plot_width", 40))
        plot_height = max(self._PLOT_MIN_HEIGHT, raw_height)
        plot_width = max(self._PLOT_MIN_WIDTH, raw_width)

        if plot_height <= 0 or plot_width <= 0:
            return "Initializing..."

        try:
            plt.clear_figure()
            plt.plot_size(height=plot_height, width=plot_width)
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
            num_ticks = min(5, plot_height - 1)
            tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

            y_ticks = []
            y_labels = []

            for i in range(num_ticks):
                value = -y_limit + i * tick_step
                y_ticks.append(value)
                if value == 0:
                    y_labels.append("0")
                elif value > 0:
                    y_labels.append(f"{value:.1f}↑")
                else:
                    y_labels.append(f"{abs(value):.1f}↓")

            plt.yticks(y_ticks, y_labels)

            plt.plot(positive_downloads, marker="braille", label="Upload")
            plt.plot(negative_downloads, marker="braille", label="Download")
            plt.hline(0.0)
            plt.yfrequency(5)
            plt.xfrequency(0)

            download_color = get_rich_color("network_plot_download", "#FF8C00")
            upload_color = get_rich_color("network_plot_upload", "#00FF00")
            return (
                ansi2rich(plt.build())
                .replace("\x1b[0m", "")
                .replace("[blue]", f"[{download_color}]")
                .replace("[green]", f"[{upload_color}]")
                .replace("──────┐", "─MB/s─┐")
            )
        except Exception:
            # Plotext can IndexError on small dimensions; return a safe placeholder
            plt.clear_figure()
            plt.plot_size(height=self._PLOT_MIN_HEIGHT, width=self._PLOT_MIN_WIDTH)
            plt.theme("pro")
            plt.ylim(-1, 1)
            plt.plot([0] * min(10, plot_width), marker="braille", label="Network")
            plt.hline(0.0)
            return ansi2rich(plt.build()).replace("\x1b[0m", "")

    def update_content(self, download_speed: float, upload_speed: float):
        if self.first:
            self.first = False
            return
        self.download_history.append(download_speed)
        self.upload_history.append(upload_speed)
        logger.info(
            "download_speed_mb_s: %.2f, upload_speed_mb_s: %.2f",
            download_speed, upload_speed,
        )

        total_width = (
            self.size.width
            - len("")
            - len(f"{download_speed:6.1f} MB/s ")
            - len(f"{upload_speed:6.1f} MB/s")
            - 2
        )
        try:
            self.query_one("#current-value").update(
                self.create_center_bar(
                    download_speed, upload_speed, total_width=total_width
                )
            )
            self.query_one("#history-plot").update(self.get_dual_plot())
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout change)

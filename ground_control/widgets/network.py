import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, format_throughput, recolor, substitute_plot_timeframe
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.network")


class NetworkIOWidget(MetricWidget):
    """Widget for network I/O with dual plots."""

    # The bar under the plot is one row, so the plot region is stable from the
    # first layout pass (plot = 1fr of what is left).
    BAR_HEIGHT = 1

    DEFAULT_CSS = """
    NetworkIOWidget {
        layout: vertical;
    }
    """

    @staticmethod
    def download_color() -> str:
        """Colour of the download series — used for both the plot line and the bar."""
        return get_rich_color("network_plot_download", get_rich_color("network_download", "#FF8C00"))

    @staticmethod
    def upload_color() -> str:
        """Colour of the upload series — used for both the plot line and the bar."""
        return get_rich_color("network_plot_upload", get_rich_color("network_upload", "#00FF00"))

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
        self, download_speed: float, upload_speed: float, total_width: int
    ) -> str:
        """Download/upload bar: one line, centre separator on the middle column.

        Sides use the same colours as the corresponding plot lines.
        """
        scale = self.max_net or 1
        return self.build_split_bar(
            total_width,
            left_fraction=download_speed / scale,
            right_fraction=upload_speed / scale,
            left_color=self.download_color(),
            right_color=self.upload_color(),
            left_label=format_throughput(download_speed),
            right_label=format_throughput(upload_speed),
        )

    def get_dual_plot(self, width: int, height: int) -> str:
        if not self.download_history:
            return "No data yet..."
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)

        try:
            plt.clear_figure()
            plt.plot_size(height=height, width=width)
            plt.theme("pro")

            # Upload above the zero line, download mirrored below it
            upload_series = [x + 0.1 for x in self.upload_history]
            download_series = [-x - 0.1 for x in self.download_history]

            # Find the maximum value between uploads and downloads to set symmetric y-axis limits
            max_value = max(
                max(self.upload_history, default=0),
                max(download_series, key=abs, default=0),
            )

            # Add some padding to the max value
            y_limit = max_value
            if y_limit < 10:
                y_limit = 10
            self.max_net = y_limit

            # Set y-axis limits symmetrically around zero
            plt.ylim(-y_limit, y_limit)
            # Create custom y-axis ticks with MB/s labels
            num_ticks = max(2, min(5, height - 1))
            tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

            y_ticks = []
            y_labels = []

            for i in range(num_ticks):
                value = -y_limit + i * tick_step
                y_ticks.append(value)
                # Tick labels: numbers only; unit on top of plot (─MB/s─┐)
                if value == 0:
                    y_labels.append("0")
                elif value > 0:
                    y_labels.append(str(round(value)) + "↑")
                else:
                    y_labels.append(str(round(abs(value))) + "↓")

            plt.yticks(y_ticks, y_labels)

            plt.plot(upload_series, marker="braille", label="Upload")
            plt.plot(download_series, marker="braille", label="Download")
            plt.hline(0.0)
            plt.yfrequency(5)
            plt.xfrequency(0)

            # Series order fixes the colours: 1st plotted -> [blue], 2nd -> [green].
            build = recolor(
                ansi2rich(plt.build()).replace("\x1b[0m", ""),
                {"blue": self.upload_color(), "green": self.download_color()},
            ).replace("──────┐", "─MB/s─┐")
            if len(self.download_history) >= self.download_history.maxlen:
                build = substitute_plot_timeframe(build, self.download_history.maxlen)
            return self.finish_plot(build, width, height)
        except Exception:
            # Plotext can IndexError on unusual data; return a safe placeholder at the same size
            plt.clear_figure()
            plt.plot_size(height=height, width=width)
            plt.theme("pro")
            plt.ylim(-1, 1)
            plt.plot([0] * min(10, width), marker="braille", label="Network")
            plt.hline(0.0)
            build = ansi2rich(plt.build()).replace("\x1b[0m", "")
            return self.finish_plot(build, width, height)

    def rerender(self) -> None:
        """Re-draw plot and bar from stored history at the current region size."""
        if not self.download_history:
            return
        plot_width, plot_height = self.plot_region("#history-plot", reserve_height=self.BAR_HEIGHT)
        bar_width, _ = self.region_size("#current-value")
        try:
            self.query_one("#history-plot").update(self.get_dual_plot(plot_width, plot_height))
            self.query_one("#current-value").update(
                self.create_center_bar(
                    self.download_history[-1], self.upload_history[-1], total_width=bar_width
                )
            )
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout change)

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
        self.rerender()

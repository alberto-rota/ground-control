import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.containers import Horizontal
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align, format_size, format_throughput, substitute_plot_timeframe
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.disk")


def rotate_text(text: str) -> str:
    # Rotate by printing one character per line.
    return "\n".join(list(text))


class DiskIOWidget(MetricWidget):
    """Widget for disk I/O with dual plots and vertical read/write bar."""

    def __init__(self, title: str, id: str = None, history_size: int = 120):
        super().__init__(title=title, color=get_rich_color("disk_read", "#FF00FF"), history_size=history_size, id=id)
        self.read_history = deque(maxlen=history_size)
        self.write_history = deque(maxlen=history_size)
        self.max_io = 100
        self.disk_total = 0
        self.disk_used = 0
        self.first = True
        self.title = title
        self.border_title = title#f"{title} [magenta]MB/s[/]"

    def compose(self) -> ComposeResult:
        # Arrange the plot and read/write bar side by side.
        with Horizontal():
            yield Static("", id="history-plot", classes="metric-plot")
            yield Static("", id="current-value", classes="metric-value-vertical")
        yield Static("", id="disk-usage")

    def create_readwrite_bar(
        self, read_speed: float, write_speed: float, total_width: int
    ) -> str:
        try:
            # Safety checks
            read_speed = max(0.0, float(read_speed))
            write_speed = max(0.0, float(write_speed))
            total_width = max(10, int(total_width))

            read_speed_withunits = align(format_throughput(read_speed), 12, "right")
            write_speed_withunits = align(format_throughput(write_speed), 12, "left")
            aval_width = total_width
            half_width = aval_width // 2

            # Avoid division by zero
            max_io = max(1.0, self.max_io)
            read_percent = min((read_speed / max_io) * 100, 100)
            write_percent = min((write_speed / max_io) * 100, 100)

            read_blocks = int((half_width * read_percent) / 100)
            write_blocks = int((half_width * write_percent) / 100)

            read_color = get_rich_color("disk_read", "#FF00FF")
            write_color = get_rich_color("disk_write", "#00FFFF")
            left_bar = (
                (
                    f"{'─' * (half_width - read_blocks)}"
                    f"[{read_color}]{''}{'█' * (read_blocks-1)}[/]"
                )
                if read_blocks >= 1
                else f"{'─' * half_width}"
            )
            right_bar = (
                (
                    f"[{write_color}]{'█' * (write_blocks-1)}{''}[/]{'─' * (half_width - write_blocks)}"
                )
                if write_blocks >= 1
                else f"{'─' * half_width}"
            )

            return f"DSK  {read_speed_withunits} {left_bar}│{right_bar} {write_speed_withunits}"
        except Exception as e:
            return "DSK  Error creating read/write bar"

    def create_disk_usage_bar(
        self, disk_used: float, disk_total: float, total_width: int = 40
    ) -> str:
        try:
            # Safety checks
            disk_used = max(0, int(disk_used) if disk_used is not None else 0)
            disk_total = max(
                1, int(disk_total) if disk_total is not None else 1
            )  # Avoid division by zero
            total_width = max(10, int(total_width))

            if disk_total <= 0:
                return "No disk usage data..."

            usage_percent = (disk_used / disk_total) * 100
            available = disk_total - disk_used

            usable_width = total_width - 2
            used_blocks = int((usable_width * usage_percent) / 100)
            free_blocks = usable_width - used_blocks

            disk_used_color = get_rich_color("disk_used", "#FF00FF")
            disk_free_color = get_rich_color("disk_free", "#00FFFF")
            usage_bar = f"[{disk_used_color}]{'█' * used_blocks}[/][{disk_free_color}]{'█' * free_blocks}[/]"

            used_gb_txt = align(f"{format_size(disk_used)} USED", total_width // 2 - 2, "left")
            free_gb_txt = align(
                f"FREE: {format_size(available)} ", total_width // 2 - 2, "right"
            )
            return f" [{disk_used_color}]{used_gb_txt}[/]    [{disk_free_color}]{free_gb_txt}[/]\n {usage_bar}"
        except Exception as e:
            return "Error displaying disk usage"

    # Plotext needs minimum height for plot + legend; smaller values trigger IndexError in build()
    _PLOT_MIN_HEIGHT = 6
    _PLOT_MIN_WIDTH = 12

    def get_dual_plot(self) -> str:
        try:
            # Validate plot dimensions; enforce minima so plotext's legend build doesn't index out of range
            raw_height = max(1, getattr(self, "plot_height", 10) - 1)
            raw_width = max(10, getattr(self, "plot_width", 40))
            plot_height = max(self._PLOT_MIN_HEIGHT, raw_height)
            plot_width = max(self._PLOT_MIN_WIDTH, raw_width)

            # If dimensions are invalid, return early
            if plot_height <= 0 or plot_width <= 0:
                return "Initializing..."
            
            # Initialize with default values if history is empty
            if (
                not self.read_history
                or not self.write_history
                or len(self.read_history) < 1
                or len(self.write_history) < 1
            ):
                # Create some dummy data for initial plot
                positive_downloads = [0] * 10
                negative_downloads = [0] * 10

                plt.clear_figure()
                plt.plot_size(
                    height=plot_height,
                    width=plot_width,
                )
                plt.theme("pro")
                plt.ylim(-1, 1)  # Set default range
                plt.plot(positive_downloads, marker="braille", label="Read")
                plt.plot(negative_downloads, marker="braille", label="Write")
                plt.hline(0.0)

                # Custom y-ticks: numbers only; unit on top of plot (─MB/s─┐)
                y_ticks = [-1.0, -0.5, 0.0, 0.5, 1.0]
                y_labels = ["1↓", "0↓", "0", "0↑", "1↑"]
                plt.yticks(y_ticks, y_labels)
                plt.xfrequency(0)

                read_color = get_rich_color("disk_plot_read", "#FF00FF")
                write_color = get_rich_color("disk_plot_write", "#00FFFF")
                build = ansi2rich(plt.build()).replace("\x1b[0m", "").replace("[blue]", f"[{read_color}]").replace("[green]", f"[{write_color}]")
                if len(self.read_history) >= self.read_history.maxlen:
                    build = substitute_plot_timeframe(build, self.read_history.maxlen)
                return build

            # Process actual data if we have history
            plt.clear_figure()
            plt.plot_size(height=plot_height, width=plot_width)
            plt.theme("pro")

            # Safety conversion of values
            try:
                positive_downloads = [float(x) for x in self.read_history]
            except (TypeError, ValueError):
                positive_downloads = [0.0] * len(self.read_history)

            try:
                negative_downloads = [-float(x) for x in self.write_history]
            except (TypeError, ValueError):
                negative_downloads = [-0.0] * len(self.write_history)

            # Use safe methods to find max/min with empty list protection
            max_positive = 0.1
            max_read = 0.1
            min_negative = -0.1
            min_write = -0.1

            try:
                if positive_downloads:
                    max_positive = max(positive_downloads)
            except Exception as e:
                pass

            try:
                if self.read_history:
                    max_read = max(float(x) for x in self.read_history)
            except Exception as e:
                pass

            try:
                if negative_downloads:
                    min_negative = min(negative_downloads)
            except Exception as e:
                pass

            try:
                if self.write_history:
                    min_write = -min(float(x) for x in self.write_history)
            except Exception as e:
                pass

            max_value = int(max(max_positive, max_read, 1))  # At least 1
            min_value = abs(int(min(min_negative, min_write, -1)))  # At least -1

            limit = max(max_value, min_value)
            y_min, y_max = -limit, limit
            plt.ylim(y_min, y_max)

            # For very low activity disks, use fixed scale to make it visible
            if all(x < 0.01 for x in self.read_history) and all(
                x < 0.01 for x in self.write_history
            ):
                y_min, y_max = -0.5, 0.5
                plt.ylim(y_min, y_max)

            # Create custom y-axis ticks with MB/s labels
            num_ticks = min(
                5, plot_height - 1
            )  # Don't use too many ticks in small plots
            tick_step = (y_max - y_min) / (num_ticks - 1) if num_ticks > 1 else 1

            y_ticks = []
            y_labels = []

            for i in range(num_ticks):
                value = y_min + i * tick_step
                y_ticks.append(value)
                # Tick labels: numbers only; unit on top of plot (─MB/s─┐)
                if value == 0:
                    y_labels.append("0")
                elif value > 0:
                    y_labels.append(str(round(value)) + "↑")
                else:
                    y_labels.append(str(round(abs(value))) + "↓")

            plt.yticks(y_ticks, y_labels)

            plt.plot(positive_downloads, marker="braille", label="Read")
            plt.plot(negative_downloads, marker="braille", label="Write")
            plt.hline(0.0)
            plt.xfrequency(0)
            read_color = get_rich_color("disk_plot_read", "#FF00FF")
            write_color = get_rich_color("disk_plot_write", "#00FFFF")
            build = ansi2rich(plt.build())
            build = build.replace("\x1b[0m", "").replace("[blue]", f"[{read_color}]").replace("[green]", f"[{write_color}]").replace("──────┐", "─MB/s─┐")
            build = substitute_plot_timeframe(build, self.read_history.maxlen)
            return build
        except Exception as e:
            logger.debug("Plot error in %s: %s", self.title, e, exc_info=True)
            
            # Return a simple error placeholder plot
            try:
                safe_height = max(
                    self._PLOT_MIN_HEIGHT,
                    max(1, getattr(self, "plot_height", 10) - 1),
                )
                safe_width = max(
                    self._PLOT_MIN_WIDTH,
                    getattr(self, "plot_width", 40),
                )
                plt.clear_figure()
                plt.plot_size(height=safe_height, width=safe_width)
                plt.theme("pro")
                plt.ylim(-1, 1)
                dummy_data = [0] * min(10, safe_width)
                plt.plot(dummy_data, marker="braille", label="Error")
                plt.hline(0.0)

                # Even in error state: numbers only; unit on top
                y_ticks = [-1.0, -0.5, 0.0, 0.5, 1.0]
                y_labels = ["1↓", "0↓", "0", "0↑", "1↑"]
                plt.yticks(y_ticks, y_labels)

                error_color = get_rich_color("high_value", "#FF0000")
                build = ansi2rich(plt.build()).replace("\x1b[0m", "").replace("[blue]", f"[{error_color}]")
                if len(self.read_history) >= self.read_history.maxlen:
                    build = substitute_plot_timeframe(build, self.read_history.maxlen)
                return build
            except Exception as inner_e:
                logger.debug("Error creating error plot: %s", inner_e)
                return "Error displaying plot"

    def update_content(
        self,
        read_speed: float,
        write_speed: float,
        disk_used: int = None,
        disk_total: int = None,
    ):
        try:
            # Safety checks and defaults
            read_speed = float(read_speed) if read_speed is not None else 0.0
            write_speed = float(write_speed) if write_speed is not None else 0.0
            disk_used = int(disk_used) if disk_used is not None else 0
            disk_total = (
                int(disk_total) if disk_total is not None else 1
            )  # Avoid division by zero

            # Update histories
            self.read_history.append(read_speed)
            self.write_history.append(write_speed)

            self.disk_used = disk_used
            self.disk_total = disk_total

            mountpoint = self.title.replace("Disk @ ", "").strip()
            used_gb = disk_used / (1024 ** 3)
            total_gb = disk_total / (1024 ** 3) if disk_total else 0.0
            logger.info(
                "mountpoint: %s, read_speed_mb_s: %.2f, write_speed_mb_s: %.2f, disk_used_gb: %.2f, disk_total_gb: %.2f",
                mountpoint, read_speed, write_speed, used_gb, total_gb,
            )

            # Check if we have a valid size before calculating
            if self.size and self.size.width > 0:
                total_width = max(
                    10,
                    self.size.width
                    - len("DISK ")
                    - len(format_throughput(read_speed) + " ")
                    - len(format_throughput(write_speed))
                    - 2,
                )
            else:
                total_width = 40  # Default width if size not available

            # Update plot safely
            try:
                history_plot = self.query_one("#history-plot")
                history_plot.update(self.get_dual_plot())
            except Exception as e:
                pass

            # Update read/write bar safely
            try:
                horizontal_bar = self.create_readwrite_bar(
                    read_speed, write_speed, total_width=total_width
                )
                vertical_bar = rotate_text(horizontal_bar)

                current_value = self.query_one("#current-value")
                current_value.update(vertical_bar)
            except Exception as e:
                pass

            # Update disk usage safely
            try:
                disk_usage = self.query_one("#disk-usage")
                plot_width = getattr(self, "plot_width", 40)  # Default if not set
                disk_usage.update(
                    self.create_disk_usage_bar(disk_used, disk_total, plot_width + 1)
                )
            except Exception as e:
                pass

            self.first = False
        except Exception as e:
            pass

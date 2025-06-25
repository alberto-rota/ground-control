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

            read_speed_withunits = align(f"{read_speed:.1f} MB/s", 12, "right")
            write_speed_withunits = align(f"{write_speed:.1f} MB/s", 12, "left")
            aval_width = total_width
            half_width = aval_width // 2

            # Avoid division by zero
            max_io = max(1.0, self.max_io)
            read_percent = min((read_speed / max_io) * 100, 100)
            write_percent = min((write_speed / max_io) * 100, 100)

            read_blocks = int((half_width * read_percent) / 100)
            write_blocks = int((half_width * write_percent) / 100)

            left_bar = (
                (
                    f"{'─' * (half_width - read_blocks)}"
                    f"[magenta]{''}{'█' * (read_blocks-1)}[/]"
                )
                if read_blocks >= 1
                else f"{'─' * half_width}"
            )
            right_bar = (
                (
                    f"[cyan]{'█' * (write_blocks-1)}{''}[/]{'─' * (half_width - write_blocks)}"
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

            usage_bar = f"[magenta]{'█' * used_blocks}[/][cyan]{'█' * free_blocks}[/]"

            used_gb = disk_used / (1024**3)
            available_gb = available / (1024**3)
            used_gb_txt = align(f"{used_gb:.1f} GB USED", total_width // 2 - 2, "left")
            free_gb_txt = align(
                f"FREE: {available_gb:.1f} GB ", total_width // 2 - 2, "right"
            )
            return f" [magenta]{used_gb_txt}[/]    [cyan]{free_gb_txt}[/]\n {usage_bar}"
        except Exception as e:
            return "Error displaying disk usage"

    def get_dual_plot(self) -> str:
        try:
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
                    height=max(1, getattr(self, "plot_height", 10) - 1),
                    width=max(10, getattr(self, "plot_width", 40)),
                )
                plt.theme("pro")
                plt.ylim(-1, 1)  # Set default range
                plt.plot(positive_downloads, marker="braille", label="Read")
                plt.plot(negative_downloads, marker="braille", label="Write")
                plt.hline(0.0)

                # Custom y-ticks with MB/s labels
                y_ticks = [-1.0, -0.5, 0.0, 0.5, 1.0]
                y_labels = ["-1.0 MB/s", "-0.5 MB/s", "0.0", "0.5 MB/s", "1.0 MB/s"]
                plt.yticks(y_ticks, y_labels)
                plt.xfrequency(0)

                return (
                    ansi2rich(plt.build())
                    .replace("\x1b[0m", "")
                    .replace("[blue]", "[blue]")
                    .replace("[green]", "[magenta]")
                )

            # Process actual data if we have history
            plt.clear_figure()

            # Ensure plot dimensions are valid
            plot_height = max(1, getattr(self, "plot_height", 10) - 1)
            plot_width = max(10, getattr(self, "plot_width", 40))
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
                # Add MB/s to positive values (read speed) and negative values (write speed)
                if value == 0:
                    y_labels.append("0")
                elif value > 0:
                    y_labels.append(f"{value:.1f}↑")  # Up arrow for read
                else:
                    y_labels.append(f"{abs(value):.1f}↓")  # Down arrow for write

            plt.yticks(y_ticks, y_labels)

            plt.plot(positive_downloads, marker="braille", label="Read")
            plt.plot(negative_downloads, marker="braille", label="Write")
            plt.hline(0.0)
            plt.xfrequency(0)
            return (
                ansi2rich(plt.build())
                .replace("\x1b[0m", "")
                .replace("[blue]", "[blue]")
                .replace("[green]", "[magenta]")
                .replace("──────┐","─MB/s─┐")
            )
        except Exception as e:
            # Return a simple error placeholder plot
            try:
                plt.clear_figure()
                plt.plot_size(height=10, width=40)
                plt.theme("pro")
                plt.ylim(-1, 1)
                dummy_data = [0] * 10
                plt.plot(dummy_data, marker="braille", label="Error")
                plt.text("Plot Error", 5, 0)
                plt.hline(0.0)

                # Even in error state, add MB/s labels
                y_ticks = [-1.0, -0.5, 0.0, 0.5, 1.0]
                y_labels = [
                    "1.0 MB/s ↓",
                    "0.5 MB/s ↓",
                    "0.0",
                    "0.5 MB/s ↑",
                    "1.0 MB/s ↑",
                ]
                plt.yticks(y_ticks, y_labels)

                return (
                    ansi2rich(plt.build())
                    .replace("\x1b[0m", "")
                    .replace("[blue]", "[red]")
                )
            except:
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

            # Check if we have a valid size before calculating
            if self.size and self.size.width > 0:
                total_width = max(
                    10,
                    self.size.width
                    - len("DISK ")
                    - len(f"{read_speed:6.1f} MB/s ")
                    - len(f"{write_speed:6.1f} MB/s")
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

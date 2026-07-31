import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.containers import Horizontal
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import (
    ansi2rich,
    align,
    format_size,
    format_throughput,
    pad_to_width,
    recolor,
    substitute_plot_timeframe,
)
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.disk")


class DiskIOWidget(MetricWidget):
    """Widget for disk I/O with dual plots and vertical read/write bar."""

    # Rows used by the disk-usage block under the plot (labels + bar).
    USAGE_HEIGHT = 2

    DEFAULT_CSS = """
    DiskIOWidget {
        layout: vertical;
    }
    /* Plot and the vertical read/write bar share one row of the panel. */
    DiskIOWidget > Horizontal {
        height: 1fr;
    }
    /* One cell wide: the bar is rendered one character per line. */
    DiskIOWidget .metric-value-vertical {
        width: 1;
        height: 1fr;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    DiskIOWidget #disk-usage {
        height: 2;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    """

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

    @staticmethod
    def read_color() -> str:
        """Colour of the read series — used for both the plot line and the bar."""
        return get_rich_color("disk_plot_read", get_rich_color("disk_read", "#FF00FF"))

    @staticmethod
    def write_color() -> str:
        """Colour of the write series — used for both the plot line and the bar."""
        return get_rich_color("disk_plot_write", get_rich_color("disk_write", "#00FFFF"))

    def create_readwrite_bar(
        self, read_speed: float, write_speed: float, total_height: int
    ) -> str:
        """Build the vertical read/write bar: one character per line, exactly ``total_height`` lines.

        Colours are applied per line — markup cannot be rotated, so the bar is built
        as (character, colour) cells and each cell becomes its own line.
        """
        try:
            read_speed = max(0.0, float(read_speed))
            write_speed = max(0.0, float(write_speed))
            n = max(1, int(total_height))

            read_color = self.read_color()
            write_color = self.write_color()

            read_label = format_throughput(read_speed)
            write_label = format_throughput(write_speed)
            # Labels (read on top, write at the bottom) only when a usable bar is left.
            label_cells = len(read_label) + len(write_label) + 2
            show_labels = n - label_cells >= 5
            bar_len = n - label_cells if show_labels else n

            half = max(0, (bar_len - 1) // 2)
            extra = max(0, bar_len - 1 - 2 * half)  # odd lengths: one cell to the write side

            max_io = max(1.0, self.max_io)
            read_blocks = min(half, int(half * min(read_speed / max_io, 1.0)))
            write_blocks = min(half, int(half * min(write_speed / max_io, 1.0)))

            cells: list[tuple[str, str | None]] = []
            if show_labels:
                cells += [(c, read_color) for c in read_label]
                cells.append((" ", None))
            # Read grows towards the centre separator, write away from it.
            cells += [("─", None)] * (half - read_blocks)
            cells += [("█", read_color)] * read_blocks
            cells.append(("│", None))
            cells += [("█", write_color)] * write_blocks
            cells += [("─", None)] * (half - write_blocks + extra)
            if show_labels:
                cells.append((" ", None))
                cells += [(c, write_color) for c in write_label]

            cells = cells[:n]
            return "\n".join(
                ch if color is None else f"[{color}]{ch}[/]" for ch, color in cells
            )
        except Exception:
            return ""

    def create_disk_usage_bar(
        self, disk_used: float, disk_total: float, total_width: int = 40
    ) -> str:
        """Used/free labels plus a usage bar; both lines are exactly ``total_width`` cells."""
        try:
            disk_used = max(0, int(disk_used) if disk_used is not None else 0)
            disk_total = max(1, int(disk_total) if disk_total is not None else 1)
            total_width = int(total_width)
            if total_width <= 0:
                return ""

            usage_percent = min(100.0, (disk_used / disk_total) * 100)
            available = max(0, disk_total - disk_used)

            disk_used_color = get_rich_color("disk_used", "#FF00FF")
            disk_free_color = get_rich_color("disk_free", "#00FFFF")

            used_blocks = int((total_width * usage_percent) / 100)
            free_blocks = total_width - used_blocks
            usage_bar = (
                f"[{disk_used_color}]{'█' * used_blocks}[/]"
                f"[{disk_free_color}]{'█' * free_blocks}[/]"
            )

            # Label line: used on the left, free on the right, exactly total_width wide.
            label_w = (total_width - 1) // 2
            if label_w < 9:
                # "16 MB USED" / "FREE 2 TB" need ~9 cells; below that the labels would
                # be cut mid-value, so show the bar alone.
                return usage_bar
            used_txt = f"{format_size(disk_used)} USED"
            free_txt = f"FREE {format_size(available)}"
            used_txt = align(used_txt, label_w, "left")
            free_txt = align(free_txt, label_w, "right")
            labels = pad_to_width(
                f"[{disk_used_color}]{used_txt}[/] [{disk_free_color}]{free_txt}[/]",
                2 * label_w + 1,
                total_width,
            )
            return f"{labels}\n{usage_bar}"
        except Exception:
            return "Error displaying disk usage"

    def get_dual_plot(self, width: int, height: int) -> str:
        """Read/write history plot drawn on a (width, height) canvas."""
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)
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
                plt.plot_size(height=height, width=width)
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

                read_color = self.read_color()
                write_color = self.write_color()
                build = recolor(
                    ansi2rich(plt.build()).replace("\x1b[0m", ""),
                    {"blue": read_color, "green": write_color},
                )
                return self.finish_plot(build, width, height)

            # Process actual data if we have history
            plt.clear_figure()
            plt.plot_size(height=height, width=width)
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

            max_value = int(max(max(positive_downloads, default=0.1), 1))
            min_value = abs(int(min(min(negative_downloads, default=-0.1), -1)))

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
            num_ticks = max(2, min(5, height - 1))
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
            read_color = self.read_color()
            write_color = self.write_color()
            # Read is plotted first -> [blue], write second -> [green]; same colours as the bar.
            build = recolor(
                ansi2rich(plt.build()).replace("\x1b[0m", ""),
                {"blue": read_color, "green": write_color},
            ).replace("──────┐", "─MB/s─┐")
            build = substitute_plot_timeframe(build, self.read_history.maxlen)
            return self.finish_plot(build, width, height)
        except Exception as e:
            logger.debug("Plot error in %s: %s", self.title, e, exc_info=True)

            # Return a simple error placeholder plot at the same size
            try:
                plt.clear_figure()
                plt.plot_size(height=height, width=width)
                plt.theme("pro")
                plt.ylim(-1, 1)
                plt.plot([0] * min(10, width), marker="braille", label="Error")
                plt.hline(0.0)
                error_color = get_rich_color("high_value", "#FF0000")
                build = recolor(ansi2rich(plt.build()).replace("\x1b[0m", ""), {"blue": error_color})
                return self.finish_plot(build, width, height)
            except Exception as inner_e:
                logger.debug("Error creating error plot: %s", inner_e)
                return "Error displaying plot"

    def rerender(self) -> None:
        """Re-draw plot, read/write bar and usage bar at the current region sizes."""
        plot_width, plot_height = self.plot_region(
            "#history-plot", reserve_width=1, reserve_height=self.USAGE_HEIGHT
        )
        _, bar_height = self.region_size("#current-value", reserve_height=self.USAGE_HEIGHT)
        usage_width, _ = self.region_size("#disk-usage")
        try:
            self.query_one("#history-plot").update(self.get_dual_plot(plot_width, plot_height))
        except NoMatches:
            pass
        try:
            read = self.read_history[-1] if self.read_history else 0.0
            write = self.write_history[-1] if self.write_history else 0.0
            self.query_one("#current-value").update(
                self.create_readwrite_bar(read, write, total_height=bar_height)
            )
        except NoMatches:
            pass
        try:
            self.query_one("#disk-usage").update(
                self.create_disk_usage_bar(self.disk_used, self.disk_total, usage_width)
            )
        except NoMatches:
            pass

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

            self.rerender()
            self.first = False
        except Exception:
            pass

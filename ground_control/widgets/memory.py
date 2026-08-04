import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import (
    align,
    ansi2rich,
    format_size,
    pad_to_width,
    recolor,
    substitute_plot_timeframe,
)
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.memory")

class MemoryWidget(MetricWidget):
    """Memory (RAM) usage display widget with dual plots for RAM and SWAP over time."""

    # The bar block below the plot is exactly two rows (labels + bar), so the plot
    # gets a stable 1fr region from the very first layout pass.
    BAR_HEIGHT = 2

    DEFAULT_CSS = """
    MemoryWidget {
        layout: vertical;
    }
    MemoryWidget #current-value {
        height: 2;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    """

    def __init__(self, title: str = "Memory", id: str = None):
        super().__init__(title=title, id=id, color=get_rich_color("memory_ram", "#FF8C00"))
        self.ram_history = deque(maxlen=120)
        self.swap_history = deque(maxlen=120)
        self.first = True
        self.title = title
        self.border_title = title
        self.total_ram = 0
        self.total_swap = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="history-plot", classes="metric-plot")
        yield Static("", id="current-value", classes="metric-value")

    def create_center_bar(
        self, ram_usage: float, swap_usage: float, total_width: int
    ) -> str:
        """Create a center bar showing used/free RAM and used/free SWAP with four different colors.

        Both returned lines are exactly ``total_width`` cells wide: the bar is built
        from block counts that sum to ``total_width``, and the label line is padded
        to the same width.
        """
        ram_usage = max(0.0, float(ram_usage))
        swap_usage = max(0.0, float(swap_usage))
        total_width = int(total_width)
        if total_width <= 0:
            return ""

        free_ram = max(0.0, self.total_ram - ram_usage)
        free_swap = max(0.0, self.total_swap - swap_usage)

        ram_used_percent = min(ram_usage/self.total_ram if self.total_ram > 0 else 0, 1)
        swap_used_percent = min(swap_usage/self.total_swap if self.total_swap > 0 else 0, 1)

        # Block counts add up to total_width by construction (RAM half | SWAP half).
        ram_blocks = total_width // 2
        swap_blocks = total_width - ram_blocks

        ram_color = get_rich_color("memory_ram_used", "#FF8C00")
        swap_color = get_rich_color("memory_swap", "#00FFFF")

        # Both halves meet at the centre: RAM fills leftwards from it, SWAP
        # rightwards, so each tip points the way its half grows.
        ram_bar = self.build_gauge_bar(
            ram_blocks, ram_used_percent, ram_color,
            grow="left", track_color=ram_color,
        )
        swap_bar = self.build_gauge_bar(
            swap_blocks, swap_used_percent, swap_color, track_color=swap_color,
        )
        bar = f"{ram_bar}{swap_bar}"

        # Label line: " " + L1 + " " + L2 + " " + L3 + " " + L4 -> 4 spaces + 4*label_w,
        # padded to total_width so it lines up with the bar quarters below it.
        label_w = (total_width - 4) // 4
        if label_w < 6:  # "FREE 3 GB" style labels need ~6 cells to mean anything
            # No room for four readable labels: show the bar alone rather than a line
            # that cannot fit (which would wrap or be cut mid-value).
            return bar
        # RAM half: Free outside (left), Used inside (right). SWAP half: Used inside (left), Free outside (right).
        ram_free_label = align(f"FREE {format_size(free_ram, in_gb=True)}", label_w, "left")
        ram_used_label = align(f"{format_size(ram_usage, in_gb=True)} RAM", label_w, "right")
        swap_used_label = align(f"{format_size(swap_usage, in_gb=True)} SWAP", label_w, "left")
        swap_free_label = align(f"FREE {format_size(free_swap, in_gb=True)}", label_w, "right")

        labels = (
            f" [{ram_color} italic]{ram_free_label}[/] [{ram_color}]{ram_used_label}[/] "
            f"[{swap_color}]{swap_used_label}[/] [{swap_color} italic]{swap_free_label}[/]"
        )
        labels = pad_to_width(labels, 4 + 4 * label_w, total_width)
        return f"{labels}\n{bar}"

    def get_dual_plot(self, width: int, height: int) -> str:
        """Create a dual plot showing RAM and SWAP usage over time, sized to (width, height)."""
        if not self.ram_history:
            return "No data yet..."
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)

        plt.clear_figure()
        plt.plot_size(height=height, width=width)
        plt.theme("pro")

        # Create negative values for SWAP to show it below zero
        negative_swap = [-x - 0.1 for x in self.swap_history]
        positive_ram = [x + 0.1 for x in self.ram_history]

        # Y-axis max from current data in the window (not fixed to total RAM/SWAP)
        max_ram_in_window = max(self.ram_history) if self.ram_history else 0
        max_swap_in_window = max(self.swap_history) if self.swap_history else 0
        y_limit = max(max_ram_in_window, max_swap_in_window, 2)  # At least 2 GB scale

        # Set y-axis limits symmetrically around zero
        plt.ylim(-y_limit, y_limit)

        # Create custom y-axis ticks with GB labels
        num_ticks = max(2, min(5, height - 1))
        tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

        y_ticks = []
        y_labels = []

        for i in range(num_ticks):
            value = -y_limit + i * tick_step
            y_ticks.append(value)
            # Tick labels: numbers only; unit is on top of plot (──GB──┐)
            y_labels.append(str(round(abs(value))) if value != 0 else "0")

        plt.yticks(y_ticks, y_labels)

        # No plotext labels: the bar below already names RAM and SWAP in the same
        # colours, and plotext's legend renderer raises IndexError at some panel
        # geometries, which used to disable the whole widget.
        plt.plot(positive_ram, marker="braille")
        plt.plot(negative_swap, marker="braille")

        # Add a zero line
        plt.hline(0.00)

        plt.yfrequency(5)
        plt.xfrequency(0)

        ram_color = get_rich_color("memory_ram_used", "#FF8C00")
        swap_color = get_rich_color("memory_swap", "#00FFFF")
        # RAM is plotted first -> [blue], SWAP second -> [green]; same colours as the bar.
        build = recolor(
            ansi2rich(plt.build()).replace("\x1b[0m", ""),
            {"blue": ram_color, "green": swap_color},
        ).replace("──────┐", "──GB──┐")
        if len(self.ram_history) >= self.ram_history.maxlen:
            build = substitute_plot_timeframe(build, self.ram_history.maxlen)
        return self.finish_plot(build, width, height)

    def rerender(self) -> None:
        """Re-draw plot and bar from the stored history at the current region size."""
        if not self.ram_history:
            return
        plot_width, plot_height = self.plot_region("#history-plot", reserve_height=self.BAR_HEIGHT)
        bar_width, _ = self.region_size("#current-value")
        try:
            self.query_one("#history-plot").update(self.get_dual_plot(plot_width, plot_height))
            self.query_one("#current-value").update(
                self.create_center_bar(
                    self.ram_history[-1],
                    self.swap_history[-1],
                    total_width=bar_width,
                )
            )
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout change)

    def update_content(self, memory_info, swap_info, meminfo=None, commit_ratio=None, top_processes=None, memory_history=None):
        # Add current values to history
        ram_used_gb = memory_info.used / 1024 / 1024 / 1024
        swap_used_gb = swap_info.used / 1024 / 1024 / 1024
        self.ram_history.append(ram_used_gb)
        self.swap_history.append(swap_used_gb)

        # Update total RAM and SWAP sizes
        self.total_ram = memory_info.total / 1024 / 1024 / 1024
        self.total_swap = swap_info.total / 1024 / 1024 / 1024
        self.max_mem = max(self.total_ram, self.total_swap)  # For plot scaling

        logger.info(
            "ram_used_gb: %.2f, ram_total_gb: %.2f, swap_used_gb: %.2f, swap_total_gb: %.2f",
            ram_used_gb, self.total_ram, swap_used_gb, self.total_swap,
        )

        # Via set_display_title, not border_title: assigning the border directly
        # would wipe the alert marker and the job-focus suffix on every render.
        self.set_display_title(
            f"RAM [{format_size(self.total_ram, in_gb=True)}] "
            f"SWAP [{format_size(self.total_swap, in_gb=True)}]"
        )

        self.rerender()

import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align, format_size, substitute_plot_timeframe
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.memory")

class MemoryWidget(MetricWidget):
    """Memory (RAM) usage display widget with dual plots for RAM and SWAP over time."""
    def __init__(self, title: str = "Memory", id: str = None):
        
        DEFAULT_CSS = """
        MemoryWidget {
            height: 100%;
            border: solid green;
            background: $surface;
            layout: vertical;
            overflow-y: auto;
        }
        
        .metric-title {
            text-align: left;
        }
        
        .current-value {
            height: 2fr;
        }
        """
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
        yield Static("", id="current-value", classes="current-value")

    def create_center_bar(
        self, ram_usage: float, swap_usage: float, total_width: int
    ) -> str:
        """Create a center bar showing used/free RAM and used/free SWAP with four different colors.

        total_width must match the widget content width (same as plot_width) so the bar
        and label line do not overflow. Label line uses structure " [L1] [L2] [L3] [L4]"
        (11 chars structure + 4*label_w); bar line is exactly total_width chars.
        """
        ram_usage = max(0.0, float(ram_usage))
        swap_usage = max(0.0, float(swap_usage))
        total_width = max(10, int(total_width))

        free_ram = max(0.0, self.total_ram - ram_usage)
        free_swap = max(0.0, self.total_swap - swap_usage)

        ram_used_percent = min(ram_usage/self.total_ram if self.total_ram > 0 else 0, 1)
        ram_free_percent = min(free_ram/self.total_ram if self.total_ram > 0 else 0, 1)
        swap_used_percent = min(swap_usage/self.total_swap if self.total_swap > 0 else 0, 1)
        swap_free_percent = min(free_swap/self.total_swap if self.total_swap > 0 else 0, 1)

        total_blocks = total_width
        half_blocks = total_blocks // 2

        ram_used_blocks = int(half_blocks * ram_used_percent)
        ram_free_blocks = half_blocks - ram_used_blocks
        swap_used_blocks = int(half_blocks * swap_used_percent)
        swap_free_blocks = total_blocks - ram_used_blocks - ram_free_blocks - swap_used_blocks
        ram_free_blocks = max(0, ram_free_blocks)
        swap_free_blocks = max(0, swap_free_blocks)

        ram_color = get_rich_color("memory_ram_used", "#FF8C00")
        swap_color = get_rich_color("memory_swap", "#00FFFF")

        ram_free_bar = f"[{ram_color}]{'─' * ram_free_blocks}[/]"
        ram_used_bar = f"[{ram_color}]{'█' * ram_used_blocks}[/]"
        swap_used_bar = f"[{swap_color}]{'█' * swap_used_blocks}[/]"
        swap_free_bar = f"[{swap_color}]{'─' * swap_free_blocks}[/]"
        bar = f"{ram_free_bar}{ram_used_bar}{swap_used_bar}{swap_free_bar}"

        # Label line: " " + L1 + " " + L2 + " " + L3 + " " + L4 = 4 spaces + 4*label_w = total_width
        # So each of the 4 segments has width label_w and aligns with bar quarters (RAM half, SWAP half).
        label_w = max(1, (total_width - 4) // 4)
        # RAM half: Free outside (left), Used inside (right). SWAP half: Used inside (left), Free outside (right).
        ram_free_label = align(f"FREE {format_size(free_ram, in_gb=True)}", label_w, "left")
        ram_used_label = align(f"{format_size(ram_usage, in_gb=True)} RAM", label_w, "right")
        swap_used_label = align(f"{format_size(swap_usage, in_gb=True)} SWAP", label_w, "left")
        swap_free_label = align(f"FREE {format_size(free_swap, in_gb=True)}", label_w, "right")

        return (
            f" [{ram_color} italic]{ram_free_label}[/] [{ram_color}]{ram_used_label}[/] "
            f"[{swap_color}]{swap_used_label}[/] [{swap_color} italic]{swap_free_label}[/]\n{bar}"
        )

    def get_dual_plot(self) -> str:
        """Create a dual plot showing RAM and SWAP usage over time."""
        if not self.ram_history:
            return "No data yet..."

        # Validate plot dimensions
        plot_height = max(1, getattr(self, "plot_height", 10) - 1)
        plot_width = max(10, getattr(self, "plot_width", 40))
        
        if plot_height <= 0 or plot_width <= 0:
            return "Initializing..."

        plt.clear_figure()
        plt.plot_size(height=plot_height, width=plot_width)
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
        num_ticks = min(5, self.plot_height - 1)
        tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

        y_ticks = []
        y_labels = []

        for i in range(num_ticks):
            value = -y_limit + i * tick_step
            y_ticks.append(value)
            # Tick labels: numbers only; unit is on top of plot (──GB──┐)
            y_labels.append(str(round(abs(value))) if value != 0 else "0")

        plt.yticks(y_ticks, y_labels)

        # Plot RAM usage (positive values)
        plt.plot(positive_ram, marker="braille", label="RAM")

        # Plot SWAP usage (negative values)
        plt.plot(negative_swap, marker="braille", label="SWAP")

        # Add a zero line
        plt.hline(0.00)

        plt.yfrequency(5)
        plt.xfrequency(0)

        ram_color = get_rich_color("memory_ram_used", "#FF8C00")
        swap_color = get_rich_color("memory_swap", "#00FFFF")
        build = ansi2rich(plt.build())
        build = build.replace("\x1b[0m", "")
        build = build.replace("[blue]", f"[{ram_color}]")
        build = build.replace("[green]", f"[{swap_color}]")
        build = build.replace("──────┐", "──GB──┐")
        if len(self.ram_history) >= self.ram_history.maxlen:
            build = substitute_plot_timeframe(build, self.ram_history.maxlen)
        return build

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

        self.border_title = f"RAM [{format_size(self.total_ram, in_gb=True)}] SWAP [{format_size(self.total_swap, in_gb=True)}]"
        
        # Calculate total width for the center bar (use same calculation as plot)
        # Use plot_width which is set by on_resize in base class (width - 3)
        total_width = max(10, getattr(self, "plot_width", self.size.width - 3))
        try:
            self.query_one("#history-plot").update(self.get_dual_plot())
            self.query_one("#current-value").update(
                self.create_center_bar(
                    ram_used_gb,
                    swap_used_gb,
                    total_width=total_width
                )
            )
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout change)
        
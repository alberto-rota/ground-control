from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align
import numpy as np


class GPUWidget(MetricWidget):
    """Widget for GPU monitoring with dual plots for GPU RAM and Usage and a bar below."""

    def __init__(
        self, title: str, id: str = None, color: str = "green", history_size: int = 120
    ):
        super().__init__(title=title, color=color, history_size=history_size, id=id)
        self.gpu_ram_history = deque(maxlen=history_size)
        self.gpu_usage_history = deque(maxlen=history_size)
        # self.max_val = 100  # initial max value; will update based on incoming data
        self.first = True
        self.title = title
        self.border_title = title #f"{title} [green]GB/%[/]"
        self.usage_is_available = True

    def compose(self) -> ComposeResult:
        yield Static("", id="history-plot", classes="metric-plot")
        yield Static("", id="current-value", classes="metric-value")

    def create_center_bar(
        self, gpu_ram: float, gpu_usage: float, total_width: int
    ) -> str:
        gpu_ram_withunits = align(f"{gpu_ram:.1f} GB", 12, "right")
        gpu_usage_withunits = align(f"{gpu_usage:.1f} %", 14, "left")
        aval_width = total_width
        half_width = aval_width // 2
        # Compute the percentage relative to the current maximum value
        gpu_ram_percent = min((gpu_ram / self.max_val) * 100, 100)
        gpu_usage_percent = gpu_usage

        ram_blocks = int((half_width * gpu_ram_percent) / 100)
        usage_blocks = int((half_width * gpu_usage_percent) / 100)

        left_bar = (
            (
                f"[green]{'█' * (ram_blocks-1)}{''}[/][white]{'─' * (half_width - ram_blocks)}[/]"
            )
            if ram_blocks >= 1
            else f"{'─' * half_width}"
        )
        right_bar = (
            (
                f"[cyan]{'█' * (usage_blocks-3)}{''}[/]{'─' * (half_width - usage_blocks)}"
            )
            if usage_blocks >= 1
            else f"{'─' * half_width}"
        )

        if gpu_ram_percent >= 90:
            gpu_ram_withunits = f"[red]{gpu_ram_withunits}[/]"
            left_bar = left_bar.replace("[green]", "[red]")
        return f"{gpu_ram_withunits} {left_bar}│{right_bar} {gpu_usage_withunits}"

    def get_dual_plot(self) -> str:
        if not self.gpu_ram_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width)
        plt.theme("pro")

        # Plot GPU RAM as positive values and GPU Usage as negative
        positive_series = [x + 0.1 for x in self.gpu_usage_history]
        if not self.usage_is_available:
            positive_series = [-100 for x in self.gpu_usage_history]
        negative_series = [
            -100 + (x / self.max_val) * 100 for x in self.gpu_ram_history
        ]

        # Determine symmetric y-axis limits based on incoming data
        # max_value = 100
        # y_limit = max_value if max_value >= 10 else 10
        # # self.max_val = y_limit

        # if self.usage_is_available:
        plt.ylim(-100, 100)
        if not self.usage_is_available:
            plt.ylim(-100, 0)
        plt.plot(
            positive_series,
            marker="braille",
            label="Usage UNAV" if not self.usage_is_available else "Usage",
        )
        plt.plot(negative_series, marker="braille", label="RAM")
        if self.usage_is_available:
            plt.hline(0.0)
        plt.yfrequency(5)
        plt.xfrequency(0)

        current_yticks = [-100, -50, 0, 50, 100]
        plt.yticks(current_yticks, [0, 50, 100, 50, 100])
        if not self.usage_is_available:
            current_yticks = [-100, -75, -50, -25, 0]
            plt.yticks(current_yticks, [0, 25, 50, 75, 100])
        return (
            ansi2rich(plt.build())
            .replace("\x1b[0m", "")
            .replace("[blue]", "[cyan]")
            .replace("[green]", "[green]")
            .replace("-", " ")
            .replace("──────┐","─GB─%─┐")
            
        )

    def update_content(
        self, gpu_name, gpu_usage, mem_used, mem_total
    ):  # gpu_ram: float, gpu_usage: float, mem_used: float = None, mem_total: float = None):
        if self.first:
            self.first = False
            return
        self.gpu_ram_history.append(mem_used)
        self.gpu_usage_history.append(gpu_usage)
        self.max_val = mem_total
        self.usage_is_available = gpu_usage >= -0.5
        total_width = (
            self.size.width
            - len(f"{mem_used:6.1f} % ")
            - len(f"{gpu_usage:6.1f} %")
            - 2
        )
        self.query_one("#history-plot").update(self.get_dual_plot())
        self.query_one("#current-value").update(
            self.create_center_bar(mem_used, gpu_usage, total_width=total_width)
        )

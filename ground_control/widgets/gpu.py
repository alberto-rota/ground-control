from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from utils.formatting import ansi2rich

class GPUWidget(MetricWidget):
    """Widget for GPU metrics with dual plots."""
    def __init__(self, title: str, history_size: int = 120):
        super().__init__(title, "yellow", history_size)
        self.util_history = deque(maxlen=history_size)
        self.mem_history = deque(maxlen=history_size)

    def compose(self) -> ComposeResult:
        yield Static("", id="gpu-util-value", classes="metric-value")
        yield Static("", id="gpu-util-plot", classes="metric-plot")
        yield Static("", id="gpu-mem-value", classes="metric-value")
        yield Static("", id="gpu-mem-plot", classes="metric-plot")

    def update_content(self, gpu_util: float, mem_used: float, mem_total: float):
        self.util_history.append(gpu_util)
        mem_percent = (mem_used / mem_total) * 100
        self.mem_history.append(mem_percent)
        mem_used_str = f"{mem_used:.1f}GB"
        gpu_util_str = f"{gpu_util:.1f}%"

        bar_width = self.size.width - 10
        bar = self.create_gradient_bar(gpu_util, bar_width, color="green")
        self.query_one("#gpu-util-value").update(f"{'GUTL':<4}{bar}{gpu_util_str:>7}")
        self.query_one("#gpu-util-plot").update(self.get_plot(
            data=self.util_history, 
            height=self.plot_height // 2-1,
            color="green"
        ))

        bar = self.create_gradient_bar(mem_percent, bar_width, color="cyan")
        self.query_one("#gpu-mem-value").update(f"{'GMEM':<4}{bar}{mem_used_str:>7}")
        self.query_one("#gpu-mem-plot").update(self.get_plot(
            data=self.mem_history, 
            height=self.plot_height // 2,
            color="cyan"
        ))

    def get_plot(self, data: deque, height: int, color: str = None, y_min: float = 0, y_max: float = 100) -> str:
        if not data:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=height, width=self.plot_width+1)
        plt.theme("pro")
        plt.plot(list(data), marker="braille")
        plt.ylim(y_min, y_max)
        plt.yfrequency(3)
        plt.xfrequency(0)
        return ansi2rich(plt.build()).replace("\x1b[0m","").replace("[blue]",f"[{color}]")

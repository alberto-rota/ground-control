from collections import deque
from textual.widgets import Static
from textual.message import Message
import plotext as plt
from ..utils.formatting import ansi2rich
from textual.scroll_view import ScrollView
from textual.geometry import Size
class MetricWidget(Static):
    """Base widget for system metrics with plot."""
    DEFAULT_CSS = """
    MetricWidget {
        height: 100%;
        border: solid green;
        background: $surface;
        overflow-y: auto;
        overflow-x: auto;
    }
    
    .metric-title {
        text-align: left;
        height: 1;
    }
    
    .metric-value {
        text-align: left;
        height: 1;
    }
    .cpu-metric-value {
        text-align: left;
    }
    
    .metric-plot {
        height: 1fr;
    }
    """

    def __init__(self, title: str, id: str, color: str = "blue", history_size: int = 120):
        super().__init__(id=id)
        self.title = title
        self.color = color
        self.history = deque(maxlen=history_size)
        self.plot_width = 0
        self.plot_height = 0

    def on_resize(self, event: Message) -> None:
        """Handle resize events to update plot dimensions."""
        self.plot_width = event.size.width - 3
        self.plot_height = event.size.height - 3
        self.virtual_size = Size(event.size.height/4,event.size.width)
        self.refresh()

    def get_plot(self, y_min=0, y_max=100) -> str:
        if not self.history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height, width=self.plot_width)
        plt.theme("pro")
        plt.plot(list(self.history), marker="braille")
        plt.ylim(y_min, y_max)
        plt.xfrequency(0)
        plt.yfrequency(3)
        return ansi2rich(plt.build()).replace("\x1b[0m","").replace("[blue]",f"[{self.color}]")
    
    def create_gradient_bar(self, value: float, width: int = 20, color: str = None) -> str:
        """Creates a gradient bar with custom base color."""
        filled = int((width * value) / 100)
        if filled > width * 0.8:
            color = "bright_red"
        empty = width - filled
        
        if filled == 0:
            return "─" * width
        
        if value < 20:
            return f"[{self.color if color is None else color}]{'█' * filled}[/]{'─' * empty}"
        
        bar = (f"[{self.color if color is None else color}]{'█' * filled}[/]"
               f"{'─' * empty}")
        
        return bar

    def format_metric_line(self, label: str, value: float, suffix: str = "%") -> str:
        """Creates a consistent metric line with label, bar, and value."""
        bar = self.create_gradient_bar(value)
        return f"{label:<4}{bar}{value:>7.1f}{suffix}"

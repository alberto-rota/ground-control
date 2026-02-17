from collections import deque
from textual.widgets import Static
from textual.message import Message
from textual.css.query import NoMatches
import plotext as plt
from ..utils.formatting import ansi2rich
from ..utils.colors import get_rich_color, load_colors
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
        min-height: 10;
    }
    """

    def __init__(self, title: str, id: str, color: str = "blue", history_size: int = 120):
        super().__init__(id=id)
        self.title = title
        # If color is a hex value or color name, use it; otherwise use default
        # Note: We use _color_config to avoid conflict with Textual's colors attribute
        if color.startswith("#"):
            self.color = color
        else:
            # Try to get color from config by widget type, fallback to provided color
            self.color = get_rich_color("default_plot", color)
        self.history = deque(maxlen=history_size)
        self.plot_width = 0
        self.plot_height = 0

    def on_resize(self, event: Message) -> None:
        """Handle resize events to update plot dimensions.

        Uses the actual .metric-plot container size when available so width/height
        match the plot region (not the whole widget). Fallback: widget size minus
        a small margin. Min 8x30 keeps plotext readable.
        """
        # #region agent log
        used_plot_region = False
        pw, ph = 0, 0
        try:
            plot_region = self.query_one(".metric-plot")
            pw = getattr(plot_region.size, "width", 0) or 0
            ph = getattr(plot_region.size, "height", 0) or 0
            if pw > 0 and ph > 0:
                self.plot_width = max(30, pw)
                self.plot_height = max(8, ph)
                rows = max(1, ph)
                self.virtual_size = Size(rows, pw)
                self.refresh()
                used_plot_region = True
                try:
                    f = open("/home/atuin/v120bb/v120bb18/ground-control/.cursor/debug.log", "a"); f.write('{"timestamp":' + str(int(__import__("time").time()*1000)) + ',"location":"base.py:on_resize","message":"resize using plot_region","data":{"event_w":' + str(event.size.width) + ',"event_h":' + str(event.size.height) + ',"pw":' + str(pw) + ',"ph":' + str(ph) + ',"plot_width":' + str(self.plot_width) + ',"plot_height":' + str(self.plot_height) + ',"used_plot_region":true},"hypothesisId":"H1,H3"}\n'); f.close()
                except Exception: pass
                return
        except NoMatches:
            pass
        w = max(0, event.size.width - 3)
        h = max(0, event.size.height - 3)
        self.plot_width = max(30, w)
        self.plot_height = max(8, h)
        rows = max(1, event.size.height // 4)
        self.virtual_size = Size(rows, event.size.width)
        self.refresh()
        try:
            f = open("/home/atuin/v120bb/v120bb18/ground-control/.cursor/debug.log", "a"); f.write('{"timestamp":' + str(int(__import__("time").time()*1000)) + ',"location":"base.py:on_resize","message":"resize fallback","data":{"event_w":' + str(event.size.width) + ',"event_h":' + str(event.size.height) + ',"pw":' + str(pw) + ',"ph":' + str(ph) + ',"plot_width":' + str(self.plot_width) + ',"plot_height":' + str(self.plot_height) + ',"used_plot_region":false},"hypothesisId":"H1,H3,H5"}\n'); f.close()
        except Exception: pass
        # #endregion

    def get_plot(self, y_min=0, y_max=100) -> str:
        if not self.history:
            return "No data yet..."

        plt.clear_figure()
        h = max(1, getattr(self, "plot_height", 10))
        w = max(10, getattr(self, "plot_width", 40))
        plt.plot_size(height=h, width=w)
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
            color = get_rich_color("high_value", "#FF0000")
        empty = width - filled
        
        if filled == 0:
            return "─" * width
        
        bar_color = self.color if color is None else color
        
        if value < 20:
            return f"[{bar_color}]{'█' * filled}[/]{'─' * empty}"
        
        bar = (f"[{bar_color}]{'█' * filled}[/]"
               f"{'─' * empty}")
        
        return bar

    def format_metric_line(self, label: str, value: float, suffix: str = "%") -> str:
        """Creates a consistent metric line with label, bar, and value."""
        bar = self.create_gradient_bar(value)
        return f"{label:<4}{bar}{value:>7.1f}{suffix}"

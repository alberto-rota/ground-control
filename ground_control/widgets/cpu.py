from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget

class CPUWidget(MetricWidget):
    """CPU usage display widget."""
    DEFAULT_CSS = """
    CPUWidget {
        height: 100%;
        border: solid green;
        background: $surface;
        layout: vertical;
    }
    
    .metric-title {
        text-align: left;
    }
    
    .cpu-metric-value {
        height: 1fr;
    }
    """
    
    def compose(self) -> ComposeResult:
        yield Static("", id="cpu-content", classes="cpu-metric-value")

    def update_content(self, cpu_percentages, cpu_freqs, mem_percent):
        bar_width = self.size.width - 16
        
        lines = []
        for idx, (percent, freq) in enumerate(zip(cpu_percentages, cpu_freqs)):
            core_name = f"Core {idx}: "
            percentage = f"{percent:5.1f}%"
            color = "green" if idx % 2 == 0 else "dark_green"
            bar = self.create_gradient_bar(percent, bar_width, color="dodger_blue1")
            line = f"{core_name:<4}{bar}{percentage:>7}"
            lines.append(line)
            
        bar_width = self.size.width - 16
        bar = self.create_gradient_bar(mem_percent, bar_width, color="orange1")
        ram_line = f"{'RAM   : ':<4}{bar}{mem_percent:>7.1f}%"
        lines.append(ram_line)
        
        self.query_one("#cpu-content").update("\n".join(lines))

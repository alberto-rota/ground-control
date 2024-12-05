from textual.app import ComposeResult
from textual.widgets import Static
from ground_control.base import MetricWidget
import plotext as plt
from ground_control.utils.formatting import ansi2rich

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
    def __init__(self, title: str, id: str = None):
        super().__init__(title=title,id=id)
        self.title = title
        
    def compose(self) -> ComposeResult:
        yield Static("", id="cpu-content", classes="cpu-metric-value")

    def create_bar_chart(self, cpu_percentages, cpu_freqs, mem_percent, width, height):
        plt.clear_figure()
        plt.theme("pro")
        plt.plot_size(width=width+4, height=len(cpu_percentages) + 2)
        # plt.xticks([1, 25, 50, 75, 100],["0", "25", "50", "75", "100"])  # Show more x-axis labels
        plt.xfrequency(0)
        plt.xlim(5, 100)  # Set x-axis limits to 0-100%
        # Create labels for CPU cores and RAM
        labels = [f" C{i}" for i in range(len(cpu_percentages))]
        # labels.append("RAM")
        # Combine CPU percentages with RAM percentage
        corevalues = list(cpu_percentages) #+ [-10]
        # ramvalues = [0] * len(cpu_percentages)*2 + [mem_percent]
        # Create horizontal bar chart
        plt.bar(
            labels,
            corevalues,
            orientation="h"
        )        
        cpubars = ansi2rich(plt.build()).replace("\x1b[0m","").replace("\x1b[1m","").replace("[blue]","[blue]").replace("[green]","[green]")
        
        plt.clear_figure()
        plt.theme("pro")
        plt.plot_size(width=width+4, height=1 + 3)
        plt.xticks([1, 25, 50, 75, 100],["0", "25", "50", "75", "100"])  # Show more x-axis labels
        plt.xlim(5, 100)  # Set x-axis limits to 0-100%
        # Create labels for CPU cores and RAM
        labels = ["RAM"]
        # labels.append("RAM")
        # Combine CPU percentages with RAM percentage
        corevalues = list(cpu_percentages) #+ [-10]
        # ramvalues = [0] * len(cpu_percentages)*2 + [mem_percent]
        # Create horizontal bar chart
        plt.bar(
            labels,
            [mem_percent],
            orientation="h"
        )        
        rambars =  ansi2rich(plt.build()).replace("\x1b[0m","").replace("\x1b[1m","").replace("[blue]","[orange3]").replace("[green]","[green]")
        return cpubars + rambars
    def update_content(self, cpu_percentages, cpu_freqs, mem_percent):
        # Calculate available space for the plot
        # Subtract some padding for borders and margins
        width = self.size.width - 4
        height = self.size.height - 2
        
        # Create and update the bar chart
        chart = self.create_bar_chart(
            cpu_percentages,
            cpu_freqs,
            mem_percent,
            width,
            height
        )
        
        self.query_one("#cpu-content").update(chart)
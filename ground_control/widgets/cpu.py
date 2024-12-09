from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align

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
        
    def create_disk_usage_bar(self,disk_used: float, disk_total: float, total_width: int = 40) -> str:
        if disk_total == 0:
            return "No disk usage data..."
        
        usage_percent = (disk_used / disk_total) * 100
        available = disk_total - disk_used

        usable_width = total_width
        used_blocks = int((usable_width * usage_percent) / 100)
        free_blocks = usable_width - used_blocks

        usage_bar = f"[magenta]{'█' * used_blocks}[/][cyan]{'█' * free_blocks}[/]"

        used_gb = disk_used / (1024 ** 3)
        available_gb = available / (1024 ** 3)
        used_gb_txt = align(f"{used_gb:.1f} GB USED",total_width//2-2,"left")
        free_gb_txt = align(f"FREE: {available_gb:.1f} GB ",total_width//2-2,"right")
        return f' [magenta]{used_gb_txt}[/]DISK[cyan]{free_gb_txt}[/]\n {usage_bar}'

    def create_bar_chart(self, cpu_percentages, cpu_freqs, mem_percent, disk_used, disk_total, width, height):
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
        
        # plt.clear_figure()
        # plt.theme("pro")
        # plt.plot_size(width=width+4, height=1 + 3)
        # plt.xticks([1, 25, 50, 75, 100],["0", "25", "50", "75", "100"])  # Show more x-axis labels
        # plt.xlim(5, 100)  # Set x-axis limits to 0-100%
        # # Create labels for CPU cores and RAM
        # labels = ["DSK"]
        # # labels.append("RAM")
        # # Combine CPU percentages with RAM percentage
        # corevalues = list(cpu_percentages) #+ [-10]
        # # ramvalues = [0] * len(cpu_percentages)*2 + [mem_percent]
        # # Create horizontal bar chart
        # plt.stacked_bar(
        #     labels,
        #     [[disk_used], [disk_total-disk_used]],
        #     orientation="h"
        # )        
        # diskbars =  ansi2rich(plt.build()).replace("\x1b[0m","").replace("\x1b[1m","").replace("[blue]","[magenta]").replace("[green]","[cyan]")
        return cpubars + rambars #+ str([[disk_used/(1024**3)], [disk_total/(1024**3)-disk_used/(1024**3)]])
    def update_content(self, cpu_percentages, cpu_freqs, mem_percent, disk_used, disk_total):
        # Calculate available space for the plot
        # Subtract some padding for borders and margins
        width = self.size.width - 4
        height = self.size.height - 2
        
        # Create and update the bar chart
        cpuram_chart = self.create_bar_chart(
            cpu_percentages,
            cpu_freqs,
            mem_percent,
            disk_used,
            disk_total,
            width,
            height
        )
        disk_chart = self.create_disk_usage_bar(disk_used, disk_total,width+2)
        self.query_one("#cpu-content").update(cpuram_chart+"\n"+disk_chart)
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static, Sparkline
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
        overflow-y: auto;
    }
    
    .metric-title {
        text-align: left;
    }
    
    .cpu-metric-value {
        height: 1fr;
    }
    """
    def __init__(self, title: str, id: str = None):
        super().__init__(title=title, id=id)
        self.title = title
        self.border_title = title#f"{title} [green]%[/]"
        
    def compose(self) -> ComposeResult:
        yield Static("", id="cpu-content", classes="cpu-metric-value")
        
    def create_disk_usage_bar(self, disk_used: float, disk_total: float, total_width: int = 40) -> str:
        if disk_total == 0:
            return "No disk usage data..."
        
        usage_percent = (disk_used / disk_total) * 100
        available = disk_total - disk_used

        usable_width = total_width - 2
        used_blocks = int((usable_width * usage_percent) / 100)
        free_blocks = usable_width - used_blocks

        usage_bar = f"[magenta]{'█' * used_blocks}[/][cyan]{'█' * free_blocks}[/]"

        used_gb = disk_used / (1024 ** 3)
        available_gb = available / (1024 ** 3)
        used_gb_txt = align(f"{used_gb:.1f} GB USED", total_width // 2 - 2, "left")
        free_gb_txt = align(f"FREE: {available_gb:.1f} GB ", total_width // 2 - 2, "right")
        return f' [magenta]{used_gb_txt}[/]DISK[cyan]{free_gb_txt}[/]\n {usage_bar}'

    def create_bar_chart(self, cpu_percentages, cpu_freqs, mem_percent, disk_used, disk_total, width, height):
        # If the full CPU chart fits vertically, use the single chart approach.
        cpu_percentages = [int(x) for x in cpu_percentages]
        if len(cpu_percentages) + 2 <= height-2:
            plt.clear_figure()
            plt.theme("pro")
            # plt.xfrequency(0)
            # plt.xlim(6, 100)
            labels = [f" C{i}" for i in range(len(cpu_percentages))]
            if len(cpu_percentages) + 2 <= height-2:
                orientation = "v" 
                plt.ylim(6, 100)
                plt.plot_size(width=width+1, height=height+2)
                
                # plt.yfrequency(0)
                
            else:
                orientation = "h"
                plt.xlim(6, 100)
                plt.xfrequency(0)
                plt.plot_size(width=width+1, height=len(cpu_percentages) + 2)
                
            plt.bar(labels, list(cpu_percentages), orientation=orientation)
            cpubars = ansi2rich(plt.build()).replace("\x1b[0m", "").replace("\x1b[1m", "").replace("──────┐","────%─┐")

            
            # plt.clear_figure()
            # plt.theme("pro")
            # plt.plot_size(width=width+1, height=4)
            # plt.xticks([1, 25, 50, 75, 100], ["0", "25", "50", "75", "100"])
            # plt.xlim(5, 100)
            # plt.bar(["RAM"], [mem_percent], orientation="h")
            # rambars = ansi2rich(plt.build()).replace("blue","orange1").replace("──────┐","────%─┐")

            return cpubars#+ rambars
        else:
            # Group CPU cores to avoid an overly tall chart.
            # Maximum rows per group is the available height minus 2 (for borders/margins).
            max_rows = height
            groups = [cpu_percentages[i:i+max_rows] for i in range(0, len(cpu_percentages), max_rows)]
            num_groups = len(groups)
            # Divide available width among the groups (with a minimum width).
            group_width = max(1, width // num_groups)
            group_charts = []
            for idx, group in enumerate(groups):
                plt.clear_figure()
                plt.theme("pro")
                chart_height = len(group) +2
                plt.plot_size(width=group_width+1, height=chart_height)
                plt.xfrequency(0)
                plt.xlim(6, 100)
                start_index = idx * max_rows
                labels = [f" C{start_index + i}" for i in range(len(group))]
                plt.bar(labels, group, orientation="h")
                chart_str = ansi2rich(plt.build()).replace("\x1b[0m", "").replace("\x1b[1m", "")
                group_charts.append(chart_str)
            # Combine the group charts horizontally.
            group_lines = [chart.splitlines() for chart in group_charts]
            max_lines = max(len(lines) for lines in group_lines)
            for lines in group_lines:
                while len(lines) < max_lines:
                    lines.append(" " * group_width)
            combined_lines = []
            for i in range(max_lines):
                combined_line = "".join(lines[i] for lines in group_lines)
                combined_lines.append(combined_line)
            combined_cpu_chart = "\n".join(combined_lines)
            
            # plt.clear_figure()
            # plt.theme("pro")
            # plt.plot_size(width=width+2, height=4)
            # plt.xticks([1, 25, 50, 75, 100], ["0", "25", "50", "75", "100"])
            # plt.xlim(5, 100)
            # plt.bar(["RAM"], [mem_percent], orientation="h")
            rambars = ansi2rich(plt.build()).replace("\x1b[0m", "").replace("\x1b[1m", "")
            
            return combined_cpu_chart#+"\n"+ rambars

    def update_content(self, cpu_percentages, cpu_freqs, mem_percent, disk_used, disk_total):
        # Calculate available width and height inside the widget.
        width = self.size.width - 1
        height = self.size.height - 2
        
        cpuram_chart = self.create_bar_chart(
            cpu_percentages,
            cpu_freqs,
            mem_percent,
            disk_used,
            disk_total,
            width,
            height
        )
        disk_chart = self.create_disk_usage_bar(disk_used, disk_total, width+2)
        self.query_one("#cpu-content").update(cpuram_chart)# + "\n" + disk_chart)

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
import psutil
from ..utils.formatting import ansi2rich, align

class MemoryWidget(MetricWidget):
    """Memory (RAM) usage display widget."""
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
    
    .memory-metric-value {
        height: 1fr;
        min-height: 20;
    }
    """
    def __init__(self, title: str = "Memory", id: str = None):
        super().__init__(title=title, id=id, color="orange1")
        self.title = title
        self.border_title = title#f"{title} [orange1]GB[/]"
        
    def compose(self) -> ComposeResult:
        yield Static(id="memory-content", classes="memory-metric-value")
        
    def create_memory_usage_bar(self, used_bytes: float, total_bytes: float, total_width: int = 40) -> str:
        if total_bytes == 0:
            return "No memory data available..."
        
        usage_percent = (used_bytes / total_bytes) * 100
        available = total_bytes - used_bytes

        usable_width = total_width - 2
        used_blocks = int((usable_width * usage_percent) / 100)
        free_blocks = usable_width - used_blocks

        usage_bar = f"[orange1]{'█' * used_blocks}[/][cyan]{'█' * free_blocks}[/]"

        used_gb = used_bytes / (1024 ** 3)
        available_gb = available / (1024 ** 3)
        total_gb = total_bytes / (1024 ** 3)
        used_gb_txt = align(f"{used_gb:.1f} GB USED", total_width // 2 - 2, "left")
        free_gb_txt = align(f"FREE: {available_gb:.1f} GB ", total_width // 2 - 2, "right")
        
        return f' [orange1]{used_gb_txt}[/]RAM[cyan]{free_gb_txt}[/]\n {usage_bar} \n[white]{usage_percent:.1f}% of {total_gb:.1f} GB[/]'

    def get_top_memory_processes(self, count=5):
        """Get the top memory-consuming processes."""
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'memory_percent']):
            try:
                processes.append({
                    'pid': proc.info['pid'],
                    'name': proc.info['name'],
                    'memory_percent': proc.info['memory_percent']
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        # Sort by memory usage (descending) and take top count
        top_processes = sorted(processes, key=lambda x: x['memory_percent'], reverse=True)[:count]
        return top_processes

    def create_detailed_view(self, memory_info, swap_info, width, height, meminfo=None, commit_ratio=None, top_processes=None):
        """Create a detailed view of memory metrics."""
        # Create a visualization of memory usage
        
        # Calculate memory information
        total_ram = memory_info.total / (1024 ** 3)
        used_ram = memory_info.used / (1024 ** 3)
        available_ram = memory_info.available / (1024 ** 3)
        
        total_swap = swap_info.total / (1024 ** 3)
        used_swap = swap_info.used / (1024 ** 3)
        free_swap = swap_info.free / (1024 ** 3)
        
        # Create text information
        detailed_info = (
            f"[bold]RAM Usage[/]\n"
            f"Used:      [orange1]{used_ram:.2f} GB[/] ({memory_info.percent:.1f}%)\n"
            f"Available: [cyan]{available_ram:.2f} GB[/]\n"
            f"Total:     [white]{total_ram:.2f} GB[/]\n\n"
            f"[bold]SWAP Usage[/]\n"
            f"Used:      [yellow]{used_swap:.2f} GB[/] ({swap_info.percent:.1f}%)\n"
            f"Free:      [cyan]{free_swap:.2f} GB[/]\n"
            f"Total:     [white]{total_swap:.2f} GB[/]"
        )
        
        # Create a text box with buffer statistics
        buffers = memory_info.buffers / (1024 ** 3)
        cached = memory_info.cached / (1024 ** 3) if hasattr(memory_info, 'cached') else 0
        shared = memory_info.shared / (1024 ** 3) if hasattr(memory_info, 'shared') else 0
        
        buffer_info = (
            f"\n\n[bold]Memory Buffers[/]\n"
            f"Buffers:   [green]{buffers:.2f} GB[/]\n"
            f"Cached:    [green]{cached:.2f} GB[/]\n"
            f"Shared:    [green]{shared:.2f} GB[/]"
        )
        
        # Add commit ratio information if available
        commit_info = ""
        if commit_ratio is not None:
            commit_info = f"\n\n[bold]Memory Commit[/]\nRatio: [{'red' if commit_ratio > 0.8 else 'green'}]{commit_ratio:.2f}[/]"
        elif meminfo and 'CommitLimit' in meminfo and 'Committed_AS' in meminfo:
            try:
                commit_limit = int(meminfo['CommitLimit'].split()[0]) / 1024 / 1024  # Convert to GB
                committed = int(meminfo['Committed_AS'].split()[0]) / 1024 / 1024    # Convert to GB
                ratio = committed / commit_limit if commit_limit > 0 else 0
                commit_info = (
                    f"\n\n[bold]Memory Commit[/]\n"
                    f"Used:  [{'red' if ratio > 0.8 else 'green'}]{committed:.2f} GB[/]\n"
                    f"Limit: [white]{commit_limit:.2f} GB[/]\n"
                    f"Ratio: [{'red' if ratio > 0.8 else 'green'}]{ratio:.2f}[/]"
                )
            except:
                pass
        
        # Add page fault statistics if available
        page_fault_info = ""
        try:
            page_stats = psutil.Process().memory_full_info()
            if hasattr(page_stats, 'num_page_faults'):
                page_fault_info = f"\n\n[bold]Page Faults[/]\nTotal: [magenta]{page_stats.num_page_faults:,}[/]"
            elif hasattr(page_stats, 'pfaults'):
                page_fault_info = f"\n\n[bold]Page Faults[/]\nTotal: [magenta]{page_stats.pfaults:,}[/]"
        except:
            pass
        
        # Get memory watermark info (high/low memory situations)
        watermark_info = ""
        if meminfo:
            highwater = meminfo.get('HighTotal', meminfo.get('MemHighTotal', ''))
            lowwater = meminfo.get('LowTotal', meminfo.get('MemLowTotal', ''))
            if highwater or lowwater:
                watermark_info = "\n\n[bold]Memory Watermarks[/]"
                if highwater:
                    watermark_info += f"\nHigh: {highwater}"
                if lowwater:
                    watermark_info += f"\nLow: {lowwater}"
        
        # Add process count information
        process_count = len(list(psutil.process_iter()))
        process_info = f"\n\n[bold]System[/]\nProcesses: [blue]{process_count}[/]"
    
        all_info = f"{detailed_info}{buffer_info}{commit_info}{page_fault_info}{watermark_info}{process_info}"
        plt.clear_figure()
        plt.theme("pro")
        infolines = min(len(all_info.splitlines()), 10)  # Set height based on number of lines in all_info, limit to 10
        plt.plot_size(width=width, height=height-infolines)
        
        # Create a horizontal bar chart for memory types
        categories = ["RAM", "SWAP"]
        values = [memory_info.percent, swap_info.percent]
        colors = ["orange1", "yellow"]
        
        plt.bar(categories, values, orientation="h", color=colors)
        plt.xlim(0, 100)
        plt.xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
        memory_chart = ansi2rich(plt.build()).replace("\x1b[0m", "").replace("──────┐","────%─┐")
        return f"{memory_chart}{all_info}"

    def update_content(self, memory_info, swap_info, meminfo=None, commit_ratio=None, top_processes=None):
        # Calculate available width and height inside the widget
        width = self.size.width - 4
        height = self.size.height - 2
        
        try:
            # Create the content for the widget
            detailed_view = self.create_detailed_view(
                memory_info, 
                swap_info, 
                width, 
                height, 
                meminfo=meminfo, 
                commit_ratio=commit_ratio,
                top_processes=top_processes
            )
            
            # Update the widget content
            self.query_one("#memory-content").update(detailed_view)
        except Exception as e:
            # Handle any errors during rendering
            self.query_one("#memory-content").update(f"[red]Error updating memory widget: {str(e)}[/]") 
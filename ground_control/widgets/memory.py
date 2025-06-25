from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import ansi2rich, align

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
        super().__init__(title=title, id=id, color="orange1")
        self.ram_history = deque(maxlen=120)
        self.swap_history = deque(maxlen=120)
        self.first = True
        self.title = title
        self.border_title = title
        
    def compose(self) -> ComposeResult:
        yield Static("", id="history-plot", classes="metric-plot")
        yield Static("", id="current-value", classes="current-value")

    def create_center_bar(
        self, ram_usage: float, swap_usage: float, total_width: int
    ) -> str:
        """Create a center bar showing RAM, SWAP, and free space with three different colors."""
            # Safety checks
        ram_usage = max(0.0, float(ram_usage))
        swap_usage = max(0.0, float(swap_usage))
        total_width = max(0.0, int(total_width))+21

        # Calculate free space (assuming total memory is 100%)
        free_usage = max(0.0, self.max_mem - ram_usage - swap_usage)
        
        # Calculate percentages for the bar visualization
        ram_percent = min(ram_usage/self.max_mem, 1)
        swap_percent = min(swap_usage/self.max_mem, 1)
        free_percent = min(free_usage/self.max_mem, 1)

        # Calculate blocks for each section
        total_blocks = total_width - 1  # Leave space for borders
        ram_blocks = int((total_blocks * ram_percent))
        swap_blocks = int((total_blocks * swap_percent))
        free_blocks = total_blocks - ram_blocks - swap_blocks

        # Ensure we don't have negative blocks
        if free_blocks < 0:
            free_blocks = 0
            # Adjust other blocks proportionally
            total_used = ram_blocks + swap_blocks
            if total_used > 0:
                ram_blocks = int((ram_blocks / total_used) * total_blocks)
                swap_blocks = total_blocks - ram_blocks

        # Create the three-section bar
        ram_bar = f"[orange3]{'█' * ram_blocks}[/]"
        swap_bar = f"[cyan]{'█' * swap_blocks}[/]"
        free_bar = f"[green]{'-' * free_blocks}[/]"

        # Create labels with alignment
        ram_label = f"{ram_usage:.1f}GB RAM"
        swap_label = align(f"{swap_usage:.1f}GB SWAP", total_width // 2 - 2, "left")
        free_label = align(f"FREE: {free_usage:.1f}GB", total_width // 2 -8, "right")

        # Combine everything
        bar = f"{ram_bar}{swap_bar}{free_bar}\n"
        
        return f" [orange3]{ram_label}[/]/[cyan]{swap_label}[/][green]{free_label}[/]\n {ram_bar}{swap_bar}{free_bar}"

    def get_dual_plot(self) -> str:
        """Create a dual plot showing RAM and SWAP usage over time."""
        if not self.ram_history:
            return "No data yet..."

        plt.clear_figure()
        plt.plot_size(height=self.plot_height-1, width=self.plot_width)
        plt.theme("pro")

        # Create negative values for SWAP to show it below zero
        negative_swap = [-x - 0.1 for x in self.swap_history]
        positive_ram = [x + 0.1 for x in self.ram_history]

        # Find the maximum value to set symmetric y-axis limits
        max_value = max(
            max(self.ram_history, default=0),
            max(negative_swap, key=abs, default=0),
        )

        # Add some padding to the max value
        y_limit = max_value
        if y_limit < 2:
            y_limit = 2
        self.max_mem = y_limit

        # Set y-axis limits symmetrically around zero
        plt.ylim(-y_limit, y_limit)
        
        # Create custom y-axis ticks with % labels
        num_ticks = min(5, self.plot_height - 1)
        tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

        y_ticks = []
        y_labels = []

        for i in range(num_ticks):
            value = -y_limit + i * tick_step
            y_ticks.append(value)
            # Add % to positive values (RAM) and negative values (SWAP)
            if value == 0:
                y_labels.append("0")
            elif value > 0:
                y_labels.append(f"{value:.0f}GB")  # Up arrow for RAM
            else:
                y_labels.append(f"{abs(value):.0f}GB")  # Down arrow for SWAP

        plt.yticks(y_ticks, y_labels)

        # Plot RAM values above zero (positive)
        plt.plot(positive_ram, marker="braille", label="RAM")

        # Plot SWAP values below zero (negative)
        plt.plot(negative_swap, marker="braille", label="SWAP")

        # Add a zero line
        plt.hline(0.0)

        plt.yfrequency(5)
        plt.xfrequency(0)




        # plot = ansi2rich(plt.build())
        # plot_lines = plot.splitlines()
        # plot_lines.append(bar)

        return (
            ansi2rich(plt.build())
            .replace("\x1b[0m", "")
            .replace("[blue]", "[orange3]")
            .replace("[green]", "[cyan]")
            .replace("──────┐","───MB─┐")
            
        )

    def update_content(self, memory_info, swap_info, meminfo=None, commit_ratio=None, top_processes=None, memory_history=None):
        if self.first:
            self.first = False
            return
            
        # Add current values to history
        self.ram_history.append(memory_info.used/1024/1024/1024)
        self.swap_history.append(swap_info.used/1024/1024/1024)
        self.max_mem = memory_info.total/1024/1024/1024
        self.border_title = f"RAM [{self.max_mem:2f}GB]"
        # Calculate total width for the center bar
        total_width = (
            self.size.width
            - len("MEM ")
            - len(f"{memory_info.used:.1f}GB ")
            - len(f"{self.max_mem - memory_info.used - swap_info.used:.2f}GB")
            +13
        )
        
        self.query_one("#history-plot").update(self.get_dual_plot()) 
        # Update the center bar
        self.query_one("#current-value").update(
            self.create_center_bar(
                memory_info.used/1024/1024/1024, swap_info.used/1024/1024/1024, total_width=total_width
            )
        )
        
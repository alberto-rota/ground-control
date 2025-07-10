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
        self.total_ram = 0
        self.total_swap = 0
        
    def compose(self) -> ComposeResult:
        yield Static("", id="history-plot", classes="metric-plot")
        yield Static("", id="current-value", classes="current-value")

    def create_center_bar(
        self, ram_usage: float, swap_usage: float, total_width: int
    ) -> str:
        """Create a center bar showing used/free RAM and used/free SWAP with four different colors."""
        # Safety checks
        ram_usage = max(0.0, float(ram_usage))
        swap_usage = max(0.0, float(swap_usage))
        total_width = max(0.0, int(total_width))+21

        # Calculate free spaces using actual total RAM and SWAP
        free_ram = max(0.0, self.total_ram - ram_usage)
        free_swap = max(0.0, self.total_swap - swap_usage)
        
        # Calculate percentages for the bar visualization using respective totals
        ram_used_percent = min(ram_usage/self.total_ram if self.total_ram > 0 else 0, 1)
        ram_free_percent = min(free_ram/self.total_ram if self.total_ram > 0 else 0, 1)
        swap_used_percent = min(swap_usage/self.total_swap if self.total_swap > 0 else 0, 1)
        swap_free_percent = min(free_swap/self.total_swap if self.total_swap > 0 else 0, 1)

        # Calculate blocks for each section
        total_blocks = total_width - 1  # Leave space for borders
        half_blocks = total_blocks // 2  # Split between RAM and SWAP sections
        
        ram_used_blocks = int(half_blocks * ram_used_percent)
        ram_free_blocks = half_blocks - ram_used_blocks
        swap_used_blocks = int(half_blocks * swap_used_percent)
        swap_free_blocks = total_blocks - ram_used_blocks - ram_free_blocks - swap_used_blocks

        # Ensure no negative blocks
        ram_free_blocks = max(0, ram_free_blocks)
        swap_free_blocks = max(0, swap_free_blocks)

        # Create the four-section bar
        ram_free_bar = f"[orange3]{'─' * ram_free_blocks}[/]"
        ram_used_bar = f"[orange3]{'█' * ram_used_blocks}[/]"
        swap_used_bar = f"[cyan]{'█' * swap_used_blocks}[/]"
        swap_free_bar = f"[cyan]{'─' * swap_free_blocks}[/]"

        # Create labels with alignment
        ram_free_label = align(f"{free_ram:.1f}GB FREE", (total_width-2) // 4, "left")
        ram_label = align(f"{ram_usage:.1f}GB RAM", (total_width-2) // 4, "right")
        swap_label = align(f" SWAP {swap_usage:.1f}GB", (total_width-2) // 4, "left")
        swap_free_label = align(f"FREE {free_swap:.1f}GB", (total_width-2) // 4, "right")

        # Combine everything
        bar = f"{ram_free_bar}{ram_used_bar}{swap_used_bar}{swap_free_bar}"
        
        return f" [orange3 italic]{ram_free_label}[/] [orange3]{ram_label}[/] [cyan]{swap_label}[/] [cyan italic]{swap_free_label}[/]\n {bar}"

    def get_dual_plot(self) -> str:
        """Create a dual plot showing RAM and SWAP usage over time."""
        if not self.ram_history:
            return Static("No data yet...")

        plt.clear_figure()
        plt.plot_size(height=self.plot_height-1, width=self.plot_width)
        plt.theme("pro")

        # Create negative values for SWAP to show it below zero
        negative_swap = [-x - 0.1 for x in self.swap_history]
        positive_ram = [x + 0.1 for x in self.ram_history]

        # Use the actual total RAM and SWAP sizes for y-axis limits
        ram_limit = self.total_ram
        swap_limit = self.total_swap

        # Add some padding and ensure minimum scale
        y_limit = max(ram_limit, swap_limit, 2)  # At least 2GB scale
        
        # Set y-axis limits symmetrically around zero
        plt.ylim(-y_limit, y_limit)
        
        # Create custom y-axis ticks with GB labels
        num_ticks = min(5, self.plot_height - 1)
        tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

        y_ticks = []
        y_labels = []

        for i in range(num_ticks):
            value = -y_limit + i * tick_step
            y_ticks.append(value)
            # Add GB labels for both RAM (positive) and SWAP (negative)
            if value == 0:
                y_labels.append("0GB")
            elif value > 0:
                y_labels.append(f"{value:.1f}GB")  # RAM
            else:
                y_labels.append(f"{abs(value):.1f}GB")  # SWAP

        plt.yticks(y_ticks, y_labels)

        # Plot RAM usage (positive values)
        plt.plot(positive_ram, marker="braille", label="RAM")

        # Plot SWAP usage (negative values)
        plt.plot(negative_swap, marker="braille", label="SWAP")

        # Add a zero line
        plt.hline(0.00)

        plt.yfrequency(5)
        plt.xfrequency(0)

        return (
            ansi2rich(plt.build())
            .replace("\x1b[0m", "")
            .replace("[blue]", "[orange3]")
            .replace("[green]", "[cyan]")
            .replace("──────┐","───GB─┐")
        )

    def update_content(self, memory_info, swap_info, meminfo=None, commit_ratio=None, top_processes=None, memory_history=None):
        # Add current values to history
        self.ram_history.append(memory_info.used/1024/1024/1024)
        self.swap_history.append(swap_info.used/1024/1024/1024)
        
        # Update total RAM and SWAP sizes
        self.total_ram = memory_info.total/1024/1024/1024
        self.total_swap = swap_info.total/1024/1024/1024
        self.max_mem = max(self.total_ram, self.total_swap)  # For plot scaling
        
        self.border_title = f"RAM [{self.total_ram:.1f}GB] SWAP [{self.total_swap:.1f}GB]"
        
        # Calculate total width for the center bar
        total_width = (
            self.size.width
            - len("MEM ")
            - len(f"{memory_info.used:.1f}GB ")
            - len(f"{self.total_ram - memory_info.used:.2f}GB")
            +13
        )
        
        self.query_one("#history-plot").update(self.get_dual_plot()) 
        # Update the center bar
        self.query_one("#current-value").update(
            self.create_center_bar(
                memory_info.used/1024/1024/1024,
                swap_info.used/1024/1024/1024,
                total_width=total_width
            )
        )
        
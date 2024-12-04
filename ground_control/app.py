from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import Header, Static
from widgets.cpu import CPUWidget
from widgets.disk import DiskIOWidget
from widgets.network import NetworkIOWidget
from widgets.gpu import GPUWidget
from utils.system_metrics import SystemMetrics, NVML_AVAILABLE

class GroundControl(App):
    """Main system monitor application with dynamic layout."""
    
    HORIZONTAL_RATIO_THRESHOLD = 6
    VERTICAL_RATIO_THRESHOLD = 1
    
    CSS = """
    Grid {
        grid-gutter: 0;
        padding: 0;
    }
    Grid.default {
        grid-size: 2 2;
    }
    
    Grid.horizontal {
        grid-size: 4 1;
    }
    
    Grid.vertical {
        grid-size: 1 4;
    }
    """
    
    BINDINGS = [
        ("h", "set_horizontal", "Horizontal Layout"),
        ("v", "set_vertical", "Vertical Layout"),
        ("g", "set_grid", "Grid Layout"),
        ("a", "toggle_auto", "Toggle Auto Layout"),
    ]

    def __init__(self):
        super().__init__()
        self.current_layout = "vertical"
        self.auto_layout = True
        self.system_metrics = SystemMetrics()

    def get_layout_class(self, width: int, height: int) -> str:
        """Determine the appropriate layout based on terminal dimensions."""
        ratio = width / height if height > 0 else 0
        if ratio >= self.HORIZONTAL_RATIO_THRESHOLD:
            return "horizontal"
        elif ratio <= self.VERTICAL_RATIO_THRESHOLD:
            return "vertical"
        else:
            return "default"

    def compose(self) -> ComposeResult:
        """Create the initial layout."""
        yield Header()
        with Grid():
            yield CPUWidget("CPU Cores")
            yield DiskIOWidget("Disk")
            yield NetworkIOWidget("Network")
            if NVML_AVAILABLE:
                yield GPUWidget("GPU")
            else:
                yield Static("GPU Not Available")

    def action_set_horizontal(self) -> None:
        """Switch to horizontal layout."""
        self.auto_layout = False
        self.set_layout("horizontal")

    def action_set_vertical(self) -> None:
        """Switch to vertical layout."""
        self.auto_layout = False
        self.set_layout("vertical")

    def action_set_grid(self) -> None:
        """Switch to grid layout."""
        self.auto_layout = False
        self.set_layout("default")

    def action_toggle_auto(self) -> None:
        """Toggle automatic layout adjustment."""
        self.auto_layout = not self.auto_layout
        if self.auto_layout:
            self.update_layout()

    def set_layout(self, new_layout: str) -> None:
        """Apply the specified layout."""
        if new_layout != self.current_layout:
            grid = self.query_one(Grid)
            grid.remove_class(self.current_layout)
            grid.add_class(new_layout)
            self.current_layout = new_layout

    async def on_mount(self) -> None:
        """Initialize the app and start update intervals."""
        self.set_interval(1.0, self.update_metrics)
        self.update_layout()

    def on_resize(self) -> None:
        """Handle terminal resize events."""
        if self.auto_layout:
            self.update_layout()

    def update_layout(self) -> None:
        """Update the grid layout based on terminal dimensions."""
        if not self.is_mounted:
            return
            
        if self.auto_layout:
            width = self.size.width
            height = self.size.height
            new_layout = self.get_layout_class(width, height)
            self.set_layout(new_layout)

    def update_metrics(self):
        """Update all system metrics."""
        # CPU Update
        cpu_metrics = self.system_metrics.get_cpu_metrics()
        cpu_widget = self.query_one(CPUWidget)
        cpu_widget.update_content(
            cpu_metrics['cpu_percentages'],
            cpu_metrics['cpu_freqs'],
            cpu_metrics['mem_percent']
        )

        # Disk I/O Update
        disk_metrics = self.system_metrics.get_disk_metrics()
        disk_widget = self.query_one(DiskIOWidget)
        disk_widget.update_content(
            disk_metrics['read_speed'],
            disk_metrics['write_speed'],
            disk_metrics['disk_used'],
            disk_metrics['disk_total']
        )

        # Network I/O Update
        network_metrics = self.system_metrics.get_network_metrics()
        network_widget = self.query_one(NetworkIOWidget)
        network_widget.update_content(
            network_metrics['download_speed'],
            network_metrics['upload_speed']
        )

        # GPU Update if available
        if NVML_AVAILABLE:
            gpu_metrics = self.system_metrics.get_gpu_metrics()
            if gpu_metrics:
                gpu_widget = self.query_one(GPUWidget)
                gpu_widget.update_content(
                    gpu_metrics['gpu_util'],
                    gpu_metrics['mem_used'],
                    gpu_metrics['mem_total']
                )

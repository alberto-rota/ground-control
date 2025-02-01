import asyncio
from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import Header, Footer, SelectionList
from textual.widgets.selection_list import Selection
import math
from textual import on
from textual.events import Mount
from ground_control.widgets.cpu import CPUWidget
from ground_control.widgets.disk import DiskIOWidget
from ground_control.widgets.network import NetworkIOWidget
from ground_control.widgets.gpu import GPUWidget
from ground_control.utils.system_metrics import SystemMetrics

class GroundControl(App):
    CSS = """
    Grid {
        grid-size: 3 3;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "set_horizontal", "Horizontal Layout"),
        ("v", "set_vertical", "Vertical Layout"),
        ("g", "set_grid", "Grid Layout"),
        ("a", "toggle_auto", "Toggle Auto Layout"),
        ("c", "configure", "Configure"),
    ]

    def __init__(self):
        super().__init__()
        self.current_layout = "horizontal"
        self.auto_layout = False
        self.system_metrics = SystemMetrics()
        self.gpu_widgets = []
        self.grid = None
        self.select = None

    def get_layout_columns(self, num_gpus: int) -> int:
        self.notify(f"{self.select.selected}")
        return len(self.select.selected)

    def compose(self) -> ComposeResult:
        yield Header()
        # Disable multiple selection to ensure only one is selected at a time.
        self.select = SelectionList[str]()
        yield self.select
        self.grid = Grid(classes=self.current_layout)
        yield self.grid
        yield Footer()

    async def on_mount(self) -> None:
        await self.setup_widgets()
        self.update_selection_list()
        
        self.set_interval(1.0, self.update_metrics)

    async def setup_widgets(self) -> None:
        self.grid.remove_children()
        gpu_metrics = self.system_metrics.get_gpu_metrics()
        num_gpus = len(gpu_metrics)
        grid_columns = self.get_layout_columns(num_gpus)
        if self.current_layout == "horizontal":
            self.grid.styles.grid_size_rows = 1
            self.grid.styles.grid_size_columns = grid_columns
        elif self.current_layout == "vertical":
            self.grid.styles.grid_size_rows = grid_columns
            self.grid.styles.grid_size_columns = 1
        elif self.current_layout == "grid":
            if grid_columns <= 12:
                self.grid.styles.grid_size_rows = 2
                self.grid.styles.grid_size_columns = int(math.ceil(grid_columns / 2))
            else:
                self.grid.styles.grid_size_rows = 3
                self.grid.styles.grid_size_columns = int(math.ceil(grid_columns / 3))

        cpu_widget = CPUWidget("CPU Cores")
        disk_widget = DiskIOWidget("Disk I/O")
        network_widget = NetworkIOWidget("Network")
        await self.grid.mount(cpu_widget)
        await self.grid.mount(disk_widget)
        await self.grid.mount(network_widget)

        self.gpu_widgets = []
        for idx in range(num_gpus):
            gpu_widget = GPUWidget(f"GPU {idx}")
            self.gpu_widgets.append(gpu_widget)
            await self.grid.mount(gpu_widget)
        self.toggle_widget_visibility(self.query_one(SelectionList).selected)
        # self.update_selection_list()

    def update_selection_list(self) -> None:
        self.select.clear_options()
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                self.select.add_option(Selection(widget.title, widget.title, True))
        # Select the first widget by default.
        if self.select.children:
            self.select.index = 0
            self.toggle_widget_visibility(self.select.children[0].value)

    # @on(Mount)
    @on(SelectionList.SelectedChanged)
    async def on_selection_list_selected(self) -> None:
        # if event.selection:
        self.toggle_widget_visibility(self.query_one(SelectionList).selected)

    def toggle_widget_visibility(self, selected_title: str) -> None:
        
        # self.notify(f"{selected_title}")
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                # self.notify(f"Changes {widget.title}")
                widget.styles.display = "block" if widget.title in selected_title else "none"

    def update_metrics(self):
        cpu_metrics = self.system_metrics.get_cpu_metrics()
        disk_metrics = self.system_metrics.get_disk_metrics()
        try:
            cpu_widget = self.query_one(CPUWidget)
            cpu_widget.update_content(
                cpu_metrics['cpu_percentages'],
                cpu_metrics['cpu_freqs'],
                cpu_metrics['mem_percent'],
                disk_metrics['disk_used'],
                disk_metrics['disk_total']
            )
        except Exception as e:
            print(f"Error updating CPUWidget: {e}")

        try:
            disk_widget = self.query_one(DiskIOWidget)
            disk_widget.update_content(
                disk_metrics['read_speed'],
                disk_metrics['write_speed'],
                disk_metrics['disk_used'],
                disk_metrics['disk_total']
            )
        except Exception as e:
            print(f"Error updating DiskIOWidget: {e}")

        network_metrics = self.system_metrics.get_network_metrics()
        try:
            network_widget = self.query_one(NetworkIOWidget)
            network_widget.update_content(
                network_metrics['download_speed'],
                network_metrics['upload_speed']
            )
        except Exception as e:
            print(f"Error updating NetworkIOWidget: {e}")

        gpu_metrics = self.system_metrics.get_gpu_metrics()
        for gpu_widget, gpu_metric in zip(self.gpu_widgets, gpu_metrics):
            try:
                gpu_widget.update_content(
                    gpu_metric['gpu_util'],
                    gpu_metric['mem_used'],
                    gpu_metric['mem_total']
                )
            except Exception as e:
                print(f"Error updating {gpu_widget.title}: {e}")

    def action_toggle_auto(self) -> None:
        self.auto_layout = not self.auto_layout
        if self.auto_layout:
            self.update_layout()

    def action_set_horizontal(self) -> None:
        self.auto_layout = False
        self.set_layout("horizontal")

    def action_set_vertical(self) -> None:
        self.auto_layout = False
        self.set_layout("vertical")

    def action_set_grid(self) -> None:
        self.auto_layout = False
        self.set_layout("grid")

    def action_configure(self) -> None:
        widgetslist = self.select
        widgetslist.styles.display = "block" if widgetslist.styles.display == "none" else "none"

    def action_quit(self) -> None:
        self.exit()

    def on_resize(self) -> None:
        if self.auto_layout:
            self.update_layout()

    def update_layout(self) -> None:
        if not self.is_mounted:
            return
        if self.auto_layout:
            width = self.size.width
            height = self.size.height
            ratio = width / height if height > 0 else 0
            if ratio >= 3:
                self.set_layout("horizontal")
            elif ratio <= 0.33:
                self.set_layout("vertical")
            else:
                self.set_layout("grid")

    def set_layout(self, layout: str):
        if layout != self.current_layout:
            grid = self.query_one(Grid)
            grid.remove_class(self.current_layout)
            self.current_layout = layout
            grid.add_class(layout)
        asyncio.create_task(self.setup_widgets())

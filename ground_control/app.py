import asyncio
from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import Header, Footer, SelectionList
from textual.widgets.selection_list import Selection
import math
import os
import json
from textual import on
from textual.events import Mount
from ground_control.widgets.cpu import CPUWidget
from ground_control.widgets.disk import DiskIOWidget
from ground_control.widgets.network import NetworkIOWidget
from ground_control.widgets.gpu import GPUWidget
from ground_control.utils.system_metrics import SystemMetrics
from platformdirs import user_config_dir  # Import for cross-platform config directory

# Set up the user-specific config file path
CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Ensure the directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)

class GroundControl(App):
    CSS = """
    Grid {
        grid-size: 3 3;
    }   
    GPUWidget, NetworkIOWidget, DiskIOWidget, CPUWidget {
        border: round rgb(19, 161, 14);
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "set_horizontal", "Horizontal Layout"),
        ("v", "set_vertical", "Vertical Layout"),
        ("g", "set_grid", "Grid Layout"),
        ("c", "configure", "Configure"),
    ]

    def __init__(self):
        super().__init__()
        # self.set_layout(self.current_layout)
        # self.auto_layout = False
        self.system_metrics = SystemMetrics()
        self.gpu_widgets = []
        self.grid = None
        self.select = None
        self.selectionoptions = []
        self.need_to_change_layout = False
        self.json_exists = os.path.exists(CONFIG_FILE)

    def load_selection(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f).get("selected", {})
            except json.JSONDecodeError:
                return {}
        return {}

    
    def load_layout(self):  
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:   
                    return json.load(f).get("layout", "grid")
            except json.JSONDecodeError:
                return "grid"
        return "grid"

    def save_selection(self):
        try:
            with open(CONFIG_FILE, "r") as f:
                config_data = json.load(f)
            # selected_dict = {option.value: option.selected for option in self.selected_widgets}
            config_data["selected"] = self.selected_widgets
            with open(CONFIG_FILE, "w") as f:
                json.dump(config_data, f, indent=4)
        except FileNotFoundError:
            # selected_dict = {option.value: option.selected for option in self.selected_widgets}
            with open(CONFIG_FILE, "w") as f:
                json.dump({"selected": self.selected_widgets}, f, indent=4)

    
    def save_layout(self):
        try:
            # First read the existing data
            with open(CONFIG_FILE, "r") as f:
                config_data = json.load(f)
        
            # Update only the selected key
            config_data["layout"] = self.current_layout
        
            # Write back the entire updated config
            with open(CONFIG_FILE, "w") as f:
                json.dump(config_data, f)
        except FileNotFoundError:
            # If file doesn't exist, create it with just the selected data
            with open(CONFIG_FILE, "w") as f:
                json.dump({"layout": self.current_layout}, f)

    def get_layout_columns(self, num_gpus: int) -> int:
        return len(self.select.selected)

    def compose(self) -> ComposeResult:
        yield Header()
        # Disable multiple selection to ensure only one is selected at a time.
        self.select = SelectionList[str]()
        self.select.styles.display = "none"
        yield self.select
        self.grid = Grid(classes="grid")
        yield self.grid
        yield Footer()

    async def on_mount(self) -> None:
        self.current_layout = "grid"
        await self.setup_widgets()
        if not self.json_exists:
            self.create_json()
        self.set_layout(self.load_layout())
        self.selected_widgets = self.load_selection()
        
        self.create_selection_list()
        self.set_interval(1.0, self.update_metrics)

    async def setup_widgets(self) -> None:
        self.grid.remove_children()
        gpu_metrics = self.system_metrics.get_gpu_metrics()
        cpu_metrics = self.system_metrics.get_cpu_metrics()
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

        if not self.need_to_change_layout:
            cpu_widget = CPUWidget(f"{cpu_metrics['cpu_name']}")
            disk_widget = DiskIOWidget("Disk I/O")
            network_widget = NetworkIOWidget("Network")
        
        await self.grid.mount(cpu_widget)
        await self.grid.mount(disk_widget)
        await self.grid.mount(network_widget)

        if not self.need_to_change_layout:
            self.gpu_widgets = []
        for gpu in self.system_metrics.get_gpu_metrics():
            if not self.need_to_change_layout:
                gpu_widget = GPUWidget(gpu["gpu_name"])
                self.gpu_widgets.append(gpu_widget)
            await self.grid.mount(gpu_widget)
        self.toggle_widget_visibility(self.query_one(SelectionList).selected)
        self.need_to_change_layout = False

    def create_json(self) -> None:
        selection_dict = {}
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                selection_dict[widget.title] = True
        default_config = {
            "selected": selection_dict,
            "layout": "grid"
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)

                
    def create_selection_list(self) -> None:
        self.select.clear_options()
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                # Default to True if the widget is missing in the loaded config.
                selected = self.selected_widgets.get(widget.title, True)
                self.select.add_option(Selection(widget.title, widget.title, selected))
                self.selectionoptions.append(widget.title)


    @on(SelectionList.SelectedChanged)
    async def on_selection_list_selected(self) -> None:
        # if event.selection:
        selected = self.query_one(SelectionList).selected
        hidden = [option for option in self.selectionoptions if option not in selected]
        self.toggle_widget_visibility(selected)
        for visible in selected:
            self.selected_widgets[visible] = True
        for hide in hidden:
            self.selected_widgets[hide] = False
        
        self.save_selection()

    def toggle_widget_visibility(self, selected_title: str) -> None:
        
        for widget in self.grid.children:
            if hasattr(widget, "title"):
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
            # self.notify(disk_widget.create_usage_bar())
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
            # try:
            gpu_widget.update_content(
                gpu_metric["gpu_name"],
                gpu_metric['gpu_util'],
                gpu_metric['mem_used'],
                gpu_metric['mem_total']
            )
        # except Exception as e:
            #     print(f"Error updating {gpu_widget.title}: {e}")

    def action_configure(self) -> None:
        widgetslist = self.select
        widgetslist.styles.display = "block" if widgetslist.styles.display == "none" else "none"
        
    def action_toggle_auto(self) -> None:
        # self.auto_layout = not self.auto_layout
        if self.auto_layout:
            self.update_layout()

    def action_set_horizontal(self) -> None:
        self.need_to_change_layout = False
        # self.auto_layout = False
        self.set_layout("horizontal")

    def action_set_vertical(self) -> None:
        self.need_to_change_layout = False
        # self.auto_layout = False
        self.set_layout("vertical")

    def action_set_grid(self) -> None:
        self.need_to_change_layout = False
        # self.auto_layout = False
        self.set_layout("grid")

    def action_quit(self) -> None:
        self.exit()

    # def on_resize(self) -> None:
    #     if self.auto_layout:
    #         self.update_layout()

    def update_layout(self) -> None:
        if not self.is_mounted:
            return
        # if self.auto_layout:
        #     width = self.size.width
        #     height = self.size.height
        #     ratio = width / height if height > 0 else 0
        #     if ratio >= 3:
        #         self.set_layout("horizontal")
        #     elif ratio <= 0.33:
        #         self.set_layout("vertical")
        #     else:
        #         self.set_layout("grid")

    def set_layout(self, layout: str):
        if layout != self.current_layout:
            grid = self.query_one(Grid)
            grid.remove_class(self.current_layout)
            self.current_layout = layout
            grid.add_class(layout)
        asyncio.create_task(self.setup_widgets())
        self.save_layout()
        

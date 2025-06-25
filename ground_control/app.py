import asyncio
from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import Header, Footer, SelectionList
from textual.widgets.selection_list import Selection
import math
import os
import json
import logging
from textual import on
from textual.events import Mount
from ground_control.widgets.cpu import CPUWidget
from ground_control.widgets.disk import DiskIOWidget
from ground_control.widgets.network import NetworkIOWidget
from ground_control.widgets.gpu import GPUWidget
from ground_control.widgets.memory import MemoryWidget
from ground_control.utils.system_metrics import SystemMetrics
from platformdirs import user_config_dir  # Import for cross-platform config directory

# Set up the user-specific config file path
CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_FILE = "ground_control.log"

# Set up logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ground-control")

# Ensure the directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)

class GroundControl(App):
    CSS = """
    Grid {
        grid-size: 3 3;
    }   
    GPUWidget, NetworkIOWidget, DiskIOWidget, CPUWidget, MemoryWidget {
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
        self.selected_widgets = self.load_selection()  # Load selection first
        await self.setup_widgets()
        if not self.json_exists:
            self.create_json()
        self.set_layout(self.load_layout())
        
        self.apply_widget_visibility()  # Apply visibility after creating widgets
        self.set_interval(1.0, self.update_metrics)

    async def setup_widgets(self) -> None:
        self.grid.remove_children()
        gpu_metrics = self.system_metrics.get_gpu_metrics()
        cpu_metrics = self.system_metrics.get_cpu_metrics()
        disk_metrics = self.system_metrics.get_disk_metrics()
        memory_metrics = self.system_metrics.get_memory_metrics()
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

        # Always create new widgets when setup_widgets is called
        cpu_widget = CPUWidget(f"{cpu_metrics['cpu_name']}")
        memory_widget = MemoryWidget("Memory")
        self.disk_widgets = []
        self.gpu_widgets = []
        network_widget = NetworkIOWidget("Network")
    
        await self.grid.mount(cpu_widget)
        await self.grid.mount(memory_widget)
    
        # Mount multiple disk widgets
        for disk in disk_metrics['disks']:
            # Skip /boot/efi partitions - they should never be shown as widgets
            if '/boot/efi' in disk['mountpoint']:
                logger.info(f"Skipping EFI partition at {disk['mountpoint']} - not creating widget")
                continue
                
            disk_widget = DiskIOWidget(f"Disk @ {disk['mountpoint']}", id=f"disk_{disk['mountpoint'].replace('/', '_')}")
            self.disk_widgets.append(disk_widget)
            await self.grid.mount(disk_widget)
        
        await self.grid.mount(network_widget)
        
        # Mount GPU widgets
        for gpu in gpu_metrics:
            gpu_widget = GPUWidget(f"GPU @ {gpu['gpu_name']}", id=f"gpu_{len(self.gpu_widgets)}")
            self.gpu_widgets.append(gpu_widget)
            await self.grid.mount(gpu_widget)
        
        logger.info(f"Setup complete: {len(self.disk_widgets)} disk widgets, {len(self.gpu_widgets)} GPU widgets")
        
        # Update selection list after widgets are created
        self.create_selection_list()

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
        self.selectionoptions.clear()  # Clear the list before adding new options
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
        # Update selected_widgets dictionary
        self.selected_widgets = {option: (option in selected) for option in self.selectionoptions}
        self.save_selection()

    def toggle_widget_visibility(self, selected_titles) -> None:
        """Toggle widget visibility based on selected titles
        
        Args:
            selected_titles: List of widget titles that should be visible
        """
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                widget.styles.display = "block" if widget.title in selected_titles else "none"
                logger.debug(f"Setting {widget.title} display to {'block' if widget.title in selected_titles else 'none'}")

    def update_metrics(self):
        try:
            cpu_metrics = self.system_metrics.get_cpu_metrics()
            disk_metrics = self.system_metrics.get_disk_metrics()
            network_metrics = self.system_metrics.get_network_metrics()
            gpu_metrics = self.system_metrics.get_gpu_metrics()
            memory_metrics = self.system_metrics.get_memory_metrics()
            
            # Update CPU widget
            cpu_widget = self.query_one(CPUWidget)
            cpu_widget.update_content(
                cpu_metrics['cpu_percentages'],
                cpu_metrics['cpu_freqs'],
                cpu_metrics['mem_percent'],
                disk_metrics['total_disk_used'],
                disk_metrics['total_disk_total']
            )
            
            # Update Memory widget
            try:
                memory_widget = self.query_one(MemoryWidget)
                memory_widget.update_content(
                    memory_metrics['memory_info'],
                    memory_metrics['swap_info'],
                    meminfo=memory_metrics.get('meminfo'),
                    commit_ratio=memory_metrics.get('commit_ratio'),
                    top_processes=memory_metrics.get('top_processes'),
                    memory_history=memory_metrics.get('memory_history')
                )
            except Exception as e:
                logger.error(f"Error updating memory widget: {str(e)}")
            
            # Update each disk widget with its specific metrics
            for disk_widget in self.disk_widgets:
                try:
                    logger.debug(f"Disk widget: {disk_widget.title}")
                    for disk in disk_metrics['disks']:
                        logger.debug(f"Checking disk: {disk['mountpoint']}")
                        if disk_widget.title == f"Disk @ {disk['mountpoint']}":
                            logger.debug(f"Match found for {disk['mountpoint']}")
                            try:
                                # Log the values we're providing
                                logger.debug(f"Disk values: read={disk['read_speed']}, write={disk['write_speed']}, used={disk['disk_used']}, total={disk['disk_total']}")
                                
                                disk_widget.update_content(
                                    disk['read_speed'],
                                    disk['write_speed'],
                                    disk['disk_used'],
                                    disk['disk_total']
                                )
                            except Exception as e:
                                import traceback
                                logger.error(f"Error updating disk widget {disk_widget.title}: {e}")
                                logger.error(f"Error details: {traceback.format_exc()}")
                            break
                except Exception as e:
                    import traceback
                    logger.error(f"Error updating disk widget {disk_widget.title}: {e}")
                    logger.error(f"Error details: {traceback.format_exc()}")

            network_metrics = self.system_metrics.get_network_metrics()
            try:
                network_widget = self.query_one(NetworkIOWidget)
                network_widget.update_content(
                    network_metrics['download_speed'],
                    network_metrics['upload_speed']
                )
            except Exception as e:
                logger.error(f"Error updating NetworkIOWidget: {e}")

            gpu_metrics = self.system_metrics.get_gpu_metrics()
            for gpu_widget, gpu_metric in zip(self.gpu_widgets, gpu_metrics):
                try:
                    gpu_widget.update_content(
                        gpu_metric["gpu_name"],
                        gpu_metric['gpu_util'],
                        gpu_metric['mem_used'],
                        gpu_metric['mem_total']
                    )
                except Exception as e:
                    logger.error(f"Error updating {gpu_widget.title}: {e}")

        except Exception as e:
            logger.error(f"Error updating metrics: {e}")

    def action_configure(self) -> None:
        widgetslist = self.select
        widgetslist.styles.display = "block" if widgetslist.styles.display == "none" else "none"
        
    def action_toggle_auto(self) -> None:
        # self.auto_layout = not self.auto_layout
        if self.auto_layout:
            self.update_layout()

    def action_set_horizontal(self) -> None:
        # self.auto_layout = False
        self.set_layout("horizontal")

    def action_set_vertical(self) -> None:
        # self.auto_layout = False
        self.set_layout("vertical")

    def action_set_grid(self) -> None:
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
        # Apply widget visibility after changing layout
        # We need to wait for setup_widgets to finish
        asyncio.create_task(self.apply_visibility_after_setup())
        
    async def apply_visibility_after_setup(self):
        """Apply widget visibility after layout change and widget setup"""
        # Wait a short time for setup_widgets to complete
        await asyncio.sleep(0.2)
        # Then apply the visibility settings
        self.apply_widget_visibility()

    def apply_widget_visibility(self) -> None:
        """Apply the saved widget visibility settings from config"""
        logger.info(f"Applying widget visibility from config: {self.selected_widgets}")
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                is_visible = self.selected_widgets.get(widget.title, True)
                widget.styles.display = "block" if is_visible else "none"
                logger.debug(f"Widget {widget.title}: visible = {is_visible}")

        
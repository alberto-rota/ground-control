import asyncio
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal
from textual.widgets import Header, Footer, SelectionList, Button, Static,Input
from textual.widgets.selection_list import Selection
from textual.reactive import reactive
from textual import on
import math
import os
import json
import logging
from textual.events import Mount
from ground_control.widgets.cpu import CPUWidget
from ground_control.widgets.disk import DiskIOWidget
from ground_control.widgets.network import NetworkIOWidget
from ground_control.widgets.gpu import GPUWidget
from ground_control.widgets.memory import MemoryWidget
from ground_control.widgets.temperature import TemperatureWidget
from ground_control.utils.system_metrics import SystemMetrics
from platformdirs import user_config_dir  # Import for cross-platform config directory
from textual.screen import Screen

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


class RefreshRateButtons(Static):
    """A horizontal array of refresh rate buttons"""
    DEFAULT_CSS = """
    .title {
        text-align: left;
        height: 1;
    }
    """
    def __init__(self, title="Refresh Rate"):
        super().__init__(id="refresh-buttons")
        self.border_title = title
        # Rates in seconds: 0.5, 1, 2, 5, 10, 15, 30, 60 (1 min)
        self.rates = [60, 30, 15, 10, 5, 2, 1, 500]

    def compose(self) -> ComposeResult:
        """Create the refresh rate buttons"""
        with Horizontal(id="refresh-container"):
            for rate in self.rates:
                label = "1m" if rate == 60 else "30s" if rate == 30 else "500ms" if rate == 500 else f"{rate}s"
                yield Button(label, id=f"refresh-{rate}".replace(".", ""), classes="refresh-button")

class HistorySizeButtons(Static):
    """A horizontal array of history size buttons"""
    DEFAULT_CSS = """
    .title {
        text-align: left;
        height: 1;
    }
    """
    def __init__(self, title="History Size"):
        super().__init__(id="history-buttons")
        self.border_title = title
        # History sizes in seconds
        self.sizes = [600, 300, 180, 120, 60, 30]

    def compose(self) -> ComposeResult:
        """Create the history size buttons"""
        with Horizontal(id="history-container"):
            for size in self.sizes:
                label = f"{size//60}m" if size >= 60 else f"{size}s"
                yield Button(label, id=f"history-{size}", classes="history-button")

class GroundControl(App):
    CSS = """
    Grid {
        grid-size: 3 3;
        align: center middle;
        width: 100%;
        height: 100%;
    }   
    GPUWidget, NetworkIOWidget, DiskIOWidget, CPUWidget, MemoryWidget, TemperatureWidget {
        border: round rgb(19, 161, 14);
    }
    
    SelectionList {
        background: $surface;
        border: round rgb(19, 161, 14);
        width: 100%;
        height: auto;
    }

    #config-container {
        width: 100%;
        layout: vertical;
        background: $surface;
        height: auto;
    }
    
    #controls-container {
        width: 100%;
        layout: horizontal;
        height: auto;
    }
    
    #refresh-buttons, #history-buttons {
        width: 50%;
        height: auto;
        padding: 0;
        border: round rgb(19, 161, 14);
        margin: 0 0;
    }
    
    #refresh-container, #history-container {
        width: 100%;
        height: 3;
        align: center middle;
        background: $surface;
        padding: 0;
    }
    
    .refresh-button, .history-button {
        margin: 0 0;
        height: 3;
        min-width: 6;
        background: $boost;
    }
    
    .refresh-button:hover, .history-button:hover {
        background: $accent;
    }
    
    .refresh-button.-active, .history-button.-active {
        background: rgb(19, 161, 14);
        color: $text;
    }
    
    .config-title {
        text-align: left;
        height: 1;
    }
    """

    # Define reactive properties
    refresh_rate = reactive(1.0)
    history_size = reactive(120)
    MIN_REFRESH_RATE = 1
    MAX_REFRESH_RATE = 100
    REFRESH_STEP = 0.05

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "set_horizontal", "Horizontal Layout"),
        ("v", "set_vertical", "Vertical Layout"),
        ("g", "set_grid", "Grid Layout"),
        ("c", "configure", "Configure"),
    ]

    def __init__(self):
        super().__init__()
        self.system_metrics = SystemMetrics()
        self.gpu_widgets = []
        self.disk_widgets = []
        self.temperature_widget = None
        self.grid = None
        self.select = None
        self.refresh_buttons = None
        self.history_buttons = None
        self.selectionoptions = []
        self.selected_widgets = {}  # Initialize selected_widgets
        self.json_exists = os.path.exists(CONFIG_FILE)
        self._update_timer = None
        self._is_initializing = True  # Flag to prevent toast notifications during startup


    def watch_refresh_rate(self, new_rate: float) -> None:
        """React to changes in refresh rate"""
        if self._update_timer:
            self._update_timer.stop()
        self._update_timer = self.set_interval(new_rate, self.update_metrics)
        self.save_config()
        self._update_refresh_buttons()
        # Show toast notification only when not initializing
        if not self._is_initializing:
            self.notify(f"Refresh rate changed to {new_rate}s", title="Settings Updated", severity="information")

    def watch_history_size(self, new_size: int) -> None:
        """React to changes in history size"""
        self.save_config()
        self._update_history_buttons()
        # Instead of recreating all widgets, just update the history size for existing widgets
        self._update_widget_history_sizes(new_size)
        # Show toast notification only when not initializing
        if not self._is_initializing:
            self.notify(f"History size changed to {new_size}s", title="Settings Updated", severity="information")
        logger.debug(f"History size changed to {new_size}s")

    @on(Button.Pressed)
    def handle_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        if event.button.id:
            if event.button.id.startswith("refresh-"):
                try:
                    # Remove active class from all refresh buttons
                    for button in self.query(f".refresh-button"):
                        button.remove_class("-active")
                    # Add active class to clicked button
                    event.button.add_class("-active")
                    # Update the refresh rate
                    rate = float(event.button.id.replace("refresh-", ""))
                    if rate == 500:
                        rate = 0.5
                    self.refresh_rate = rate
                    # The watch_refresh_rate method will handle timer management
                except (ValueError, IndexError):
                    self.notify(f"Invalid refresh rate value: {event.button.id}", title="Error", severity="error")
            elif event.button.id.startswith("history-"):
                try:
                    # Remove active class from all history buttons
                    for button in self.query(f".history-button"):
                        button.remove_class("-active")
                    # Add active class to clicked button
                    event.button.add_class("-active")
                    # Update the history size
                    size = int(event.button.id.replace("history-", ""))
                    self.history_size = size
                except (ValueError, IndexError):
                    pass

    def _update_refresh_buttons(self) -> None:
        """Update the active state of refresh rate buttons"""
        if self.refresh_buttons:
            # First remove active class from all buttons
            for button in self.query(f".refresh-button"):
                button.remove_class("-active")
            # Then add it to the matching one
            for rate in self.refresh_buttons.rates:
                button = self.query_one(f"#refresh-{rate}".replace(".", ""))
                if button:
                    # Handle the special case where 500ms button maps to 0.5s refresh rate
                    expected_rate = 0.5 if rate == 500 else rate
                    if abs(expected_rate - self.refresh_rate) < 0.01:  # Compare with small epsilon
                        button.add_class("-active")

    def _update_history_buttons(self) -> None:
        """Update the active state of history size buttons"""
        if self.history_buttons:
            # First remove active class from all buttons
            for button in self.query(f".history-button"):
                button.remove_class("-active")
            # Then add it to the matching one
            for size in self.history_buttons.sizes:
                button = self.query_one(f"#history-{size}")
                if button and size == self.history_size:
                    button.add_class("-active")

    def load_config(self):
        """Load configuration from file"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    self.refresh_rate = float(config.get("refresh_rate", 1.0))
                    self.history_size = int(config.get("history_size", 120))
                    return config.get("selected", {})
            except (json.JSONDecodeError, ValueError):
                pass
        return {}

    def save_config(self):
        """Save configuration to file"""
        try:
            with open(CONFIG_FILE, "r") as f:
                config_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            config_data = {}
        
        config_data.update({
            "refresh_rate": self.refresh_rate,
            "history_size": self.history_size,
            "selected": self.selected_widgets
        })
        
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)

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
        """Create child widgets for the app."""
        yield Header()
        
        # Create a container for configuration elements
        with Grid(id="config-container") as config:
            self.select = SelectionList[str]()
            self.select.border_title = "Visible Widgets"
            yield self.select
            # Create horizontal container for refresh rate and history size buttons
            with Horizontal(id="controls-container"):
                self.refresh_buttons = RefreshRateButtons()
                yield self.refresh_buttons
                self.history_buttons = HistorySizeButtons()
                yield self.history_buttons
        config.styles.display = "none"
        
        self.grid = Grid(classes="grid")
        yield self.grid
        yield Footer()

    async def on_mount(self) -> None:
        self.current_layout = "grid"
        self.selected_widgets = self.load_config()  # Load all config
        await self.setup_widgets()
        if not self.json_exists:
            self.create_json()
        self.set_layout(self.load_layout())
        
        self.apply_widget_visibility()
        self._update_timer = self.set_interval(self.refresh_rate, self.update_metrics)
        self._update_refresh_buttons()
        self._update_history_buttons()
        
        # Mark initialization as complete - now toast notifications can be shown
        self._is_initializing = False

    async def setup_widgets(self) -> None:
        self.grid.remove_children()
        gpu_metrics = self.system_metrics.get_gpu_metrics()
        cpu_metrics = self.system_metrics.get_cpu_metrics()
        disk_metrics = self.system_metrics.get_disk_metrics()
        memory_metrics = self.system_metrics.get_memory_metrics()
        temperature_metrics = self.system_metrics.get_temperature_metrics()
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
        self.temperature_widget = None
        network_widget = NetworkIOWidget("Network")
    
        await self.grid.mount(cpu_widget)
        await self.grid.mount(memory_widget)
        
        # Create temperature widget only if temperature data is available
        temperature_metrics = self.system_metrics.get_temperature_metrics()
        logger.info(f"Temperature metrics: {temperature_metrics}")
        if temperature_metrics:
            self.temperature_widget = TemperatureWidget("Temperature", history_size=int(self.history_size))
            await self.grid.mount(self.temperature_widget)
        else:
            logger.info("No temperature sensors found - skipping temperature widget")
            self.temperature_widget = None
    
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
            "layout": "grid",
            "refresh_rate": self.refresh_rate,
            "history_size": self.history_size
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
            temperature_metrics = self.system_metrics.get_temperature_metrics()
            
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

            # Update temperature widget if available
            if self.temperature_widget and temperature_metrics:
                try:
                    logger.debug(f"Updating temperature widget with: {temperature_metrics}")
                    self.temperature_widget.update_content(temperature_metrics)
                except Exception as e:
                    logger.error(f"Error updating temperature widget: {e}")
            elif self.temperature_widget:
                logger.debug("Temperature widget exists but no temperature metrics available")
            else:
                logger.debug("No temperature widget available")

        except Exception as e:
            logger.error(f"Error updating metrics: {e}")

    def action_configure(self) -> None:
        """Toggle configuration panel visibility"""
        config = self.query_one("#config-container")
        config.styles.display = "none" if config.styles.display == "block" else "block"
        if config.styles.display == "block":
            self._update_refresh_buttons()
        
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

    def _update_widget_history_sizes(self, new_size: int) -> None:
        """Update history size for all existing widgets without recreating them"""
        # Update CPU widget
        try:
            cpu_widget = self.query_one(CPUWidget)
            if hasattr(cpu_widget, 'history'):
                cpu_widget.history = cpu_widget.history.__class__(maxlen=new_size)
        except:
            pass
        
        # Update Memory widget
        try:
            memory_widget = self.query_one(MemoryWidget)
            if hasattr(memory_widget, 'ram_history'):
                memory_widget.ram_history = memory_widget.ram_history.__class__(maxlen=new_size)
            if hasattr(memory_widget, 'swap_history'):
                memory_widget.swap_history = memory_widget.swap_history.__class__(maxlen=new_size)
        except:
            pass
        
        # Update Network widget
        try:
            network_widget = self.query_one(NetworkIOWidget)
            if hasattr(network_widget, 'download_history'):
                network_widget.download_history = network_widget.download_history.__class__(maxlen=new_size)
            if hasattr(network_widget, 'upload_history'):
                network_widget.upload_history = network_widget.upload_history.__class__(maxlen=new_size)
        except:
            pass
        
        # Update Temperature widget
        if self.temperature_widget:
            try:
                if hasattr(self.temperature_widget, 'temperature_histories'):
                    for sensor_name in self.temperature_widget.temperature_histories:
                        self.temperature_widget.temperature_histories[sensor_name] = \
                            self.temperature_widget.temperature_histories[sensor_name].__class__(maxlen=new_size)
            except:
                pass
        
        # Update Disk widgets
        for disk_widget in self.disk_widgets:
            try:
                if hasattr(disk_widget, 'read_history'):
                    disk_widget.read_history = disk_widget.read_history.__class__(maxlen=new_size)
                if hasattr(disk_widget, 'write_history'):
                    disk_widget.write_history = disk_widget.write_history.__class__(maxlen=new_size)
            except:
                pass
        
        # Update GPU widgets
        for gpu_widget in self.gpu_widgets:
            try:
                if hasattr(gpu_widget, 'gpu_ram_history'):
                    gpu_widget.gpu_ram_history = gpu_widget.gpu_ram_history.__class__(maxlen=new_size)
                if hasattr(gpu_widget, 'gpu_usage_history'):
                    gpu_widget.gpu_usage_history = gpu_widget.gpu_usage_history.__class__(maxlen=new_size)
            except:
                pass
        
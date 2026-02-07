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
from ground_control.utils.colors import load_colors, ensure_colors_in_config
from platformdirs import user_config_dir  # Import for cross-platform config directory
from textual.screen import Screen

# Set up the user-specific config file path
CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple for CSS."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

# Logger will be set up in main.py before app is created
logger = logging.getLogger("ground-control")


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
        self.rates = [60, 30, 15, 10, 5, 2, 1, 0.5]

    def compose(self) -> ComposeResult:
        """Create the refresh rate buttons"""
        with Horizontal(id="refresh-container"):
            for rate in self.rates:
                label = "1m" if rate == 60 else "30s" if rate == 30 else "500ms" if rate == 0.5 else f"{rate}s"
                yield Button(label, id=f"refresh-{rate}".replace(".", "-"), classes="refresh-button")

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
    def __init__(self, allowed_types: set[str] | None = None):
        super().__init__()
        # Load colors and generate CSS dynamically
        self._color_config = load_colors()
        self._generate_css()
        
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
        self._config_save_task = None  # For debounced config saves (asyncio.Task)
        self._update_in_progress = False  # Prevent concurrent updates
        self.allowed_types = allowed_types

    def _generate_css(self):
        """Generate CSS with colors from config."""
        border_rgb = hex_to_rgb(self._color_config.get("border", "#13A10E"))
        active_button_rgb = hex_to_rgb(self._color_config.get("active_button", "#13A10E"))
        
        self.CSS = f"""
    Grid {{
        grid-size: 3 3;
        align: center middle;
        width: 100%;
        height: 100%;
    }}   
    GPUWidget, NetworkIOWidget, DiskIOWidget, CPUWidget, MemoryWidget, TemperatureWidget {{
        border: round rgb({border_rgb[0]}, {border_rgb[1]}, {border_rgb[2]});
    }}
    
    SelectionList {{
        background: $surface;
        border: round rgb({border_rgb[0]}, {border_rgb[1]}, {border_rgb[2]});
        width: 100%;
        height: auto;
    }}

    #config-container {{
        width: 100%;
        layout: vertical;
        background: $surface;
        height: auto;
    }}
    
    #controls-container {{
        width: 100%;
        layout: horizontal;
        height: auto;
    }}
    
    #refresh-buttons, #history-buttons {{
        width: 50%;
        height: auto;
        padding: 0;
        border: round rgb({border_rgb[0]}, {border_rgb[1]}, {border_rgb[2]});
        margin: 0 0;
    }}
    
    #refresh-container, #history-container {{
        width: 100%;
        height: 3;
        align: center middle;
        background: $surface;
        padding: 0;
    }}
    
    .refresh-button, .history-button {{
        margin: 0 0;
        height: 3;
        min-width: 6;
        background: $boost;
    }}
    
    .refresh-button:hover, .history-button:hover {{
        background: $accent;
    }}
    
    .refresh-button.-active, .history-button.-active {{
        background: rgb({active_button_rgb[0]}, {active_button_rgb[1]}, {active_button_rgb[2]});
        color: $text;
    }}
    
    .config-title {{
        text-align: left;
        height: 1;
    }}
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



    def watch_refresh_rate(self, new_rate: float) -> None:
        """React to changes in refresh rate"""
        if self._update_timer:
            self._update_timer.stop()
        self._update_timer = self.set_interval(new_rate, self._update_metrics_sync)
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
                    rate_str = event.button.id.replace("refresh-", "").replace("-", ".")
                    rate = float(rate_str)
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
                button = self.query_one(f"#refresh-{rate}".replace(".", "-"))
                if button:
                    if abs(rate - self.refresh_rate) < 0.01:  # Compare with small epsilon
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
        """Save configuration to file (debounced)"""
        # Cancel any pending save task
        if self._config_save_task and not self._config_save_task.done():
            self._config_save_task.cancel()
        
        # Schedule a new save task after 0.5 seconds
        self._config_save_task = asyncio.create_task(self._debounced_save_config())
    
    async def _debounced_save_config(self):
        """Debounced config save - waits 0.5 seconds before actually saving"""
        await asyncio.sleep(0.5)
        self._do_save_config()
    
    def _do_save_config(self):
        """Actually perform the config file write"""
        try:
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
        except Exception as e:
            logger.error(f"Error saving config: {e}")

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
        """Save selection (uses debounced save_config)"""
        self.save_config()  # Use the debounced save method

    
    
    def save_layout(self):
        """Save layout (uses debounced save_config)"""
        self.save_config()  # Use the debounced save method

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
        
        # If CLI args are provided, override the selection
        # But we need widgets to be created first to know their titles
        # So we'll handle filtering in create_selection_list or separate method
        
        await self.setup_widgets()
        if not self.json_exists:
            self.create_json()
        self.set_layout(self.load_layout())
        
        self.apply_widget_visibility()
        self._update_timer = self.set_interval(self.refresh_rate, self._update_metrics_sync)
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
        for i, disk in enumerate(disk_metrics['disks']):
            # Skip /boot/efi partitions - they should never be shown as widgets
            if '/boot/efi' in disk['mountpoint']:
                logger.info(f"Skipping EFI partition at {disk['mountpoint']} - not creating widget")
                continue
                
            disk_widget = DiskIOWidget(f"Disk @ {disk['mountpoint']}", id=f"disk_{i}_{disk['mountpoint'].replace('/', '_')}")
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
        # Ensure colors section exists in config
        ensure_colors_in_config()
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
        
        # If allowed_types is set, we need to enforce it
        # This overrides saved config for this session
        
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                # Determine if this widget should be selected
                selected = True
                
                # First check if explicit CLI args were provided
                if self.allowed_types:
                    # Check if widget matches allowed types
                    widget_type = self._get_widget_type(widget)
                    if widget_type not in self.allowed_types:
                        selected = False
                    else:
                        selected = True
                else:
                    # Fallback to saved config
                    selected = self.selected_widgets.get(widget.title, True)
                
                self.select.add_option(Selection(widget.title, widget.title, selected))
                self.selectionoptions.append(widget.title)
                
                # Update selected_widgets to match current state (important for toggle_widget_visibility)
                if self.allowed_types:
                    self.selected_widgets[widget.title] = selected

    def _get_widget_type(self, widget) -> str:
        """Helper to map widget instance to type string"""
        if isinstance(widget, CPUWidget):
            return "cpu"
        elif isinstance(widget, GPUWidget):
            return "gpu"
        elif isinstance(widget, MemoryWidget):
            return "ram"
        elif isinstance(widget, DiskIOWidget):
            return "disk"
        elif isinstance(widget, NetworkIOWidget):
            return "net"
        elif isinstance(widget, TemperatureWidget):
            return "temp"
        return "unknown"


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

    def _update_metrics_sync(self):
        """Synchronous wrapper to trigger async update_metrics"""
        asyncio.create_task(self.update_metrics())
    
    async def update_metrics(self):
        """Update all metrics asynchronously, parallelizing collection and skipping hidden widgets"""
        # Prevent concurrent updates
        if self._update_in_progress:
            return
        self._update_in_progress = True
        
        try:
            # Parallelize metric collection using asyncio
            loop = asyncio.get_event_loop()
            cpu_task = loop.run_in_executor(None, self.system_metrics.get_cpu_metrics)
            disk_task = loop.run_in_executor(None, self.system_metrics.get_disk_metrics)
            network_task = loop.run_in_executor(None, self.system_metrics.get_network_metrics)
            gpu_task = loop.run_in_executor(None, self.system_metrics.get_gpu_metrics)
            memory_task = loop.run_in_executor(None, self.system_metrics.get_memory_metrics)
            temperature_task = loop.run_in_executor(None, self.system_metrics.get_temperature_metrics)
            
            # Wait for all metrics to be collected in parallel
            cpu_metrics, disk_metrics, network_metrics, gpu_metrics, memory_metrics, temperature_metrics = await asyncio.gather(
                cpu_task, disk_task, network_task, gpu_task, memory_task, temperature_task
            )
            
            # Update widgets - only update visible ones
            update_tasks = []
            
            # Update CPU widget (only if visible)
            try:
                cpu_widget = self.query_one(CPUWidget)
                if cpu_widget.styles.display != "none":
                    update_tasks.append(self._update_cpu_widget(cpu_widget, cpu_metrics, disk_metrics))
            except Exception as e:
                logger.error(f"Error updating CPU widget: {str(e)}")
            
            # Update Memory widget (only if visible)
            try:
                memory_widget = self.query_one(MemoryWidget)
                if memory_widget.styles.display != "none":
                    update_tasks.append(self._update_memory_widget(memory_widget, memory_metrics))
            except Exception as e:
                logger.error(f"Error updating memory widget: {str(e)}")
            
            # Update disk widgets (only if visible)
            for disk_widget in self.disk_widgets:
                if disk_widget.styles.display != "none":
                    for disk in disk_metrics['disks']:
                        if disk_widget.title == f"Disk @ {disk['mountpoint']}":
                            update_tasks.append(self._update_disk_widget(disk_widget, disk))
                            break
            
            # Update Network widget (only if visible)
            try:
                network_widget = self.query_one(NetworkIOWidget)
                if network_widget.styles.display != "none":
                    update_tasks.append(self._update_network_widget(network_widget, network_metrics))
            except Exception as e:
                logger.error(f"Error updating NetworkIOWidget: {e}")
            
            # Update GPU widgets (only if visible)
            for gpu_widget, gpu_metric in zip(self.gpu_widgets, gpu_metrics):
                if gpu_widget.styles.display != "none":
                    update_tasks.append(self._update_gpu_widget(gpu_widget, gpu_metric))
            
            # Update temperature widget (only if visible)
            if self.temperature_widget and temperature_metrics:
                if self.temperature_widget.styles.display != "none":
                    update_tasks.append(self._update_temperature_widget(self.temperature_widget, temperature_metrics))
            
            # Execute all widget updates in parallel
            if update_tasks:
                await asyncio.gather(*update_tasks, return_exceptions=True)
                
        except Exception as e:
            logger.error(f"Error updating metrics: {e}")
        finally:
            self._update_in_progress = False
    
    async def _update_cpu_widget(self, widget, cpu_metrics, disk_metrics):
        """Update CPU widget (plot operations must be on main thread due to plotext)"""
        # Plotext is not thread-safe, so we must run on main thread
        try:
            widget.update_content(
                cpu_metrics['cpu_percentages'],
                cpu_metrics['cpu_freqs'],
                cpu_metrics['mem_percent'],
                disk_metrics['total_disk_used'],
                disk_metrics['total_disk_total']
            )
        except Exception as e:
            logger.error(f"Error updating CPU widget: {e}", exc_info=True)
    
    async def _update_memory_widget(self, widget, memory_metrics):
        """Update Memory widget (plot operations must be on main thread due to plotext)"""
        try:
            widget.update_content(
                memory_metrics['memory_info'],
                memory_metrics['swap_info'],
                memory_metrics.get('meminfo'),
                memory_metrics.get('commit_ratio'),
                memory_metrics.get('top_processes'),
                memory_metrics.get('memory_history')
            )
        except Exception as e:
            logger.error(f"Error updating memory widget: {e}", exc_info=True)
    
    async def _update_disk_widget(self, widget, disk):
        """Update Disk widget (plot operations must be on main thread due to plotext)"""
        try:
            widget.update_content(
                disk['read_speed'],
                disk['write_speed'],
                disk['disk_used'],
                disk['disk_total']
            )
        except Exception as e:
            logger.error(f"Error updating disk widget {widget.title}: {e}", exc_info=True)
    
    async def _update_network_widget(self, widget, network_metrics):
        """Update Network widget (plot operations must be on main thread due to plotext)"""
        try:
            widget.update_content(
                network_metrics['download_speed'],
                network_metrics['upload_speed']
            )
        except Exception as e:
            logger.error(f"Error updating Network widget: {e}", exc_info=True)
    
    async def _update_gpu_widget(self, widget, gpu_metric):
        """Update GPU widget (plot operations must be on main thread due to plotext)"""
        try:
            widget.update_content(
                gpu_metric["gpu_name"],
                gpu_metric['gpu_util'],
                gpu_metric['mem_used'],
                gpu_metric['mem_total']
            )
        except Exception as e:
            logger.error(f"Error updating GPU widget {widget.title}: {e}", exc_info=True)
    
    async def _update_temperature_widget(self, widget, temperature_metrics):
        """Update Temperature widget (plot operations must be on main thread due to plotext)"""
        try:
            widget.update_content(temperature_metrics)
        except Exception as e:
            logger.error(f"Error updating Temperature widget: {e}", exc_info=True)

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
        
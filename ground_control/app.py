import asyncio
import queue
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import (
    Header,
    Footer,
    SelectionList,
    Button,
    Static,
    Input,
    TabbedContent,
    TabPane,
    RadioButton,
    RadioSet,
    RichLog,
)
from textual.widgets.selection_list import Selection
from textual.reactive import reactive
from textual import on
import math
import os
import json
import logging
import traceback
from textual.events import Mount
from ground_control.widgets.cpu import CPUWidget
from ground_control.widgets.disk import DiskIOWidget
from ground_control.widgets.network import NetworkIOWidget
from ground_control.widgets.gpu import GPUWidget
from ground_control.widgets.memory import MemoryWidget
from ground_control.widgets.temperature import TemperatureWidget
from ground_control.utils.system_metrics import SystemMetrics
from ground_control.utils.colors import (
    load_colors,
    load_theme,
    ensure_colors_in_config,
    apply_theme,
    get_available_themes,
    get_theme_tokens,
)
from platformdirs import user_config_dir  # Import for cross-platform config directory
from textual.css.stylesheet import CssSource
from textual.screen import Screen

# Set up the user-specific config file path
CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


# Logger will be set up in main.py before app is created
logger = logging.getLogger("ground-control")

# Level-to-Rich-markup prefix for log lines in the Logs tab
_LOG_LEVEL_MARKUP = {
    logging.DEBUG: "[dim]",
    logging.INFO: "",
    logging.WARNING: "[yellow]",
    logging.ERROR: "[red]",
    logging.CRITICAL: "[bold red]",
}


# Logging format: second precision, no subsecond
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


class RichLogHandler(logging.Handler):
    """Logging handler that enqueues records; the app drains the queue to the RichLog on the main thread."""

    def __init__(self, message_queue: "queue.Queue[str]") -> None:
        super().__init__()
        self._queue = message_queue
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", datefmt=_LOG_DATEFMT)
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            prefix = _LOG_LEVEL_MARKUP.get(record.levelno, "")
            suffix = "[/]" if prefix else ""
            styled = f"{prefix}{msg}{suffix}"
            self._queue.put_nowait(styled)
        except Exception:
            self.handleError(record)


def _build_theme_swatch(theme_name: str) -> str:
    """Build a Rich-markup label for a theme showing its name and a color swatch.

    Picks representative keys from the theme JSON and renders a row of colored
    block characters next to the theme name (19 colors: UI + widget palette).

    Args:
        theme_name: Name of the theme (without .json extension).

    Returns:
        A Rich markup string like ``monokai  ███████████████████``
    """
    colors = load_theme(theme_name) or {}
    swatch_keys = [
        "background", "surface", "accent", "border",
        "cpu_bar", "memory_ram", "memory_swap", "gpu_ram", "gpu_usage",
        "network_download", "network_upload", "disk_read", "disk_write",
        "temp_cool", "temp_normal", "temp_hot", "temp_critical",
        "text", "selection_highlight",
    ]
    blocks = ""
    for key in swatch_keys:
        hex_color = colors.get(key)
        if hex_color:
            blocks += f"[{hex_color}]██[/]"
    label = f"{theme_name:<14s} {blocks}" if blocks else theme_name
    return label


# Predefined options for refresh rate (seconds) and history size (seconds)
REFRESH_RATES = [60, 30, 15, 10, 5, 2, 1, 0.5]
HISTORY_SIZES = [600, 300, 180, 120, 60, 30]


def _refresh_label(rate: float) -> str:
    """Label for a refresh rate option."""
    if rate == 60:
        return "1m"
    if rate == 30:
        return "30s"
    if rate == 0.5:
        return "500ms"
    return f"{int(rate)}s"


def _history_label(size: int) -> str:
    """Label for a history size option."""
    return f"{size // 60}m" if size >= 60 else f"{size}s"


class GroundControl(App):
    """App uses only its own themes (themes/*.json); Textual built-in themes are disabled."""

    CSS_PATH: list[str] = []  # Do not load any Textual theme; all styling comes from _generate_css() and our theme JSONs

    def __init__(self, allowed_types: set[str] | None = None, gpu_indices: list[int] | None = None, debug: bool = False):
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
        self.selectionoptions = []
        self.selected_widgets = {}  # Initialize selected_widgets
        self.json_exists = os.path.exists(CONFIG_FILE)
        self._update_timer = None
        self._is_initializing = True  # Flag to prevent toast notifications during startup
        self._config_save_task = None  # For debounced config saves (asyncio.Task)
        self._update_in_progress = False  # Prevent concurrent updates
        self.allowed_types = allowed_types
        self.gpu_indices = gpu_indices  # None = all GPUs; [0, 1] = only those indices
        self._failed_widget_titles = set()  # Widgets disabled due to error; stay hidden and are not updated
        self._widget_tab_states: dict[str, str] = {}  # Maps widget title -> active tab pane id
        # Internal debug flag (avoid clashing with Textual's App.debug property)
        self._debug_mode = debug
        self._log_handler: logging.Handler | None = None  # RichLogHandler, set in on_mount
        self._log_queue: queue.Queue[str] = queue.Queue()
        # Prevent concurrent layout/widget rebuilds that can create duplicate widgets
        self._setup_lock: asyncio.Lock = asyncio.Lock()
        # Disk mount paths to hide: any mountpoint that starts with one of these (after normalizing) is skipped
        self.disk_ignore_prefixes: list[str] = ["/boot/efi"]

    def _refresh_stylesheet(self) -> None:
        """Replace the app's CSS in the stylesheet with current self.CSS and re-apply to the DOM."""
        # Our generated CSS is identified by a unique comment from _generate_css.
        marker = "/* Single theme"
        updated = False
        for read_from, css_source in list(self.stylesheet.source.items()):
            if marker in css_source.content:
                self.stylesheet.source[read_from] = CssSource(
                    self.CSS, css_source.is_defaults, css_source.tie_breaker, css_source.scope
                )
                updated = True
                break
        if not updated:
            # Fallback: add as new source (e.g. if stylesheet was built before our CSS was set)
            self.stylesheet.add_source(
                self.CSS,
                read_from=("", "GroundControl_theme"),
                is_default_css=False,
                tie_breaker=1000,
            )
        self.stylesheet._require_parse = True
        self.stylesheet._rules_map = None
        # Force re-parse (next .rules access parses) then re-apply to entire tree.
        _ = self.stylesheet.rules
        self.stylesheet.update(self)

    def _generate_css(self) -> None:
        """Generate CSS from theme tokens only. All colors come from theme (no hardcoded values)."""
        tok = get_theme_tokens(self._color_config)
        self.CSS = f"""
    /* Theme-driven: all colors from get_theme_tokens (colors.py) */
    GroundControl {{
        background: {tok["bg"]};
        color: {tok["text"]};
    }}
    Screen {{
        background: {tok["bg"]};
        color: {tok["text"]};
    }}
    #root-tabs {{
        background: {tok["bg"]};
        color: {tok["text"]};
    }}
    #dashboard-pane {{
        background: {tok["bg"]};
        color: {tok["text"]};
    }}
    #logs-pane {{
        background: {tok["bg"]};
        height: 1fr;
        overflow: hidden;
        padding: 0 1;
        color: {tok["text"]};
    }}
    #app-log {{
        width: 100%;
        height: 100%;
        border: round {tok["border"]};
        padding: 1;
        color: {tok["text"]};
    }}
    #settings-pane {{
        background: {tok["bg"]};
        color: {tok["text"]};
    }}
    Grid {{
        grid-size: 3 3;
        align: center middle;
        width: 100%;
        height: 100%;
    }}
    GPUWidget, NetworkIOWidget, DiskIOWidget, CPUWidget, MemoryWidget, TemperatureWidget {{
        background: {tok["bg"]};
        border: round {tok["border"]};
        min-height: 10;
        color: {tok["text"]};
    }}

    Tab {{
        background: {tok["tab_inactive_bg"]};
        color: {tok["tab_inactive_fg"]};
    }}
    Tab:hover {{
        background: {tok["tab_active_bg"]};
        color: {tok["tab_active_fg"]};
    }}
    Tab.-active {{
        background: {tok["tab_active_bg"]};
        color: {tok["tab_active_fg"]};
    }}
    Underline > .underline--bar {{
        background: {tok["tab_active_bg"]};
        color: {tok["tab_active_bg"]};
    }}

    /* Scoped under GroundControl so we override Textual's default theme for header/footer */
    GroundControl > Header {{
        background: {tok["header_bg"]};
        color: {tok["header_fg"]};
    }}
    GroundControl > Footer {{
        background: {tok["footer_bg"]};
        color: {tok["footer_fg"]};
    }}
    GroundControl > Footer FooterKey {{
        color: {tok["footer_fg"]};
    }}
    GroundControl > Footer > FooterKey > .footer-key--key {{
        background: {tok["footer_key_bg"]};
        color: {tok["footer_key_fg"]};
    }}

    SelectionList {{
        background: {tok["bg"]};
        width: 100%;
        height: auto;
        padding: 0;
    }}

    #settings-pane {{
        height: 1fr;
        overflow-y: auto;
        padding: 0;
    }}
    #settings-sections {{
        width: 100%;
        height: auto;
        layout: vertical;
        padding: 0;
    }}
    #settings-row-1, #settings-row-2 {{
        width: 100%;
        height: auto;
    }}
    #settings-row-1 > Vertical, #settings-row-2 > Vertical {{
        width: 1fr;
    }}
    .settings-block {{
        width: 100%;
        height: auto;
        min-height: 3;
        padding: 0;
        margin: 0;
        border: round {tok["border"]};
        background: {tok["bg"]};
    }}
    .settings-section-title {{
        text-style: bold;
        height: 1;
        margin: 0 0 1 0;
        padding: 0;
        color: {tok["text"]};
    }}

    #refresh-radio-set, #history-radio-set, #theme-radio-set, #layout-radio-set {{
        width: 100%;
        height: auto;
        background: transparent;
        border: none;
        padding: 0;
    }}
    RadioButton {{
        background: transparent;
        color: {tok["text"]};
        padding: 0;
    }}
    RadioButton:hover {{
        background: {tok["selection"]} 15%;
    }}
    RadioSet:focus > RadioButton.-on, RadioButton.-on {{
        color: {tok["selection"]};
        text-style: bold;
    }}

    #dashboard-pane {{
        height: 1fr;
        overflow: auto;
    }}
    #logs-pane {{
        height: 1fr;
    }}
    #root-tabs {{
        height: 1fr;
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
        ("d", "open_dashboard", "Dashboard"),
        ("s", "open_settings", "Settings"),
        ("l", "open_logs", "Logs"),
        ("g", "set_grid", "Grid"),
        ("h", "set_horizontal", "Horiz"),
        ("v", "set_vertical", "Vert"),
        ("r", "force_refresh", "Refresh"),
    ]



    def watch_refresh_rate(self, new_rate: float) -> None:
        """React to changes in refresh rate."""
        if self._update_timer:
            self._update_timer.stop()
        self._update_timer = self.set_interval(new_rate, self._update_metrics_sync)
        self.save_config()
        self._select_refresh_radio(new_rate)

    def watch_history_size(self, new_size: int) -> None:
        """React to changes in history size."""
        self.save_config()
        self._select_history_radio(new_size)
        if not self._is_initializing:
            self._update_widget_history_sizes(new_size)
        logger.debug(f"History size changed to {new_size}s")

    @on(RadioSet.Changed, "#refresh-radio-set")
    def _on_refresh_radio_changed(self, event: RadioSet.Changed) -> None:
        """Handle refresh rate selection from the RadioSet."""
        if self._is_initializing:
            return
        btn = event.pressed
        if btn and btn.id and btn.id.startswith("refresh-"):
            rate_str = btn.id.replace("refresh-", "").replace("-", ".")
            try:
                self.refresh_rate = float(rate_str)
            except ValueError:
                pass

    @on(RadioSet.Changed, "#history-radio-set")
    def _on_history_radio_changed(self, event: RadioSet.Changed) -> None:
        """Handle history size selection from the RadioSet."""
        if self._is_initializing:
            return
        btn = event.pressed
        if btn and btn.id and btn.id.startswith("history-"):
            try:
                self.history_size = int(btn.id.replace("history-", ""))
            except ValueError:
                pass

    @on(RadioSet.Changed, "#theme-radio-set")
    def _on_theme_radio_changed(self, event: RadioSet.Changed) -> None:
        """Handle theme selection from the RadioSet.

        Extracts the theme name from the pressed RadioButton's id
        (``theme-<name>``) and applies it live.
        """
        if self._is_initializing:
            return
        btn = event.pressed
        if btn and btn.id and btn.id.startswith("theme-"):
            theme_name = btn.id.replace("theme-", "")
            self._apply_theme_from_ui(theme_name)

    @on(RadioSet.Changed, "#layout-radio-set")
    def _on_layout_radio_changed(self, event: RadioSet.Changed) -> None:
        """Handle layout selection from the RadioSet."""
        if self._is_initializing:
            return
        btn = event.pressed
        if btn and btn.id:
            layout = btn.id.replace("layout-", "")
            if layout in {"grid", "horizontal", "vertical"}:
                self.set_layout(layout)

    @on(Input.Submitted, "#disk-ignore-prefixes")
    async def _on_disk_ignore_prefixes_submitted(self, event: Input.Submitted) -> None:
        """Apply disk ignore prefixes and rebuild disk widgets."""
        raw = (event.value or "").strip()
        self.disk_ignore_prefixes = [p.strip() for p in raw.split(",") if p.strip()]
        if not self.disk_ignore_prefixes:
            self.disk_ignore_prefixes = ["/boot/efi"]
        self._do_save_config()
        await self.setup_widgets()
        self.apply_widget_visibility()

    def _select_layout_radio(self, layout: str) -> None:
        """Pre-select the correct layout RadioButton on mount.

        Args:
            layout: One of ``grid``, ``horizontal``, ``vertical``.
        """
        try:
            radio_set = self.query_one("#layout-radio-set", RadioSet)
            for idx, btn in enumerate(radio_set.query(RadioButton)):
                if btn.id == f"layout-{layout}":
                    radio_set._selected = idx
                    btn.value = True
                    break
        except Exception:
            if self._debug_mode:
                raise

    def _select_refresh_radio(self, rate: float) -> None:
        """Pre-select the refresh rate RadioButton matching the given rate."""
        try:
            radio_set = self.query_one("#refresh-radio-set", RadioSet)
            bid = f"refresh-{rate}".replace(".", "-")
            for idx, btn in enumerate(radio_set.query(RadioButton)):
                if btn.id == bid:
                    radio_set._selected = idx
                    btn.value = True
                    return
        except Exception:
            if self._debug_mode:
                raise

    def _update_refresh_buttons(self) -> None:
        """Sync the refresh rate radio selection with the current refresh_rate.

        Used when opening the settings panel so the correct button is highlighted.
        Safe to call even if the settings DOM is not mounted yet.
        """
        try:
            self._select_refresh_radio(self.refresh_rate)
        except Exception:
            pass

    def _select_history_radio(self, size: int) -> None:
        """Pre-select the history size RadioButton matching the given size."""
        try:
            radio_set = self.query_one("#history-radio-set", RadioSet)
            for idx, btn in enumerate(radio_set.query(RadioButton)):
                if btn.id == f"history-{size}":
                    radio_set._selected = idx
                    btn.value = True
                    return
        except Exception:
            if self._debug_mode:
                raise

    def _select_current_theme_radio(self) -> None:
        """Pre-select the RadioButton corresponding to the active theme.

        Reads the config file to determine which theme is currently active
        and checks that theme against each RadioButton id.
        """
        try:
            current_theme = None
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                current_colors = config.get("colors", {})
                # Compare against each available theme to find a match
                for name in get_available_themes():
                    theme_colors = load_theme(name) or {}
                    if theme_colors == current_colors:
                        current_theme = name
                        break
            if current_theme:
                radio_set = self.query_one("#theme-radio-set", RadioSet)
                for idx, btn in enumerate(radio_set.query(RadioButton)):
                    if btn.id == f"theme-{current_theme}":
                        radio_set._selected = idx
                        btn.value = True
                        break
        except Exception:
            if self._debug_mode:
                raise

    def load_config(self) -> dict:
        """Load full configuration from file.

        Restores refresh_rate, history_size, layout, widget tab states and
        widget visibility (selected dict). All values fall back to sensible
        defaults when the config file is missing or malformed.

        Returns:
            dict: The ``selected`` widget-visibility mapping.
        """
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    self.refresh_rate = float(config.get("refresh_rate", 1.0))
                    self.history_size = int(config.get("history_size", 120))
                    self._widget_tab_states = config.get("widget_tabs", {})
                    # Disk ignore prefixes: comma-separated in config, e.g. "/boot/efi, /boot, /snap"
                    raw = config.get("disk_ignore_prefixes", "/boot/efi")
                    if isinstance(raw, list):
                        self.disk_ignore_prefixes = [str(p).strip() for p in raw if str(p).strip()]
                    else:
                        self.disk_ignore_prefixes = [p.strip() for p in str(raw).split(",") if p.strip()]
                    if not self.disk_ignore_prefixes:
                        self.disk_ignore_prefixes = ["/boot/efi"]
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
        """Actually perform the config file write.

        Persists *all* runtime state: refresh_rate, history_size, selected
        widgets, layout, and active tab states.  Existing keys in the config
        file (e.g. ``colors``) are preserved because we read-then-update.
        """
        try:
            try:
                with open(CONFIG_FILE, "r") as f:
                    config_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                config_data = {}

            config_data.update({
                "refresh_rate": self.refresh_rate,
                "history_size": self.history_size,
                "selected": self.selected_widgets,
                "layout": getattr(self, "current_layout", "grid"),
                "widget_tabs": self._widget_tab_states,
                "disk_ignore_prefixes": ", ".join(self.disk_ignore_prefixes),
            })

            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            logger.error("Config save failed: %s", e)
            if self._debug_mode:
                raise

    def _apply_theme_from_ui(self, theme_name: str) -> None:
        """Apply a theme selected from the Settings tab.

        This updates the persistent config (via ``apply_theme``) and then
        reloads colors and CSS for the running app so plots and UI pick up
        the new palette immediately.
        """
        available = get_available_themes()
        if theme_name not in available:
            logger.error("Unknown theme: %s", theme_name)
            return

        if not apply_theme(theme_name):
            logger.error(f"Failed to apply theme {theme_name!r}")
            return

        # Reload colors from the updated config and regenerate CSS.
        self._color_config = load_colors()
        self._generate_css()
        # Apply the new CSS immediately: update the stylesheet source and re-apply to the DOM.
        self._refresh_stylesheet()

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

    def _disk_mount_ignored(self, mountpoint: str) -> bool:
        """True if mountpoint should be hidden (matches any configured ignore prefix)."""
        mp = mountpoint.rstrip("/") or "/"
        for prefix in self.disk_ignore_prefixes:
            p = prefix.rstrip("/") or "/"
            if mp == p or mp.startswith(p + "/"):
                return True
        return False

    def get_layout_columns(self, num_gpus: int) -> int:
        return len(self.select.selected)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app.

        Three top-level tabs:

        - ``Dashboard``: live metric widgets in a configurable grid layout.
        - ``Logs``: streaming app logs in a scrollable RichLog.
        - ``Settings``: all configuration controls (widget visibility,
          refresh rate, history size, theme selector with colour swatches,
          and layout selector).
        """
        yield Header()

        with TabbedContent(initial="dashboard", id="root-tabs"):
            # Dashboard tab: just the widget grid.
            with TabPane("Dashboard", id="dashboard"):
                with Vertical(id="dashboard-pane"):
                    self.grid = Grid(classes="grid")
                    yield self.grid

            # Settings tab: row 1 = Visible Widgets & Theme; row 2 = Refresh, History, Layout; row 3 = Disk ignore.
            with TabPane("Settings", id="settings"):
                with Vertical(id="settings-pane"):
                    with Vertical(id="settings-sections"):
                        # Row 1: Visible Widgets | Theme
                        with Horizontal(id="settings-row-1"):
                            with Vertical(classes="settings-block"):
                                yield Static(" Visible Widgets", classes="settings-section-title")
                                self.select = SelectionList[str]()
                                yield self.select
                            with Vertical(classes="settings-block"):
                                yield Static(" Theme", classes="settings-section-title")
                                with RadioSet(id="theme-radio-set"):
                                    for name in get_available_themes():
                                        yield RadioButton(
                                            _build_theme_swatch(name),
                                            id=f"theme-{name}",
                                        )

                        # Row 2: Refresh rate | History | Layout
                        with Horizontal(id="settings-row-2"):
                            with Vertical(classes="settings-block"):
                                yield Static(" Refresh rate", classes="settings-section-title")
                                with RadioSet(id="refresh-radio-set"):
                                    for rate in REFRESH_RATES:
                                        bid = f"refresh-{rate}".replace(".", "-")
                                        yield RadioButton(_refresh_label(rate), id=bid)
                            with Vertical(classes="settings-block"):
                                yield Static(" History size", classes="settings-section-title")
                                with RadioSet(id="history-radio-set"):
                                    for size in HISTORY_SIZES:
                                        yield RadioButton(_history_label(size), id=f"history-{size}")
                            with Vertical(classes="settings-block"):
                                yield Static(" Layout", classes="settings-section-title")
                                with RadioSet(id="layout-radio-set"):
                                    yield RadioButton("Grid", id="layout-grid")
                                    yield RadioButton("Horizontal", id="layout-horizontal")
                                    yield RadioButton("Vertical", id="layout-vertical")

                        # Row 3: Disk ignore (full width)
                        with Vertical(classes="settings-block"):
                            yield Static(" Disk ignore prefixes", classes="settings-section-title")
                            yield Input(
                                id="disk-ignore-prefixes",
                                placeholder="e.g. /boot/efi, /boot, /snap",
                            )
            
            # Logs tab: streaming app logs in a scrollable RichLog.
            with TabPane("Logs", id="logs"):
                with Vertical(id="logs-pane"):
                    yield RichLog(
                        id="app-log",
                        markup=True,
                        highlight=False,
                        max_lines=10_000,
                        auto_scroll=True,
                        wrap=True,
                    )
        yield Footer()

    async def on_mount(self) -> None:
        self.current_layout = "grid"
        self.selected_widgets = self.load_config()  # Load all config

        await self.setup_widgets()
        if not self.json_exists:
            self.create_json()
        self.set_layout(self.load_layout())

        self.apply_widget_visibility()
        self._update_timer = self.set_interval(self.refresh_rate, self._update_metrics_sync)
        self._select_refresh_radio(self.refresh_rate)
        self._select_history_radio(self.history_size)
        self._select_layout_radio(self.current_layout)
        self._select_current_theme_radio()
        try:
            self.query_one("#disk-ignore-prefixes", Input).value = ", ".join(self.disk_ignore_prefixes)
        except Exception:
            pass

        # Mark initialization as complete - now toast notifications can be shown
        self._is_initializing = False

        # Stream logs to the Logs tab (handler enqueues; timer drains to RichLog on main thread)
        rich_handler = RichLogHandler(self._log_queue)
        rich_handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(rich_handler)
        self._log_handler = rich_handler
        self._log_drain_timer = self.set_interval(0.15, self._drain_log_queue)

    def _drain_log_queue(self) -> None:
        """Drain pending log lines from the queue into the RichLog (called on main thread)."""
        try:
            log_widget = self.query_one("#app-log", RichLog)
        except Exception:
            return
        while True:
            try:
                styled = self._log_queue.get_nowait()
            except queue.Empty:
                break
            try:
                log_widget.write(styled, scroll_end=None)
            except Exception:
                break

    async def setup_widgets(self) -> None:
        """(Re)build all metric widgets and grid layout.

        Protected by an async lock so multiple callers (e.g. rapid layout
        changes, temperature prefix edits, initial mount + layout restore)
        cannot interleave and accidentally create duplicate widgets.
        """
        async with self._setup_lock:
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

            # Force equal fractional row/column sizes so each cell gets real space.
            # Without this, vertical (and sometimes horizontal) layout can use
            # content-based sizing and give 0 height to plot widgets.
            rows = self.grid.styles.grid_size_rows
            cols = self.grid.styles.grid_size_columns
            self.grid.styles.grid_rows = " ".join("1fr" for _ in range(rows))
            self.grid.styles.grid_columns = " ".join("1fr" for _ in range(cols))

            # Always create new widgets when setup_widgets is called
            # Resolve saved tab state for CPU widget
            cpu_title = f"{cpu_metrics['cpu_name']}"
            cpu_initial_tab = self._widget_tab_states.get(cpu_title, "all")
            cpu_widget = CPUWidget(cpu_title, initial_tab=cpu_initial_tab)
            memory_widget = MemoryWidget("Memory")
            self.disk_widgets = []
            self.gpu_widgets = []
            self.temperature_widget = None
            network_widget = NetworkIOWidget("Network")
        
            await self.grid.mount(cpu_widget)
            await self.grid.mount(memory_widget)
            
            # Create temperature widget only if temperature data is available
            temperature_metrics = self.system_metrics.get_temperature_metrics()
            logger.info("Setup: temperature metrics: %s", temperature_metrics)
            if temperature_metrics:
                self.temperature_widget = TemperatureWidget("Temperature", history_size=int(self.history_size))
                await self.grid.mount(self.temperature_widget)
            else:
                logger.info("No temperature sensors found - skipping temperature widget")
                self.temperature_widget = None
        
            # Mount multiple disk widgets (skip mountpoints matching user-configured prefixes)
            # Use a running index for IDs so each mounted widget has a unique id (avoids DuplicateIds
            # when mountpoint "/" becomes "_" and would yield "disk_0__", or when setup_widgets runs again).
            def _disk_id_suffix(mountpoint: str) -> str:
                if mountpoint == "/" or not mountpoint:
                    return "root"
                return mountpoint.replace("/", "_").strip("_") or "root"

            disk_index = 0
            for disk in disk_metrics["disks"]:
                if self._disk_mount_ignored(disk["mountpoint"]):
                    logger.info("Setup: skipping disk mountpoint %s (matches ignore prefix)", disk["mountpoint"])
                    continue
                disk_id = f"disk_{disk_index}_{_disk_id_suffix(disk['mountpoint'])}"
                disk_widget = DiskIOWidget(f"Disk @ {disk['mountpoint']}", id=disk_id)
                self.disk_widgets.append(disk_widget)
                await self.grid.mount(disk_widget)
                disk_index += 1
            
            await self.grid.mount(network_widget)
        
            # Filter GPU metrics by gpu_indices if set (e.g. gc -g 0 or --gpu-index 0,1)
            if self.gpu_indices is not None:
                gpu_metrics = [gpu_metrics[i] for i in self.gpu_indices if 0 <= i < len(gpu_metrics)]
            # Mount GPU widgets (restore saved tab state per GPU)
            for gpu in gpu_metrics:
                gpu_title = f"GPU @ {gpu['gpu_name']}"
                gpu_initial_tab = self._widget_tab_states.get(gpu_title, "plot")
                gpu_widget = GPUWidget(
                    gpu_title,
                    id=f"gpu_{len(self.gpu_widgets)}",
                    initial_tab=gpu_initial_tab,
                )
                self.gpu_widgets.append(gpu_widget)
                await self.grid.mount(gpu_widget)
            
            logger.info(f"Setup complete: {len(self.disk_widgets)} disk widgets, {len(self.gpu_widgets)} GPU widgets")
            
            # Update selection list after widgets are created
            self.create_selection_list()

    def create_json(self) -> None:
        """Create the initial config file with all current state.

        Called only the very first time the application runs and no config
        file exists yet.
        """
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
            "history_size": self.history_size,
            "widget_tabs": self._widget_tab_states,
        }
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)

                
    def create_selection_list(self) -> None:
        """Rebuild the config selection list from current grid children.

        Visibility is driven by selected_widgets (saved config + user toggles).
        When a CLI filter (allowed_types / -g etc.) is set, it is used only as
        the default for widgets that have no entry in selected_widgets; the user
        can still change visibility from the config panel and that is respected.
        """
        self.select.clear_options()
        self.selectionoptions.clear()

        for widget in self.grid.children:
            if not hasattr(widget, "title"):
                continue
            widget_type = self._get_widget_type(widget)
            # CPU and Memory are always visible; do not add them to the selector list
            if widget_type in ("cpu", "ram"):
                continue
            # Prefer saved/config and user choices; use CLI filter only as default when missing
            if self.allowed_types:
                default = widget_type in self.allowed_types
            else:
                default = True
            had_key = widget.title in self.selected_widgets
            selected = self.selected_widgets.get(widget.title, default)
            if not had_key:
                self.selected_widgets[widget.title] = selected

            self.select.add_option(Selection(widget.title, widget.title, selected))
            self.selectionoptions.append(widget.title)

    def _get_widget_type(self, widget) -> str:
        """Helper to map widget instance to type string."""
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

    # Metric types required per widget type (used to collect only what visible widgets need)
    _WIDGET_TYPE_REQUIRED_METRICS = {
        "cpu": ["cpu"],
        "ram": ["memory"],
        "disk": ["disk"],
        "net": ["network"],
        "gpu": ["gpu"],
        "temp": ["temperature"],
    }

    def _get_required_metric_types(self, widget) -> set:
        """Return the set of metric type strings required by this widget."""
        t = self._get_widget_type(widget)
        return set(self._WIDGET_TYPE_REQUIRED_METRICS.get(t, []))

    @on(TabbedContent.TabActivated)
    def _on_any_tab_changed(self, event: TabbedContent.TabActivated) -> None:
        """Persist the active tab whenever a tab is switched in any widget.

        Walks up the DOM from the TabbedContent to find the owning
        MetricWidget and stores a mapping of widget-title -> active-pane-id
        in the config.
        """
        if self._is_initializing:
            return
        pane_id = event.pane.id if event.pane else None
        if not pane_id:
            return
        # Walk up to find the parent MetricWidget
        node = event.tabbed_content
        while node is not None:
            if isinstance(node, (CPUWidget, GPUWidget)):
                break
            node = node.parent
        if node is not None and hasattr(node, "title"):
            self._widget_tab_states[node.title] = pane_id
            self.save_config()

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
        """Toggle widget visibility based on selected titles.

        CPU and Memory are always shown (not in selector). In normal mode, failed
        widgets are fully hidden. In debug mode, failed widgets remain visible.
        """
        for widget in self.grid.children:
            if not hasattr(widget, "title"):
                continue
            wt = self._get_widget_type(widget)
            if widget.title in self._failed_widget_titles:
                widget.styles.display = "block" if self._debug_mode else "none"
            elif wt in ("cpu", "ram"):
                widget.styles.display = "block"
            else:
                widget.styles.display = "block" if widget.title in selected_titles else "none"
            logger.debug("Widget %s display: %s", widget.title, widget.styles.display)

    def _update_metrics_sync(self):
        """Synchronous wrapper to trigger async update_metrics"""
        asyncio.create_task(self.update_metrics())
    
    async def update_metrics(self):
        """Collect only metrics required by visible widgets; update only active widgets. On widget error, disable that widget."""
        if self._update_in_progress:
            return
        self._update_in_progress = True

        try:
            # Active = visible and not failed
            active_widgets = [
                w for w in self.grid.children
                if hasattr(w, "title")
                and w.styles.display != "none"
                and w.title not in self._failed_widget_titles
            ]
            if not active_widgets:
                return

            # Required metric types = union over active widgets
            required_types = set()
            for w in active_widgets:
                required_types |= self._get_required_metric_types(w)

            # Run only required collectors in executor
            loop = asyncio.get_event_loop()
            collectors = {
                "cpu": self.system_metrics.get_cpu_metrics,
                "disk": self.system_metrics.get_disk_metrics,
                "memory": self.system_metrics.get_memory_metrics,
                "network": self.system_metrics.get_network_metrics,
                "gpu": self.system_metrics.get_gpu_metrics,
                "temperature": self.system_metrics.get_temperature_metrics,
            }
            tasks = [loop.run_in_executor(None, collectors[t]) for t in required_types]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            metrics_by_type = {}
            collector_errors = {}  # type -> exception, for debug display
            for t, r in zip(required_types, results):
                if isinstance(r, BaseException):
                    logger.error("Collector %s failed: %s", t, r, exc_info=True)
                    metrics_by_type[t] = None
                    collector_errors[t] = r
                else:
                    metrics_by_type[t] = r

            # In debug mode, any collector exception should kill the app
            if self._debug_mode and collector_errors:
                first_err = next(iter(collector_errors.values()))
                raise first_err

            # Update each active widget; on exception disable that widget (or show error in debug mode)
            for widget in active_widgets:
                needed = self._get_required_metric_types(widget)
                missing = [t for t in needed if metrics_by_type.get(t) is None]
                if missing:
                    if self._debug_mode:
                        # Show collector failure in the widget
                        err = collector_errors.get(missing[0])
                        if err is not None:
                            self._failed_widget_titles.add(widget.title)
                            try:
                                widget.styles.display = "block"
                                full_tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
                                error_text = (
                                    f"[bold red]{widget.title} — Collector error[/]\n\n"
                                    f"[bold]{type(err).__name__}:[/] {err}\n\n"
                                    f"[dim]Traceback:[/]\n{full_tb}"
                                )
                                for child in list(widget.children):
                                    child.remove()
                                await widget.mount(Static(error_text))
                            except Exception:
                                raise
                    continue
                try:
                    await self._dispatch_widget_update(widget, metrics_by_type)
                except Exception as e:
                    logger.error("Widget %s failed: %s", widget.title, e, exc_info=True)
                    # Mark widget as failed so it is skipped on future updates
                    self._failed_widget_titles.add(widget.title)
                    if self._debug_mode:
                        # In debug mode: keep the widget visible and show full error + traceback in the widget
                        try:
                            widget.styles.display = "block"
                            full_tb = traceback.format_exc()
                            error_text = (
                                f"[bold red]{widget.title} — Widget error[/]\n\n"
                                f"[bold]{type(e).__name__}:[/] {e}\n\n"
                                f"[dim]Traceback:[/]\n{full_tb}"
                            )
                            # Replace widget content so the error is visible (widget has tabs/children)
                            for child in list(widget.children):
                                child.remove()
                            await widget.mount(Static(error_text))
                        except Exception:
                            # If even rendering the error fails, let the exception propagate in debug mode
                            raise
                    else:
                        # Normal mode: hide failed widgets to avoid a broken dashboard view
                        widget.styles.display = "none"
        except Exception as e:
            logger.error("Error in update_metrics: %s", e, exc_info=True)
            if self._debug_mode:
                # Let the exception propagate in debug mode so it is visible
                raise
        finally:
            self._update_in_progress = False

    async def _dispatch_widget_update(self, widget, metrics_by_type: dict):
        """Call the appropriate _update_* for widget using metrics_by_type. Caller must ensure required metrics are present."""
        if isinstance(widget, CPUWidget):
            await self._update_cpu_widget(widget, metrics_by_type["cpu"])
        elif isinstance(widget, MemoryWidget):
            await self._update_memory_widget(widget, metrics_by_type["memory"])
        elif isinstance(widget, DiskIOWidget):
            disk_metrics = metrics_by_type["disk"]
            for disk in disk_metrics["disks"]:
                if widget.title == f"Disk @ {disk['mountpoint']}":
                    await self._update_disk_widget(widget, disk)
                    return
        elif isinstance(widget, NetworkIOWidget):
            await self._update_network_widget(widget, metrics_by_type["network"])
        elif isinstance(widget, GPUWidget):
            gpu_metrics = metrics_by_type["gpu"]
            if self.gpu_indices is not None:
                gpu_metrics = [gpu_metrics[i] for i in self.gpu_indices if 0 <= i < len(gpu_metrics)]
            # Resolve index from widget id (e.g. gpu_0 -> 0); grid children may be new instances after layout/setup
            idx = None
            if widget.id and str(widget.id).startswith("gpu_"):
                try:
                    idx = int(str(widget.id).split("_", 1)[1])
                except (ValueError, IndexError):
                    pass
            if idx is None:
                try:
                    idx = self.gpu_widgets.index(widget)
                except ValueError:
                    return
            if 0 <= idx < len(gpu_metrics):
                await self._update_gpu_widget(widget, gpu_metrics[idx])
        elif isinstance(widget, TemperatureWidget):
            temp = metrics_by_type.get("temperature")
            if temp is not None:
                await self._update_temperature_widget(widget, temp)
    
    async def _update_cpu_widget(self, widget, cpu_metrics):
        """Update CPU widget (plot operations on main thread due to plotext)."""
        widget.update_content(
            cpu_metrics['cpu_percentages'],
            cpu_metrics['cpu_freqs'],
            cpu_metrics['mem_percent'],
        )

    async def _update_memory_widget(self, widget, memory_metrics):
        """Update Memory widget (plot operations on main thread due to plotext)."""
        widget.update_content(
            memory_metrics['memory_info'],
            memory_metrics['swap_info'],
            memory_metrics.get('meminfo'),
            memory_metrics.get('commit_ratio'),
            memory_metrics.get('top_processes'),
            memory_metrics.get('memory_history')
        )

    async def _update_disk_widget(self, widget, disk):
        """Update Disk widget (plot operations on main thread due to plotext)."""
        widget.update_content(
            disk['read_speed'],
            disk['write_speed'],
            disk['disk_used'],
            disk['disk_total']
        )

    async def _update_network_widget(self, widget, network_metrics):
        """Update Network widget (plot operations on main thread due to plotext)."""
        widget.update_content(
            network_metrics['download_speed'],
            network_metrics['upload_speed']
        )

    async def _update_gpu_widget(self, widget, gpu_metric):
        """Update GPU widget (plot operations on main thread due to plotext)."""
        widget.update_content(
            gpu_metric["gpu_name"],
            gpu_metric['gpu_util'],
            gpu_metric['mem_used'],
            gpu_metric['mem_total'],
            gpu_metric.get('processes'),
        )

    async def _update_temperature_widget(self, widget, temperature_metrics):
        """Update Temperature widget (plot operations on main thread due to plotext)."""
        widget.update_content(temperature_metrics)

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

    def action_open_settings(self) -> None:
        """Switch to the Settings tab."""
        try:
            root_tabs = self.query_one("#root-tabs", TabbedContent)
            root_tabs.active = "settings"
        except Exception:
            if self._debug_mode:
                raise

    def action_open_dashboard(self) -> None:
        """Switch back to the main Dashboard tab."""
        try:
            root_tabs = self.query_one("#root-tabs", TabbedContent)
            root_tabs.active = "dashboard"
        except Exception:
            if self._debug_mode:
                raise

    def action_open_logs(self) -> None:
        """Switch to the Logs tab."""
        try:
            root_tabs = self.query_one("#root-tabs", TabbedContent)
            root_tabs.active = "logs"
        except Exception:
            if self._debug_mode:
                raise

    def _iter_visible_metric_widgets(self):
        """Return a list of visible metric widgets in the grid, in DOM order."""
        if self.grid is None:
            return []
        widgets = []
        for w in self.grid.children:
            if hasattr(w, "title") and getattr(w.styles, "display", "block") != "none":
                widgets.append(w)
        return widgets

    def _focus_widget_by_offset(self, offset: int) -> None:
        """Move focus to the next/previous visible metric widget."""
        widgets = self._iter_visible_metric_widgets()
        if not widgets:
            return
        current = getattr(self, "focused", None)
        try:
            idx = widgets.index(current)
        except ValueError:
            idx = 0
        new_idx = (idx + offset) % len(widgets)
        try:
            self.set_focus(widgets[new_idx])
        except Exception:
            if self._debug_mode:
                raise

    def action_focus_next_widget(self) -> None:
        """Cycle focus to the next visible metric widget in the dashboard grid."""
        self._focus_widget_by_offset(1)

    def action_force_refresh(self) -> None:
        """Force an immediate metrics refresh."""
        self._update_metrics_sync()

    def action_faster_refresh(self) -> None:
        """Decrease the refresh interval (faster updates).

        Cycles through the predefined refresh rate buttons towards faster
        values, clamping at 0.5 s.
        """
        rates = sorted([60, 30, 15, 10, 5, 2, 1, 0.5])  # ascending
        current = self.refresh_rate
        # Find next faster (smaller) rate
        for r in reversed(rates):
            if r < current - 0.01:
                self.refresh_rate = r
                return
        # Already at fastest
        logger.info("Refresh: already at fastest rate")

    def action_slower_refresh(self) -> None:
        """Increase the refresh interval (slower updates).

        Cycles through the predefined refresh rate buttons towards slower
        values, clamping at 60 s.
        """
        rates = sorted([60, 30, 15, 10, 5, 2, 1, 0.5])  # ascending
        current = self.refresh_rate
        # Find next slower (larger) rate
        for r in rates:
            if r > current + 0.01:
                self.refresh_rate = r
                return
        # Already at slowest
        logger.info("Refresh: already at slowest rate")

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
        if layout != self.current_layout and self.grid is not None:
            self.grid.remove_class(self.current_layout)
            self.current_layout = layout
            self.grid.add_class(layout)
        asyncio.create_task(self.setup_widgets())
        self.save_layout()
        # Apply widget visibility after changing layout
        # We need to wait for setup_widgets to finish
        asyncio.create_task(self.apply_visibility_after_setup())
        
    async def apply_visibility_after_setup(self):
        """Apply widget visibility after layout change and widget setup.

        Then run one update cycle so plotext plots get valid dimensions (layout
        has run and on_resize has fired). Otherwise tabbed plot content can stay
        empty in some layouts until the next timer tick.
        """
        # Wait for layout/setup to settle so widgets have non-zero size
        await asyncio.sleep(0.2)
        self.apply_widget_visibility()
        # One immediate update so plots render with correct dimensions
        await self.update_metrics()

    def apply_widget_visibility(self) -> None:
        """Apply the saved widget visibility settings from config.

        In normal mode, failed widgets are hidden. In debug mode, failed widgets
        remain visible so any error text they render is visible in the layout.
        """
        logger.info("Applying widget visibility: %s", self.selected_widgets)
        for widget in self.grid.children:
            if not hasattr(widget, "title"):
                continue
            wt = self._get_widget_type(widget)
            if widget.title in self._failed_widget_titles:
                widget.styles.display = "block" if self._debug_mode else "none"
            elif wt in ("cpu", "ram"):
                widget.styles.display = "block"
            else:
                is_visible = self.selected_widgets.get(widget.title, True)
                widget.styles.display = "block" if is_visible else "none"
            logger.debug("Widget %s visible: %s", widget.title, widget.styles.display != "none")

    def _update_widget_history_sizes(self, new_size: int) -> None:
        """Update history size for all existing widgets without recreating them"""
        # Update CPU widget
        try:
            cpu_widget = self.query_one(CPUWidget)
            if hasattr(cpu_widget, 'history'):
                cpu_widget.history = cpu_widget.history.__class__(maxlen=new_size)
        except Exception:
            if self._debug_mode:
                raise
        
        # Update Memory widget
        try:
            memory_widget = self.query_one(MemoryWidget)
            if hasattr(memory_widget, 'ram_history'):
                memory_widget.ram_history = memory_widget.ram_history.__class__(maxlen=new_size)
            if hasattr(memory_widget, 'swap_history'):
                memory_widget.swap_history = memory_widget.swap_history.__class__(maxlen=new_size)
        except Exception:
            if self._debug_mode:
                raise
        
        # Update Network widget
        try:
            network_widget = self.query_one(NetworkIOWidget)
            if hasattr(network_widget, 'download_history'):
                network_widget.download_history = network_widget.download_history.__class__(maxlen=new_size)
            if hasattr(network_widget, 'upload_history'):
                network_widget.upload_history = network_widget.upload_history.__class__(maxlen=new_size)
        except Exception:
            if self._debug_mode:
                raise
        
        # Update Temperature widget
        if self.temperature_widget:
            try:
                if hasattr(self.temperature_widget, 'temperature_histories'):
                    for sensor_name in self.temperature_widget.temperature_histories:
                        self.temperature_widget.temperature_histories[sensor_name] = \
                            self.temperature_widget.temperature_histories[sensor_name].__class__(maxlen=new_size)
            except Exception:
                if self._debug_mode:
                    raise
        
        # Update Disk widgets
        for disk_widget in self.disk_widgets:
            try:
                if hasattr(disk_widget, 'read_history'):
                    disk_widget.read_history = disk_widget.read_history.__class__(maxlen=new_size)
                if hasattr(disk_widget, 'write_history'):
                    disk_widget.write_history = disk_widget.write_history.__class__(maxlen=new_size)
            except Exception:
                if self._debug_mode:
                    raise
        
        # Update GPU widgets
        for gpu_widget in self.gpu_widgets:
            try:
                if hasattr(gpu_widget, 'gpu_ram_history'):
                    gpu_widget.gpu_ram_history = gpu_widget.gpu_ram_history.__class__(maxlen=new_size)
                if hasattr(gpu_widget, 'gpu_usage_history'):
                    gpu_widget.gpu_usage_history = gpu_widget.gpu_usage_history.__class__(maxlen=new_size)
            except Exception:
                if self._debug_mode:
                    raise
        
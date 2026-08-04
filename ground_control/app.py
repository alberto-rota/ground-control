from __future__ import annotations

import asyncio
import queue
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
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
    Select,
    OptionList,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection
from textual.reactive import reactive
from textual.binding import Binding
from textual import events, on
import math
import os
import json
import logging
import time
import traceback
from ground_control.widgets.cpu import CPUWidget
from ground_control.widgets.disk import DiskIOWidget
from ground_control.widgets.network import NetworkIOWidget
from ground_control.widgets.gpu import GPUWidget
from ground_control.widgets.memory import MemoryWidget
from ground_control.widgets.temperature import TemperatureWidget
from ground_control.widgets.slurm_jobs import JobRow, SlurmJobsWidget
from ground_control.widgets.resizable_grid import ResizableGrid
from ground_control.widgets.color_picker import (
    ColorPickerScreen,
    build_color_options,
    color_option_prompt,
)
from ground_control.utils.system_metrics import SystemMetrics
from ground_control.utils import slurm as slurm_utils
from ground_control.utils.snapshot import metrics_from_snapshot
from ground_control.utils.grid_sizing import NUDGE_STEP, normalize_weights
from ground_control.utils.alerts import (
    CRIT as ALERT_CRIT,
    OK as ALERT_OK,
    WARN as ALERT_WARN,
    evaluate_snapshot,
    merge_thresholds,
)
from ground_control.utils.colors import (
    load_colors,
    load_theme,
    ensure_colors_in_config,
    apply_theme,
    get_available_themes,
    get_theme_tokens,
    COLOR_KEYS,
    DEFAULT_COLORS,
    delete_theme,
    get_active_theme,
    is_user_theme,
    normalize_hex,
    save_theme,
    set_color,
    slugify_theme_name,
)
from platformdirs import user_config_dir  # Import for cross-platform config directory
from textual.css.stylesheet import CssSource
from textual.screen import ModalScreen, Screen

# Set up the user-specific config file path
CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Mountpoints hidden by default: pseudo-filesystems that would otherwise fill the
# dashboard with one panel per entry (a squashfs mount per installed snap, the ESP).
# Editable per user in Settings -> Disk ignore.
DEFAULT_DISK_IGNORE_PREFIXES: list[str] = ["/boot/efi", "/snap"]
# Previous default, kept so a config saved before /snap was added still picks it up
# instead of pinning the user to the old list forever.
_LEGACY_DISK_IGNORE_PREFIXES: list[str] = ["/boot/efi"]


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


def _build_theme_swatch(theme_name: str, colors: dict | None = None, modified: bool = False) -> str:
    """Build a Rich-markup label for a theme showing its name and a color swatch.

    Picks representative keys from the theme JSON and renders a row of colored
    block characters next to the theme name (19 colors: UI + widget palette).

    Args:
        theme_name: Name of the theme (without .json extension).
        colors: Palette to draw the swatch from. Defaults to the theme's own
            file; the live config is passed instead for the active theme so
            single-colour edits show up in its swatch immediately.
        modified: Mark the name with ``*`` (palette edited away from the file).

    Returns:
        A Rich markup string like ``monokai  ███████████████████``
    """
    if colors is None:
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
    name = f"{theme_name}*" if modified else theme_name
    if is_user_theme(theme_name):
        name = f"{name} (custom)"
    label = f"{name:<22s} {blocks}" if blocks else name
    return label


# Which metric widget a palette key belongs to, for the picker's live preview.
# Order matters: "cpu_disk_used" is a CPU-widget colour, not a disk one.
_PREVIEW_GROUP_PREFIXES = (
    ("cpu_", "cpu"),
    ("memory_", "memory"),
    ("network_", "network"),
    ("disk_", "disk"),
    ("gpu_", "gpu"),
    ("temp_", "temperature"),
)


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


def _nearest_refresh(rate: float) -> float:
    """Snap a refresh rate to an offered option (a hand-edited config can hold any value)."""
    return float(min(REFRESH_RATES, key=lambda r: abs(float(r) - float(rate))))


def _nearest_history(size: int) -> int:
    """Snap a history size to an offered option."""
    return int(min(HISTORY_SIZES, key=lambda s: abs(s - int(size))))


class ShortcutsScreen(ModalScreen):
    """Modal screen showing all available key bindings (navigation + widget tabs)."""

    BINDINGS = [
        ("q", "close", "Close"),
        ("escape", "close", "Close"),
    ]

    DEFAULT_CSS = """
    ShortcutsScreen {
        align: center middle;
    }
    #shortcuts-content {
        width: 90;
        max-height: 80%;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    """

    def __init__(self, content: str = "", **kwargs):
        super().__init__(**kwargs)
        self._content = content

    def compose(self):
        yield Static(self._content, id="shortcuts-content", markup=True)

    def action_close(self) -> None:
        self.app.pop_screen()


def _job_option_label(job: dict) -> str:
    """Build a one-line Rich label for a job in the selection picker."""
    jid = job.get("jobid", "?")
    state = (job.get("state") or "").upper()
    name = (job.get("name") or "")[:24]
    part = job.get("partition") or ""
    nodes = job.get("nodes") or ""
    elapsed = job.get("elapsed") or ""
    nodelist = job.get("nodelist") or ""
    state_color = {
        "RUNNING": "green", "PENDING": "yellow", "COMPLETING": "cyan",
        "SUSPENDED": "magenta",
    }.get(state, "white")
    return (
        f"[bold]{jid:<10}[/] [{state_color}]{state:<8}[/] "
        f"{name:<22} [dim]{part:<10} {nodes:>2}n {nodelist:<12} {elapsed}[/]"
    )


class JobSelectScreen(ModalScreen):
    """Modal to pick Slurm jobs, keyboard-first.

    Two distinct outcomes, because there are two distinct questions a job list
    raises:

    * *Monitor* (checkboxes, multi-select) — "what is the queue doing with my
      jobs?" Lists them in the Slurm panel with state, allocation and time used.
      Costs one squeue/scontrol call every few seconds and nothing else; the rest
      of the dashboard keeps showing this host.
    * *Focus* (the highlighted row, single) — "what is my job doing *inside*?"
      Points every panel at that one job by running a Ground Control collector
      inside its allocation, so CPU/memory/GPU/process panels describe the
      compute node's view of the job rather than this login node.

    Dismisses with a dict describing which was chosen, or ``None`` if cancelled.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("a", "toggle_all", "All/none"),
        Binding("f", "focus_job", "Focus", priority=True),
        Binding("u", "unfocus_job", "Unfocus"),
        Binding("enter", "confirm", "Monitor", priority=True),
    ]

    DEFAULT_CSS = """
    JobSelectScreen {
        align: center middle;
    }
    #job-select-box {
        width: 84;
        max-width: 95%;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #job-select-title {
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    #job-select-list {
        height: auto;
        max-height: 20;
        background: transparent;
        border: none;
    }
    #job-select-hint {
        /* auto, not 1: the hint is two lines, and a fixed height clipped the
           second one (which is where 'enter' was explained). */
        height: auto;
        margin-top: 1;
    }
    #job-select-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    #job-select-buttons Button {
        margin-left: 2;
    }
    """

    def __init__(self, jobs: list, preselected: set | None = None,
                 focused_jobid: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._jobs = jobs or []
        self._preselected = preselected or set()
        self._focused_jobid = focused_jobid

    def compose(self) -> ComposeResult:
        with Vertical(id="job-select-box"):
            yield Static("Your running Slurm jobs", id="job-select-title")
            if not self._jobs:
                yield Static(
                    "[dim]You have no running jobs.[/]\n\n"
                    "[dim]Only running jobs are listed — a queued job has no\n"
                    "resources to monitor yet.[/]\n\n"
                    "[dim]Press Escape to close.[/]",
                    id="job-select-empty",
                )
            else:
                options = [
                    Selection(
                        _job_option_label(job),
                        job.get("jobid"),
                        job.get("jobid") in self._preselected,
                    )
                    for job in self._jobs
                ]
                yield SelectionList[str](*options, id="job-select-list")
                yield Static(
                    "[dim]enter: list the [bold]checked[/] jobs in the Slurm panel"
                    "   ·   f: run the whole dashboard [bold]inside[/] the "
                    "highlighted job\nspace: check · a: all/none · "
                    "u: back to this host · esc: cancel[/]",
                    id="job-select-hint",
                )
                with Horizontal(id="job-select-buttons"):
                    yield Button("Focus", variant="primary", id="job-focus")
                    yield Button("Monitor", variant="success", id="job-confirm")
                    yield Button("Cancel", id="job-cancel")

    def _highlighted_job(self) -> dict | None:
        """The job under the cursor, which is what Focus acts on."""
        try:
            sl = self.query_one("#job-select-list", SelectionList)
            index = sl.highlighted
        except Exception:
            return None
        if index is None or not (0 <= index < len(self._jobs)):
            return None
        return self._jobs[index]

    def action_confirm(self) -> None:
        try:
            sl = self.query_one("#job-select-list", SelectionList)
            self.dismiss({"action": "monitor", "jobids": list(sl.selected)})
        except Exception:
            self.dismiss(None)

    def action_focus_job(self) -> None:
        job = self._highlighted_job()
        if job is None:
            self.dismiss(None)
            return
        self.dismiss({"action": "focus", "job": job})

    def action_unfocus_job(self) -> None:
        self.dismiss({"action": "unfocus"})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_all(self) -> None:
        try:
            sl = self.query_one("#job-select-list", SelectionList)
        except Exception:
            return
        if set(sl.selected):
            sl.deselect_all()
        else:
            sl.select_all()

    @on(Button.Pressed, "#job-focus")
    def _focus_btn(self) -> None:
        self.action_focus_job()

    @on(Button.Pressed, "#job-confirm")
    def _confirm_btn(self) -> None:
        self.action_confirm()

    @on(Button.Pressed, "#job-cancel")
    def _cancel_btn(self) -> None:
        self.action_cancel()


class GroundControl(App):
    """App uses only its own themes (themes/*.json); Textual built-in themes are disabled."""

    CSS_PATH: list[str] = []  # Do not load any Textual theme; all styling comes from _generate_css() and our theme JSONs

    # Consecutive failed renders before a panel is disabled. Above 1 so a
    # transient collector/psutil race costs a frame, not the whole panel.
    WIDGET_FAILURE_LIMIT = 3

    def __init__(self, allowed_types: set[str] | None = None, gpu_indices: list[int] | None = None,
                 debug: bool = False, all_gpus: bool = False, squeue: bool = False):
        super().__init__()
        # Load colors and generate CSS dynamically
        self._color_config = load_colors()
        self._generate_css()

        self.system_metrics = SystemMetrics(all_gpus=all_gpus)
        # Slurm job monitoring (--squeue). The widget is only added to the grid
        # when squeue_mode is on; the monitor throttles its own polling.
        self.squeue_mode = squeue
        self.slurm_jobs_widget = None
        self._slurm_monitor = slurm_utils.SlurmMonitor()
        self._monitored_jobids: list[str] = []
        # Job-focus mode: when set, every panel is fed from a sample taken
        # *inside* this job's allocation on its compute node instead of from
        # local collectors. See _enter_job_focus.
        self._focused_job: dict | None = None
        self._job_sampler: slurm_utils.JobFocusSampler | None = None
        # Streak of failed probes, used to notice a job that has ended and drop
        # focus rather than leaving the dashboard frozen on a dead sample.
        self._job_focus_probe_failures = 0
        self.gpu_widgets = []
        self.disk_widgets = []
        self.temperature_widget = None
        self.grid = None
        # Panel proportions, per layout mode: {"grid": {"columns": [...], "rows": [...]}}.
        # Kept per mode because a mode change re-tracks the grid entirely (one
        # row in horizontal, one column in vertical), so weights do not carry
        # any meaning across modes.
        self._grid_weights: dict[str, dict[str, list[float]]] = {}
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
        # Consecutive render failures per widget title. A panel is only
        # disabled once it fails WIDGET_FAILURE_LIMIT ticks in a row, so a
        # one-off race (a process exiting mid-scan, a device blinking out)
        # no longer makes a panel vanish for the rest of the session.
        self._widget_failure_streaks: dict[str, int] = {}
        self._widget_tab_states: dict[str, str] = {}  # Maps widget title -> active tab pane id
        # Internal debug flag (avoid clashing with Textual's App.debug property)
        self._debug_mode = debug
        self._log_handler: logging.Handler | None = None  # RichLogHandler, set in on_mount
        self._log_queue: queue.Queue[str] = queue.Queue()
        # Prevent concurrent layout/widget rebuilds that can create duplicate widgets
        self._setup_lock: asyncio.Lock = asyncio.Lock()
        # Disk mount paths to hide: any mountpoint that starts with one of these (after normalizing) is skipped
        self.disk_ignore_prefixes: list[str] = list(DEFAULT_DISK_IGNORE_PREFIXES)
        # Set while we drive the theme RadioSet from code, so the resulting
        # Changed messages don't re-apply a theme we just applied.
        self._suppress_theme_radio = False
        # Metric widget currently mounted in the colour picker's preview pane,
        # plus the last collected metrics so recolouring it costs no I/O.
        self._color_preview_widget = None
        self._last_metrics_by_type: dict = {}
        # Threshold alerting. Populated properly from the config in load_config;
        # defaults here so the app is usable if the config is missing or corrupt.
        self.alerts_enabled: bool = True
        self.alert_sticky_seconds: float = 30.0
        self.thresholds: dict = merge_thresholds(None)
        self._active_breaches: list = []

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
    GPUWidget, NetworkIOWidget, DiskIOWidget, CPUWidget, MemoryWidget, TemperatureWidget, SlurmJobsWidget {{
        background: {tok["bg"]};
        border: round {tok["border"]};
        /* No min-height: a grid cell must never be taller than its share of the
           screen, or the dashboard itself would start to scroll. Panels shrink and
           their content degrades gracefully instead. */
        min-height: 0;
        color: {tok["text"]};
    }}
    /* Highlight the focused panel so keyboard navigation is visible */
    GPUWidget:focus, NetworkIOWidget:focus, DiskIOWidget:focus, CPUWidget:focus, MemoryWidget:focus, TemperatureWidget:focus, SlurmJobsWidget:focus,
    GPUWidget:focus-within, CPUWidget:focus-within, SlurmJobsWidget:focus-within {{
        border: round {tok["selection"]};
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

    /* Signal buttons on GPU process rows: themed background with the theme's
       on-accent text colour, so labels stay readable in light and dark themes. */
    ProcessRow .sigkill-btn {{
        background: {tok["danger"]};
        color: {tok["text_on_accent"]};
    }}
    ProcessRow .sigterm-btn {{
        background: {tok["warn"]};
        color: {tok["text_on_accent"]};
    }}
    ProcessRow .sigint-btn {{
        background: {tok["caution"]};
        color: {tok["text_on_accent"]};
    }}

    SelectionList {{
        background: {tok["bg"]};
        color: {tok["text"]};
        width: 100%;
        height: auto;
        padding: 0;
    }}
    /* Match Visible Widgets selector to other settings (RadioSets): same container look.
       Option text takes the list's own color — SelectionList has no per-option
       component class, only the OptionList highlight/hover ones below. */
    #visible-widgets-list {{
        width: 100%;
        height: auto;
        background: transparent;
        color: {tok["text"]};
        border: none;
        padding: 0;
    }}
    #visible-widgets-list:focus {{
        border: none;
    }}
    #visible-widgets-list > .option-list--option-highlighted {{
        background: {tok["selection"]} 25%;
        color: {tok["text"]};
        text-style: bold;
    }}
    #visible-widgets-list > .option-list--option-hover {{
        background: {tok["selection"]} 15%;
        color: {tok["text"]};
    }}
    /* Checkbox glyphs: off = dim, on = theme accent (Textual defaults to green) */
    #visible-widgets-list > .selection-list--button,
    #visible-widgets-list > .selection-list--button-highlighted {{
        color: {tok["text"]} 30%;
        background: {tok["panel_dim"]};
    }}
    #visible-widgets-list > .selection-list--button-selected,
    #visible-widgets-list > .selection-list--button-selected-highlighted {{
        color: {tok["selection"]};
        background: {tok["panel_dim"]};
    }}

    /* Settings: two columns. Left = what to show + how; right = the (long)
       theme list, which scrolls on its own so the pane itself never has to. */
    #settings-pane {{
        height: 1fr;
        overflow: hidden hidden;
        padding: 0 1 0 1;
    }}
    #settings-columns {{
        width: 100%;
        height: 1fr;
    }}
    /* Narrow terminals: stack the columns and scroll the whole page instead of
       squeezing both into a width where labels get clipped (see on_resize). */
    #settings-columns.-stacked {{
        layout: vertical;
        overflow-y: auto;
        scrollbar-size-vertical: 1;
    }}
    #settings-columns.-stacked > #settings-col-left,
    #settings-columns.-stacked > #settings-col-right {{
        width: 100%;
        height: auto;
        overflow: hidden hidden;
    }}
    #settings-columns.-stacked #theme-block, #settings-columns.-stacked #theme-radio-set,
    #settings-columns.-stacked #colors-block, #settings-columns.-stacked #color-key-list {{
        height: auto;
    }}
    #settings-col-left {{
        width: 2fr;
        height: 1fr;
        overflow-y: auto;
    }}
    #settings-col-right {{
        width: 3fr;
        height: 1fr;
    }}
    #settings-timing-row {{
        width: 100%;
        height: auto;
    }}
    #settings-timing-row > .settings-block {{
        width: 1fr;
    }}
    .settings-block {{
        width: 100%;
        height: auto;
        min-height: 3;
        padding: 0 1;
        margin: 0 1 1 0;
        border: round {tok["border"]};
        background: {tok["bg"]};
        border-title-color: {tok["text"]};
        border-title-style: bold;
    }}
    #theme-block {{
        height: 1fr;
    }}

    #theme-radio-set {{
        width: 100%;
        height: 1fr;
        background: transparent;
        border: none;
        padding: 0;
        scrollbar-size-vertical: 1;
    }}

    /* Colour editor: the key list scrolls inside its block so the hex field
       below it stays put while you page through 54 entries. */
    #colors-block {{
        height: 1fr;
    }}
    #color-key-list {{
        width: 100%;
        height: 1fr;
        background: transparent;
        color: {tok["text"]};
        border: none;
        padding: 0;
        scrollbar-size-vertical: 1;
    }}
    #color-key-list:focus {{
        border: none;
    }}
    #color-key-list > .option-list--option-highlighted {{
        background: {tok["selection"]} 25%;
        color: {tok["text"]};
        text-style: bold;
    }}
    #color-key-list > .option-list--option-hover {{
        background: {tok["selection"]} 15%;
        color: {tok["text"]};
    }}
    #color-key-list > .option-list--option-disabled {{
        color: {tok["text"]} 50%;
        text-style: bold;
    }}
    #color-hex-input, #theme-name-input {{
        width: 1fr;
        height: 1;
        background: transparent;
        color: {tok["text"]};
        padding: 0;
    }}
    #color-hex-row {{
        width: 100%;
        height: auto;
    }}
    #color-hex-label {{
        width: auto;
        height: 1;
        margin: 0 1 0 0;
        color: {tok["text"]} 60%;
    }}
    #theme-save-row {{
        width: 100%;
        height: auto;
    }}
    #theme-save-row > Button {{
        height: 1;
        min-width: 8;
        margin: 0 0 0 1;
        border: none;
        padding: 0 1;
        background: {tok["panel"]};
        color: {tok["text"]};
    }}
    #theme-save-row > Button:hover {{
        background: {tok["selection"]} 40%;
    }}
    #theme-save-row > #theme-save-btn {{
        background: {tok["accent"]};
        color: {tok["text_on_accent"]};
    }}
    /* Layout is a 3-option choice: one segmented row instead of a stacked list. */
    #layout-radio-set {{
        layout: horizontal;
        width: 100%;
        height: auto;
        background: transparent;
        border: none;
        padding: 0;
        overflow: hidden hidden;
    }}
    #layout-radio-set > RadioButton {{
        width: auto;
        margin: 0 2 0 0;
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
    /* Radio glyphs: off = dim, on = theme accent (Textual defaults to green) */
    RadioButton .toggle--button {{
        color: {tok["text"]} 30%;
        background: {tok["panel_dim"]};
    }}
    RadioButton.-on .toggle--button {{
        color: {tok["selection"]};
        background: {tok["panel_dim"]};
    }}

    /* Refresh rate / history window: ordered scales with one answer each, so a
       compact dropdown beats a column of radio buttons. */
    #refresh-select, #history-select {{
        width: 100%;
        height: 1;
        background: transparent;
    }}
    #refresh-select > SelectCurrent, #history-select > SelectCurrent {{
        background: transparent;
        color: {tok["text"]};
        padding: 0;
    }}
    #refresh-select > SelectCurrent Static#label,
    #history-select > SelectCurrent Static#label {{
        color: {tok["text"]};
    }}
    #refresh-select > SelectCurrent .arrow,
    #history-select > SelectCurrent .arrow {{
        color: {tok["text"]};
    }}
    #refresh-select:focus > SelectCurrent Static#label,
    #history-select:focus > SelectCurrent Static#label {{
        color: {tok["selection"]};
        text-style: bold;
    }}
    #refresh-select > SelectOverlay, #history-select > SelectOverlay {{
        background: {tok["bg"]};
        border: round {tok["border"]};
        max-height: 12;
    }}
    #refresh-select > SelectOverlay > .option-list--option-highlighted,
    #history-select > SelectOverlay > .option-list--option-highlighted {{
        background: {tok["selection"]} 25%;
        color: {tok["text"]};
        text-style: bold;
    }}
    #refresh-select > SelectOverlay > .option-list--option-hover,
    #history-select > SelectOverlay > .option-list--option-hover {{
        background: {tok["selection"]} 15%;
        color: {tok["text"]};
    }}

    #disk-ignore-prefixes {{
        width: 100%;
        height: 1;
        background: transparent;
        color: {tok["text"]};
        padding: 0;
    }}

    /* Colour picker modal: key list | palette + steppers | live widget preview. */
    ColorPickerScreen {{
        align: center middle;
        background: {tok["bg"]} 88%;
    }}
    #picker-box {{
        width: 98%;
        height: 94%;
        padding: 0 1;
        border: round {tok["border"]};
        background: {tok["bg"]};
        border-title-color: {tok["text"]};
        border-title-style: bold;
    }}
    #picker-cols {{
        width: 100%;
        height: 1fr;
    }}
    .picker-block {{
        height: 1fr;
        padding: 0 1;
        margin: 0 1 0 0;
        border: round {tok["border"]};
        background: {tok["bg"]};
        border-title-color: {tok["text"]};
        border-title-style: bold;
    }}
    #picker-keys-block {{
        /* Wide enough for "███ <22-char key> #RRGGBB" without wrapping. */
        width: 40;
    }}
    #picker-mid {{
        width: auto;
        height: 1fr;
    }}
    #picker-palette-block, #picker-hsv-block {{
        width: 40;
        height: auto;
    }}
    #picker-preview-block {{
        width: 1fr;
        min-width: 40;
    }}
    /* Narrow terminals: the plots in the preview become unreadable long before
       the pane itself stops fitting, so drop it instead of shrinking it. */
    #picker-cols.-narrow > #picker-preview-block {{
        display: none;
    }}
    #picker-keys {{
        width: 100%;
        height: 1fr;
        background: transparent;
        color: {tok["text"]};
        border: none;
        padding: 0;
        scrollbar-size-vertical: 1;
    }}
    #picker-keys:focus {{
        border: none;
    }}
    #picker-keys > .option-list--option-highlighted {{
        background: {tok["selection"]} 25%;
        color: {tok["text"]};
        text-style: bold;
    }}
    #picker-keys > .option-list--option-hover {{
        background: {tok["selection"]} 15%;
        color: {tok["text"]};
    }}
    #picker-keys > .option-list--option-disabled {{
        color: {tok["text"]} 50%;
        text-style: bold;
    }}
    PaletteGrid, HsvSliders {{
        width: 100%;
        height: auto;
        padding: 0;
        background: transparent;
        color: {tok["text"]};
    }}
    PaletteGrid:focus, HsvSliders:focus {{
        /* Focus is shown by the block border, so the widget itself only needs
           to mark that it is the active pane. */
        text-style: none;
    }}
    #picker-palette-block:focus-within, #picker-hsv-block:focus-within,
    #picker-keys-block:focus-within {{
        border: round {tok["accent"]};
    }}
    #picker-hint {{
        width: 100%;
        height: auto;
        padding: 0 1;
        color: {tok["text"]} 55%;
    }}
    .picker-preview-empty {{
        width: 100%;
        height: auto;
        padding: 1;
        color: {tok["text"]} 60%;
    }}
    #picker-footer {{
        width: 100%;
        height: 1;
        margin: 0 0 0 1;
    }}
    #picker-hex-label {{
        width: auto;
        height: 1;
        margin: 0 1 0 0;
        color: {tok["text"]} 60%;
    }}
    #picker-hex {{
        width: 32;
        height: 1;
        background: transparent;
        color: {tok["text"]};
        padding: 0;
    }}
    #picker-footer > Button {{
        height: 1;
        min-width: 9;
        margin: 0 0 0 2;
        border: none;
        padding: 0 1;
        background: {tok["panel"]};
        color: {tok["text"]};
    }}
    #picker-footer > Button:hover {{
        background: {tok["selection"]} 40%;
    }}
    #picker-footer > #picker-close {{
        background: {tok["accent"]};
        color: {tok["text_on_accent"]};
    }}

    #dashboard-pane {{
        height: 1fr;
        /* The grid always fits the viewport exactly, so scrollbars would only ever
           appear because of a sizing bug — and would shrink every panel by a cell. */
        overflow: hidden hidden;
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
    # Below this terminal width the Settings tab stacks its two columns.
    SETTINGS_TWO_COLUMN_WIDTH = 100

    # Keyboard map. Single-key, conflict-free. Less-common actions are kept off
    # the footer (show=False) but are listed in the ? help overlay. Per-panel
    # keys (CPU 1/2/3, GPU 1/2/p, x=hide) are local bindings on the widgets and
    # only fire when that panel is focused.
    BINDINGS = [
        # Views
        Binding("d", "open_dashboard", "Dash"),
        Binding("s", "open_settings", "Settings"),
        Binding("l", "open_logs", "Logs"),
        # Layout
        Binding("g", "set_grid", "Grid"),
        Binding("h", "set_horizontal", "Horiz"),
        Binding("v", "set_vertical", "Vert"),
        Binding("space", "cycle_layout", "Cycle layout", show=False),
        # Panel proportions. These resize the focused panel's grid *track*, so in
        # grid mode its row-mates get taller with it; drag a border for a
        # two-panel-only change.
        Binding("ctrl+right", "widen_panel", "Wider", show=False),
        Binding("ctrl+left", "narrow_panel", "Narrower", show=False),
        Binding("ctrl+down", "heighten_panel", "Taller", show=False),
        Binding("ctrl+up", "shorten_panel", "Shorter", show=False),
        Binding("z", "reset_panel_sizes", "Reset sizes", show=False),
        # Refresh rate
        Binding("r", "force_refresh", "Refresh"),
        Binding("plus", "faster_refresh", "Faster"),
        Binding("equals_sign", "faster_refresh", "Faster", show=False),
        Binding("minus", "slower_refresh", "Slower"),
        # Theme
        Binding("t", "cycle_theme", "Theme", show=False),
        Binding("a", "toggle_alerts", "Alerts", show=False),
        # Focus a dashboard panel (then use its local keys)
        Binding("right_square_bracket", "focus_next_widget", "Next panel", show=False),
        Binding("left_square_bracket", "focus_prev_widget", "Prev panel", show=False),
        # Slurm
        Binding("J", "select_jobs", "Jobs"),
        Binding("F", "toggle_job_focus", "Focus job"),
        # Help / quit
        Binding("question_mark", "show_shortcuts", "Help"),
        Binding("q", "quit", "Quit"),
    ]



    def watch_refresh_rate(self, new_rate: float) -> None:
        """React to changes in refresh rate: update the metrics timer so the selected rate is used."""
        if self._update_timer:
            self._update_timer.stop()
            self._update_timer = None
        self._update_timer = self.set_interval(new_rate, self._update_metrics_sync)
        self.save_config()
        self._select_refresh_option(new_rate)
        # A focused job's collector runs remotely at a cadence baked into its
        # command line, so a new rate only reaches it on the next stream.
        sampler = getattr(self, "_job_sampler", None)  # may not exist yet at init
        if sampler is not None:
            sampler.interval = max(float(new_rate), 0.5)

    def watch_history_size(self, new_size: int) -> None:
        """React to changes in history size."""
        self.save_config()
        self._select_history_option(new_size)
        if not self._is_initializing:
            self._update_widget_history_sizes(new_size)
        logger.debug(f"History size changed to {new_size}s")

    @on(Select.Changed, "#refresh-select")
    def _on_refresh_select_changed(self, event: Select.Changed) -> None:
        """Handle refresh rate selection from the Select."""
        if self._is_initializing or event.value is Select.BLANK:
            return
        self.refresh_rate = float(event.value)

    @on(Select.Changed, "#history-select")
    def _on_history_select_changed(self, event: Select.Changed) -> None:
        """Handle history size selection from the Select."""
        if self._is_initializing or event.value is Select.BLANK:
            return
        self.history_size = int(event.value)

    @on(RadioSet.Changed, "#theme-radio-set")
    def _on_theme_radio_changed(self, event: RadioSet.Changed) -> None:
        """Handle theme selection from the RadioSet.

        Extracts the theme name from the pressed RadioButton's id
        (``theme-<name>``) and applies it live.
        """
        if self._is_initializing or self._suppress_theme_radio:
            return
        btn = event.pressed
        if btn and btn.id and btn.id.startswith("theme-"):
            # removeprefix, not replace: a custom theme may itself be called
            # something like "my-theme-dark".
            theme_name = btn.id.removeprefix("theme-")
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
            self.disk_ignore_prefixes = list(DEFAULT_DISK_IGNORE_PREFIXES)
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
            buttons = list(radio_set.query(RadioButton))
            for idx, btn in enumerate(buttons):
                if btn.id == f"layout-{layout}":
                    for b in buttons:
                        b.value = False
                    radio_set._selected = idx
                    btn.value = True
                    break
        except Exception:
            if self._debug_mode:
                raise

    def _select_refresh_option(self, rate: float) -> None:
        """Show the given rate in the refresh Select (without re-firing Changed)."""
        try:
            select = self.query_one("#refresh-select", Select)
            with select.prevent(Select.Changed):
                select.value = _nearest_refresh(rate)
        except Exception:
            if self._debug_mode:
                raise

    def _select_history_option(self, size: int) -> None:
        """Show the given history size in the history Select (without re-firing Changed)."""
        try:
            select = self.query_one("#history-select", Select)
            with select.prevent(Select.Changed):
                select.value = _nearest_history(size)
        except Exception:
            if self._debug_mode:
                raise

    def _select_current_theme_radio(self) -> None:
        """Pre-select the RadioButton corresponding to the active theme.

        The active theme is the name recorded in the config; an edited palette
        still selects the theme it was derived from (marked ``*``).
        """
        current_theme, _ = get_active_theme()
        if current_theme:
            self._select_theme_radio(current_theme)
        self._update_theme_labels()

    def _select_theme_radio(self, theme_name: str) -> None:
        """Check the RadioButton for ``theme_name`` without re-applying the theme."""
        try:
            radio_set = self.query_one("#theme-radio-set", RadioSet)
            self._suppress_theme_radio = True
            try:
                for idx, btn in enumerate(radio_set.query(RadioButton)):
                    if btn.id == f"theme-{theme_name}":
                        radio_set._selected = idx
                        btn.value = True
                    else:
                        btn.value = False
            finally:
                self._suppress_theme_radio = False
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
                    raw = config.get("disk_ignore_prefixes", ", ".join(DEFAULT_DISK_IGNORE_PREFIXES))
                    if isinstance(raw, list):
                        self.disk_ignore_prefixes = [str(p).strip() for p in raw if str(p).strip()]
                    else:
                        self.disk_ignore_prefixes = [p.strip() for p in str(raw).split(",") if p.strip()]
                    if not self.disk_ignore_prefixes:
                        self.disk_ignore_prefixes = list(DEFAULT_DISK_IGNORE_PREFIXES)
                    elif self.disk_ignore_prefixes == _LEGACY_DISK_IGNORE_PREFIXES:
                        # Untouched old default: adopt the current one (adds /snap).
                        self.disk_ignore_prefixes = list(DEFAULT_DISK_IGNORE_PREFIXES)
                    self.alerts_enabled = bool(config.get("alerts_enabled", True))
                    try:
                        self.alert_sticky_seconds = max(
                            0.0, float(config.get("alert_sticky_seconds", 30.0)))
                    except (TypeError, ValueError):
                        self.alert_sticky_seconds = 30.0
                    self.thresholds = merge_thresholds(config.get("thresholds"))
                    self._grid_weights = self._parse_grid_weights(config.get("grid_weights"))
                    raw_selected = config.get("selected", {})
                    if not isinstance(raw_selected, dict):
                        return {}
                    # Normalize: only string keys, coerce values to bool so JSON true/false and edge cases work
                    return {str(k): bool(v) for k, v in raw_selected.items()}
            except (json.JSONDecodeError, ValueError):
                pass
        return {}

    @staticmethod
    def _parse_grid_weights(raw) -> dict[str, dict[str, list[float]]]:
        """Validate saved panel proportions, discarding anything malformed.

        Lengths are deliberately *not* checked here: the track counts depend on
        how many panels this machine has, which is not known yet at config-load
        time. ``set_tracks`` pads or truncates against the real counts later.
        """
        result: dict[str, dict[str, list[float]]] = {}
        if not isinstance(raw, dict):
            return result
        for mode in ("grid", "horizontal", "vertical"):
            entry = raw.get(mode)
            if not isinstance(entry, dict):
                continue
            axes = {}
            for axis in ("columns", "rows"):
                values = entry.get(axis)
                if isinstance(values, list):
                    axes[axis] = normalize_weights(values, len(values))
            if axes:
                result[mode] = axes
        return result

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
                "grid_weights": self._grid_weights,
                "widget_tabs": self._widget_tab_states,
                "disk_ignore_prefixes": ", ".join(self.disk_ignore_prefixes),
                "alerts_enabled": self.alerts_enabled,
                "alert_sticky_seconds": self.alert_sticky_seconds,
                "thresholds": self.thresholds,
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
        self._refresh_color_options()
        # Keep the radio set in step for callers that aren't the radio set
        # itself (the `t` binding, save/delete); suppressed, so no re-entry.
        self._select_theme_radio(theme_name)
        self._update_theme_labels()
        self._set_theme_name_input(theme_name)

    # ------------------------------------------------------- colour editing

    def _build_color_options(self) -> list[Option]:
        """Rows for the colour editor: a disabled header per group, then its keys."""
        return build_color_options(self._color_config or DEFAULT_COLORS)

    def _refresh_color_options(self) -> None:
        """Re-render every colour row from the current palette (after a theme change)."""
        try:
            option_list = self.query_one("#color-key-list", OptionList)
        except Exception:
            return
        colors = self._color_config or DEFAULT_COLORS
        for key in COLOR_KEYS:
            hex_color = colors.get(key, DEFAULT_COLORS.get(key, "#000000"))
            try:
                option_list.replace_option_prompt(
                    f"colorkey-{key}", color_option_prompt(key, hex_color)
                )
            except Exception:
                pass

    def _highlighted_color_key(self) -> str | None:
        """The palette key currently highlighted in the colour list, if any."""
        try:
            option_list = self.query_one("#color-key-list", OptionList)
            index = option_list.highlighted
            if index is None:
                return None
            option_id = option_list.get_option_at_index(index).id or ""
        except Exception:
            return None
        if option_id.startswith("colorkey-"):
            return option_id.removeprefix("colorkey-")
        return None

    @on(OptionList.OptionHighlighted, "#color-key-list")
    def _on_color_key_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Load the highlighted key's current value into the hex field."""
        option_id = event.option.id or ""
        if not option_id.startswith("colorkey-"):
            return
        key = option_id.removeprefix("colorkey-")
        colors = self._color_config or DEFAULT_COLORS
        try:
            hex_input = self.query_one("#color-hex-input", Input)
        except Exception:
            return
        with hex_input.prevent(Input.Changed):
            hex_input.value = colors.get(key, DEFAULT_COLORS.get(key, "#000000"))

    @on(OptionList.OptionSelected, "#color-key-list")
    def _on_color_key_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter on a colour row opens the picker on that key."""
        option_id = event.option.id or ""
        if option_id.startswith("colorkey-"):
            self._open_color_picker(option_id.removeprefix("colorkey-"))

    def _open_color_picker(self, key: str) -> None:
        """Open the modal picker, refreshing the Settings rows when it closes."""
        def _on_dismiss(_result) -> None:
            self._refresh_color_options()
            self._update_theme_labels()

        self.push_screen(ColorPickerScreen(key), _on_dismiss)

    def apply_color_live(self, key: str, hex_value: str) -> bool:
        """Persist one palette colour and apply it to the running app.

        Shared by the Settings hex field and the picker screen. Plots re-read
        the config on their next tick; chrome needs the stylesheet rebuilt now.

        Returns:
            True if the colour was written, False on bad input or write failure.
        """
        normalized = normalize_hex(hex_value)
        if normalized is None or not set_color(key, normalized):
            return False

        self._color_config = load_colors()
        self._generate_css()
        self._refresh_stylesheet()
        try:
            self.query_one("#color-key-list", OptionList).replace_option_prompt(
                f"colorkey-{key}", color_option_prompt(key, normalized)
            )
        except Exception:
            pass
        self._update_theme_labels()
        return True

    @on(Input.Submitted, "#color-hex-input")
    def _on_color_hex_submitted(self, event: Input.Submitted) -> None:
        """Apply a typed hex value to the highlighted colour key, live."""
        key = self._highlighted_color_key()
        if key is None:
            self.notify("Pick a colour in the list first", title="Colors", severity="warning")
            return

        if not self.apply_color_live(key, event.value):
            self.notify(
                f"{event.value!r} is not a hex colour (expected #RRGGBB)",
                title="Colors",
                severity="error",
            )
            return

        try:
            self.query_one("#color-hex-input", Input).value = normalize_hex(event.value)
        except Exception:
            pass

    # ------------------------------------------------------- picker preview

    def preview_group_for_key(self, key: str) -> str:
        """Metric type whose widget illustrates ``key``.

        Keys that aren't specific to one widget (base, chrome, general) fall
        back to the CPU widget, where borders, text and bar colours all show.
        """
        for prefix, group in _PREVIEW_GROUP_PREFIXES:
            if key.startswith(prefix):
                return group
        return "cpu"

    def _build_preview_widget(self, group: str):
        """A standalone metric widget for the preview pane, or None if unavailable.

        Titles and ids must match what ``_dispatch_widget_update`` looks for:
        disk widgets are matched by title, GPU widgets by ``gpu_<index>`` id.
        """
        try:
            if group == "memory":
                return MemoryWidget("Memory")
            if group == "network":
                return NetworkIOWidget("Network")
            if group == "disk":
                disks = self.system_metrics.get_disk_metrics()["disks"]
                visible = [d for d in disks if not self._disk_mount_ignored(d["mountpoint"])]
                if not visible:
                    return None
                return DiskIOWidget(f"Disk @ {visible[0]['mountpoint']}")
            if group == "gpu":
                gpus = self.system_metrics.get_gpu_metrics()
                if self.gpu_indices is not None:
                    gpus = [gpus[i] for i in self.gpu_indices if 0 <= i < len(gpus)]
                if not gpus:
                    return None
                return GPUWidget(f"GPU @ {gpus[0]['gpu_name']}", id="gpu_0")
            if group == "temperature":
                if not self.system_metrics.get_temperature_metrics():
                    return None
                return TemperatureWidget("Temperature")
            cpu_metrics = self.system_metrics.get_cpu_metrics()
            return CPUWidget(str(cpu_metrics["cpu_name"]))
        except Exception:
            logger.error("Could not build preview widget for %s", group, exc_info=True)
            if self._debug_mode:
                raise
            return None

    async def mount_color_preview(self, key: str, container) -> None:
        """Mount (or swap) the preview widget for ``key`` inside ``container``."""
        group = self.preview_group_for_key(key)
        await container.remove_children()
        self._color_preview_widget = None

        widget = self._build_preview_widget(group)
        if widget is None:
            await container.mount(
                Static(f"No {group} data on this machine.", classes="picker-preview-empty")
            )
            return

        await container.mount(widget)
        self._color_preview_widget = widget
        self.refresh_color_preview()

    def clear_color_preview(self) -> None:
        """Stop feeding the preview (the picker screen is closing)."""
        self._color_preview_widget = None

    def refresh_color_preview(self) -> None:
        """Re-render the preview widget so a colour change shows immediately.

        Reuses the metrics from the last tick where possible: this runs on every
        arrow-key press in the picker, and re-collecting GPU or disk stats that
        often would make the palette feel sluggish.
        """
        widget = self._color_preview_widget
        if widget is None or not widget.is_mounted:
            return
        needed = self._get_required_metric_types(widget)
        collectors = {
            "cpu": self.system_metrics.get_cpu_metrics,
            "disk": self.system_metrics.get_disk_metrics,
            "memory": self.system_metrics.get_memory_metrics,
            "network": self.system_metrics.get_network_metrics,
            "gpu": self.system_metrics.get_gpu_metrics,
            "temperature": self.system_metrics.get_temperature_metrics,
        }
        metrics = {}
        try:
            for metric_type in needed:
                cached = self._last_metrics_by_type.get(metric_type)
                if cached is None and metric_type in collectors:
                    cached = collectors[metric_type]()
                    self._last_metrics_by_type[metric_type] = cached
                metrics[metric_type] = cached
        except Exception:
            logger.error("Preview metric collection failed", exc_info=True)
            if self._debug_mode:
                raise
            return
        self._update_color_preview(metrics)

    def _update_color_preview(self, metrics_by_type: dict) -> None:
        """Dispatch a metrics update to the preview widget, ignoring failures.

        A broken preview must never disable the real widget of the same type,
        so this deliberately does not touch ``_failed_widget_titles``.
        """
        widget = self._color_preview_widget
        if widget is None or not widget.is_mounted:
            return
        needed = self._get_required_metric_types(widget)
        if any(metrics_by_type.get(t) is None for t in needed):
            return
        try:
            asyncio.create_task(self._dispatch_widget_update(widget, metrics_by_type))
        except Exception:
            logger.error("Preview update failed", exc_info=True)
            if self._debug_mode:
                raise

    # ---------------------------------------------------- custom theme files

    def _set_theme_name_input(self, name: str | None) -> None:
        """Pre-fill the save field with the active theme's name."""
        try:
            self.query_one("#theme-name-input", Input).value = name or ""
        except Exception:
            pass

    def _update_theme_labels(self) -> None:
        """Redraw theme labels so the active one shows its live (possibly edited) palette."""
        active, modified = get_active_theme()
        try:
            radio_set = self.query_one("#theme-radio-set", RadioSet)
        except Exception:
            return
        for btn in radio_set.query(RadioButton):
            if not btn.id:
                continue
            name = btn.id.removeprefix("theme-")
            if name == active:
                btn.label = _build_theme_swatch(name, self._color_config, modified)
            else:
                btn.label = _build_theme_swatch(name)

    async def _rebuild_theme_radio_set(self) -> None:
        """Rebuild the theme list after a custom theme is added or removed."""
        try:
            radio_set = self.query_one("#theme-radio-set", RadioSet)
        except Exception:
            return
        active, _ = get_active_theme()
        self._suppress_theme_radio = True
        try:
            # Await the removal: the old buttons stay in the node list until it
            # completes, and remounting the same ids on top raises DuplicateIds.
            radio_set._selected = None
            radio_set._pressed_button = None
            await radio_set.remove_children()
            await radio_set.mount_all([
                RadioButton(_build_theme_swatch(name), id=f"theme-{name}")
                for name in get_available_themes()
            ])
            if active:
                self._select_theme_radio(active)
        finally:
            self._suppress_theme_radio = False
        self._update_theme_labels()

    @on(Input.Submitted, "#theme-name-input")
    async def _on_theme_name_submitted(self, event: Input.Submitted) -> None:
        """Enter in the name field saves, same as the Save button."""
        await self._save_current_palette_as_theme()

    @on(Button.Pressed, "#theme-save-btn")
    async def _on_theme_save_pressed(self, event: Button.Pressed) -> None:
        await self._save_current_palette_as_theme()

    @on(Button.Pressed, "#theme-delete-btn")
    async def _on_theme_delete_pressed(self, event: Button.Pressed) -> None:
        await self._delete_named_theme()

    async def _save_current_palette_as_theme(self) -> None:
        """Write the live palette to ``~/.config/ground-control/themes/<name>.json``."""
        try:
            name = self.query_one("#theme-name-input", Input).value
        except Exception:
            return

        overwrote = is_user_theme(slugify_theme_name(name))
        ok, result = save_theme(name, self._color_config or DEFAULT_COLORS)
        if not ok:
            self.notify(result, title="Save theme", severity="error")
            return

        # The saved file is now the active theme, so the palette is no longer
        # "modified" relative to anything.
        apply_theme(result)
        self._color_config = load_colors()
        await self._rebuild_theme_radio_set()
        self._set_theme_name_input(result)
        verb = "Updated" if overwrote else "Saved"
        self.notify(f"{verb} theme '{result}'", title="Save theme", severity="information")
        logger.info("%s custom theme %s", verb, result)

    async def _delete_named_theme(self) -> None:
        """Delete the custom theme named in the field. Builtins are refused."""
        try:
            name = self.query_one("#theme-name-input", Input).value.strip()
        except Exception:
            return

        ok, result = delete_theme(name)
        if not ok:
            self.notify(result, title="Delete theme", severity="error")
            return

        # The deleted theme may have been the active one; its colours stay in
        # the config, now unnamed, so nothing visually changes.
        active, _ = get_active_theme()
        await self._rebuild_theme_radio_set()
        self._set_theme_name_input(active)
        self.notify(f"Deleted theme '{result}'", title="Delete theme", severity="information")

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

    def get_layout_columns(self, visible_count: int) -> int:
        """Return the number of columns for layout; value is the visible widget count from setup_widgets."""
        return visible_count

    def _apply_grid_layout_dimensions(self, visible_count: int) -> None:
        """Update grid rows/columns and template from current layout and visible widget count.

        Call this when the number of visible widgets changes so proportions update (e.g. after
        toggling visibility in Settings). Does not mount/unmount widgets.

        The track *counts* come from the layout mode and panel count; their
        *weights* come from the saved per-mode proportions, so a resized
        dashboard keeps its shape across rebuilds and restarts.
        """
        if self.grid is None:
            return
        grid_columns = max(1, self.get_layout_columns(visible_count))
        if self.current_layout == "horizontal":
            rows, cols = 1, grid_columns
        elif self.current_layout == "vertical":
            rows, cols = grid_columns, 1
        else:  # grid
            rows = 2 if grid_columns <= 12 else 3
            cols = int(math.ceil(grid_columns / rows))
        saved = self._grid_weights.get(self.current_layout, {})
        self.grid.set_tracks(
            cols, rows,
            column_weights=saved.get("columns"),
            row_weights=saved.get("rows"),
        )
        # Store back normalized: the counts may have just changed, and the saved
        # lists should describe the grid that actually exists.
        self._store_grid_weights(save=False)

    def _store_grid_weights(self, save: bool = True) -> None:
        """Copy the grid's live track weights into the config state."""
        if self.grid is None:
            return
        self._grid_weights[self.current_layout] = {
            "columns": list(self.grid.column_weights),
            "rows": list(self.grid.row_weights),
        }
        if save:
            self.save_config()

    @on(ResizableGrid.TracksResized)
    def _on_grid_tracks_resized(self, event: ResizableGrid.TracksResized) -> None:
        """Persist proportions after a border drag."""
        event.stop()
        self._store_grid_weights()

    def _focused_panel(self):
        """The dashboard panel that owns keyboard focus, or None.

        Walks up from the focused node because focus is often on something
        *inside* a panel -- a GPU process row's signal button, a Slurm job row.
        """
        if self.grid is None:
            return None
        node = self.focused
        while node is not None:
            if node.parent is self.grid:
                return node
            node = node.parent
        return None

    def _focused_cell(self) -> tuple[int, int] | None:
        """``(column, row)`` of the focused panel's grid cell, or None.

        Mirrors ``GridLayout.arrange``: displayed children fill cells in order,
        so a hidden panel shifts everything after it. No spans are used, so the
        index arithmetic is exact.
        """
        panel = self._focused_panel()
        if panel is None:
            return None
        displayed = [child for child in self.grid.children if child.display]
        if panel not in displayed:
            return None
        cols = max(1, int(self.grid.styles.grid_size_columns or 1))
        index = displayed.index(panel)
        return index % cols, index // cols

    def _resize_focused_panel(self, orientation: str, delta: float) -> None:
        """Nudge the row or column containing the focused panel."""
        cell = self._focused_cell()
        if cell is None:
            self.notify("Focus a panel first — ] and [ move between panels",
                        title="Panel size", severity="warning")
            return
        index = cell[0] if orientation == "columns" else cell[1]
        if not self.grid.nudge(orientation, index, delta):
            axis = "columns" if orientation == "columns" else "rows"
            count = len(self.grid.column_weights if orientation == "columns"
                        else self.grid.row_weights)
            if count < 2:
                self.notify(
                    f"This layout has a single {axis[:-1]} — nothing to resize against",
                    title="Panel size", severity="warning")
            return
        self._store_grid_weights()

    def action_widen_panel(self) -> None:
        self._resize_focused_panel("columns", NUDGE_STEP)

    def action_narrow_panel(self) -> None:
        self._resize_focused_panel("columns", -NUDGE_STEP)

    def action_heighten_panel(self) -> None:
        self._resize_focused_panel("rows", NUDGE_STEP)

    def action_shorten_panel(self) -> None:
        self._resize_focused_panel("rows", -NUDGE_STEP)

    def action_reset_panel_sizes(self) -> None:
        """Return every row and column to an equal share."""
        if self.grid is None or not self.grid.reset_tracks():
            return
        self._store_grid_weights()
        self.notify("Panel sizes reset", title="Panel size", severity="information")

    def _get_shortcuts_banner_text(self) -> str:
        """Build Rich markup text listing all key bindings, grouped by purpose."""
        def row(k: str, desc: str) -> str:
            return f"  [bold]{k:<7}[/] {desc}"

        lines = ["[bold]Keyboard shortcuts[/]\n"]
        lines.append("[bold]Views[/]")
        lines += [row("d", "Dashboard"), row("s", "Settings (jumps to widget list)"), row("l", "Logs")]
        lines.append("\n[bold]Layout[/]")
        lines += [row("g", "Grid"), row("h", "Horizontal"), row("v", "Vertical"), row("space", "Cycle layout")]
        lines.append("\n[bold]Panel size[/]")
        lines += [
            row("ctrl+←→", "Narrow / widen the focused panel's column"),
            row("ctrl+↑↓", "Shorten / heighten the focused panel's row"),
            row("z", "Reset all panels to equal size"),
            row("drag", "Drag a shared border (or corner) between panels"),
        ]
        lines.append("  [dim]Keys resize a whole row/column, so panels sharing it follow;[/]")
        lines.append("  [dim]a dragged border moves only the two panels either side of it.[/]")
        lines.append("\n[bold]Refresh & theme[/]")
        lines += [
            row("r", "Refresh now"),
            row("+ / -", "Faster / slower"),
            row("t", "Cycle theme"),
            row("a", "Toggle threshold alerts"),
        ]
        lines.append("  [dim]In Settings → Colors:[/]")
        lines += [
            row("enter", "Open the colour picker (palette, HSV, live preview)"),
            row("ctrl+z", "Revert the colour being edited"),
        ]
        lines.append("\n[bold]Dashboard panels[/]")
        lines += [
            row("] / [", "Focus next / previous panel"),
            row("tab", "Focus next panel"),
            row("x", "Hide focused panel"),
        ]
        lines.append("  [dim]When a panel is focused:[/]")
        lines += [
            row("1 2 3", "CPU: All cores / Affinity / My processes"),
            row("1 2 / p", "GPU: Plot / Processes"),
        ]
        lines.append("\n[bold]Slurm[/]")
        lines += [
            row("J", "Pick jobs — opens the list of your running jobs"),
            row("  enter", "…[bold]list[/] the checked jobs in the Slurm panel (queue view)"),
            row("  f", "…[bold]focus[/] the whole dashboard inside the highlighted job"),
            row("F", "Focus a job / return to this host"),
        ]
        lines.append("  [dim]Listing shows what the queue says about a job "
                     "(state, allocation, time).[/]")
        lines.append("  [dim]Focusing runs a collector inside the job, so every "
                     "panel shows the job's[/]")
        lines.append("  [dim]own CPU, memory, GPUs and processes instead of this "
                     "host's.[/]")
        lines.append("  [dim]In the Slurm panel: [bold]F[/] focuses that job, "
                     "[bold]C[/] cancels it (press twice).[/]")
        lines.append("\n[bold]Other[/]")
        lines += [row("?", "This help"), row("q", "Quit")]
        lines.append("\n[dim]Press q or Escape to close[/]")
        return "\n".join(lines)

    def action_show_shortcuts(self) -> None:
        """Show a modal banner with all available movement and widget tab shortcuts."""
        content = self._get_shortcuts_banner_text()
        self.push_screen(ShortcutsScreen(content=content))

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
                    self.grid = ResizableGrid(classes="grid")
                    yield self.grid

            # Settings tab: left column = widget visibility, layout, timing, disk
            # filter; right column = the theme list (long, so it gets its own column
            # and scrolls internally). Section names live in the block borders.
            with TabPane("Settings", id="settings"):
                with Vertical(id="settings-pane"):
                    with Horizontal(id="settings-columns"):
                        with Vertical(id="settings-col-left"):
                            with Vertical(classes="settings-block") as block:
                                block.border_title = "Visible widgets"
                                self.select = SelectionList[str](id="visible-widgets-list")
                                yield self.select
                            with Vertical(classes="settings-block") as block:
                                block.border_title = "Layout"
                                with RadioSet(id="layout-radio-set"):
                                    yield RadioButton("Grid", id="layout-grid")
                                    yield RadioButton("Horizontal", id="layout-horizontal")
                                    yield RadioButton("Vertical", id="layout-vertical")
                            with Horizontal(id="settings-timing-row"):
                                with Vertical(classes="settings-block") as block:
                                    block.border_title = "Refresh rate"
                                    yield Select(
                                        [(_refresh_label(r), float(r)) for r in REFRESH_RATES],
                                        value=_nearest_refresh(self.refresh_rate),
                                        allow_blank=False,
                                        compact=True,
                                        id="refresh-select",
                                    )
                                with Vertical(classes="settings-block") as block:
                                    block.border_title = "History window"
                                    yield Select(
                                        [(_history_label(s), s) for s in HISTORY_SIZES],
                                        value=_nearest_history(self.history_size),
                                        allow_blank=False,
                                        compact=True,
                                        id="history-select",
                                    )
                            with Vertical(classes="settings-block") as block:
                                block.border_title = "Disk ignore prefixes (enter to apply)"
                                yield Input(
                                    id="disk-ignore-prefixes",
                                    placeholder="e.g. /boot/efi, /boot, /snap",
                                    compact=True,
                                )
                        with Vertical(id="settings-col-right"):
                            with Vertical(classes="settings-block", id="theme-block") as block:
                                block.border_title = "Theme"
                                with RadioSet(id="theme-radio-set"):
                                    for name in get_available_themes():
                                        yield RadioButton(
                                            _build_theme_swatch(name),
                                            id=f"theme-{name}",
                                        )
                            # Per-colour editing: pick a key, type a hex value.
                            # Applies live, to the config only — naming it as a
                            # theme is the separate, explicit step below.
                            with Vertical(classes="settings-block", id="colors-block") as block:
                                block.border_title = "Colors (enter to apply)"
                                yield OptionList(
                                    *self._build_color_options(),
                                    id="color-key-list",
                                    compact=True,
                                )
                                with Horizontal(id="color-hex-row"):
                                    yield Static("hex", id="color-hex-label")
                                    yield Input(
                                        id="color-hex-input",
                                        placeholder="#RRGGBB",
                                        compact=True,
                                    )
                            with Vertical(classes="settings-block", id="theme-save-block") as block:
                                block.border_title = "Save as custom theme"
                                with Horizontal(id="theme-save-row"):
                                    yield Input(
                                        id="theme-name-input",
                                        placeholder="my-theme",
                                        compact=True,
                                    )
                                    yield Button("Save", id="theme-save-btn", compact=True)
                                    yield Button("Delete", id="theme-delete-btn", compact=True)


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
        # Single timer using the selected refresh rate (from config or default); stop any timer
        # created by watch_refresh_rate when load_config() set refresh_rate, then create one.
        if self._update_timer:
            self._update_timer.stop()
            self._update_timer = None
        self._update_timer = self.set_interval(self.refresh_rate, self._update_metrics_sync)
        self._select_refresh_option(self.refresh_rate)
        self._select_history_option(self.history_size)
        self._select_layout_radio(self.current_layout)
        self._select_current_theme_radio()
        self._set_theme_name_input(self._detect_current_theme())
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

        # In --squeue mode, prompt for which jobs to monitor once the UI is live.
        if self.squeue_mode:
            if slurm_utils.slurm_available():
                self.call_after_refresh(lambda: asyncio.create_task(self.action_select_jobs()))
            else:
                self.notify("Slurm not found; --squeue panel will be empty.",
                            title="Slurm", severity="warning")

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
            # Awaited (the other remove_children call sites do too): mounting the
            # new panels while the old ones are still being removed races the
            # DOM, and with many panels the rebuild could stop part-way through.
            await self.grid.remove_children()
            # Which panels exist follows the machine being monitored -- this
            # host normally, the focused job's compute node in focus mode.
            _layout = self._layout_metrics()
            gpu_metrics = _layout.get("gpu") or []
            cpu_metrics = _layout.get("cpu") or {}
            disk_metrics = _layout.get("disk") or {"disks": []}
            memory_metrics = _layout.get("memory") or {}
            temperature_metrics = _layout.get("temperature")
            # Build widget titles in same order as mounting to compute visible count from saved config
            _gpu_for_layout = gpu_metrics if self.gpu_indices is None else [gpu_metrics[i] for i in self.gpu_indices if 0 <= i < len(gpu_metrics)]
            _disk_titles = [f"Disk @ {d['mountpoint']}" for d in disk_metrics.get("disks", []) if not self._disk_mount_ignored(d["mountpoint"])]
            _gpu_titles = [f"GPU @ {g['gpu_name']}" for g in _gpu_for_layout]
            cpu_title = f"{cpu_metrics.get('cpu_name') or 'CPU'}"
            _visible = 0
            _visible += 1 if bool(self.selected_widgets.get(cpu_title, True)) else 0
            _visible += 1 if bool(self.selected_widgets.get("Memory", True)) else 0
            if temperature_metrics:
                _visible += 1 if bool(self.selected_widgets.get("Temperature", True)) else 0
            for _t in _disk_titles:
                _visible += 1 if bool(self.selected_widgets.get(_t, True)) else 0
            _visible += 1 if bool(self.selected_widgets.get("Network", True)) else 0
            for _t in _gpu_titles:
                _visible += 1 if bool(self.selected_widgets.get(_t, True)) else 0
            if self.squeue_mode:
                _visible += 1 if bool(self.selected_widgets.get("Slurm Jobs", True)) else 0
            self._apply_grid_layout_dimensions(_visible)

            # Always create new widgets when setup_widgets is called
            # Resolve saved tab state for CPU widget (cpu_title already set above)
            cpu_initial_tab = self._widget_tab_states.get(cpu_title, "all")
            cpu_widget = CPUWidget(cpu_title, initial_tab=cpu_initial_tab,
                                   history_size=int(self.history_size))
            memory_widget = MemoryWidget("Memory")
            self.disk_widgets = []
            self.gpu_widgets = []
            self.temperature_widget = None
            network_widget = NetworkIOWidget("Network")
        
            await self.grid.mount(cpu_widget)
            await self.grid.mount(memory_widget)
            
            # Create temperature widget only if temperature data is available
            # (temperature_metrics comes from _layout_metrics above, so a
            # focused job's sensors decide this, not the login node's).
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
            for disk in disk_metrics.get("disks", []):
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
                    # In job focus the process rows come from a compute node, so
                    # local signalling must stay off across rebuilds too.
                    signals_enabled=not self.job_focus_active,
                )
                self.gpu_widgets.append(gpu_widget)
                await self.grid.mount(gpu_widget)

            # Slurm jobs widget (only in --squeue mode)
            self.slurm_jobs_widget = None
            if self.squeue_mode:
                self.slurm_jobs_widget = SlurmJobsWidget("Slurm Jobs", id="slurm_jobs")
                await self.grid.mount(self.slurm_jobs_widget)

            logger.info(f"Setup complete: {len(self.disk_widgets)} disk widgets, {len(self.gpu_widgets)} GPU widgets")

            # Update selection list after widgets are created
            self.create_selection_list()
            # Push current monitored jobs into the freshly-mounted widget
            # (use cache to avoid a blocking subprocess on the main thread).
            if self.slurm_jobs_widget is not None:
                self._refresh_slurm_widget(self._slurm_monitor.cached())

    def create_json(self) -> None:
        """Create the initial config file with all current state.

        Called only the very first time the application runs and no config
        file exists yet.
        """
        selection_dict = {}
        for widget in self.grid.children:
            if hasattr(widget, "title"):
                selection_dict[widget.title] = True
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
        # After the write, not before: this call writes the colors section, and
        # dumping default_config over it would drop it again.
        ensure_colors_in_config()

                
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
            if widget_type == "slurm":
                # The Slurm panel is opt-in via --squeue, so always default it on.
                default = True
            elif self.allowed_types:
                default = widget_type in self.allowed_types
            else:
                default = True
            had_key = widget.title in self.selected_widgets
            selected = self.selected_widgets.get(widget.title, default)
            if not had_key:
                self.selected_widgets[widget.title] = selected
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
        elif isinstance(widget, SlurmJobsWidget):
            return "slurm"
        return "unknown"

    # Metric types required per widget type (used to collect only what visible widgets need)
    _WIDGET_TYPE_REQUIRED_METRICS = {
        "cpu": ["cpu"],
        "ram": ["memory"],
        "disk": ["disk"],
        "net": ["network"],
        "gpu": ["gpu"],
        "temp": ["temperature"],
        "slurm": ["slurm"],
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
        # During init, create_selection_list() add_option() can emit SelectedChanged;
        # ignore so we don't overwrite loaded config with a partial selection.
        if self._is_initializing:
            return
        selected = self.query_one(SelectionList).selected
        self.toggle_widget_visibility(selected)
        # Update selected_widgets dictionary
        self.selected_widgets = {option: (option in selected) for option in self.selectionoptions}
        self.save_selection()

    def toggle_widget_visibility(self, selected_titles) -> None:
        """Toggle widget visibility based on selected titles.

        In normal mode, failed widgets are fully hidden. In debug mode, failed
        widgets remain visible. Updates grid dimensions when the visible set changes.
        """
        for widget in self.grid.children:
            if not hasattr(widget, "title"):
                continue
            wt = self._get_widget_type(widget)
            if widget.title in self._failed_widget_titles:
                widget.styles.display = "block" if self._debug_mode else "none"
            else:
                widget.styles.display = "block" if widget.title in selected_titles else "none"
            logger.debug("Widget %s display: %s", widget.title, widget.styles.display)
        if self.grid is not None:
            visible_count = sum(
                1 for w in self.grid.children
                if hasattr(w, "title") and w.styles.display != "none"
            )
            self._apply_grid_layout_dimensions(visible_count)
            self.grid.refresh()

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
            # The colour picker's preview is a real widget outside the grid; it
            # keeps ticking so the preview stays live while colours are edited.
            preview_widget = self._color_preview_widget
            if preview_widget is not None and not preview_widget.is_mounted:
                preview_widget = self._color_preview_widget = None
            if not active_widgets and preview_widget is None:
                return

            # Required metric types = union over active widgets
            required_types = set()
            for w in active_widgets:
                required_types |= self._get_required_metric_types(w)
            if preview_widget is not None:
                required_types |= self._get_required_metric_types(preview_widget)

            # Run only required collectors in executor
            loop = asyncio.get_event_loop()
            collectors = {
                "cpu": self.system_metrics.get_cpu_metrics,
                "disk": self.system_metrics.get_disk_metrics,
                "memory": self.system_metrics.get_memory_metrics,
                "network": self.system_metrics.get_network_metrics,
                "gpu": self.system_metrics.get_gpu_metrics,
                "temperature": self.system_metrics.get_temperature_metrics,
                "slurm": self._slurm_monitor.poll,
            }
            # In job-focus mode the resource families come from the focused
            # job's compute node instead of this host. Reading them is a cheap
            # cache lookup -- the probe itself runs on the sampler's own thread,
            # because it takes seconds and must not gate the UI tick.
            focus_metrics = self._job_focus_metrics() if self.job_focus_active else {}
            # 'slurm' describes the queue, so it stays local either way.
            local_types = ({t for t in required_types if t == "slurm"}
                           if self.job_focus_active else required_types)

            tasks = [loop.run_in_executor(None, collectors[t]) for t in local_types]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            metrics_by_type = {}
            collector_errors = {}  # type -> exception, for debug display
            for t, r in zip(local_types, results):
                if isinstance(r, BaseException):
                    logger.error("Collector %s failed: %s", t, r, exc_info=True)
                    metrics_by_type[t] = None
                    collector_errors[t] = r
                else:
                    metrics_by_type[t] = r
            if self.job_focus_active:
                # None for a family the probe has not delivered yet, which the
                # per-widget loop below already treats as "skip this tick".
                for t in required_types:
                    if t == "slurm":
                        continue
                    metrics_by_type[t] = focus_metrics.get(t)

            # Cache successful collections for the picker preview to reuse.
            self._last_metrics_by_type.update(
                {t: m for t, m in metrics_by_type.items() if m is not None}
            )

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
                    # A tick that renders clears the strike count: only a
                    # persistently broken widget should be taken out.
                    self._widget_failure_streaks.pop(widget.title, None)
                except Exception as e:
                    streak = self._widget_failure_streaks.get(widget.title, 0) + 1
                    self._widget_failure_streaks[widget.title] = streak
                    logger.error("Widget %s failed (%d/%d): %s", widget.title, streak,
                                 self.WIDGET_FAILURE_LIMIT, e, exc_info=True)
                    if streak < self.WIDGET_FAILURE_LIMIT and not self._debug_mode:
                        # Transient: a metric source can race with a process
                        # exiting or a device disappearing for one tick. Keep
                        # the last good frame on screen and try again.
                        continue
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

            self._apply_alerts(active_widgets, metrics_by_type)
            self._update_color_preview(metrics_by_type)
        except Exception as e:
            logger.error("Error in update_metrics: %s", e, exc_info=True)
            if self._debug_mode:
                # Let the exception propagate in debug mode so it is visible
                raise
        finally:
            self._update_in_progress = False

    def _alert_target_key(self, widget, metrics_by_type: dict):
        """
        Map a panel to the key ``evaluate_snapshot`` reports it under.

        Disk panels are identified by mountpoint (parsed back out of the title,
        the same way ``_dispatch_widget_update`` matches them) and GPU panels by
        index, so per-mount and per-device alerts land on the right panel.
        """
        if isinstance(widget, CPUWidget):
            return ("cpu", None)
        if isinstance(widget, MemoryWidget):
            return ("memory", None)
        if isinstance(widget, NetworkIOWidget):
            return ("network", None)
        if isinstance(widget, TemperatureWidget):
            return ("temperature", None)
        if isinstance(widget, DiskIOWidget):
            title = widget.title or ""
            if title.startswith("Disk @ "):
                return ("disk", title[len("Disk @ "):])
            return None
        if isinstance(widget, GPUWidget):
            if widget.id and str(widget.id).startswith("gpu_"):
                try:
                    return ("gpu", int(str(widget.id).split("_", 1)[1]))
                except (ValueError, IndexError):
                    return None
            try:
                return ("gpu", self.gpu_widgets.index(widget))
            except ValueError:
                return None
        return None

    def _apply_alerts(self, active_widgets, metrics_by_type: dict) -> None:
        """Evaluate thresholds for this tick and restyle any breaching panels."""
        if not self.alerts_enabled:
            return
        try:
            targets, breaches = evaluate_snapshot(metrics_by_type, self.thresholds)
        except Exception as e:  # noqa: BLE001 - alerting must never break the loop
            logger.error("Alert evaluation failed: %s", e, exc_info=True)
            return

        self._active_breaches = breaches
        sticky = self.alert_sticky_seconds
        for widget in active_widgets:
            key = self._alert_target_key(widget, metrics_by_type)
            if key is None:
                continue
            # A panel whose metric family failed to collect this tick keeps its
            # previous state rather than being silently cleared to OK.
            if key[0] not in metrics_by_type:
                continue
            try:
                widget.set_alert(targets.get(key, ALERT_OK), sticky_seconds=sticky)
            except Exception:  # noqa: BLE001
                pass

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
        elif isinstance(widget, SlurmJobsWidget):
            self._refresh_slurm_widget(metrics_by_type.get("slurm"))

    @on(JobRow.FocusJob)
    def _on_row_focus_job(self, message: JobRow.FocusJob) -> None:
        """F button on a job row: point the dashboard at that job."""
        message.stop()
        jobid = str(message.jobid)
        if (self._focused_job or {}).get("jobid") == jobid:
            self.notify(f"Already focused on job {jobid}.", title="Slurm job focus")
            return
        # The row carries only what squeue reported; the sampler needs the
        # nodelist, which the monitor's cached rows have.
        job = next((j for j in self._slurm_monitor.cached()
                    if str(j.get("jobid")) == jobid), {"jobid": jobid})
        self._enter_job_focus(job)

    @on(JobRow.CancelJob)
    def _on_row_cancel_job(self, message: JobRow.CancelJob) -> None:
        """C button on a job row, confirmed: scancel it.

        Runs off the UI thread -- scancel talks to the controller, which can be
        slow -- and reports what Slurm actually said rather than assuming success.
        """
        message.stop()
        jobid = str(message.jobid)
        asyncio.create_task(self._cancel_job(jobid))

    async def _cancel_job(self, jobid: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            ok, detail = await loop.run_in_executor(
                None, lambda: slurm_utils.scancel_job(jobid))
        except Exception as e:  # noqa: BLE001
            logger.error("scancel %s raised: %s", jobid, e)
            self.notify(f"Could not cancel job {jobid} (see Logs).",
                        title="Slurm", severity="error")
            return
        self.notify(detail, title="Slurm",
                    severity="information" if ok else "error")
        if not ok:
            return
        # A cancelled job stops being sampleable, so drop focus rather than
        # waiting for probes to start failing.
        if (self._focused_job or {}).get("jobid") == jobid:
            self._exit_job_focus(f"Job {jobid} was cancelled.")
        await self._poll_and_refresh_slurm()

    def _refresh_slurm_widget(self, jobs) -> None:
        """Update the Slurm jobs widget, choosing an informative empty-state note."""
        if self.slurm_jobs_widget is None:
            return
        note = None
        if not slurm_utils.slurm_available():
            note = "Slurm not available on this system."
        elif not self._monitored_jobids:
            note = "No jobs selected — press [bold]J[/] to choose."
        try:
            self.slurm_jobs_widget.update_jobs(
                jobs or [], note=note,
                focused_jobid=(self._focused_job or {}).get("jobid"))
        except Exception:
            if self._debug_mode:
                raise
    
    async def _update_cpu_widget(self, widget, cpu_metrics):
        """Update CPU widget (plot operations on main thread due to plotext)."""
        widget.update_content(
            cpu_metrics['cpu_percentages'],
            cpu_metrics['cpu_freqs'],
            cpu_metrics['mem_percent'],
            telemetry=cpu_metrics.get('cpu_telemetry'),
            # Affinity / per-user core views are computed from local psutil
            # state, which says nothing about a focused job's node.
            remote=self.job_focus_active,
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
            telemetry=gpu_metric,
        )

    async def _update_temperature_widget(self, widget, temperature_metrics):
        """Update Temperature widget (plot operations on main thread due to plotext)."""
        widget.update_content(temperature_metrics)

    def action_toggle_alerts(self) -> None:
        """Toggle threshold alerting, clearing every panel's state when off."""
        self.alerts_enabled = not self.alerts_enabled
        if not self.alerts_enabled:
            self._active_breaches = []
            for widget in self.grid.children if self.grid else []:
                if hasattr(widget, "set_alert"):
                    try:
                        widget.set_alert(ALERT_OK)
                    except Exception:  # noqa: BLE001
                        pass
        self.save_config()
        try:
            self.notify(f"Alerts {'enabled' if self.alerts_enabled else 'disabled'}")
        except Exception:  # noqa: BLE001
            pass

    def action_toggle_auto(self) -> None:
        # self.auto_layout = not self.auto_layout
        if self.auto_layout:
            self.update_layout()

    def _apply_layout_from_key(self, layout: str) -> None:
        """Set a layout and keep the Settings radio in sync (for keyboard use)."""
        self.set_layout(layout)
        self._select_layout_radio(layout)

    def action_set_horizontal(self) -> None:
        self._apply_layout_from_key("horizontal")

    def action_set_vertical(self) -> None:
        self._apply_layout_from_key("vertical")

    def action_set_grid(self) -> None:
        self._apply_layout_from_key("grid")

    def action_cycle_layout(self) -> None:
        """Cycle grid -> horizontal -> vertical -> grid (single-key)."""
        order = ["grid", "horizontal", "vertical"]
        cur = getattr(self, "current_layout", "grid")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "grid"
        self._apply_layout_from_key(nxt)
        if not self._is_initializing:
            self.notify(f"Layout: {nxt}", title="Layout", severity="information")

    def action_quit(self) -> None:
        self.exit()

    def action_open_settings(self) -> None:
        """Switch to the Settings tab and focus the widget list for fast toggling."""
        try:
            root_tabs = self.query_one("#root-tabs", TabbedContent)
            root_tabs.active = "settings"
            # Focus the visibility list so the user can immediately toggle
            # widgets with arrows + space — "change displayed widgets" in 2 keys.
            self.call_after_refresh(self._focus_widget_list)
        except Exception:
            if self._debug_mode:
                raise

    def _focus_widget_list(self) -> None:
        try:
            self.query_one("#visible-widgets-list", SelectionList).focus()
        except Exception:
            pass

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

    async def action_select_jobs(self) -> None:
        """Open the Slurm job picker (works any time; enables --squeue mode).

        Only *running* jobs are listed: a queued job holds no resources, so
        there is nothing to focus on or sample yet.
        """
        if not slurm_utils.slurm_available():
            self.notify("Slurm not found (squeue is not on PATH).",
                        title="Slurm", severity="warning")
            return
        # Fetch the queue off the UI thread so a slow controller can't freeze us.
        loop = asyncio.get_event_loop()
        try:
            jobs = await loop.run_in_executor(None, slurm_utils.get_running_user_jobs)
        except Exception as e:
            logger.error("Failed to list Slurm jobs: %s", e)
            self.notify("Failed to query Slurm jobs (see Logs).",
                        title="Slurm", severity="error")
            return
        focused = (self._focused_job or {}).get("jobid")
        self.push_screen(
            JobSelectScreen(jobs, set(self._monitored_jobids), focused_jobid=focused),
            self._on_jobs_selected,
        )

    def _on_jobs_selected(self, result) -> None:
        """Callback from JobSelectScreen: focus a job, or list jobs in the panel."""
        if not result:
            return  # cancelled
        action = result.get("action")
        if action == "focus":
            self._enter_job_focus(result.get("job") or {})
            return
        if action == "unfocus":
            self._exit_job_focus()
            return

        self._monitored_jobids = [str(j) for j in (result.get("jobids") or [])]
        self._slurm_monitor.set_jobs(self._monitored_jobids)
        logger.info("Now monitoring Slurm jobs: %s", self._monitored_jobids)
        if not self.squeue_mode:
            # Pressing J without --squeue enables the panel on the fly.
            self.squeue_mode = True
            asyncio.create_task(self._enable_squeue_and_refresh())
        else:
            asyncio.create_task(self._poll_and_refresh_slurm())

    # ---------------------------------------------------------------- #
    # Job-focus mode
    # ---------------------------------------------------------------- #
    @property
    def job_focus_active(self) -> bool:
        return self._job_sampler is not None

    def _enter_job_focus(self, job: dict) -> None:
        """Point every panel at one job's own resources.

        ``gc`` normally runs on a login node while the job runs elsewhere, so
        this cannot be done by filtering local readings — the numbers would
        describe the wrong machine. Instead a sampler joins the job's allocation
        on its compute node and the panels render that.
        """
        jobid = str(job.get("jobid") or "").strip()
        if not jobid:
            return
        node = slurm_utils.first_node(job.get("nodelist"))
        # Stop any previous sampler before replacing it, or two probe threads
        # race to fill the same panels.
        self._stop_job_sampler()
        self._focused_job = dict(job)
        self._focused_job["_node"] = node
        self._job_focus_probe_failures = 0
        # Sample at the dashboard's own rate: the collector now lives inside the
        # job, so a sample costs one line of JSON rather than a new job step.
        self._job_sampler = slurm_utils.JobFocusSampler(
            jobid, node=node, interval=max(float(self.refresh_rate), 0.5))
        self._job_sampler.start()

        # Also list the job in the Slurm panel, so its allocation and time limit
        # stay visible next to the resource panels.
        self._monitored_jobids = [jobid]
        self._slurm_monitor.set_jobs(self._monitored_jobids)
        self.squeue_mode = True

        self._set_gpu_signals_enabled(False)
        where = f" on {node}" if node else ""
        self.notify(
            f"Starting Ground Control inside job {jobid}{where} — the first "
            f"sample takes a few seconds, then it streams. Press F to return to "
            f"this host.",
            title="Slurm job focus",
        )
        logger.info("Entered job focus: job=%s node=%s", jobid, node)
        # The panel set depends on the job's hardware, so it is rebuilt once the
        # first sample tells us what that hardware is.
        asyncio.create_task(self._rebuild_when_sample_arrives())

    def _stop_job_sampler(self) -> None:
        if self._job_sampler is not None:
            self._job_sampler.stop()
            self._job_sampler = None

    def _exit_job_focus(self, reason: str | None = None) -> None:
        """Return to monitoring the host gc is running on."""
        if not self.job_focus_active:
            return
        jobid = (self._focused_job or {}).get("jobid")
        self._stop_job_sampler()
        self._focused_job = None
        self._job_focus_probe_failures = 0
        self._set_gpu_signals_enabled(True)
        self._restore_panel_titles()
        message = reason or f"Stopped focusing on job {jobid}."
        self.notify(message, title="Slurm job focus")
        logger.info("Exited job focus (%s)", reason or "user request")
        # The job's panel set (its GPUs, its mounts) has to give way to this
        # host's again, which means another rebuild.
        asyncio.create_task(self._rebuild_local_dashboard())

    async def _rebuild_local_dashboard(self) -> None:
        """Rebuild panels for this host after leaving job focus."""
        try:
            await self.setup_widgets()
            self.apply_widget_visibility()
            self._restore_panel_titles()
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to rebuild dashboard after unfocus: %s", e,
                         exc_info=True)
            if self._debug_mode:
                raise

    def action_toggle_job_focus(self) -> None:
        """F: drop job focus, or open the picker to choose a job to focus."""
        if self.job_focus_active:
            self._exit_job_focus()
        else:
            asyncio.create_task(self.action_select_jobs())

    def _set_gpu_signals_enabled(self, enabled: bool) -> None:
        """Enable/disable the per-process signal buttons on GPU panels.

        In job-focus mode the listed pids live on the compute node. Signalling
        them locally would hit whatever unrelated login-node process happens to
        hold that pid, so the buttons are disabled rather than misleading.
        """
        for widget in list(self.gpu_widgets or []):
            try:
                widget.set_signals_enabled(enabled)
            except Exception:  # noqa: BLE001 - cosmetic, never break the tick
                pass

    def _panel_title_suffix(self, age: float) -> str:
        """Suffix marking which job/node a panel is showing, plus staleness."""
        job = self._focused_job or {}
        jobid = job.get("jobid", "?")
        node = job.get("_node")
        suffix = f" — job {jobid}"
        if node:
            suffix += f" @ {node}"
        if age == float("inf"):
            suffix += " (starting…)"
        elif age > self._job_sample_stale_after():
            # Well past the remote collector's own cadence: say so rather than
            # presenting an old reading as current.
            suffix += f" (stale {int(age)}s)"
        return suffix

    def _job_sample_stale_after(self) -> float:
        """Seconds after which a focused job's sample is called stale.

        Derived from the sampler's cadence, not hardcoded: a streaming collector
        delivers every second or so, while the one-shot fallback legitimately
        takes seconds per sample, and calling the latter stale on the same clock
        would flag normal operation.
        """
        sampler = self._job_sampler
        if sampler is None:
            return 15.0
        floor = 20.0 if sampler.mode == "probe" else 6.0
        return max(floor, sampler.interval * 4)

    def _apply_panel_titles(self, age: float) -> None:
        """Retitle panels so it is never ambiguous which machine is shown.

        The suffix is set through ``set_title_suffix`` rather than by assigning
        ``border_title``: alerting rebuilds that title from ``widget.title`` plus
        its ▲/■ marker, so writing it here directly would make the two clobber
        each other every tick. ``widget.title`` itself must stay untouched — the
        app matches disk panels and tracks failures by it.
        """
        suffix = self._panel_title_suffix(age)
        for widget in self._iter_visible_metric_widgets():
            if not hasattr(widget, "set_title_suffix"):
                continue  # e.g. the Slurm panel, which is not a MetricWidget
            try:
                widget.set_title_suffix(suffix)
            except Exception:  # noqa: BLE001
                pass

    def _restore_panel_titles(self) -> None:
        for widget in list(self.grid.children if self.grid else []):
            if not hasattr(widget, "set_title_suffix"):
                continue
            try:
                widget.set_title_suffix("")
            except Exception:  # noqa: BLE001
                pass

    def _layout_metrics(self) -> dict:
        """Metrics that decide *which* panels exist (GPU count, disk mounts, …).

        In job-focus mode this is the focused job's own hardware, not this
        host's: a login node typically has no GPUs at all, so building the
        dashboard from local readings would leave a GPU job with no GPU panels.
        Falls back to local metrics until the first remote sample lands.
        """
        if self.job_focus_active and self._job_sampler is not None:
            snapshot, _age, _error = self._job_sampler.latest()
            remote = metrics_from_snapshot(snapshot)
            if remote.get("cpu") or remote.get("gpu"):
                return remote
        return {
            "gpu": self.system_metrics.get_gpu_metrics(),
            "cpu": self.system_metrics.get_cpu_metrics(),
            "disk": self.system_metrics.get_disk_metrics(),
            "memory": self.system_metrics.get_memory_metrics(),
            "temperature": self.system_metrics.get_temperature_metrics(),
        }

    async def _rebuild_when_sample_arrives(self, timeout: float = 90.0) -> None:
        """Rebuild the dashboard once the first probe of a focused job lands.

        The panel set depends on the job's hardware, which is unknown until a
        sample returns, so focus mode starts on the old layout and switches as
        soon as there is something real to build from.
        """
        sampler = self._job_sampler
        if sampler is None:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sampler is not self._job_sampler or sampler.stopped:
                return  # focus changed or was dropped while we waited
            snapshot, _age, _error = sampler.latest()
            if snapshot is not None:
                try:
                    await self.setup_widgets()
                    self.apply_widget_visibility()
                    self._set_gpu_signals_enabled(False)
                    await self._poll_and_refresh_slurm()
                except Exception as e:  # noqa: BLE001
                    # Without this the failure would only surface as an orphaned
                    # task exception, leaving a half-built dashboard and no clue.
                    logger.error("Failed to rebuild dashboard for focused job: %s",
                                 e, exc_info=True)
                    self.notify("Could not build panels for the focused job "
                                "(see Logs).", title="Slurm job focus",
                                severity="error")
                    if self._debug_mode:
                        raise
                return
            await asyncio.sleep(0.5)
        # Nothing came back at all: say so instead of leaving empty panels, and
        # include what the remote side actually said -- "Access/permission
        # denied" or a missing module is a fixable error, "see Logs" is not.
        jobid = (self._focused_job or {}).get("jobid")
        detail = (sampler.diagnostics() or [None])[-1]
        self._exit_job_focus(
            f"Could not start Ground Control inside job {jobid}: "
            + (detail or "no response from srun (see Logs).")
        )

    def _job_focus_metrics(self) -> dict:
        """Metrics for the focused job, or {} until the first probe lands."""
        sampler = self._job_sampler
        if sampler is None:
            return {}
        snapshot, age, _error = sampler.latest()
        metrics = metrics_from_snapshot(snapshot)
        self._apply_panel_titles(age)

        # A job that ends makes every probe fail. Notice that and hand the
        # dashboard back rather than showing its last sample indefinitely. The
        # confirming squeue call runs off-thread: this method is on the UI tick.
        failures = sampler.consecutive_failures
        if failures >= 3 and failures != self._job_focus_probe_failures:
            self._job_focus_probe_failures = failures
            asyncio.create_task(self._drop_focus_if_job_ended())
        return metrics

    async def _drop_focus_if_job_ended(self) -> None:
        """Leave job focus if the focused job has stopped running."""
        job = self._focused_job
        if job is None:
            return
        jobid = str(job.get("jobid"))
        loop = asyncio.get_event_loop()
        try:
            rows = await loop.run_in_executor(
                None, lambda: slurm_utils.get_jobs_by_id([jobid])
            )
        except Exception as err:  # noqa: BLE001
            logger.info("Could not confirm job %s state: %s", jobid, err)
            return  # controller hiccup: don't drop focus on a guess
        # Job absent from squeue, or present but no longer running.
        row = rows.get(jobid)
        if row is not None and slurm_utils.is_running_state(row.get("state")):
            return
        # Still focused on the same job? (the user may have switched meanwhile)
        if (self._focused_job or {}).get("jobid") != job.get("jobid"):
            return
        self._exit_job_focus(
            f"Job {jobid} is no longer running — back to this host."
        )

    async def _enable_squeue_and_refresh(self) -> None:
        """Rebuild widgets so the Slurm panel appears, then refresh it."""
        await self.setup_widgets()
        self.apply_widget_visibility()
        await self._poll_and_refresh_slurm()

    async def _poll_and_refresh_slurm(self) -> None:
        """Force a Slurm poll off-thread and push results into the widget."""
        if self.slurm_jobs_widget is None:
            return
        loop = asyncio.get_event_loop()
        try:
            jobs = await loop.run_in_executor(None, lambda: self._slurm_monitor.poll(force=True))
        except Exception as e:
            logger.error("Slurm poll failed: %s", e)
            jobs = self._slurm_monitor.cached()
        self._refresh_slurm_widget(jobs)

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
        # Panel focus only makes sense on the dashboard; switch there first.
        try:
            root_tabs = self.query_one("#root-tabs", TabbedContent)
            if root_tabs.active != "dashboard":
                root_tabs.active = "dashboard"
        except Exception:
            pass
        widgets = self._iter_visible_metric_widgets()
        if not widgets:
            return
        current = getattr(self, "focused", None)
        try:
            idx = widgets.index(current)
        except ValueError:
            idx = -1 if offset > 0 else 0
        new_idx = (idx + offset) % len(widgets)
        try:
            self.set_focus(widgets[new_idx])
        except Exception:
            if self._debug_mode:
                raise

    def action_focus_next_widget(self) -> None:
        """Cycle focus to the next visible metric widget in the dashboard grid."""
        self._focus_widget_by_offset(1)

    def action_focus_prev_widget(self) -> None:
        """Cycle focus to the previous visible metric widget in the dashboard grid."""
        self._focus_widget_by_offset(-1)

    def _hide_widget(self, widget) -> None:
        """Hide a dashboard panel (triggered by the panel's local 'x' binding)."""
        title = getattr(widget, "title", None)
        if not title:
            return
        self.selected_widgets[title] = False
        # Keep the Settings selection list in sync.
        try:
            self.select.deselect(title)
        except Exception:
            pass
        widget.styles.display = "none"
        if self.grid is not None:
            visible_count = sum(
                1 for w in self.grid.children
                if hasattr(w, "title") and w.styles.display != "none"
            )
            self._apply_grid_layout_dimensions(visible_count)
            self.grid.refresh()
        self.save_config()
        if not self._is_initializing:
            self.notify(f"Hid {title} — press s to manage widgets",
                        title="Widget hidden", severity="information")

    def _detect_current_theme(self) -> str | None:
        """Return the name of the active theme, edited palette or not."""
        try:
            return get_active_theme()[0]
        except Exception:
            return None

    def action_cycle_theme(self) -> None:
        """Cycle to the next available theme and apply it live (single-key)."""
        themes = get_available_themes()
        if not themes:
            return
        cur = self._detect_current_theme()
        idx = (themes.index(cur) + 1) % len(themes) if cur in themes else 0
        name = themes[idx]
        self._apply_theme_from_ui(name)
        # Reflect the change in the Settings radio set.
        self._select_theme_radio(name)
        if not self._is_initializing:
            self.notify(f"Theme: {name}", title="Theme", severity="information")

    def action_force_refresh(self) -> None:
        """Force an immediate metrics refresh."""
        self._update_metrics_sync()

    def action_faster_refresh(self) -> None:
        """Decrease the refresh interval (faster updates).

        Cycles through the predefined refresh rate buttons towards faster
        values, clamping at 0.5 s. Shows a toast with the new rate.
        """
        rates = sorted(REFRESH_RATES)  # ascending
        current = self.refresh_rate
        for r in reversed(rates):
            if r < current - 0.01:
                self.refresh_rate = r
                if not self._is_initializing:
                    self.notify(f"Refresh rate: {_refresh_label(r)}", title="Faster", severity="information")
                return
        if not self._is_initializing:
            self.notify("Already at fastest rate (500ms)", title="Faster", severity="information")

    def action_slower_refresh(self) -> None:
        """Increase the refresh interval (slower updates).

        Cycles through the predefined refresh rate buttons towards slower
        values, clamping at 60 s. Shows a toast with the new rate.
        """
        rates = sorted(REFRESH_RATES)  # ascending
        current = self.refresh_rate
        for r in rates:
            if r > current + 0.01:
                self.refresh_rate = r
                if not self._is_initializing:
                    self.notify(f"Refresh rate: {_refresh_label(r)}", title="Slower", severity="information")
                return
        if not self._is_initializing:
            self.notify("Already at slowest rate (1m)", title="Slower", severity="information")

    def on_resize(self, event: events.Resize) -> None:
        """Stack the Settings columns on narrow terminals; side by side they clip."""
        try:
            columns = self.query_one("#settings-columns", Horizontal)
        except Exception:
            return
        columns.set_class(event.size.width < self.SETTINGS_TWO_COLUMN_WIDTH, "-stacked")

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
        """Set layout (grid/horizontal/vertical). Rebuilds widgets only when layout actually changes."""
        layout_changed = layout != self.current_layout
        if layout_changed and self.grid is not None:
            self.grid.remove_class(self.current_layout)
            self.current_layout = layout
            self.grid.add_class(layout)
            asyncio.create_task(self.setup_widgets())
            asyncio.create_task(self.apply_visibility_after_setup())
        elif self.grid is None:
            self.current_layout = layout
        self.save_layout()
        
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
            else:
                is_visible = bool(self.selected_widgets.get(widget.title, True))
                widget.styles.display = "block" if is_visible else "none"
            logger.debug("Widget %s visible: %s", widget.title, widget.styles.display != "none")

    def _update_widget_history_sizes(self, new_size: int) -> None:
        """Update history size for all existing widgets without recreating them"""
        # Update CPU widget
        try:
            cpu_widget = self.query_one(CPUWidget)
            if hasattr(cpu_widget, 'history'):
                cpu_widget.history = cpu_widget.history.__class__(maxlen=new_size)
            # The stall series is plotted alongside `history`; resizing only one
            # would leave the two plot lines covering different time spans.
            if hasattr(cpu_widget, 'stall_history'):
                cpu_widget.stall_history = cpu_widget.stall_history.__class__(maxlen=new_size)
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
        
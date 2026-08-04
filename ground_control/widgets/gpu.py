import os
import signal as signal_module
from collections import deque
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, TabbedContent, TabPane, Button
from textual import on
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import align, ansi2rich, format_size, recolor, substitute_plot_timeframe
from ..utils.colors import get_rich_color
import logging

logger = logging.getLogger("ground-control.gpu")


# --------------------------------------------------------------- process rows
#
# One process is one row, buttons included. Columns are fixed-width so values
# line up down the list and can be scanned as a table; the command takes
# whatever is left. When the panel is too narrow for everything, columns are
# dropped by priority rather than letting the line wrap or truncate blindly.
#
# (key, header, width, align, priority) -- lower priority drops first.
# PID and GPU memory have no priority: they are never dropped, since a row you
# cannot identify and whose memory cost you cannot see is not worth a row.
PROC_COLUMNS = (
    ("username",    "USER",   10, "left",  3),
    ("pid",         "PID",     7, "right", None),
    ("gpu_memory",  "GPU MEM", 8, "right", None),
    ("cpu_percent", "CPU",     6, "right", 1),
    ("memory",      "HOST",    8, "right", 2),
)
COL_GAP = 1
# Narrower than this and the command is unreadable, so it is dropped instead.
CMD_MIN_WIDTH = 8
# One cell of label plus a margin, three times over: the strip the buttons
# occupy, which the header must reserve so its columns line up with the rows.
SIG_BUTTON_WIDTH = 3
SIG_BUTTON_STRIP = 3 * (SIG_BUTTON_WIDTH + 1)


def _proc_value(process: dict, key: str) -> str:
    """Display string for one column of a process row."""
    if key == "pid":
        return str(process.get("pid", ""))
    if key == "gpu_memory":
        return str(process.get("gpu_memory") or "-")
    if key == "cpu_percent":
        return str(process.get("cpu_percent") or "-")
    if key == "memory":
        return str(process.get("memory") or "-")
    if key == "username":
        return str(process.get("username") or "-")
    return ""


def _proc_command(process: dict) -> str:
    """The most informative one-line description of what the process is."""
    return str(
        process.get("script")
        or process.get("command")
        or process.get("name")
        or "?"
    )


def format_process_line(process: dict, width: int, header: bool = False) -> str:
    """Render one process (or the header) as exactly ``width`` cells of markup.

    Columns are dropped lowest-priority-first until the row fits, so a narrow
    panel still shows who owns the process, its PID and how much GPU memory it
    is holding -- the three facts needed to decide whether to kill it.
    """
    width = int(width)
    if width <= 0:
        return ""

    def fixed_width(cols):
        return sum(w for _, _, w, _, _ in cols) + COL_GAP * len(cols)

    columns = list(PROC_COLUMNS)
    # 1. Drop optional columns, cheapest first, until the command has room.
    while fixed_width(columns) + CMD_MIN_WIDTH > width:
        droppable = [c for c in columns if c[4] is not None]
        if not droppable:
            break
        columns.remove(min(droppable, key=lambda c: c[4]))

    # 2. On a very narrow panel even PID + GPU MEM overflow. Squeeze them from
    #    the right before giving up, so the row never bleeds into the buttons.
    overflow = fixed_width(columns) - width
    index = len(columns) - 1
    while overflow > 0 and index >= 0:
        key, label, col_width, alignment, priority = columns[index]
        take = min(overflow, max(0, col_width - 3))
        if take:
            columns[index] = (key, label, col_width - take, alignment, priority)
            overflow -= take
        index -= 1
    # 3. Still too narrow: drop from the right, PID last.
    while columns and fixed_width(columns) > width:
        columns.pop()

    cmd_width = width - fixed_width(columns)
    if cmd_width < CMD_MIN_WIDTH:
        cmd_width = 0

    user_color = get_rich_color("gpu_usage", "#00FFFF")
    mem_color = get_rich_color("gpu_ram", "#00FF00")

    parts = []
    for key, label, col_width, alignment, _ in columns:
        text = label if header else _proc_value(process, key)
        text = align(text[:col_width], col_width, alignment)
        if header:
            parts.append(f"[bold]{text}[/]")
        elif key == "username":
            parts.append(f"[{user_color}]{text}[/]")
        elif key == "gpu_memory":
            parts.append(f"[{mem_color}]{text}[/]")
        elif key in ("cpu_percent", "memory"):
            parts.append(f"[dim]{text}[/]")
        else:
            parts.append(text)

    if cmd_width:
        raw = "COMMAND" if header else _proc_command(process)
        if len(raw) > cmd_width:
            raw = raw[: max(0, cmd_width - 1)] + "…"
        raw = align(raw, cmd_width, "left")
        parts.append(f"[bold]{raw}[/]" if header else raw)

    line = (" " * COL_GAP).join(parts)
    # Pad rather than trust the caller: the row sits next to fixed-width
    # buttons, so a short line would let the background show through.
    plain_len = 0
    if parts:
        plain_len = (sum(w for _, _, w, _, _ in columns) + cmd_width
                     + COL_GAP * (len(parts) - 1))
    if plain_len < width:
        line += " " * (width - plain_len)
    return line


class ProcessRow(Static):
    """One process on one row: table columns plus its Kill / Term / Int buttons.

    The buttons are real ``Button`` widgets squeezed to a single cell high, so
    they keep focus, hover and keyboard activation; the full signal name lives
    in each one's tooltip, and the header row above spells out the columns.
    """

    DEFAULT_CSS = """
    ProcessRow {
        height: 1;
        min-height: 1;
        padding: 0 1;
    }
    ProcessRow Horizontal {
        height: 1;
    }
    ProcessRow .process-info {
        width: 1fr;
        min-width: 0;
        height: 1;
        overflow: hidden hidden;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    /* Buttons default to a 3-row bordered box; strip all of it so the whole
       row is one cell high. */
    ProcessRow .sig-btn {
        width: 3;
        min-width: 3;
        height: 1;
        min-height: 1;
        border: none;
        padding: 0;
        margin: 0 0 0 1;
        text-style: bold;
    }
    ProcessRow .sigkill-btn { background: #c00; color: white; }
    ProcessRow .sigterm-btn { background: #e85d00; color: white; }
    ProcessRow .sigint-btn  { background: #bb0; color: black; }
    """

    def __init__(self, process: dict, signals_enabled: bool = True, **kwargs):
        super().__init__(**kwargs)
        self._process = process
        # False while the listed pids belong to another host (Slurm job focus),
        # where signalling locally would hit an unrelated process.
        self._signals_enabled = signals_enabled

    def compose(self) -> ComposeResult:
        pid = self._process.get("pid", "?")
        with Horizontal():
            yield Static("", classes="process-info")
            for label, cls, signal_name, number in (
                ("K", "sigkill-btn", "SIGKILL", 9),
                ("T", "sigterm-btn", "SIGTERM", 15),
                ("I", "sigint-btn", "SIGINT", 2),
            ):
                button = Button(label, classes=f"sig-btn {cls}")
                button.disabled = not self._signals_enabled
                button.tooltip = (
                    f"Send {signal_name} ({number}) to PID {pid}"
                    if self._signals_enabled else
                    f"PID {pid} is on a remote node — signals are disabled "
                    f"while focused on a Slurm job"
                )
                yield button

    def set_signals_enabled(self, enabled: bool) -> None:
        """Enable/disable this row's signal buttons in place."""
        self._signals_enabled = enabled
        pid = self._process.get("pid", "?")
        for button in self.query(".sig-btn").results(Button):
            button.disabled = not enabled
            if not enabled:
                button.tooltip = (
                    f"PID {pid} is on a remote node — signals are disabled "
                    f"while focused on a Slurm job"
                )

    def on_mount(self) -> None:
        self._render_info()

    def on_resize(self, event) -> None:
        # Column set depends on the width available, so re-render on every
        # resize rather than formatting once at compose time.
        self._render_info()

    def _render_info(self) -> None:
        try:
            info = self.query_one(".process-info", Static)
        except NoMatches:
            return
        width = info.content_size.width or (self.content_size.width - SIG_BUTTON_STRIP)
        info.update(format_process_line(self._process, max(0, width)))

    @on(Button.Pressed, ".sigkill-btn")
    def _send_kill(self) -> None:
        self._send_signal("SIGKILL", signal_module.SIGKILL)

    @on(Button.Pressed, ".sigterm-btn")
    def _send_term(self) -> None:
        self._send_signal("SIGTERM", signal_module.SIGTERM)

    @on(Button.Pressed, ".sigint-btn")
    def _send_int(self) -> None:
        self._send_signal("SIGINT", signal_module.SIGINT)

    def _send_signal(self, sig_name: str, sig_num: int) -> None:
        pid = self._process.get("pid")
        if pid is None:
            return
        # Hard guard, not just a disabled button: in job-focus mode this pid was
        # read on a compute node, so os.kill here would signal whichever
        # unrelated local process happens to hold the same number.
        if not self._signals_enabled:
            logger.warning(
                "Refusing to send %s to pid %s: that pid belongs to a remote "
                "node while focused on a Slurm job", sig_name, pid,
            )
            return
        try:
            os.kill(pid, sig_num)
            name = (self._process.get("name") or "?")[:20]
            # Toast notifications disabled; log instead.
            logger.info("Signal sent: %s to pid %s (%s)", sig_name, pid, name)
        except ProcessLookupError:
            logger.error("Signal error: process %s no longer exists", pid)
        except PermissionError:
            logger.error("Signal error: permission denied sending %s to pid %s", sig_name, pid)
        except Exception as e:
            logger.error("Signal error: failed sending %s to pid %s: %s", sig_name, pid, e)


def _process_list_signature(processes: list) -> tuple:
    """Stable signature for comparison: (len, (pid, name), ...) so we only rebuild when the list actually changed."""
    if not processes:
        return (0,)
    return (len(processes),) + tuple((p.get("pid"), p.get("name")) for p in processes)


class GPUProcessList(Static):
    """Scrollable table of processes, one row each, with per-row K/T/I buttons."""

    DEFAULT_CSS = """
    GPUProcessList {
        height: 1fr;
        overflow-y: auto;
    }
    GPUProcessList #gpu-process-rows {
        height: auto;
    }
    GPUProcessList #gpu-process-header {
        height: 1;
        padding: 0 1;
    }
    GPUProcessList #gpu-process-header Horizontal {
        height: 1;
    }
    GPUProcessList .header-cols {
        width: 1fr;
        min-width: 0;
        height: 1;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    /* Same width as a row's button strip, so headers sit over their columns
       and the K/T/I letters get labelled. */
    GPUProcessList .header-buttons {
        width: 12;
        min-width: 12;
        height: 1;
        text-align: right;
    }
    """

    def __init__(self, signals_enabled: bool = True, **kwargs):
        super().__init__(**kwargs)
        self._last_sig = None
        self._signals_enabled = signals_enabled

    def set_signals_enabled(self, enabled: bool) -> None:
        """Enable/disable signal buttons on current and future rows."""
        self._signals_enabled = enabled
        for row in self.query(ProcessRow).results(ProcessRow):
            row.set_signals_enabled(enabled)

    def compose(self) -> ComposeResult:
        with Static(id="gpu-process-header"):
            with Horizontal():
                yield Static("", classes="header-cols")
                yield Static("[bold] K  T  I[/]", classes="header-buttons")
        yield Vertical(id="gpu-process-rows")

    def on_mount(self) -> None:
        self._render_header()

    def on_resize(self, event) -> None:
        self._render_header()

    def _render_header(self) -> None:
        """Draw the column headings at the same widths the rows will use."""
        try:
            header = self.query_one(".header-cols", Static)
        except NoMatches:
            return
        width = header.content_size.width or (self.content_size.width - SIG_BUTTON_STRIP - 2)
        header.update(format_process_line({}, max(0, width), header=True))

    def update_processes(self, processes: list) -> None:
        """Replace contents only when the process list (PIDs/names) has changed to avoid flicker."""
        sig = _process_list_signature(processes)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        container = self.query_one("#gpu-process-rows", Vertical)
        for child in list(container.children):
            child.remove()
        if not processes:
            return
        for p in processes:
            container.mount(ProcessRow(p, signals_enabled=self._signals_enabled))


class GPUWidget(MetricWidget):
    """Widget for GPU monitoring with dual plots for GPU RAM and Usage and a processes list in tabs."""

    # Local keyboard bindings: active only when this GPU widget has focus.
    # Numbered tabs mirror the CPU widget (1/2/3); p is a plot mnemonic.
    # Note: avoid binding "r" here — it is the app-global "refresh now".
    BINDINGS = [
        ("1", "show_plot", "GPU: plot"),
        ("2", "show_processes", "GPU: processes"),
        ("p", "show_plot", "GPU: plot"),
    ]

    # Rows taken by the tab bar (tabs + underline), the split bar and the
    # telemetry line; only used to estimate the plot region before the first
    # layout pass.
    CHROME_HEIGHT = 4
    # Below this panel height the telemetry line is hidden: the plot needs the
    # row more than the reader needs the clock speed.
    TELEMETRY_MIN_PANEL_HEIGHT = 9

    DEFAULT_CSS = """
    GPUWidget {
        layout: vertical;
    }
    #gpu-tabbed {
        height: 1fr;
    }
    .gpu-plot-pane {
        height: 1fr;
    }
    GPUWidget #current-value {
        height: 1;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    GPUWidget #gpu-telemetry {
        height: 1;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    .gpu-processes-pane {
        height: 1fr;
        overflow-y: auto;
    }
    GPUProcessList {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    """

    def __init__(
        self,
        title: str,
        id: str = None,
        color: str = "green",
        history_size: int = 120,
        initial_tab: str = "plot",
        signals_enabled: bool = True,
    ):
        """Initialise a GPU widget.

        Args:
            title: Widget border title (typically ``GPU @ <name>``).
            id: Optional Textual DOM id.
            color: Fallback plot color.
            history_size: Number of data-points to keep in the history deque.
            initial_tab: Tab pane id to show on mount (``"plot"`` or
                ``"processes"``).  Persisted across app restarts.
            signals_enabled: Whether the per-process signal buttons are live.
                False when the panel shows a remote host's processes (Slurm job
                focus), where the pids do not refer to local processes.
        """
        super().__init__(title=title, color=get_rich_color("gpu_ram", "#00FF00"), history_size=history_size, id=id)
        self.gpu_ram_history = deque(maxlen=history_size)
        self.gpu_usage_history = deque(maxlen=history_size)
        self.first = True
        self.title = title
        self.border_title = title
        self.usage_is_available = True
        self._initial_tab = initial_tab if initial_tab in ("plot", "processes") else "plot"
        self.max_val = 1
        self._last_processes = None
        # Raw per-GPU metric dict for the telemetry line, and whether that line
        # currently has a row (short panels give it back to the plot).
        self._telemetry: dict = {}
        self._telemetry_shown = True
        self._signals_enabled = signals_enabled

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=self._initial_tab, id="gpu-tabbed"):
            with TabPane("Plot", id="plot"):
                yield Static("", id="history-plot", classes="metric-plot gpu-plot-pane")
                yield Static("", id="current-value", classes="metric-value")
                yield Static("", id="gpu-telemetry", classes="metric-value")
            with TabPane("Processes", id="processes"):
                yield GPUProcessList(
                    id="gpu-processes", classes="gpu-processes-pane",
                    signals_enabled=self._signals_enabled,
                )

    @on(TabbedContent.TabActivated, "#gpu-tabbed")
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Re-draw for the newly revealed pane, whose region is only sized once active."""
        self.call_after_refresh(self.rerender)

    def _set_tab(self, tab_id: str) -> None:
        """Programmatically switch between plot and processes tabs."""
        if tab_id not in ("plot", "processes"):
            return
        try:
            tabbed = self.query_one("#gpu-tabbed")
            tabbed.active = tab_id
        except Exception:
            # Safe no-op if the tab container is not mounted yet.
            return

    def action_show_plot(self) -> None:
        """Show the GPU plot tab."""
        self._set_tab("plot")

    def action_show_processes(self) -> None:
        """Show the GPU processes tab."""
        self._set_tab("processes")

    @staticmethod
    def ram_color(warning: bool = False) -> str:
        """Colour of the GPU-RAM series — used for both the plot line and the bar."""
        if warning:
            return get_rich_color("gpu_ram_warning", "#FF0000")
        return get_rich_color("gpu_plot_ram", get_rich_color("gpu_ram", "#00FF00"))

    @staticmethod
    def usage_color() -> str:
        """Colour of the GPU-usage series — used for both the plot line and the bar."""
        return get_rich_color("gpu_plot_usage", get_rich_color("gpu_usage", "#00FFFF"))

    @staticmethod
    def temp_color(celsius: float) -> str:
        """Colour a GPU temperature on the same scale the temperature panel uses."""
        if celsius >= 85:
            return get_rich_color("temp_critical", "#FF0000")
        if celsius >= 75:
            return get_rich_color("temp_hot", "#FF8C00")
        if celsius >= 60:
            return get_rich_color("temp_warm", "#FFFF00")
        return get_rich_color("temp_normal", "#00FF00")

    def _telemetry_segments(self, t: dict) -> list:
        """(text, markup) pairs for the telemetry line, most important first.

        Everything unavailable on this card is omitted rather than rendered as
        "N/A": a line of placeholders costs the same width as real data and
        tells the reader nothing.
        """
        segments = []

        reasons = t.get("throttle_reasons") or []
        if reasons:
            # The single most actionable GPU fact: the card is being held back,
            # and by what. Severe reasons (thermal, power brake) mean lost
            # throughput now; a software power cap is normal under load.
            severe = t.get("throttle_severe")
            color = (get_rich_color("alert_crit", "#FF0000") if severe
                     else get_rich_color("alert_warn", "#FFA500"))
            marker = "■" if severe else "▲"
            text = f"{marker} {', '.join(reasons)}"
            segments.append((text, f"[{color}]{text}[/]"))

        power, limit = t.get("power_w"), t.get("power_limit_w")
        if power is not None:
            if limit:
                # Percent of cap is the honest "is this card working" number:
                # a GPU at 100% utilisation drawing 15% of its limit is stalled.
                text = f"{power:.0f}/{limit:.0f}W {power / limit * 100:.0f}%"
            else:
                text = f"{power:.0f}W"
            segments.append((text, f"[{self.usage_color()}]{text}[/]"))

        temp = t.get("temperature_c")
        if temp is not None:
            text = f"{temp:.0f}°C"
            segments.append((text, f"[{self.temp_color(temp)}]{text}[/]"))

        clock, max_clock = t.get("sm_clock_mhz"), t.get("max_sm_clock_mhz")
        if clock is not None:
            text = f"{clock:.0f}/{max_clock:.0f}MHz" if max_clock else f"{clock:.0f}MHz"
            segments.append((text, text))

        bandwidth = t.get("mem_bw_percent")
        if bandwidth is not None:
            # Memory-interface busy time, not VRAM occupancy. High SM
            # utilisation with near-zero bandwidth is the classic input-starved
            # training loop.
            text = f"BW {bandwidth:.0f}%"
            segments.append((text, f"[{self.ram_color()}]{text}[/]"))

        state = t.get("perf_state")
        if state:
            segments.append((str(state), f"[dim]{state}[/]"))

        codec = [(label, value) for label, value in
                 (("ENC", t.get("enc_percent")), ("DEC", t.get("dec_percent")))
                 if value]
        for label, value in codec:
            text = f"{label} {value:.0f}%"
            segments.append((text, f"[dim]{text}[/]"))

        return segments

    def create_telemetry_line(self, telemetry: dict, width: int) -> str:
        """Power/clock/thermal line, trimmed to ``width`` by dropping segments.

        Segments are dropped from the least important end, so a narrow panel
        keeps the throttle state and power draw rather than an arbitrary prefix.
        """
        if not telemetry:
            return ""
        return self.build_telemetry_line(self._telemetry_segments(telemetry), width)

    def create_center_bar(
        self, gpu_ram: float, gpu_usage: float, content_width: int
    ) -> str:
        """RAM | usage bar: one line, centre separator on the middle column.

        Sides use the same colours as the corresponding plot lines; RAM turns to the
        warning colour past 90% of the card's memory.
        """
        max_val = self.max_val or 1
        ram_fraction = gpu_ram / max_val
        return self.build_split_bar(
            content_width,
            left_fraction=ram_fraction,
            right_fraction=gpu_usage / 100,
            left_color=self.ram_color(warning=ram_fraction >= 0.9),
            right_color=self.usage_color(),
            left_label=format_size(gpu_ram, in_gb=True),
            right_label=f"{gpu_usage:.1f} %",
            # RAM reads as a gauge filling from the left edge towards the centre.
            left_from_centre=False,
        )

    def get_dual_plot(self, width: int, height: int) -> str:
        if not self.gpu_ram_history:
            return "No data yet..."
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)

        plot_width, plot_height = width, height
        plt.clear_figure()
        plt.plot_size(height=plot_height, width=plot_width)
        plt.theme("pro")

        # Plot GPU RAM as positive values and GPU Usage as negative
        positive_series = [x + 0.1 for x in self.gpu_usage_history]
        if not self.usage_is_available:
            positive_series = [-100 for x in self.gpu_usage_history]
        negative_series = [
            -100 + (x / self.max_val) * 100 for x in self.gpu_ram_history
        ]

        # Determine symmetric y-axis limits based on incoming data
        # max_value = 100
        # y_limit = max_value if max_value >= 10 else 10
        # # self.max_val = y_limit

        # if self.usage_is_available:
        plt.ylim(-100, 100)
        if not self.usage_is_available:
            plt.ylim(-100, 0)
        plt.plot(
            positive_series,
            marker="braille",
            label="Usage UNAV" if not self.usage_is_available else "Usage",
        )
        plt.plot(negative_series, marker="braille", label="RAM")
        if self.usage_is_available:
            plt.hline(0.0)
        plt.yfrequency(5)
        plt.xfrequency(0)

        current_yticks = [-100, -50, 0, 50, 100]
        plt.yticks(current_yticks, [0, 50, 100, 50, 100])
        if not self.usage_is_available:
            current_yticks = [-100, -75, -50, -25, 0]
            plt.yticks(current_yticks, [0, 25, 50, 75, 100])
        # Series order fixes the colours: usage is plotted first -> [blue], RAM -> [green].
        build = recolor(
            ansi2rich(plt.build()).replace("\x1b[0m", ""),
            {"blue": self.usage_color(), "green": self.ram_color()},
        )
        build = build.replace("-", " ")
        build = build.replace("──────┐", "─GB─%─┐")
        if len(self.gpu_ram_history) >= self.gpu_ram_history.maxlen:
            build = substitute_plot_timeframe(build, self.gpu_ram_history.maxlen)
        return self.finish_plot(build, plot_width, plot_height)

    def rerender(self) -> None:
        """Re-draw plot and bar from stored history at the current region sizes."""
        if not self.gpu_ram_history:
            return
        plot_width, plot_height = self.plot_region(
            "#history-plot", reserve_height=self.CHROME_HEIGHT
        )
        bar_width, _ = self.region_size("#current-value")
        try:
            telemetry_line = self.query_one("#gpu-telemetry")
        except NoMatches:
            telemetry_line = None

        # Give the row back to the plot on short panels, and re-measure once the
        # new split has been laid out.
        show_telemetry = bool(
            self._telemetry
            and self.content_size.height >= self.TELEMETRY_MIN_PANEL_HEIGHT
        )
        if telemetry_line is not None and show_telemetry != self._telemetry_shown:
            self._telemetry_shown = show_telemetry
            telemetry_line.display = show_telemetry
            self.call_after_refresh(self.rerender)

        try:
            self.query_one("#history-plot").update(self.get_dual_plot(plot_width, plot_height))
            self.query_one("#current-value").update(
                self.create_center_bar(
                    self.gpu_ram_history[-1], self.gpu_usage_history[-1], bar_width
                )
            )
            if telemetry_line is not None and show_telemetry:
                width, _ = self.region_size("#gpu-telemetry")
                telemetry_line.update(
                    self.create_telemetry_line(self._telemetry, width)
                )
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout switch)

    def set_signals_enabled(self, enabled: bool) -> None:
        """Enable/disable the per-process signal buttons on this panel.

        Called when entering/leaving Slurm job focus, where the process list
        describes a different machine than the one gc is running on.
        """
        self._signals_enabled = enabled
        try:
            self.query_one("#gpu-processes").set_signals_enabled(enabled)
        except NoMatches:
            pass  # DOM not ready yet; the flag is applied when rows are built

    def update_content(
        self, gpu_name, gpu_usage, mem_used, mem_total, processes=None,
        telemetry=None,
    ):
        """Update plot/bar and optionally the processes list. processes: list of dicts with pid, name, gpu_memory, username, command, script.

        ``telemetry`` is the raw per-GPU metric dict; the power/clock/thermal
        keys in it are optional and the line renders whatever is present.
        """
        if telemetry is not None:
            self._telemetry = telemetry
        if self.first:
            self.first = False
            return
        self.gpu_ram_history.append(mem_used)
        self.gpu_usage_history.append(gpu_usage)
        self.max_val = mem_total
        self.usage_is_available = gpu_usage >= -0.5
        n_proc = len(processes) if processes else 0
        logger.info(
            "gpu_name: %s, gpu_usage: %.1f, mem_used_gb: %.2f, mem_total_gb: %.2f, processes_count: %d",
            gpu_name, gpu_usage, mem_used, mem_total, n_proc,
        )
        self.rerender()
        if processes is not None:
            self._last_processes = processes
            try:
                self.query_one("#gpu-processes").update_processes(processes)
            except NoMatches:
                pass  # DOM not ready yet (e.g. after layout switch)

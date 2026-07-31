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
from ..utils.formatting import ansi2rich, format_size, recolor, substitute_plot_timeframe
from ..utils.colors import get_rich_color
import logging

logger = logging.getLogger("ground-control.gpu")


# Process text layout: bold labels, table-style fixed column widths so values align across rows.
# Column value widths (labels are bold in markup; spacing between columns = 2 spaces).
VAL_U_W, VAL_P_W, VAL_N_W, VAL_G_W, VAL_C_W, VAL_M_W = 10, 6, 14, 7, 6, 8
CMD_WRAP = 50
# Display widths for indent (label + space + value; Rich bold tags don't affect layout width).
W_N_COL = 3 + VAL_N_W   # "N: " + name
W_CMD_PREFIX = 5        # "Cmd: "
CMD_INDENT = 2 + W_N_COL + W_CMD_PREFIX  # spaces between cols + N column + "Cmd: "


class ProcessRow(Static):
    """One process row: 3-line process details (USER, PID, NAME, GPU MEM, COMMAND) + Kill / Term / Int buttons."""

    DEFAULT_CSS = """
    ProcessRow {
        height: 3;
        min-height: 3;
        padding: 0 1;
    }
    ProcessRow Horizontal {
        height: 3;
        align: center middle;
    }
    ProcessRow .process-info {
        width: 1fr;
        min-width: 0;
        height: 3;
        overflow: hidden;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ProcessRow .sigkill-btn {
        min-width: 5;
        height: 3;
        padding: 0 1;
        background: #c00;
        color: white;
    }
    ProcessRow .sigterm-btn {
        min-width: 5;
        height: 3;
        padding: 0 1;
        background: #e85d00;
        color: white;
    }
    ProcessRow .sigint-btn {
        min-width: 4;
        height: 3;
        padding: 0 1;
        background: #bb0;
        color: black;
    }
    """

    def __init__(self, process: dict, **kwargs):
        super().__init__(**kwargs)
        self._process = process

    def compose(self) -> ComposeResult:
        user = (self._process.get("username") or "-")[:VAL_U_W].ljust(VAL_U_W)
        pid = str(self._process.get("pid", ""))[:VAL_P_W].rjust(VAL_P_W)
        name = (self._process.get("name") or "?")[:VAL_N_W].ljust(VAL_N_W)
        gpu_mem = (self._process.get("gpu_memory") or "N/A")[:VAL_G_W].rjust(VAL_G_W)
        cpu = (self._process.get("cpu_percent") or "N/A")[:VAL_C_W].rjust(VAL_C_W)
        mem = (self._process.get("memory") or "N/A")[:VAL_M_W].rjust(VAL_M_W)
        full_cmd = self._process.get("script") or self._process.get("command") or ""
        # Table-style: bold labels, fixed-width values, 2 spaces between columns
        # Line 1: U: user  P: pid  GPU: gpu_mem  CPU: cpu%  MEM: mem
        line1 = (
            f"[bold]U:[/]  {user}  "
            f"[bold]P:[/]  {pid}  "
            f"[bold]GPU:[/] {gpu_mem}  "
            f"[bold]CPU:[/] {cpu}  "
            f"[bold]MEM:[/] {mem}"
        )
        # Line 2: N: name  Cmd: <first part of command>
        name_part = f"[bold]N:[/]  {name}  [bold]Cmd:[/] "
        cmd_part = full_cmd[:CMD_WRAP] + ("…" if len(full_cmd) > CMD_WRAP else "")
        line2 = name_part + cmd_part
        # Line 3: indented command continuation (align under command text)
        if len(full_cmd) <= CMD_WRAP:
            line3 = ""
        else:
            rest = full_cmd[CMD_WRAP : 2 * CMD_WRAP]
            line3 = " " * CMD_INDENT + rest + ("…" if len(full_cmd) > 2 * CMD_WRAP else "")
        row_text = line1 + "\n" + line2 + ("\n" + line3 if line3 else "")
        with Horizontal():
            yield Static(row_text.strip(), classes="process-info")
            yield Button("Kill", classes="sigkill-btn")
            yield Button("Term", classes="sigterm-btn")
            yield Button("Int", classes="sigint-btn")

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
    """Scrollable list of process rows; each row has its own K/T/I buttons."""

    DEFAULT_CSS = """
    GPUProcessList {
        height: 1fr;
        overflow-y: auto;
    }
    GPUProcessList #gpu-process-rows {
        height: auto;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_sig = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="gpu-process-rows")

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
            container.mount(ProcessRow(p))


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

    # Rows taken by the tab bar (tabs + underline) and the bar line under the plot;
    # only used to estimate the plot region before the first layout pass.
    CHROME_HEIGHT = 3

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
    ):
        """Initialise a GPU widget.

        Args:
            title: Widget border title (typically ``GPU @ <name>``).
            id: Optional Textual DOM id.
            color: Fallback plot color.
            history_size: Number of data-points to keep in the history deque.
            initial_tab: Tab pane id to show on mount (``"plot"`` or
                ``"processes"``).  Persisted across app restarts.
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

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=self._initial_tab, id="gpu-tabbed"):
            with TabPane("Plot", id="plot"):
                yield Static("", id="history-plot", classes="metric-plot gpu-plot-pane")
                yield Static("", id="current-value", classes="metric-value")
            with TabPane("Processes", id="processes"):
                yield GPUProcessList(id="gpu-processes", classes="gpu-processes-pane")

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
            self.query_one("#history-plot").update(self.get_dual_plot(plot_width, plot_height))
            self.query_one("#current-value").update(
                self.create_center_bar(
                    self.gpu_ram_history[-1], self.gpu_usage_history[-1], bar_width
                )
            )
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout switch)

    def update_content(
        self, gpu_name, gpu_usage, mem_used, mem_total, processes=None
    ):
        """Update plot/bar and optionally the processes list. processes: list of dicts with pid, name, gpu_memory, username, command, script."""
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

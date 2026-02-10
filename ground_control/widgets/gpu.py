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
from ..utils.formatting import ansi2rich, align
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
    BINDINGS = [
        ("p", "show_plot", "GPU: plot"),
        ("r", "show_processes", "GPU: processes"),
    ]

    DEFAULT_CSS = """
    GPUWidget {
        layout: vertical;
        overflow-y: auto;
    }
    #gpu-tabbed {
        height: 1fr;
    }
    .gpu-plot-pane {
        height: 1fr;
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

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=self._initial_tab, id="gpu-tabbed"):
            with TabPane("Plot", id="plot"):
                yield Static("", id="history-plot", classes="metric-plot gpu-plot-pane")
                yield Static("", id="current-value", classes="metric-value")
            with TabPane("Processes", id="processes"):
                yield GPUProcessList(id="gpu-processes", classes="gpu-processes-pane")

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

    def create_center_bar(
        self, gpu_ram: float, gpu_usage: float, total_width: int
    ) -> str:
        gpu_ram_withunits = align(f"{gpu_ram:.1f} GB", 12, "right")
        gpu_usage_withunits = align(f"{gpu_usage:.1f} %", 14, "left")
        aval_width = total_width
        half_width = aval_width // 2
        # Compute the percentage relative to the current maximum value
        gpu_ram_percent = min((gpu_ram / self.max_val) * 100, 100)
        gpu_usage_percent = gpu_usage

        ram_blocks = int((half_width * gpu_ram_percent) / 100)
        usage_blocks = int((half_width * gpu_usage_percent) / 100)

        gpu_ram_color = get_rich_color("gpu_ram", "#00FF00")
        gpu_usage_color = get_rich_color("gpu_usage", "#00FFFF")
        white_color = get_rich_color("white", "#FFFFFF")
        left_bar = (
            (
                f"[{gpu_ram_color}]{'█' * (ram_blocks-1)}{''}[/][{white_color}]{'─' * (half_width - ram_blocks)}[/]"
            )
            if ram_blocks >= 1
            else f"{'─' * half_width}"
        )
        right_bar = (
            (
                f"[{gpu_usage_color}]{'█' * (usage_blocks-3)}{''}[/]{'─' * (half_width - usage_blocks)}"
            )
            if usage_blocks >= 1
            else f"{'─' * half_width}"
        )

        if gpu_ram_percent >= 90:
            warning_color = get_rich_color("gpu_ram_warning", "#FF0000")
            gpu_ram_withunits = f"[{warning_color}]{gpu_ram_withunits}[/]"
            left_bar = left_bar.replace(f"[{gpu_ram_color}]", f"[{warning_color}]")
        return f"{gpu_ram_withunits} {left_bar}│{right_bar} {gpu_usage_withunits}"

    def get_dual_plot(self) -> str:
        if not self.gpu_ram_history:
            return "No data yet..."

        # Validate plot dimensions
        plot_height = max(1, getattr(self, "plot_height", 10))
        plot_width = max(10, getattr(self, "plot_width", 40))
        
        if plot_height <= 0 or plot_width <= 0:
            return "Initializing..."

        plt.clear_figure()
        plt.plot_size(height=plot_height-2, width=plot_width)
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
        gpu_ram_color = get_rich_color("gpu_plot_ram", "#00FF00")
        gpu_usage_color = get_rich_color("gpu_plot_usage", "#00FFFF")
        return (
            ansi2rich(plt.build())
            .replace("\x1b[0m", "")
            .replace("[blue]", f"[{gpu_usage_color}]")
            .replace("[green]", f"[{gpu_ram_color}]")
            .replace("-", " ")
            .replace("──────┐","─GB─%─┐")
            
        )

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
        total_width = (
            self.size.width
            - len(f"{mem_used:6.1f} % ")
            - len(f"{gpu_usage:6.1f} %")
            - 2
        )
        try:
            self.query_one("#history-plot").update(self.get_dual_plot())
            self.query_one("#current-value").update(
                self.create_center_bar(mem_used, gpu_usage, total_width=total_width)
            )
            if processes is not None:
                self.query_one("#gpu-processes").update_processes(processes)
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout switch)

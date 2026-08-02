import os
import math
import logging
from collections import deque
from textual.app import ComposeResult
from textual.widgets import Static, TabbedContent, TabPane
from textual import on
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
import psutil
from ..utils.formatting import ansi2rich, recolor, substitute_plot_timeframe
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.cpu")

# View modes: "all" | "affinity" | "user" (cores with current user's processes)
VIEW_MODES = ("all", "affinity", "user")
VIEW_LABELS = {"all": "All cores", "affinity": "Affinity", "user": "My processes"}

# Load ramp for the core heatmap. The character carries the magnitude as well
# as the colour, so the map still reads on a monochrome terminal -- the same
# reason the alert states use ▲/■ markers rather than colour alone.
HEAT_RAMP = ((0.05, "·"), (0.30, "░"), (0.55, "▒"), (0.80, "▓"), (1.01, "█"))


class CPUWidget(MetricWidget):
    """CPU panel: utilisation history, a per-core heatmap and a telemetry line.

    The three tabs filter *which cores* the heatmap shows; the history plot and
    the telemetry row are machine-wide and sit outside the tabs.
    """

    # Local keyboard bindings: active only when the CPU widget has focus.
    BINDINGS = [
        ("1", "view_all", "CPU: all cores"),
        ("2", "view_affinity", "CPU: affinity cores"),
        ("3", "view_user", "CPU: my cores"),
    ]

    # Rows taken by the tab bar (tabs + underline).
    TAB_BAR_HEIGHT = 2
    # Heatmap sizing. Cells are widened up to CELL_MAX when there is room, so a
    # 16-core box gets a readable map instead of a thin stripe, while a 256-core
    # node falls back to one cell per core.
    HEATMAP_MAX_ROWS = 4
    CELL_MAX = 3
    # Below this panel height the telemetry line gives its row back to the plot.
    # The floor is what the panel needs with the row present: a 3-row plot, the
    # tab bar, one heatmap row and the line itself. Above that the numbers are
    # worth more than a fifth plot row, so the threshold sits at the floor.
    TELEMETRY_MIN_PANEL_HEIGHT = 8

    DEFAULT_CSS = """
    CPUWidget {
        layout: vertical;
    }

    .metric-title {
        text-align: left;
    }

    #cpu-history-plot {
        height: 1fr;
    }

    /* Height is set from rerender() to exactly fit the heatmap rows: the map
       takes what it needs and the plot keeps everything else. */
    #cpu-tabbed {
        height: auto;
    }

    CPUWidget #cpu-telemetry {
        height: 1;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    """

    def __init__(self, title: str, id: str = None, initial_tab: str = "all",
                 history_size: int = 120):
        """Initialise the CPU widget.

        Args:
            title: Widget border title (typically CPU model name).
            id: Optional Textual DOM id.
            initial_tab: Tab pane id to show on mount (``"all"``, ``"affinity"``
                or ``"user"``).  Persisted across app restarts.
            history_size: Number of samples kept for the history plot.
        """
        super().__init__(title=title, id=id, history_size=history_size)
        self.title = title
        self.border_title = title
        # Clamp initial_tab to a valid mode
        if initial_tab in VIEW_MODES:
            self._view_mode_idx = VIEW_MODES.index(initial_tab)
        else:
            self._view_mode_idx = 0
        self._last_cpu_percentages = None
        # `history` (from MetricWidget) holds mean utilisation; the stall series
        # is iowait + steal, i.e. time that looks busy but does no work.
        self.stall_history = deque(maxlen=history_size)
        self._stall_available = False
        self._telemetry: dict = {}
        self._telemetry_shown = True
        self._tabbed_height = 0

    def _get_affinity_cpus(self):
        """Return list of CPU indices allowed for the current process, or None if unavailable."""
        try:
            return psutil.Process(os.getpid()).cpu_affinity()
        except (AttributeError, psutil.Error):
            return None

    def _get_user_cpus(self, n_cores: int):
        """
        Return sorted list of CPU indices that have run processes owned by the current user.
        Uses process username and cpu_num() (Linux; best-effort on other platforms).
        """
        try:
            current_user = psutil.Process(os.getpid()).username()
        except (AttributeError, psutil.Error):
            return None
        user_cpus = set()
        for proc in psutil.process_iter(attrs=["username"]):
            try:
                if proc.info.get("username") != current_user:
                    continue
                cpu_num = proc.cpu_num()
                if 0 <= cpu_num < n_cores:
                    user_cpus.add(cpu_num)
            # psutil.Error covers NoSuchProcess/ZombieProcess/AccessDenied. A
            # process exiting between process_iter() and cpu_num() is a race we
            # lose constantly on a busy box, and it is not an error: skip it.
            # Letting it escape used to disable the whole CPU panel.
            except (AttributeError, KeyError, psutil.Error):
                continue
        return sorted(user_cpus) if user_cpus else None

    def _get_display_for_mode(self, mode: str, cpu_percentages, n: int):
        """Return (display_percentages, core_indices) for the given mode.

        ``core_indices`` are the real CPU numbers behind the displayed values,
        so the heatmap gutter can name the cores a filtered view is showing.
        """
        if mode == "all":
            return cpu_percentages, list(range(n))
        if mode == "affinity":
            cpus = self._get_affinity_cpus()
            if not cpus:
                return cpu_percentages, list(range(n))
            valid = [i for i in cpus if 0 <= i < n]
            if not valid:
                return cpu_percentages, list(range(n))
            return [cpu_percentages[i] for i in valid], valid
        # "user"
        cpus = self._get_user_cpus(n)
        if not cpus:
            return cpu_percentages, list(range(n))
        return [cpu_percentages[i] for i in cpus], cpus

    def compose(self) -> ComposeResult:
        yield Static("", id="cpu-history-plot", classes="metric-plot")
        with TabbedContent(initial=VIEW_MODES[self._view_mode_idx], id="cpu-tabbed"):
            with TabPane(VIEW_LABELS["all"], id="all"):
                yield Static("", id="cpu-content-all", classes="cpu-metric-value")
            with TabPane(VIEW_LABELS["affinity"], id="affinity"):
                yield Static("", id="cpu-content-affinity", classes="cpu-metric-value")
            with TabPane(VIEW_LABELS["user"], id="user"):
                yield Static("", id="cpu-content-user", classes="cpu-metric-value")
        yield Static("", id="cpu-telemetry", classes="metric-value")

    def _set_view_mode(self, mode: str) -> None:
        """Programmatically switch to a given view mode tab."""
        if mode not in VIEW_MODES:
            return
        try:
            tabbed = self.query_one("#cpu-tabbed")
            tabbed.active = mode
        except Exception:
            # Safe no-op if the tab container is not mounted yet.
            return

    def action_view_all(self) -> None:
        """Switch CPU widget to 'All cores' tab."""
        self._set_view_mode("all")

    def action_view_affinity(self) -> None:
        """Switch CPU widget to 'Affinity' tab."""
        self._set_view_mode("affinity")

    def action_view_user(self) -> None:
        """Switch CPU widget to 'My processes' tab."""
        self._set_view_mode("user")

    @on(TabbedContent.TabActivated, "#cpu-tabbed")
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle view mode change when a tab is selected."""
        if event.pane is None:
            return
        mode = event.pane.id
        self._view_mode_idx = VIEW_MODES.index(mode)
        # The newly revealed pane only gets a region once it is active: redraw for it.
        self.call_after_refresh(self.rerender)

    # ------------------------------------------------------------------ colours

    @staticmethod
    def load_color(percent: float) -> str:
        """Colour a per-core load on the shared alert scale.

        Reuses the existing palette keys rather than introducing new ones, so
        every bundled theme colours the heatmap correctly with no extra work.
        """
        if percent >= 90:
            return get_rich_color("alert_crit", "#FF0000")
        if percent >= 70:
            return get_rich_color("alert_warn", "#FFA500")
        return get_rich_color("cpu_bar", "#0080FF")

    @staticmethod
    def stall_color() -> str:
        """Colour of the iowait+steal series: time that looks busy but is not."""
        return get_rich_color("alert_warn", "#FFA500")

    # ------------------------------------------------------------------ heatmap

    def heatmap_layout(self, n_cores: int, width: int, max_rows: int):
        """Choose ``(cell_width, per_row, rows, gutter)`` for ``n_cores``.

        Prefers wide cells, then adds rows, and only falls back to one cell per
        core when nothing else fits -- so the map degrades from "readable bar
        strip" to "dense pixel map" instead of dropping cores off the end.
        """
        width = max(0, int(width))
        max_rows = max(1, int(max_rows))
        if n_cores <= 0 or width <= 0:
            return 1, 0, 0, 0

        gutter = len(f"C{n_cores - 1}") + 1
        # A gutter is only worth its cells when something is left to draw in.
        if width - gutter < 4:
            gutter = 0
        avail = max(1, width - gutter)

        for rows in range(1, max_rows + 1):
            per_row = math.ceil(n_cores / rows)
            cell_w = min(self.CELL_MAX, avail // per_row)
            if cell_w >= 1:
                return cell_w, avail // cell_w, rows, gutter
        # Denser than the box allows: pack one cell per core and let the caller
        # report the overflow rather than silently truncating the core list.
        return 1, avail, max_rows, gutter

    def create_heatmap(self, percentages, core_indices, width: int, height: int) -> str:
        """One cell per core, shaded and coloured by load.

        Unlike the per-core bar chart this replaces, the cost of a core is one
        cell rather than one row, so a 128-core node fits in the same space a
        16-core one used and no core is ever dropped without saying so.
        """
        width, height = int(width), int(height)
        if width <= 0 or height <= 0 or not percentages:
            return ""

        n = len(percentages)
        cell_w, per_row, rows, gutter = self.heatmap_layout(n, width, height)
        if per_row <= 0:
            return ""

        drawn = min(n, per_row * rows)
        # Rows are built as cell lists first so the "+N" note below can take
        # trailing cells back without anything having to be re-rendered.
        grid = []
        for row in range(rows):
            start = row * per_row
            if start >= drawn:
                break
            chunk = percentages[start:min(start + per_row, drawn)]
            cells = []
            for value in chunk:
                fraction = min(max(float(value), 0.0), 100.0) / 100.0
                char = next(c for limit, c in HEAT_RAMP if fraction < limit)
                run = char * cell_w
                color = "dim" if value < 5 else self.load_color(value)
                cells.append(f"[{color}]{run}[/]")
            first = core_indices[start] if start < len(core_indices) else start
            grid.append([first, cells])

        note = ""
        if drawn < n and grid:
            # Hiding cores is acceptable when the panel is genuinely too small;
            # hiding the fact that they are hidden is not. The count buys its
            # own space out of the last row until it fits.
            last = grid[-1][1]
            while True:
                used = gutter + len(last) * cell_w
                candidate = f"+{n - drawn}"
                if used + len(candidate) + 1 <= width:
                    note = candidate
                    break
                if not last:
                    break
                last.pop()
                drawn -= 1

        lines = []
        for index, (first, cells) in enumerate(grid):
            used = gutter + len(cells) * cell_w
            label = f"[dim]{f'C{first}':<{gutter}}[/]" if gutter else ""
            line = label + "".join(cells)
            if note and index == len(grid) - 1:
                line += f" [dim]{note}[/]"
                used += len(note) + 1
            lines.append(line + " " * max(0, width - used))

        return "\n".join(lines)

    # ------------------------------------------------------------------ telemetry

    def _telemetry_segments(self, t: dict) -> list:
        """(text, markup) pairs for the telemetry line, most important first.

        Anything the platform does not report is omitted rather than printed as
        "N/A" -- a row of placeholders costs the same width as real data and
        tells the reader nothing.
        """
        segments = []
        percentages = self._last_cpu_percentages or []

        # Headline first, so it is the last thing dropped on a narrow panel.
        if percentages:
            avg = sum(percentages) / len(percentages)
            text = f"avg {avg:.0f}%"
            segments.append((text, f"[{self.load_color(avg)}]{text}[/]"))
            peak = max(percentages)
            # A single pegged core beside idle ones is a serialised workload
            # (GIL-bound Python, a single-threaded preprocessing stage) -- the
            # average alone hides it completely.
            text = f"pk {peak:.0f}%"
            segments.append((text, f"[{self.load_color(peak)}]{text}[/]"))

        throttled = t.get("cgroup_throttled_percent")
        if throttled:
            # The container-era equivalent of a GPU power cap: the kernel is
            # taking the CPU away at the quota boundary, and utilisation alone
            # will never show it.
            severe = throttled >= 25
            color = (get_rich_color("alert_crit", "#FF0000") if severe
                     else get_rich_color("alert_warn", "#FFA500"))
            text = f"{'■' if severe else '▲'} throttled {throttled:.0f}%"
            segments.append((text, f"[{color}]{text}[/]"))

        steal = t.get("steal_percent")
        if steal and steal >= 0.5:
            # Time the hypervisor gave to somebody else's VM. Never normal.
            severe = steal >= 10
            color = (get_rich_color("alert_crit", "#FF0000") if severe
                     else get_rich_color("alert_warn", "#FFA500"))
            text = f"{'■' if severe else '▲'} steal {steal:.0f}%"
            segments.append((text, f"[{color}]{text}[/]"))

        iowait = t.get("iowait_percent")
        if iowait and iowait >= 0.5:
            # High iowait under a "100% busy" training loop means the GPUs are
            # being starved by the input pipeline, not that the CPU is working.
            text = f"io {iowait:.0f}%"
            segments.append((text, f"[{self.stall_color()}]{text}[/]"))

        load, per_core = t.get("load_1"), t.get("load_per_core")
        if load is not None:
            cores = len(percentages)
            # Two decimals matter near idle and are noise at load 48.
            shown = f"{load:.2f}" if load < 10 else f"{load:.0f}"
            text = f"load {shown}/{cores}" if cores else f"load {shown}"
            if per_core is not None and per_core >= 1.0:
                color = (get_rich_color("alert_crit", "#FF0000") if per_core >= 2.0
                         else get_rich_color("alert_warn", "#FFA500"))
                segments.append((text, f"[{color}]{text}[/]"))
            else:
                segments.append((text, text))

        psi = t.get("psi_some_avg10")
        if psi is not None:
            # Share of the last 10s in which some task was runnable but had no
            # CPU to run on: saturation measured directly rather than inferred.
            text = f"psi {psi:.0f}%"
            if psi >= 20:
                color = (get_rich_color("alert_crit", "#FF0000") if psi >= 50
                         else get_rich_color("alert_warn", "#FFA500"))
                segments.append((text, f"[{color}]{text}[/]"))
            else:
                segments.append((text, f"[dim]{text}[/]"))

        freq, freq_max = t.get("freq_mhz"), t.get("freq_max_mhz")
        if freq is not None:
            if freq_max:
                text = f"{freq / 1000:.1f}/{freq_max / 1000:.1f}GHz"
            else:
                text = f"{freq / 1000:.1f}GHz"
            segments.append((text, text))

        quota = t.get("cgroup_quota_cores")
        if quota:
            text = f"quota {quota:g}c"
            segments.append((text, f"[dim]{text}[/]"))

        user, system = t.get("user_percent"), t.get("system_percent")
        if user is not None and system is not None:
            # Kernel time far above user time is a syscall or interrupt storm,
            # a different problem from a genuinely busy application.
            text = f"u{user:.0f}/s{system:.0f}"
            segments.append((text, f"[dim]{text}[/]"))

        ctx = t.get("ctx_switches_per_s")
        if ctx:
            text = f"ctx {ctx / 1000:.0f}k/s" if ctx >= 1000 else f"ctx {ctx:.0f}/s"
            segments.append((text, f"[dim]{text}[/]"))

        running, total = t.get("procs_running"), t.get("procs_total")
        if running is not None and total is not None:
            text = f"run {running}/{total}"
            segments.append((text, f"[dim]{text}[/]"))

        return segments

    def create_telemetry_line(self, width: int) -> str:
        """Saturation/stall row, trimmed to ``width`` by dropping segments."""
        if not self._telemetry and not self._last_cpu_percentages:
            return ""
        return self.build_telemetry_line(
            self._telemetry_segments(self._telemetry or {}), width
        )

    # ---------------------------------------------------------------------- plot

    def create_history_plot(self, width: int, height: int) -> str:
        """Mean utilisation over time, with stall time (iowait+steal) beneath it.

        The panel previously showed only an instantaneous snapshot, so a spike
        that happened while the user was looking elsewhere left no trace. The
        second series answers the follow-up question -- whether that busy time
        was doing work or waiting.
        """
        if not self.history:
            return "No data yet..."
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)

        plt.clear_figure()
        plt.plot_size(width=width, height=height)
        plt.theme("pro")
        plt.ylim(0, 100)
        plt.xfrequency(0)
        plt.yfrequency(3)
        # Series order fixes plotext's palette: first -> blue, second -> green.
        plt.plot(list(self.history), marker="braille")
        if self._stall_available:
            plt.plot(list(self.stall_history), marker="braille")
        # No legend: the telemetry line already names these numbers in their
        # plot colours, and plotext's legend renderer raises IndexError at some
        # panel geometries.
        try:
            build = recolor(
                ansi2rich(plt.build()).replace("\x1b[0m", "").replace("\x1b[1m", ""),
                {"blue": get_rich_color("cpu_bar", "#0080FF"),
                 "green": self.stall_color()},
            ).replace("──────┐", "────%─┐")
        except (ValueError, IndexError, TypeError):
            return self.too_small_text(width, height)
        if len(self.history) >= self.history.maxlen:
            build = substitute_plot_timeframe(build, self.history.maxlen)
        return self.finish_plot(build, width, height)

    # -------------------------------------------------------------------- layout

    def _max_heatmap_rows(self, panel_height: int, telemetry_rows: int) -> int:
        """Most rows the heatmap may claim while leaving the plot a usable region."""
        spare = panel_height - self.TAB_BAR_HEIGHT - telemetry_rows - self.MIN_PLOT_HEIGHT
        return max(1, min(self.HEATMAP_MAX_ROWS, spare))

    def rerender(self) -> None:
        """Re-draw plot, heatmap and telemetry at the current region sizes."""
        if self._last_cpu_percentages is None:
            return

        panel_height = self.content_size.height
        show_telemetry = panel_height >= self.TELEMETRY_MIN_PANEL_HEIGHT
        try:
            telemetry_line = self.query_one("#cpu-telemetry")
        except NoMatches:
            telemetry_line = None
        if telemetry_line is not None and show_telemetry != self._telemetry_shown:
            self._telemetry_shown = show_telemetry
            telemetry_line.display = show_telemetry
            # The split has changed; measure again once it has been laid out.
            self.call_after_refresh(self.rerender)

        cpu_percentages = self._last_cpu_percentages
        n = len(cpu_percentages)
        active = VIEW_MODES[self._view_mode_idx]
        # Only the active pane has a region (Textual gives hidden panes zero
        # size) and all three share the same geometry, so it defines the canvas.
        map_width, _ = self.region_size(f"#cpu-content-{active}")
        # Claim only the rows the map actually needs -- a 20-core box wants one
        # row and the plot should have the rest. Row count depends on the width
        # but not on the height, so measuring before the resize is safe. The
        # "all" view is the widest case; the filtered tabs are subsets of it.
        max_rows = self._max_heatmap_rows(panel_height, 1 if show_telemetry else 0)
        _, _, rows, _ = self.heatmap_layout(n, map_width, max_rows)
        rows = max(1, rows)

        tabbed_height = rows + self.TAB_BAR_HEIGHT
        try:
            tabbed = self.query_one("#cpu-tabbed")
            if tabbed_height != self._tabbed_height:
                self._tabbed_height = tabbed_height
                tabbed.styles.height = tabbed_height
                self.call_after_refresh(self.rerender)
        except NoMatches:
            pass

        plot_width, plot_height = self.plot_region(
            "#cpu-history-plot",
            reserve_height=tabbed_height + (1 if show_telemetry else 0),
        )

        for mode in VIEW_MODES:
            display_percentages, core_indices = self._get_display_for_mode(
                mode, cpu_percentages, n
            )
            heatmap = self.create_heatmap(
                display_percentages, core_indices, map_width, rows
            )
            try:
                self.query_one(f"#cpu-content-{mode}").update(heatmap)
            except NoMatches:
                pass  # DOM not ready yet (e.g. after layout change)

        try:
            self.query_one("#cpu-history-plot").update(
                self.create_history_plot(plot_width, plot_height)
            )
        except NoMatches:
            pass
        if telemetry_line is not None and show_telemetry:
            width, _ = self.region_size("#cpu-telemetry")
            telemetry_line.update(self.create_telemetry_line(width))

    def update_content(self, cpu_percentages, cpu_freqs=None, mem_percent=None,
                       telemetry=None):
        """Store a fresh sample and redraw.

        ``cpu_freqs`` and ``mem_percent`` are accepted for call compatibility;
        frequency now reaches the panel through ``telemetry``, which carries
        the load, stall and throttling figures as optional keys.
        """
        self._last_cpu_percentages = cpu_percentages
        if telemetry is not None:
            self._telemetry = telemetry

        n = len(cpu_percentages)
        avg = sum(cpu_percentages) / n if n else 0.0
        max_cpu = max(cpu_percentages) if cpu_percentages else 0.0
        self.history.append(avg)

        t = self._telemetry or {}
        iowait, steal = t.get("iowait_percent"), t.get("steal_percent")
        if iowait is not None or steal is not None:
            self._stall_available = True
        # Appended unconditionally: the two series are plotted against the same
        # x-axis, so a source that starts reporting mid-session must not leave
        # the stall line shorter and therefore shifted in time.
        self.stall_history.append((iowait or 0.0) + (steal or 0.0))

        logger.info(
            "cpu_percent_avg: %.1f, cpu_percent_max: %.1f, n_cores: %d, "
            "load_1: %s, iowait: %s, steal: %s, throttled: %s",
            avg, max_cpu, n, t.get("load_1"), iowait, steal,
            t.get("cgroup_throttled_percent"),
        )
        self.rerender()

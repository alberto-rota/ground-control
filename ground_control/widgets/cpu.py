import os
import logging
from textual.app import ComposeResult
from textual.widgets import Static, TabbedContent, TabPane
from textual import on
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
import psutil
from ..utils.formatting import ansi2rich, fit_lines, pad_markup, recolor
from ..utils.colors import get_rich_color

logger = logging.getLogger("ground-control.cpu")

# View modes: "all" | "affinity" | "user" (cores with current user's processes)
VIEW_MODES = ("all", "affinity", "user")
VIEW_LABELS = {"all": "All cores", "affinity": "Affinity", "user": "My processes"}

class CPUWidget(MetricWidget):
    """CPU usage display widget with tabbed views (All / Affinity / User)."""

    # Local keyboard bindings: active only when the CPU widget has focus.
    BINDINGS = [
        ("1", "view_all", "CPU: all cores"),
        ("2", "view_affinity", "CPU: affinity cores"),
        ("3", "view_user", "CPU: my cores"),
    ]

    # Rows taken by the tab bar (tabs + underline); only used to estimate the chart
    # region before the first layout pass.
    TAB_BAR_HEIGHT = 2
    # Narrowest column that still shows a core label plus a few bar cells. Cores that
    # do not fit in width // MIN_GROUP_WIDTH columns are not drawn (a 3-cell column
    # would be label-only anyway).
    MIN_GROUP_WIDTH = 8

    DEFAULT_CSS = """
    CPUWidget {
        layout: vertical;
    }

    .metric-title {
        text-align: left;
    }

    #cpu-tabbed {
        height: 1fr;
    }
    """

    def __init__(self, title: str, id: str = None, initial_tab: str = "all"):
        """Initialise the CPU widget.

        Args:
            title: Widget border title (typically CPU model name).
            id: Optional Textual DOM id.
            initial_tab: Tab pane id to show on mount (``"all"``, ``"affinity"``
                or ``"user"``).  Persisted across app restarts.
        """
        super().__init__(title=title, id=id)
        self.title = title
        self.border_title = title
        # Clamp initial_tab to a valid mode
        if initial_tab in VIEW_MODES:
            self._view_mode_idx = VIEW_MODES.index(initial_tab)
        else:
            self._view_mode_idx = 0
        self._last_cpu_percentages = None
        self._last_cpu_freqs = None
        self._last_mem_percent = None

    def _get_affinity_cpus(self):
        """Return list of CPU indices allowed for the current process, or None if unavailable."""
        try:
            return psutil.Process(os.getpid()).cpu_affinity()
        except (AttributeError, PermissionError):
            return None

    def _get_user_cpus(self, n_cores: int):
        """
        Return sorted list of CPU indices that have run processes owned by the current user.
        Uses process username and cpu_num() (Linux; best-effort on other platforms).
        """
        try:
            current_user = psutil.Process(os.getpid()).username()
        except (AttributeError, PermissionError):
            return None
        user_cpus = set()
        for proc in psutil.process_iter(attrs=["username"]):
            try:
                if proc.info.get("username") != current_user:
                    continue
                cpu_num = proc.cpu_num()
                if 0 <= cpu_num < n_cores:
                    user_cpus.add(cpu_num)
            except (AttributeError, PermissionError, KeyError):
                continue
        return sorted(user_cpus) if user_cpus else None

    def _get_display_for_mode(self, mode: str, cpu_percentages, n: int):
        """Return (display_percentages, labels_override) for the given mode."""
        if mode == "all":
            return cpu_percentages, None
        if mode == "affinity":
            cpus = self._get_affinity_cpus()
            if not cpus:
                return cpu_percentages, None
            valid = [i for i in cpus if 0 <= i < n]
            if not valid:
                return cpu_percentages, None
            return [cpu_percentages[i] for i in valid], [f" C{i}" for i in valid]
        # "user"
        cpus = self._get_user_cpus(n)
        if not cpus:
            return cpu_percentages, None
        return [cpu_percentages[i] for i in cpus], [f" C{i}" for i in cpus]

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=VIEW_MODES[self._view_mode_idx], id="cpu-tabbed"):
            with TabPane(VIEW_LABELS["all"], id="all"):
                yield Static("", id="cpu-content-all", classes="cpu-metric-value")
            with TabPane(VIEW_LABELS["affinity"], id="affinity"):
                yield Static("", id="cpu-content-affinity", classes="cpu-metric-value")
            with TabPane(VIEW_LABELS["user"], id="user"):
                yield Static("", id="cpu-content-user", classes="cpu-metric-value")

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

    def _refresh_display(self) -> None:
        """Re-render the chart from last stored data using current view mode."""
        self.rerender()

    def create_bar_chart(self, cpu_percentages, cpu_freqs, mem_percent, width, height, labels_override=None):
        """
        Build a bar chart for CPU usage, drawn on a canvas of exactly (width, height).
        If labels_override is provided (e.g. affinity core indices), those labels are
        used instead of 0..N.
        """
        cpu_percentages = [int(x) for x in cpu_percentages]
        labels = labels_override if labels_override is not None else [f" C{i}" for i in range(len(cpu_percentages))]
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)
        if len(cpu_percentages) + 2 <= height - 2:
            plt.clear_figure()
            plt.theme("pro")
            orientation = "v"
            plt.ylim(6, 100)
            plt.plot_size(width=width, height=height)
            plt.bar(labels, list(cpu_percentages), orientation=orientation)
            cpu_bar_color = get_rich_color("cpu_bar", "#0080FF")
            try:
                cpubars = recolor(
                    ansi2rich(plt.build()).replace("\x1b[0m", "").replace("\x1b[1m", ""),
                    {"blue": cpu_bar_color},
                ).replace("──────┐", "────%─┐")
            except (ValueError, IndexError, TypeError):
                cpubars = "\n".join(f"{l}: {v}%" for l, v in zip(labels, list(cpu_percentages)))

            
            # plt.clear_figure()
            # plt.theme("pro")
            # plt.plot_size(width=width+1, height=4)
            # plt.xticks([1, 25, 50, 75, 100], ["0", "25", "50", "75", "100"])
            # plt.xlim(5, 100)
            # plt.bar(["RAM"], [mem_percent], orientation="h")
            # rambars = ansi2rich(plt.build()).replace("blue","orange1").replace("──────┐","────%─┐")

            return self.finish_plot(cpubars, width, height)
        else:
            # Group CPU cores into side-by-side columns so the chart never grows
            # taller than the region: each group chart is len(group) + 2 rows
            # (2 rows of axis), hence at most `height` rows.
            max_rows = max(1, height - 2)
            groups = [cpu_percentages[i:i+max_rows] for i in range(0, len(cpu_percentages), max_rows)]
            # Plotext needs ~MIN_GROUP_WIDTH columns per group for the core label and a
            # visible bar; showing fewer complete columns beats squeezing every core
            # into a column too narrow to draw (which plotext would silently widen).
            max_groups = max(1, width // self.MIN_GROUP_WIDTH)
            if len(groups) > max_groups:
                dropped = sum(len(g) for g in groups[max_groups:])
                logger.debug("CPU chart: %d core(s) not shown at %dx%d", dropped, width, height)
                groups = groups[:max_groups]
            num_groups = len(groups)
            # Divide available width among the groups.
            group_width = max(1, width // num_groups)
            group_charts = []
            for idx, group in enumerate(groups):
                group = list(group)
                if not group:
                    continue
                plt.clear_figure()
                plt.theme("pro")
                chart_height = len(group) + 2
                plt.plot_size(width=max(1, group_width), height=chart_height)
                plt.xfrequency(0)
                plt.xlim(6, 100)
                start_index = idx * max_rows
                group_labels = labels_override[start_index:start_index + len(group)] if labels_override else [f" C{start_index + i}" for i in range(len(group))]
                group_labels = group_labels[: len(group)]
                plt.bar(group_labels, group, orientation="h")
                cpu_bar_color = get_rich_color("cpu_bar", "#0080FF")
                try:
                    chart_str = recolor(
                        ansi2rich(plt.build()).replace("\x1b[0m", "").replace("\x1b[1m", ""),
                        {"blue": cpu_bar_color},
                    )
                except (ValueError, IndexError, TypeError):
                    chart_str = "\n".join(f" C{start_index + i}: {v}%" for i, v in enumerate(group))
                # Clip each column before stacking them side by side, so the combined
                # line width stays num_groups * group_width.
                chart_str = fit_lines(chart_str, chart_height)
                group_charts.append(
                    "\n".join(pad_markup(line, group_width) for line in chart_str.split("\n"))
                )
            # Combine the group charts horizontally.
            if not group_charts:
                return "CPU chart unavailable"
            group_lines = [chart.splitlines() for chart in group_charts]
            max_lines = max(len(lines) for lines in group_lines)
            for lines in group_lines:
                while len(lines) < max_lines:
                    lines.append(" " * group_width)  # short columns padded to align rows
            combined_lines = []
            for i in range(max_lines):
                combined_line = "".join(lines[i] for lines in group_lines)
                combined_lines.append(combined_line)
            combined_cpu_chart = "\n".join(combined_lines)
            return self.finish_plot(combined_cpu_chart, width, height)

    def rerender(self) -> None:
        """Re-draw every pane's chart at the size of the active pane's region.

        Only the active pane has a region (Textual gives hidden panes zero size) and
        all three panes share the same geometry, so the active one defines the canvas.
        """
        if self._last_cpu_percentages is None:
            return
        active = VIEW_MODES[self._view_mode_idx]
        width, height = self.plot_region(
            f"#cpu-content-{active}", reserve_height=self.TAB_BAR_HEIGHT
        )
        cpu_percentages = self._last_cpu_percentages
        n = len(cpu_percentages)
        for mode in VIEW_MODES:
            display_percentages, labels_override = self._get_display_for_mode(mode, cpu_percentages, n)
            chart = self.create_bar_chart(
                display_percentages,
                self._last_cpu_freqs,
                self._last_mem_percent,
                width,
                height,
                labels_override=labels_override,
            )
            try:
                self.query_one(f"#cpu-content-{mode}").update(chart)
            except NoMatches:
                pass  # DOM not ready yet (e.g. after layout change)

    def update_content(self, cpu_percentages, cpu_freqs, mem_percent):
        """Update the CPU widget: store data and refresh the chart in each tab pane."""
        self._last_cpu_percentages = cpu_percentages
        self._last_cpu_freqs = cpu_freqs
        self._last_mem_percent = mem_percent

        n = len(cpu_percentages)
        avg = sum(cpu_percentages) / n if n else 0.0
        max_cpu = max(cpu_percentages) if cpu_percentages else 0.0
        logger.info(
            "cpu_percent_avg: %.1f, cpu_percent_max: %.1f, n_cores: %d, mem_percent: %.1f",
            avg, max_cpu, n, mem_percent,
        )
        self.rerender()

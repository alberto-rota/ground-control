import os
import logging
from textual.app import ComposeResult
from textual.widgets import Static, TabbedContent, TabPane
from textual import on
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
import psutil
from ..utils.formatting import ansi2rich
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

    DEFAULT_CSS = """
    CPUWidget {
        height: 100%;
        border: solid green;
        background: $surface;
        layout: vertical;
        overflow-y: auto;
    }
    
    .metric-title {
        text-align: left;
    }
    
    .cpu-metric-value {
        height: 1fr;
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
        n = len(self._last_cpu_percentages) if self._last_cpu_percentages else 0
        if mode == "all":
            msg = f"Showing all {n} cores."
        elif mode == "affinity":
            aff = self._get_affinity_cpus()
            k = len(aff) if aff else n
            msg = f"Showing {k} affinity core(s)." if aff else "Affinity not available; showing all cores."
        else:
            user_cpus = self._get_user_cpus(n) if n else None
            k = len(user_cpus) if user_cpus else 0
            msg = f"Showing {k} core(s) with your processes." if user_cpus else "No user cores found; showing all cores."

    def _refresh_display(self) -> None:
        """Re-render the chart from last stored data using current view mode."""
        if self._last_cpu_percentages is None:
            return
        self.update_content(
            self._last_cpu_percentages,
            self._last_cpu_freqs,
            self._last_mem_percent,
        )

    def create_bar_chart(self, cpu_percentages, cpu_freqs, mem_percent, width, height, labels_override=None):
        """
        Build a bar chart for CPU usage. If labels_override is provided (e.g. affinity core indices),
        those labels are used instead of 0..N.
        """
        cpu_percentages = [int(x) for x in cpu_percentages]
        labels = labels_override if labels_override is not None else [f" C{i}" for i in range(len(cpu_percentages))]
        if len(cpu_percentages) + 2 <= height-2:
            plt.clear_figure()
            plt.theme("pro")
            if len(cpu_percentages) + 2 <= height-2:
                orientation = "v" 
                plt.ylim(6, 100)
                plt.plot_size(width=width+1, height=height+2)
                
                # plt.yfrequency(0)
                
            else:
                orientation = "h"
                plt.xlim(6, 100)
                plt.xfrequency(0)
                plt.plot_size(width=width, height=len(cpu_percentages) + 2)
                
            plt.bar(labels, list(cpu_percentages), orientation=orientation)
            cpu_bar_color = get_rich_color("cpu_bar", "#0080FF")
            try:
                cpubars = (ansi2rich(plt.build())
                          .replace("\x1b[0m", "")
                          .replace("\x1b[1m", "")
                          .replace("[blue]", f"[{cpu_bar_color}]")
                          .replace("──────┐","────%─┐"))
            except (ValueError, IndexError, TypeError):
                cpubars = "\n".join(f"{l}: {v}%" for l, v in zip(labels, list(cpu_percentages)))

            
            # plt.clear_figure()
            # plt.theme("pro")
            # plt.plot_size(width=width+1, height=4)
            # plt.xticks([1, 25, 50, 75, 100], ["0", "25", "50", "75", "100"])
            # plt.xlim(5, 100)
            # plt.bar(["RAM"], [mem_percent], orientation="h")
            # rambars = ansi2rich(plt.build()).replace("blue","orange1").replace("──────┐","────%─┐")

            return cpubars#+ rambars
        else:
            # Group CPU cores to avoid an overly tall chart.
            # Maximum rows per group is the available height minus 2 (for borders/margins).
            max_rows = height
            groups = [cpu_percentages[i:i+max_rows] for i in range(0, len(cpu_percentages), max_rows)]
            num_groups = len(groups)
            # Divide available width among the groups (with a minimum width).
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
                    chart_str = (ansi2rich(plt.build())
                                .replace("\x1b[0m", "")
                                .replace("\x1b[1m", "")
                                .replace("[blue]", f"[{cpu_bar_color}]"))
                except (ValueError, IndexError, TypeError):
                    chart_str = "\n".join(f" C{start_index + i}: {v}%" for i, v in enumerate(group))
                group_charts.append(chart_str)
            # Combine the group charts horizontally.
            if not group_charts:
                return "CPU chart unavailable"
            group_lines = [chart.splitlines() for chart in group_charts]
            max_lines = max(len(lines) for lines in group_lines)
            for lines in group_lines:
                while len(lines) < max_lines:
                    lines.append(" " * group_width)
            combined_lines = []
            for i in range(max_lines):
                combined_line = "".join(lines[i] for lines in group_lines)
                combined_lines.append(combined_line)
            combined_cpu_chart = "\n".join(combined_lines)
            

            
            return combined_cpu_chart#+"\n"+ rambars

    def update_content(self, cpu_percentages, cpu_freqs, mem_percent):
        """Update the CPU widget: store data and refresh the plot in each tab pane."""
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
        # Clamp so plotext gets valid size when layout has not run yet (e.g. after layout change)
        width = max(10, (self.size.width or 0) - 1)
        height = max(4, (self.size.height or 0) - 4)  # border + tab bar

        for mode in VIEW_MODES:
            display_percentages, labels_override = self._get_display_for_mode(mode, cpu_percentages, n)
            chart = self.create_bar_chart(
                display_percentages,
                cpu_freqs,
                mem_percent,
                width,
                height,
                labels_override=labels_override,
            )
            try:
                self.query_one(f"#cpu-content-{mode}").update(chart)
            except NoMatches:
                pass  # DOM not ready yet (e.g. after layout change)

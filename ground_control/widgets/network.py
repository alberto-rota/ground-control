import logging
from collections import deque
from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static, TabbedContent, TabPane
from textual.css.query import NoMatches
from .base import MetricWidget
import plotext as plt
from ..utils.formatting import (
    ansi2rich,
    format_throughput,
    recolor,
    substitute_plot_timeframe,
)
from ..utils.colors import get_rich_color
from ..utils.network_details import EXPOSURE_ALL, EXPOSURE_LOOPBACK

logger = logging.getLogger("ground-control.network")


# ------------------------------------------------------------------ tables
#
# Same idea as the GPU process rows: fixed-width columns so values line up,
# dropped lowest-priority-first when the panel narrows, every line exactly
# `width` cells. Cells are (plain text, style) so padding is measured on the
# text and colour is applied afterwards.
#
# (key, header, width, align, priority) -- None priority is never dropped.
LINK_COLUMNS = (
    ("name", "IF", 12, "left", None),
    ("state", "LINK", 4, "left", None),
    ("ipv4", "ADDRESS", 15, "left", 4),
    ("speed", "SPEED", 5, "right", 1),
    ("rx", "RX", 10, "right", 3),
    ("tx", "TX", 10, "right", 2),
)
PORT_COLUMNS = (
    ("port", "PORT", 5, "right", None),
    # PROTO outlives PID: without it the tcp and tcp6 sockets of one service
    # read as a duplicated row, while the service column already names the
    # process the pid would identify.
    ("proto", "PROTO", 5, "left", 3),
    ("bind", "BIND", 12, "left", None),
    ("pid", "PID", 7, "right", 2),
    ("user", "USER", 9, "left", 1),
)
TABLE_GAP = 1
# The last column (the service) takes what is left, if at least this much.
FLEX_MIN_WIDTH = 10


def _fit_columns(columns, width: int, flex_min: int = 0):
    def used(cols):
        return sum(c[2] for c in cols) + TABLE_GAP * max(len(cols) - 1, 0)

    columns = list(columns)
    while used(columns) + (flex_min + TABLE_GAP if flex_min else 0) > width:
        droppable = [c for c in columns if c[4] is not None]
        if not droppable:
            break
        columns.remove(min(droppable, key=lambda c: c[4]))
    return columns, used(columns)


def _cell(text: str, width: int, align: str) -> str:
    text = text if len(text) <= width else text[: max(width - 1, 0)] + "…"
    return text.rjust(width) if align == "right" else text.ljust(width)


def format_table_line(cells: dict, columns, width: int, flex=None,
                      header: bool = False) -> str:
    """One row (or the header) as exactly ``width`` cells of markup.

    ``cells`` maps column key -> ``(text, style)``; ``flex`` is the same for
    the trailing free-width column, or its header label.
    """
    width = int(width)
    if width <= 0:
        return ""
    kept, used = _fit_columns(columns, width, FLEX_MIN_WIDTH if flex else 0)
    parts, plain_len = [], 0
    for key, label, col_width, align, _ in kept:
        col_width = min(col_width, width - plain_len)
        if col_width <= 0:
            break
        text, style = (label, "bold") if header else cells.get(key, ("", None))
        padded = _cell(str(text), col_width, align)
        parts.append(f"[{style}]{escape(padded)}[/]" if style else escape(padded))
        plain_len += col_width
        if plain_len < width:
            parts.append(" " * TABLE_GAP)
            plain_len += TABLE_GAP
    if flex is not None and width - plain_len >= FLEX_MIN_WIDTH:
        text, style = (flex, "bold") if header else flex
        padded = _cell(str(text), width - plain_len, "left")
        parts.append(f"[{style}]{escape(padded)}[/]" if style else escape(padded))
        plain_len = width
    return "".join(parts) + " " * max(width - plain_len, 0)


def _speed_label(mbit) -> str:
    if not mbit:
        return "-"
    return f"{mbit / 1000:g}G" if mbit >= 1000 else f"{mbit}M"


def format_link_lines(interfaces, width: int) -> list:
    """Header plus one line per network interface."""
    lines = [format_table_line({}, LINK_COLUMNS, width, header=True)]
    up_color = get_rich_color("network_upload", "#00FF00")
    for iface in interfaces or []:
        up = iface.get("is_up")
        kind = iface.get("kind")
        name = iface.get("name") or "?"
        if kind == "infiniband":
            name = f"{name} ib"
        rx, tx = iface.get("rx_mbps"), iface.get("tx_mbps")
        cells = {
            "name": (name, "bold" if up and kind != "virtual" else ("dim" if not up else None)),
            "state": ("up" if up else "down" if up is False else "?",
                      up_color if up else "dim"),
            "ipv4": (", ".join(iface.get("ipv4") or []) or "-", None if up else "dim"),
            "speed": (_speed_label(iface.get("speed_mbit")), "dim"),
            "rx": ("-" if rx is None else format_throughput(rx), None),
            "tx": ("-" if tx is None else format_throughput(tx), None),
        }
        lines.append(format_table_line(cells, LINK_COLUMNS, width))
    return lines


def _bind_label(exposure: str) -> str:
    if exposure == EXPOSURE_ALL:
        return "any ▲"
    if exposure == EXPOSURE_LOOPBACK:
        return "local"
    return exposure[:-5] if exposure.endswith(" only") else exposure


def format_port_lines(listening, width: int) -> list:
    """Header plus one line per listening socket, the user's own first.

    Bound to every interface is the case worth noticing (reachable from the
    network unless a firewall intervenes), so it carries a marker as well as a
    colour; loopback-only listeners are dimmed.
    """
    lines = [format_table_line({}, PORT_COLUMNS, width, flex="SERVICE", header=True)]
    warn = get_rich_color("alert_warn", "#FFA500")
    for row in listening or []:
        exposure = row.get("exposure") or ""
        mine = row.get("mine")
        pids = row.get("pids") or ([row["pid"]] if row.get("pid") is not None else [])
        pid = str(pids[0]) + ("+" if len(pids) > 1 else "") if pids else "-"
        what = row.get("process") or ""
        hint = row.get("service_hint")
        if hint and hint != what:
            what = f"{what} ({hint})" if what else f"{hint}?"
        base = "bold" if mine else ("dim" if exposure == EXPOSURE_LOOPBACK else None)
        cells = {
            "port": (str(row.get("port", "")), base),
            "proto": (row.get("proto") or "", "dim"),
            "bind": (_bind_label(exposure), warn if exposure == EXPOSURE_ALL else base),
            "pid": (pid, base),
            "user": (row.get("username") or "-", base),
        }
        lines.append(format_table_line(cells, PORT_COLUMNS, width,
                                       flex=(what or "-", base)))
    return lines


def primary_interface(interfaces):
    """The interface most likely carrying this host's traffic, or None."""
    for iface in interfaces or []:
        if iface.get("is_up") and iface.get("kind") != "virtual" and iface.get("ipv4"):
            return iface
    return None


class NetworkIOWidget(MetricWidget):
    """Widget for network I/O with dual plots."""

    # Rows taken by the tab bar, the split bar and the summary line; only used
    # to estimate the plot region before the first layout pass.
    BAR_HEIGHT = 4
    # Below this panel height the summary line goes, so the plot keeps the row.
    SUMMARY_MIN_PANEL_HEIGHT = 9
    TABS = ("plot", "links", "ports")

    BINDINGS = [
        ("1", "show_tab('plot')", "Net: plot"),
        ("2", "show_tab('links')", "Net: interfaces"),
        ("3", "show_tab('ports')", "Net: listening ports"),
    ]

    DEFAULT_CSS = """
    NetworkIOWidget {
        layout: vertical;
    }
    NetworkIOWidget #net-tabbed {
        height: 1fr;
    }
    NetworkIOWidget .net-plot-pane {
        height: 1fr;
    }
    NetworkIOWidget #current-value, NetworkIOWidget #net-summary,
    NetworkIOWidget .net-table-header {
        height: 1;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    NetworkIOWidget .net-table {
        height: 1fr;
        /* Always reserved, so the rows' width does not change -- and lines
           do not wrap -- the moment the list grows long enough to scroll. */
        scrollbar-gutter: stable;
    }
    NetworkIOWidget .net-table-body {
        height: auto;
        text-wrap: nowrap;
        overflow-x: hidden;
    }
    """

    @staticmethod
    def download_color() -> str:
        """Colour of the download series — used for both the plot line and the bar."""
        return get_rich_color(
            "network_plot_download", get_rich_color("network_download", "#FF8C00")
        )

    @staticmethod
    def upload_color() -> str:
        """Colour of the upload series — used for both the plot line and the bar."""
        return get_rich_color(
            "network_plot_upload", get_rich_color("network_upload", "#00FF00")
        )

    def __init__(
        self, title: str, id: str = None, color: str = "blue", history_size: int = 120,
        initial_tab: str = "plot",
    ):
        super().__init__(
            title=title,
            color=get_rich_color("default_plot", "#0080FF"),
            history_size=history_size,
            id=id,
        )
        self.download_history = deque(maxlen=history_size)
        self.upload_history = deque(maxlen=history_size)
        self.max_net = 100
        self.first = True
        self.title = title
        self.border_title = title  # f"{title}"  # [blue]MB/s[/]"
        self._initial_tab = initial_tab if initial_tab in self.TABS else "plot"
        # Last get_network_details() reading; None until one arrives (or when
        # the source -- an older remote gc -- does not provide one).
        self.details = None
        self._summary_shown = True

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=self._initial_tab, id="net-tabbed"):
            with TabPane("Plot", id="plot"):
                yield Static("", id="history-plot", classes="metric-plot net-plot-pane")
                yield Static("", id="current-value", classes="metric-value")
                yield Static("", id="net-summary", classes="metric-value")
            with TabPane("Links", id="links"):
                yield Static("", id="links-header", classes="net-table-header")
                with VerticalScroll(classes="net-table"):
                    yield Static("[dim]Waiting for data…[/]", id="links-body",
                                 classes="net-table-body")
            with TabPane("Ports", id="ports"):
                yield Static("", id="ports-header", classes="net-table-header")
                with VerticalScroll(classes="net-table"):
                    yield Static("[dim]Waiting for data…[/]", id="ports-body",
                                 classes="net-table-body")

    @on(TabbedContent.TabActivated, "#net-tabbed")
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Re-draw for the newly revealed pane, whose region is only sized once active."""
        self.call_after_refresh(self.rerender)

    def action_show_tab(self, tab_id: str) -> None:
        if tab_id not in self.TABS:
            return
        try:
            tabbed = self.query_one("#net-tabbed", TabbedContent)
        except NoMatches:
            return
        # A focused (scrolled) table sits in the pane being hidden, and
        # TabbedContent keeps the pane holding focus active; take focus back.
        focused = self.screen.focused
        if focused is not None and focused is not self and self in focused.ancestors:
            self.focus()
        tabbed.active = tab_id

    # ---------------------------------------------------------------- details

    def summary_segments(self, details: dict) -> list:
        """(text, markup) pairs for the line under the bar, most important first."""
        segments = []
        tcp = details.get("tcp") or {}
        retrans = tcp.get("retransmit_percent")
        if retrans is not None and retrans >= 2.0:
            # Worth leading with: a lossy link stalls transfers in a way the
            # throughput plot above cannot explain.
            color = get_rich_color("alert_crit" if retrans >= 10 else "alert_warn", "#FFA500")
            text = f"▲ retx {retrans:.1f}%"
            segments.append((text, f"[{color}]{text}[/]"))

        iface = primary_interface(details.get("interfaces"))
        if iface is not None:
            text = f"{iface['name']} {iface['ipv4'][0]}"
            segments.append((text, f"[bold]{escape(text)}[/]"))

        summary = details.get("listening_summary") or {}
        if summary.get("listening"):
            text = f"{summary['listening']} ports"
            extra = []
            if summary.get("exposed"):
                extra.append(f"{summary['exposed']} open")
            if summary.get("mine"):
                extra.append(f"{summary['mine']} yours")
            if extra:
                text += f" ({', '.join(extra)})"
            segments.append((text, text))

        established = (details.get("connections") or {}).get("established")
        if established is None:
            established = tcp.get("established")
        if established is not None:
            text = f"{established} conn"
            segments.append((text, f"[dim]{text}[/]"))

        if retrans is not None and retrans < 2.0:
            text = f"retx {retrans:.1f}%"
            segments.append((text, f"[dim]{text}[/]"))
        return segments

    NO_DETAILS = ("[dim]Not reported — the collector failed this tick, or the "
                  "focused job runs an older ground-control.[/]")

    def _render_tables(self) -> None:
        details = self.details
        if not details:
            # Say so rather than leave an old reading (or "Waiting…") up
            # forever: a table with no age shown must not pass for current.
            for name in ("links", "ports"):
                try:
                    self.query_one(f"#{name}-header", Static).update("")
                    self.query_one(f"#{name}-body", Static).update(self.NO_DETAILS)
                except NoMatches:
                    pass
            return
        for name, formatter, rows in (
            ("links", format_link_lines, details.get("interfaces")),
            ("ports", format_port_lines, details.get("listening")),
        ):
            try:
                header = self.query_one(f"#{name}-header", Static)
                body = self.query_one(f"#{name}-body", Static)
            except NoMatches:
                continue
            # Measured on the body, inside the scroll gutter: the header spans the
            # gutter too, but its columns must line up with the rows.
            width = body.content_size.width
            if width <= 0:
                width, _ = self.region_size(f"#{name}-header", reserve_width=2)
            if width <= 0:
                continue
            lines = formatter(rows, width)
            header.update(lines[0])
            body.update("\n".join(lines[1:]) if len(lines) > 1
                        else "[dim]none[/]")

    def create_center_bar(
        self, download_speed: float, upload_speed: float, total_width: int
    ) -> str:
        """Download/upload bar: one line, centre separator on the middle column.

        Sides use the same colours as the corresponding plot lines.
        """
        scale = self.max_net or 1
        return self.build_split_bar(
            total_width,
            left_fraction=download_speed / scale,
            right_fraction=upload_speed / scale,
            left_color=self.download_color(),
            right_color=self.upload_color(),
            left_label=format_throughput(download_speed),
            right_label=format_throughput(upload_speed),
        )

    def get_dual_plot(self, width: int, height: int) -> str:
        if not self.download_history:
            return "No data yet..."
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)

        try:
            plt.clear_figure()
            plt.plot_size(height=height, width=width)
            plt.theme("pro")

            # Upload above the zero line, download mirrored below it
            upload_series = [x + 0.1 for x in self.upload_history]
            download_series = [-x - 0.1 for x in self.download_history]

            # Symmetric y-axis with bounds following the data in the window, like
            # the memory and disk panels. A fixed 10 MB/s floor used to squash
            # everyday sub-10 MB/s traffic into a flat line against the zero axis.
            # Floor of 1 keeps an idle link (max_value ~0.1 after the offsets)
            # from collapsing the plot and bar to a bare zero line.
            max_value = max(
                max(self.upload_history, default=0),
                max(download_series, key=abs, default=0),
            )
            y_limit = max(max_value, 1)
            self.max_net = y_limit

            # Set y-axis limits symmetrically around zero
            plt.ylim(-y_limit, y_limit)
            # Create custom y-axis ticks with MB/s labels
            num_ticks = max(2, min(5, height - 1))
            tick_step = 2 * y_limit / (num_ticks - 1) if num_ticks > 1 else 1

            y_ticks = []
            y_labels = []

            for i in range(num_ticks):
                value = -y_limit + i * tick_step
                y_ticks.append(value)
                # Tick labels: numbers only; unit on top of plot (─MB/s─┐)
                if value == 0:
                    y_labels.append("0")
                elif value > 0:
                    y_labels.append(str(round(value)) + "↑")
                else:
                    y_labels.append(str(round(abs(value))) + "↓")

            plt.yticks(y_ticks, y_labels)

            plt.plot(upload_series, marker="braille", label="Upload")
            plt.plot(download_series, marker="braille", label="Download")
            plt.hline(0.0)
            plt.yfrequency(5)
            plt.xfrequency(0)

            # Series order fixes the colours: 1st plotted -> [blue], 2nd -> [green].
            build = recolor(
                ansi2rich(plt.build()).replace("\x1b[0m", ""),
                {"blue": self.upload_color(), "green": self.download_color()},
            ).replace("──────┐", "─MB/s─┐")
            if len(self.download_history) >= self.download_history.maxlen:
                build = substitute_plot_timeframe(build, self.download_history.maxlen)
            return self.finish_plot(build, width, height)
        except Exception:
            # Plotext can IndexError on unusual data; return a safe placeholder at the same size
            plt.clear_figure()
            plt.plot_size(height=height, width=width)
            plt.theme("pro")
            plt.ylim(-1, 1)
            plt.plot([0] * min(10, width), marker="braille", label="Network")
            plt.hline(0.0)
            build = ansi2rich(plt.build()).replace("\x1b[0m", "")
            return self.finish_plot(build, width, height)

    def rerender(self) -> None:
        """Re-draw plot and bar from stored history at the current region size."""
        if not self.download_history:
            return
        plot_width, plot_height = self.plot_region(
            "#history-plot", reserve_height=self.BAR_HEIGHT
        )
        bar_width, _ = self.region_size("#current-value")
        self._render_tables()
        try:
            summary_line = self.query_one("#net-summary", Static)
        except NoMatches:
            summary_line = None
        show_summary = bool(
            self.details and self.content_size.height >= self.SUMMARY_MIN_PANEL_HEIGHT)
        if summary_line is not None and show_summary != self._summary_shown:
            self._summary_shown = show_summary
            summary_line.display = show_summary
            self.call_after_refresh(self.rerender)
        if summary_line is not None and show_summary:
            width, _ = self.region_size("#net-summary")
            summary_line.update(
                self.build_telemetry_line(self.summary_segments(self.details), width))
        try:
            self.query_one("#history-plot").update(
                self.get_dual_plot(plot_width, plot_height)
            )
            self.query_one("#current-value").update(
                self.create_center_bar(
                    self.download_history[-1],
                    self.upload_history[-1],
                    total_width=bar_width,
                )
            )
        except NoMatches:
            pass  # DOM not ready yet (e.g. after layout change)

    def update_content(self, download_speed: float, upload_speed: float,
                       details: dict = None):
        # None replaces the previous reading: details are a snapshot of now, and
        # keeping the last one would show stale sockets with no sign of age.
        self.details = details
        if self.first:
            self.first = False
            return
        self.download_history.append(download_speed)
        self.upload_history.append(upload_speed)
        logger.info(
            "download_speed_mb_s: %.2f, upload_speed_mb_s: %.2f",
            download_speed,
            upload_speed,
        )
        self.rerender()

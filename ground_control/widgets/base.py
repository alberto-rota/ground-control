import time
from collections import deque
from textual.widgets import Static
from textual.message import Message
from textual.css.query import NoMatches
import plotext as plt
from ..utils.formatting import align, ansi2rich, fit_lines
from ..utils.colors import get_color, get_rich_color, load_colors
from ..utils.alerts import CRIT as ALERT_CRIT, OK as ALERT_OK, WARN as ALERT_WARN, LEVEL_ORDER
class MetricWidget(Static):
    """Base widget for system metrics with plot."""

    # Focusable so the dashboard can be driven entirely from the keyboard:
    # Tab / [ / ] move focus between panels, and a focused panel's local
    # BINDINGS (e.g. CPU 1/2/3, GPU 1/2) switch its tabs.
    can_focus = True

    # Shared binding for every metric panel: hide the focused panel quickly.
    BINDINGS = [
        ("x", "hide_widget", "Hide"),
    ]

    def action_hide_widget(self) -> None:
        """Hide this panel from the dashboard (re-enable from Settings)."""
        try:
            self.app._hide_widget(self)
        except Exception:
            pass

    # Below this, a plotext canvas has no room for axis + labels: show a hint instead.
    MIN_PLOT_WIDTH = 8
    MIN_PLOT_HEIGHT = 3

    DEFAULT_CSS = """
    MetricWidget {
        height: 100%;
        border: solid green;
        background: $surface;
        /* Panels never scroll: every child is rendered to fit its region exactly,
           so a scrollbar would only ever appear because of a sizing bug (and would
           itself steal a row/column and make the overflow worse). */
        overflow: hidden hidden;
    }

    .metric-title {
        text-align: left;
        height: 1;
    }

    /* Plot/bar content is pre-sized to the region: never re-wrap it, clip instead.
       A single wrapped line would shift everything below it out of the panel. */
    .metric-value {
        text-align: left;
        height: 1;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    .cpu-metric-value {
        text-align: left;
        height: 1fr;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }

    .metric-plot {
        width: 1fr;
        height: 1fr;
        min-height: 0;
        text-wrap: nowrap;
        text-overflow: clip;
        overflow: hidden hidden;
    }
    """

    def __init__(self, title: str, id: str, color: str = "blue", history_size: int = 120):
        super().__init__(id=id)
        self.title = title
        # Threshold state, driven by the app each tick. `_alert_level` is the
        # current reading; `_alert_sticky` remembers the worst level seen inside
        # the sticky window, so a spike that happened while the user was looking
        # at another tab is still visible when they come back.
        self._alert_level = ALERT_OK
        self._alert_sticky = ALERT_OK
        self._alert_sticky_until = 0.0
        # If color is a hex value or color name, use it; otherwise use default
        # Note: We use _color_config to avoid conflict with Textual's colors attribute
        if color.startswith("#"):
            self.color = color
        else:
            # Try to get color from config by widget type, fallback to provided color
            self.color = get_rich_color("default_plot", color)
        self.history = deque(maxlen=history_size)
        self.plot_width = 0
        self.plot_height = 0

    # -------------------------------------------------------------------- alerts

    # Marker prefixed to the panel title. Chosen over colour alone so the state
    # survives a monochrome terminal and colour-blind viewers.
    ALERT_MARKERS = {ALERT_OK: "", ALERT_WARN: "▲ ", ALERT_CRIT: "■ "}

    def set_alert(self, level: str, sticky_seconds: float = 0.0) -> None:
        """
        Set this panel's alert level and restyle its border.

        Args:
            level: One of ``ok`` / ``warn`` / ``crit``.
            sticky_seconds: Keep showing a breach for this long after the value
                recovers. 0 disables stickiness.
        """
        level = level if level in LEVEL_ORDER else ALERT_OK
        now = time.monotonic()

        if level != ALERT_OK:
            if sticky_seconds > 0:
                # Escalate immediately; never downgrade inside the window.
                if LEVEL_ORDER[level] >= LEVEL_ORDER.get(self._alert_sticky, 0) \
                        or now >= self._alert_sticky_until:
                    self._alert_sticky = level
                self._alert_sticky_until = now + sticky_seconds
        elif now >= self._alert_sticky_until:
            self._alert_sticky = ALERT_OK

        effective = level
        if sticky_seconds > 0 and now < self._alert_sticky_until \
                and LEVEL_ORDER.get(self._alert_sticky, 0) > LEVEL_ORDER[level]:
            effective = self._alert_sticky

        if effective == self._alert_level:
            return
        self._alert_level = effective
        self._apply_alert_style()

    @property
    def alert_level(self) -> str:
        """The level currently being displayed (may be sticky, not live)."""
        return self._alert_level

    def _apply_alert_style(self) -> None:
        """Repaint border and title to match ``_alert_level``."""
        level = self._alert_level
        try:
            if level == ALERT_OK:
                # Textual stores `border` as four per-edge rules, so clearing
                # "border" itself is a no-op and would strand the alert colour.
                for edge in ("border_top", "border_right",
                             "border_bottom", "border_left"):
                    self.styles.clear_rule(edge)
            else:
                key = "alert_crit" if level == ALERT_CRIT else "alert_warn"
                self.styles.border = ("heavy", get_color(key))
        except Exception:  # noqa: BLE001 - styling must never break a panel
            pass
        try:
            marker = self.ALERT_MARKERS.get(level, "")
            self.border_title = f"{marker}{self.title}"
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ geometry

    def region_size(self, selector: str, reserve_width: int = 0, reserve_height: int = 0):
        """Return the live content region ``(width, height)`` of a child widget.

        This is the single source of truth for how big a plot or bar may be: the
        child's region already excludes the panel border, the tab bar and any
        sibling rows, so plotext can be asked for exactly that many cells.

        Before the first layout pass the child has no size yet; then fall back to
        this widget's own content area minus the chrome the caller knows about.
        """
        try:
            child = self.query_one(selector)
        except NoMatches:
            child = None
        if child is not None:
            size = child.content_size
            if size.width > 0 and size.height > 0:
                return size.width, size.height
        own = self.content_size
        return (
            max(0, own.width - reserve_width),
            max(0, own.height - reserve_height),
        )

    def plot_region(self, selector: str = ".metric-plot", reserve_width: int = 0, reserve_height: int = 0):
        """Region size for a plot child; also caches it as ``plot_width``/``plot_height``."""
        width, height = self.region_size(selector, reserve_width, reserve_height)
        self.plot_width = width
        self.plot_height = height
        return width, height

    def plot_fits(self, width: int, height: int) -> bool:
        """True when the region is big enough for plotext to draw axes and labels."""
        return width >= self.MIN_PLOT_WIDTH and height >= self.MIN_PLOT_HEIGHT

    def too_small_text(self, width: int, height: int) -> str:
        """Placeholder for regions too small to plot in; never wider than the region."""
        if width <= 0 or height <= 0:
            return ""
        for text in ("too small", "small", "···"):
            if len(text) <= width:
                return f"[dim]{text}[/]"
        return "[dim]" + "·" * width + "[/]"

    # ------------------------------------------------------------------ split bars

    # Powerline tips drawn at the growing end of a bar (one cell each).
    ARROW_LEFT = ""
    ARROW_RIGHT = ""
    # Width of the value labels flanking a split bar, and the smallest bar worth
    # keeping them for (below it the bar spans the whole width instead).
    SPLIT_LABEL_WIDTH = 9
    SPLIT_MIN_BAR = 6

    def build_split_bar(
        self,
        total_width: int,
        left_fraction: float,
        right_fraction: float,
        left_color: str,
        right_color: str,
        left_label: str = None,
        right_label: str = None,
        left_from_centre: bool = True,
    ) -> str:
        """One-line bar split by a separator sitting on the exact centre column.

        The separator is placed at ``total_width // 2`` and the two label fields have
        equal width, so the bar reads as centred; the left label is flush with the left
        edge and the right label with the right edge, so the line spans the full region.
        The result is exactly ``total_width`` cells.

        ``left_from_centre`` mirrors the left half (blocks grow from the separator
        outwards, as in the network widget); when False the left half is a gauge growing
        from the left edge towards the separator, as in the GPU widget.
        """
        total_width = int(total_width)
        if total_width <= 0:
            return ""

        label_w = self.SPLIT_LABEL_WIDTH if (left_label is not None and right_label is not None) else 0
        separator = total_width // 2
        left_half = separator - (label_w + 1 if label_w else 0)
        right_half = total_width - separator - 1 - (label_w + 1 if label_w else 0)
        if label_w and min(left_half, right_half) < self.SPLIT_MIN_BAR // 2:
            # Not enough room for labels: let the bar use the full width.
            label_w = 0
            left_half = separator
            right_half = total_width - separator - 1
        left_half = max(0, left_half)
        right_half = max(0, right_half)

        left_blocks = min(left_half, int(left_half * min(max(left_fraction, 0.0), 1.0)))
        right_blocks = min(right_half, int(right_half * min(max(right_fraction, 0.0), 1.0)))

        if left_blocks >= 1:
            if left_from_centre:
                left_bar = (
                    f"{'─' * (left_half - left_blocks)}"
                    f"[{left_color}]{self.ARROW_LEFT}{'█' * (left_blocks - 1)}[/]"
                )
            else:
                left_bar = (
                    f"[{left_color}]{'█' * (left_blocks - 1)}{self.ARROW_RIGHT}[/]"
                    f"{'─' * (left_half - left_blocks)}"
                )
        else:
            left_bar = "─" * left_half
        if right_blocks >= 1:
            right_bar = (
                f"[{right_color}]{'█' * (right_blocks - 1)}{self.ARROW_RIGHT}[/]"
                f"{'─' * (right_half - right_blocks)}"
            )
        else:
            right_bar = "─" * right_half

        if not label_w:
            return f"{left_bar}│{right_bar}"
        # Labels hug the outer edges so the line reaches both borders.
        left_text = align(left_label, label_w, "left")
        right_text = align(right_label, label_w, "right")
        return (
            f"[{left_color}]{left_text}[/] {left_bar}│{right_bar} "
            f"[{right_color}]{right_text}[/]"
        )

    def finish_plot(self, build: str, width: int, height: int) -> str:
        """Trim a built plot so it can never exceed its region.

        ``plt.build()`` terminates with a newline, which Rich renders as an extra
        blank row; combined with an off-by-one canvas that alone pushed the last
        plot row out of the panel. Lines are clipped too, because plotext quietly
        overshoots the requested width on very narrow labelled bar charts.
        """
        return fit_lines(build, height, width)

    def on_resize(self, event: Message) -> None:
        """Re-draw cached data at the new size (nothing is re-sampled).

        Runs after the refresh so children have their new regions when we measure.
        """
        self.call_after_refresh(self.rerender)

    def rerender(self) -> None:
        """Re-draw from the last received data. Overridden by widgets that plot."""

    # -------------------------------------------------------------------- drawing

    def get_plot(self, y_min=0, y_max=100, width: int = None, height: int = None) -> str:
        if not self.history:
            return "No data yet..."

        if width is None or height is None:
            width, height = self.plot_region()
        if not self.plot_fits(width, height):
            return self.too_small_text(width, height)

        plt.clear_figure()
        plt.plot_size(height=height, width=width)
        plt.theme("pro")
        plt.plot(list(self.history), marker="braille")
        plt.ylim(y_min, y_max)
        plt.xfrequency(0)
        plt.yfrequency(3)
        build = ansi2rich(plt.build()).replace("\x1b[0m", "").replace("[blue]", f"[{self.color}]")
        return self.finish_plot(build, width, height)

    def create_gradient_bar(self, value: float, width: int = 20, color: str = None) -> str:
        """Creates a gradient bar with custom base color."""
        filled = int((width * value) / 100)
        if filled > width * 0.8:
            color = get_rich_color("high_value", "#FF0000")
        empty = width - filled

        if filled == 0:
            return "─" * width

        bar_color = self.color if color is None else color

        if value < 20:
            return f"[{bar_color}]{'█' * filled}[/]{'─' * empty}"

        bar = (f"[{bar_color}]{'█' * filled}[/]"
               f"{'─' * empty}")

        return bar

    def format_metric_line(self, label: str, value: float, suffix: str = "%") -> str:
        """Creates a consistent metric line with label, bar, and value."""
        bar = self.create_gradient_bar(value)
        return f"{label:<4}{bar}{value:>7.1f}{suffix}"

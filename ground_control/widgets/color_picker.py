"""Interactive colour picking: a swatch grid, HSV steppers and a live preview.

Textual ships neither a colour picker nor a slider, so both are built here on
top of ``Static`` plus key bindings and click handling.

Everything in this screen applies *live*: each change writes through the same
``set_color()`` path the Settings hex field uses, so the running app — including
the real metric widget shown in the preview pane — recolours immediately. There
is therefore no "apply" step, only Revert (restore the value the key had when
the screen opened) and Close.
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.events import Click, Resize
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..utils.colors import COLOR_GROUPS, DEFAULT_COLORS, normalize_hex

# Palette geometry. Cells are drawn edge to edge (no separator) so the grid
# reads as a gradient; the cursor cell is marked with a contrasting dot.
PALETTE_HUES = 12
PALETTE_SHADES = 5
CELL_WIDTH = 3
# Track length of the H/S/V steppers, in cells.
TRACK_WIDTH = 24


def color_option_prompt(key: str, hex_color: str) -> str:
    """One row of a colour list: swatch, key name, hex value.

    Args:
        key: Palette key (one of ``COLOR_KEYS``).
        hex_color: Current value as ``#RRGGBB``.
    """
    return f"[{hex_color}]███[/] {key:<22s} {hex_color}"


def build_color_option(key: str, hex_color: str) -> Option:
    """A colour row as an OptionList entry."""
    return Option(color_option_prompt(key, hex_color), id=f"colorkey-{key}")


def build_color_options(colors: dict, compact: bool = False) -> list[Option]:
    """Rows for a colour list: a disabled header per group, then its keys.

    Args:
        colors: The palette to read current values from.
        compact: Omit group headers (used where vertical space is tight).
    """
    options: list[Option] = []
    for group, keys in COLOR_GROUPS:
        if not compact:
            options.append(Option(group, id=f"colorgroup-{group}", disabled=True))
        for key in keys:
            options.append(
                build_color_option(key, colors.get(key, DEFAULT_COLORS.get(key, "#000000")))
            )
    return options


def _contrast_hex(hex_color: str) -> str:
    """Black or white, whichever stays readable on ``hex_color``."""
    try:
        return Color.parse(hex_color).get_contrast_text().hex6
    except Exception:
        return "#000000"


class PaletteGrid(Static):
    """A focusable grid of colour swatches: hues across, shades down, greys last.

    Arrow keys move the cursor and select as they go (so the preview follows the
    cursor); clicking a swatch selects it directly.
    """

    can_focus = True

    BINDINGS = [
        Binding("up", "move(-1, 0)", "Up", show=False),
        Binding("down", "move(1, 0)", "Down", show=False),
        Binding("left", "move(0, -1)", "Left", show=False),
        Binding("right", "move(0, 1)", "Right", show=False),
        Binding("home", "move_edge(0, -1)", "First", show=False),
        Binding("end", "move_edge(0, 1)", "Last", show=False),
    ]

    class Picked(Message):
        """Posted when the cursor lands on (or clicks) a swatch."""

        def __init__(self, grid: "PaletteGrid", color: str) -> None:
            super().__init__()
            self.grid = grid
            self.color = color

        @property
        def control(self) -> "PaletteGrid":
            """The grid that posted this, so ``@on`` can match on its id."""
            return self.grid

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._grid: list[list[str]] = self._build_grid()
        self._row = 0
        self._col = 0

    @staticmethod
    def _build_grid() -> list[list[str]]:
        """Hue columns × shade rows, plus a greyscale row along the bottom."""
        grid: list[list[str]] = []
        for row in range(PALETTE_SHADES):
            # Light at the top, dark at the bottom.
            lightness = 0.84 - row * 0.15
            grid.append([
                Color.from_hsl(col / PALETTE_HUES, 0.85, lightness).hex6
                for col in range(PALETTE_HUES)
            ])
        greys = []
        for col in range(PALETTE_HUES):
            level = round(255 * col / (PALETTE_HUES - 1))
            greys.append(Color(level, level, level).hex6)
        grid.append(greys)
        return grid

    def on_mount(self) -> None:
        self._redraw()

    @property
    def color(self) -> str:
        """Hex value under the cursor."""
        return self._grid[self._row][self._col]

    def select_nearest(self, hex_color: str) -> None:
        """Move the cursor to the closest swatch to ``hex_color``, without posting.

        Used when the target colour changes from outside (a different key, a
        typed hex value), so the cursor shows roughly where that colour sits.
        """
        try:
            target = Color.parse(hex_color)
        except Exception:
            return
        best = None
        for r, row in enumerate(self._grid):
            for c, cell in enumerate(row):
                cell_color = Color.parse(cell)
                dist = (
                    (cell_color.r - target.r) ** 2
                    + (cell_color.g - target.g) ** 2
                    + (cell_color.b - target.b) ** 2
                )
                if best is None or dist < best[0]:
                    best = (dist, r, c)
        if best is not None:
            _, self._row, self._col = best
            self._redraw()

    def action_move(self, row_delta: int, col_delta: int) -> None:
        """Move the cursor and select the swatch it lands on."""
        rows = len(self._grid)
        cols = len(self._grid[0])
        self._row = max(0, min(rows - 1, self._row + row_delta))
        self._col = max(0, min(cols - 1, self._col + col_delta))
        self._redraw()
        self.post_message(self.Picked(self, self.color))

    def action_move_edge(self, row_delta: int, col_delta: int) -> None:
        """Jump to the first/last column of the current row."""
        self._col = 0 if col_delta < 0 else len(self._grid[0]) - 1
        self._redraw()
        self.post_message(self.Picked(self, self.color))

    def on_click(self, event: Click) -> None:
        row = event.y
        col = event.x // CELL_WIDTH
        if 0 <= row < len(self._grid) and 0 <= col < len(self._grid[0]):
            self._row, self._col = row, col
            self._redraw()
            self.focus()
            self.post_message(self.Picked(self, self.color))

    def _redraw(self) -> None:
        lines = []
        for r, row in enumerate(self._grid):
            cells = []
            for c, hex_color in enumerate(row):
                if r == self._row and c == self._col:
                    dot = _contrast_hex(hex_color)
                    cells.append(f"[{hex_color}]█[/][{dot}]●[/][{hex_color}]█[/]")
                else:
                    cells.append(f"[{hex_color}]{'█' * CELL_WIDTH}[/]")
            lines.append("".join(cells))
        self.update("\n".join(lines))


class HsvSliders(Static):
    """Three stepper rows (hue, saturation, value) for nudging a colour.

    Up/down picks the channel, left/right adjusts it; shift multiplies the step
    by ten. Every adjustment posts :class:`Changed` so the app applies it live.
    """

    can_focus = True

    BINDINGS = [
        Binding("up", "channel(-1)", "Prev channel", show=False),
        Binding("down", "channel(1)", "Next channel", show=False),
        Binding("left", "adjust(-1)", "Decrease", show=False),
        Binding("right", "adjust(1)", "Increase", show=False),
        Binding("shift+left", "adjust(-10)", "Decrease x10", show=False),
        Binding("shift+right", "adjust(10)", "Increase x10", show=False),
    ]

    # Fine step per channel, in channel units (hue is a fraction of the circle).
    _STEPS = (1 / 360, 0.01, 0.01)
    _LABELS = ("H", "S", "V")

    class Changed(Message):
        """Posted when a channel is adjusted."""

        def __init__(self, sliders: "HsvSliders", color: str) -> None:
            super().__init__()
            self.sliders = sliders
            self.color = color

        @property
        def control(self) -> "HsvSliders":
            """The stepper block that posted this, so ``@on`` can match on its id."""
            return self.sliders

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hsv = [0.0, 0.0, 0.0]
        self._channel = 0

    def on_mount(self) -> None:
        self._redraw()

    @property
    def color(self) -> str:
        h, s, v = self._hsv
        return Color.from_hsv(h, s, v).hex6

    def set_color(self, hex_color: str) -> None:
        """Load a colour into the steppers without posting a change."""
        try:
            hsv = Color.parse(hex_color).hsv
        except Exception:
            return
        self._hsv = [hsv.h, hsv.s, hsv.v]
        self._redraw()

    def action_channel(self, delta: int) -> None:
        self._channel = (self._channel + delta) % 3
        self._redraw()

    def action_adjust(self, multiplier: int) -> None:
        step = self._STEPS[self._channel] * multiplier
        value = self._hsv[self._channel] + step
        if self._channel == 0:
            value %= 1.0  # hue wraps
        else:
            value = max(0.0, min(1.0, value))
        self._hsv[self._channel] = value
        self._redraw()
        self.post_message(self.Changed(self, self.color))

    def _redraw(self) -> None:
        current = self.color
        lines = []
        for index, (label, value) in enumerate(zip(self._LABELS, self._hsv)):
            position = min(TRACK_WIDTH - 1, int(value * TRACK_WIDTH))
            track = "━" * position + "●" + "━" * (TRACK_WIDTH - position - 1)
            readout = f"{value * 360:>4.0f}°" if index == 0 else f"{value * 100:>4.0f}%"
            marker = "[b]" if index == self._channel else "[dim]"
            lines.append(f"{marker}{label} ◀ {track} ▶ {readout}[/]")
        lines.append(f"[{current}]{'█' * 16}[/]  {current}")
        self.update("\n".join(lines))


class ColorPickerScreen(ModalScreen):
    """Full-screen colour editor: key list, palette + steppers, live preview.

    Dismisses with no value; all changes are already applied and persisted by
    the time the screen closes.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+z", "revert", "Revert"),
    ]

    # Below this width the preview pane is dropped rather than squeezed to a
    # size where the plots render as noise.
    PREVIEW_MIN_WIDTH = 112

    def __init__(self, color_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._key = color_key
        # Value the key had when the screen opened, for Revert.
        self._original: str | None = None

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        colors = self.app._color_config or DEFAULT_COLORS
        with Vertical(id="picker-box"):
            with Horizontal(id="picker-cols"):
                with Vertical(classes="picker-block", id="picker-keys-block") as block:
                    block.border_title = "Colors"
                    yield OptionList(
                        *build_color_options(colors),
                        id="picker-keys",
                        compact=True,
                    )
                with Vertical(id="picker-mid"):
                    with Vertical(classes="picker-block", id="picker-palette-block") as block:
                        block.border_title = "Palette"
                        yield PaletteGrid(id="picker-palette")
                    with Vertical(classes="picker-block", id="picker-hsv-block") as block:
                        block.border_title = "Adjust"
                        yield HsvSliders(id="picker-hsv")
                    # Two deliberate lines: the mid column is too narrow for one.
                    yield Static(
                        "arrows move · shift+arrows = x10\ntab switches pane",
                        id="picker-hint",
                    )
                with Vertical(classes="picker-block", id="picker-preview-block") as block:
                    block.border_title = "Preview"
                    yield Vertical(id="picker-preview")
            with Horizontal(id="picker-footer"):
                yield Static("hex", id="picker-hex-label")
                yield Input(id="picker-hex", placeholder="#RRGGBB or a CSS name", compact=True)
                yield Button("Revert", id="picker-revert", compact=True)
                yield Button("Close", id="picker-close", compact=True)

    async def on_mount(self) -> None:
        self._original = self._current_value()
        keys = self.query_one("#picker-keys", OptionList)
        try:
            keys.highlighted = keys.get_option_index(f"colorkey-{self._key}")
        except Exception:
            pass
        keys.focus()
        self._sync_controls()
        await self._mount_preview()
        self._apply_narrow(self.app.size.width)

    def on_resize(self, event: Resize) -> None:
        self._apply_narrow(event.size.width)

    def _apply_narrow(self, width: int) -> None:
        """Drop the preview pane when the terminal is too narrow to render it."""
        try:
            self.query_one("#picker-cols").set_class(
                width < self.PREVIEW_MIN_WIDTH, "-narrow"
            )
        except Exception:
            pass

    # ----------------------------------------------------------- current key

    def _current_value(self) -> str:
        colors = self.app._color_config or DEFAULT_COLORS
        return colors.get(self._key, DEFAULT_COLORS.get(self._key, "#000000"))

    def _sync_controls(self) -> None:
        """Push the current key's value into the palette, steppers and hex field."""
        value = self._current_value()
        self.query_one("#picker-palette", PaletteGrid).select_nearest(value)
        self.query_one("#picker-hsv", HsvSliders).set_color(value)
        hex_input = self.query_one("#picker-hex", Input)
        with hex_input.prevent(Input.Changed):
            hex_input.value = value
        try:
            self.query_one("#picker-box").border_title = f"Edit color — {self._key}"
        except Exception:
            pass

    async def _mount_preview(self) -> None:
        """Show the metric widget that the current key actually affects."""
        try:
            container = self.query_one("#picker-preview", Vertical)
        except Exception:
            return
        await self.app.mount_color_preview(self._key, container)

    # -------------------------------------------------------------- applying

    def _apply(self, value: str) -> bool:
        """Write ``value`` to the current key and refresh everything that shows it."""
        normalized = normalize_hex(value)
        if normalized is None:
            # Accept CSS names too ("tomato"), since Color.parse understands them.
            try:
                normalized = Color.parse(value.strip()).hex6
            except Exception:
                return False

        if not self.app.apply_color_live(self._key, normalized):
            return False

        hex_input = self.query_one("#picker-hex", Input)
        with hex_input.prevent(Input.Changed):
            hex_input.value = normalized
        try:
            self.query_one("#picker-keys", OptionList).replace_option_prompt(
                f"colorkey-{self._key}", color_option_prompt(self._key, normalized)
            )
        except Exception:
            pass
        self.app.refresh_color_preview()
        return True

    @on(OptionList.OptionHighlighted, "#picker-keys")
    async def _on_key_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Switch the target key; the preview follows to that key's widget."""
        option_id = event.option.id or ""
        if not option_id.startswith("colorkey-"):
            return
        new_key = option_id.removeprefix("colorkey-")
        if new_key == self._key:
            return
        previous_group = self.app.preview_group_for_key(self._key)
        self._key = new_key
        self._original = self._current_value()
        self._sync_controls()
        # Only rebuild the preview when it would actually be a different widget.
        if self.app.preview_group_for_key(new_key) != previous_group:
            await self._mount_preview()

    @on(PaletteGrid.Picked, "#picker-palette")
    def _on_palette_picked(self, event: PaletteGrid.Picked) -> None:
        self._apply(event.color)
        self.query_one("#picker-hsv", HsvSliders).set_color(event.color)

    @on(HsvSliders.Changed, "#picker-hsv")
    def _on_hsv_changed(self, event: HsvSliders.Changed) -> None:
        self._apply(event.color)

    @on(Input.Submitted, "#picker-hex")
    def _on_hex_submitted(self, event: Input.Submitted) -> None:
        if self._apply(event.value):
            value = self._current_value()
            self.query_one("#picker-palette", PaletteGrid).select_nearest(value)
            self.query_one("#picker-hsv", HsvSliders).set_color(value)
        else:
            self.notify(
                f"{event.value!r} is not a colour (expected #RRGGBB or a CSS name)",
                title="Colors",
                severity="error",
            )

    @on(Button.Pressed, "#picker-revert")
    def _on_revert_pressed(self, event: Button.Pressed) -> None:
        self.action_revert()

    @on(Button.Pressed, "#picker-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        self.action_close()

    def action_revert(self) -> None:
        """Restore the value this key had when the screen opened."""
        if self._original is None:
            return
        if self._apply(self._original):
            self._sync_controls()

    def action_close(self) -> None:
        self.app.clear_color_preview()
        self.dismiss(None)

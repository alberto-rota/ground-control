"""Tests for the contrast helpers behind the header/footer CSS tokens."""
import json

from ground_control.utils.colors import (
    THEMES_DIR,
    contrast_ratio,
    get_theme_tokens,
    most_readable_on,
    relative_luminance,
)

# WCAG AA for large text. The header title is one line of large-ish terminal
# text, so this is the bar it has to clear -- not AAA.
MIN_CONTRAST = 3.0


def test_relative_luminance_endpoints():
    assert relative_luminance("#000000") == 0.0
    assert relative_luminance("#FFFFFF") == 1.0
    assert 0.0 < relative_luminance("#808080") < 1.0


def test_contrast_ratio_bounds():
    assert contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert contrast_ratio("#3C3836", "#3C3836") == 1.0
    # Order does not matter.
    assert contrast_ratio("#282828", "#EBDBB2") == contrast_ratio("#EBDBB2", "#282828")


def test_most_readable_on_picks_the_higher_contrast_candidate():
    # gruvbox: header_bg is a *surface* colour, so the light text wins over the
    # near-black text_on_accent.
    assert most_readable_on("#3C3836", "#EBDBB2", "#282828") == "#EBDBB2"
    # classic: header_bg is the *accent* colour, so the dark one wins instead.
    assert most_readable_on("#13A10E", "#E0E0E0", "#000000") == "#000000"


def test_most_readable_on_ignores_empty_candidates():
    assert most_readable_on("#000000", "", None, "#FFFFFF") == "#FFFFFF"
    # With nothing usable it still returns a colour rather than raising.
    assert most_readable_on("#000000") == "#FFFFFF"


def test_explicit_header_fg_is_not_overridden():
    tokens = get_theme_tokens({"header_bg": "#3C3836", "header_fg": "#FF0000"})
    assert tokens["header_fg"] == "rgb(255, 0, 0)"


def test_every_shipped_theme_has_a_readable_header():
    """
    No shipped theme defines ``header_fg``, so this is entirely down to the
    fallback. It used to be a fixed ``text_on_accent``, which is correct only
    for the themes that paint the header in the accent colour and left the
    other thirteen with near-invisible title text.
    """
    themes = sorted(THEMES_DIR.glob("*.json"))
    assert themes, "no built-in themes found"

    for path in themes:
        colors = json.loads(path.read_text())
        chosen = most_readable_on(
            colors["header_bg"], colors.get("text"), colors.get("text_on_accent"))
        ratio = contrast_ratio(colors["header_bg"], chosen)
        assert ratio >= MIN_CONTRAST, (
            f"{path.stem}: header text {chosen} on {colors['header_bg']} "
            f"is only {ratio:.2f}:1")

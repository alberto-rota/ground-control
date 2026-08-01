"""
Color configuration utility for loading and managing colors from config file.
Converts hex colors to Rich color names or hex format as needed.

Themes come from two places: the read-only set shipped with the package
(``ground_control/themes``) and the user's own, saved under
``~/.config/ground-control/themes``. User themes shadow builtins of the same
name, and only user themes may be written or deleted.
"""
import json
import os
import re
from typing import Dict, Optional, List, Tuple
from platformdirs import user_config_dir
from pathlib import Path

# Themes directory: resolve once so it works from source and from installed package.
def _resolve_themes_dir() -> Path:
    # Same package as this module: ground_control/themes
    pkg_dir = Path(__file__).resolve().parent.parent
    themes_dir = pkg_dir / "themes"
    if themes_dir.is_dir():
        return themes_dir
    # Fallback for installed package (e.g. PEP 517 build without package-data in older setuptools)
    try:
        from importlib.resources import files
        traversable = files("ground_control").joinpath("themes")
        if traversable.is_dir():
            return Path(str(traversable))
    except Exception:
        pass
    return themes_dir

# Rich color name mapping (for backward compatibility with Rich markup)
HEX_TO_RICH = {
    "#13A10E": "green",
    "#0080FF": "blue",
    "#FF00FF": "magenta",
    "#00FFFF": "cyan",
    "#FF8C00": "orange1",
    "#00FF00": "green",
    "#FF0000": "red",
    "#FFFF00": "yellow",
    "#FFFFFF": "white",
    "#FF8C00": "dark_orange",  # Note: dark_orange might map to orange1 in Rich
}

CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Get the themes directory (in the package)
THEMES_DIR = _resolve_themes_dir()

# User-created themes live next to the config, so they survive reinstalls and
# need no write access to the installed package.
USER_THEMES_DIR = Path(CONFIG_DIR) / "themes"

# Names that would collide with `gc theme` subcommand flags.
RESERVED_THEME_NAMES = frozenset({"list", "save-as", "delete", "new"})

# Hardcoded fallback default colors
_FALLBACK_DEFAULT_COLORS = {
    # UI Colors
    "border": "#13A10E",  # rgb(19, 161, 14) - green border
    "active_button": "#13A10E",  # Active button background
    
    # Widget Base Colors
    "widget_border": "#13A10E",  # Widget border color
    "default_plot": "#0080FF",  # Default plot color (blue)
    
    # CPU Widget
    "cpu_bar": "#0080FF",  # CPU bar chart color
    "cpu_disk_used": "#FF00FF",  # magenta
    "cpu_disk_free": "#00FFFF",  # cyan
    
    # Memory Widget
    "memory_ram": "#FF8C00",  # orange1
    "memory_ram_used": "#FF8C00",  # orange3
    "memory_swap": "#00FFFF",  # cyan
    
    # Network Widget
    "network_download": "#FF8C00",  # dark_orange
    "network_upload": "#00FF00",  # green
    "network_plot_download": "#FF8C00",  # dark_orange
    "network_plot_upload": "#00FF00",  # green
    
    # Disk Widget
    "disk_read": "#FF00FF",  # magenta
    "disk_write": "#00FFFF",  # cyan
    "disk_used": "#FF00FF",  # magenta
    "disk_free": "#00FFFF",  # cyan
    "disk_plot_read": "#FF00FF",  # magenta
    "disk_plot_write": "#00FFFF",  # cyan
    
    # GPU Widget
    "gpu_ram": "#00FF00",  # green
    "gpu_usage": "#00FFFF",  # cyan
    "gpu_ram_warning": "#FF0000",  # red
    "gpu_plot_ram": "#00FF00",  # green
    "gpu_plot_usage": "#00FFFF",  # cyan
    
    # Temperature Widget
    "temp_cool": "#00FFFF",  # cyan (< 30°C)
    "temp_normal": "#00FF00",  # green (30-50°C)
    "temp_warm": "#FFFF00",  # yellow (50-70°C)
    "temp_hot": "#FF8C00",  # orange3 (70-85°C)
    "temp_critical": "#FF0000",  # red (>= 85°C)
    "temp_plot_1": "#FF8C00",  # orange1
    "temp_plot_2": "#00FF00",  # green
    "temp_plot_3": "#0080FF",  # blue
    "temp_warning_line": "#FF0000",  # red (80°C)
    "temp_caution_line": "#FF8C00",  # orange1 (60°C)
    
    # General
    "high_value": "#FF0000",  # bright_red for high values
    "white": "#FFFFFF",  # white
    
    # Textual design-token equivalents (single theme drives full UI)
    "surface": "#1A1A1A",
    "background": "#0D0D0D",
    "text": "#E0E0E0",
    "accent": "#13A10E",
    "boost": "#252525",
    "panel": "#1E1E1E",

    # UI Element Colors (tabs, header, footer, selection)
    "tab_active_bg": "#13A10E",  # Active tab background
    "tab_active_fg": "#000000",  # Active tab foreground
    "tab_inactive_bg": "#1A1A1A",  # Inactive tab background
    "tab_inactive_fg": "#888888",  # Inactive tab foreground
    "header_bg": "#13A10E",  # Header background
    "footer_bg": "#1A1A1A",  # Footer background
    "footer_fg": "#E0E0E0",  # Footer text (binding descriptions)
    "footer_key_bg": "#13A10E",  # Footer key background
    "footer_key_fg": "#000000",  # Footer key foreground
    "selection_highlight": "#13A10E",  # Selection list highlight
    "text_on_accent": "#000000",  # Text on accent backgrounds (header, active tab, footer keys)
}

# Default color configuration in hex format (will be loaded from classic theme if available)
DEFAULT_COLORS = _FALLBACK_DEFAULT_COLORS.copy()

# The editable palette, grouped for the Settings colour editor. Every key a
# theme JSON defines appears here exactly once; the order is the display order.
COLOR_GROUPS: List[Tuple[str, Tuple[str, ...]]] = [
    ("Base", (
        "background", "surface", "panel", "boost", "text", "accent", "border",
        "active_button", "widget_border", "default_plot",
    )),
    ("CPU", ("cpu_bar", "cpu_disk_used", "cpu_disk_free")),
    ("Memory", ("memory_ram", "memory_ram_used", "memory_swap")),
    ("Network", (
        "network_download", "network_upload",
        "network_plot_download", "network_plot_upload",
    )),
    ("Disk", (
        "disk_read", "disk_write", "disk_used", "disk_free",
        "disk_plot_read", "disk_plot_write",
    )),
    ("GPU", (
        "gpu_ram", "gpu_usage", "gpu_ram_warning", "gpu_plot_ram", "gpu_plot_usage",
    )),
    ("Temperature", (
        "temp_cool", "temp_normal", "temp_warm", "temp_hot", "temp_critical",
        "temp_plot_1", "temp_plot_2", "temp_plot_3",
        "temp_warning_line", "temp_caution_line",
    )),
    ("Chrome", (
        "tab_active_bg", "tab_active_fg", "tab_inactive_bg", "tab_inactive_fg",
        "header_bg", "footer_bg", "footer_fg", "footer_key_bg", "footer_key_fg",
        "selection_highlight", "text_on_accent",
    )),
    ("General", ("high_value", "white")),
]

# Flat list of editable keys, in display order.
COLOR_KEYS: Tuple[str, ...] = tuple(k for _, keys in COLOR_GROUPS for k in keys)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")

# Theme keys used for CSS; all UI colors come from these (no hardcoded colors in app CSS).
CSS_TOKEN_KEYS = (
    "background", "surface", "panel", "border", "text", "text_on_accent",
    "accent", "tab_active_bg", "tab_active_fg", "tab_inactive_bg", "tab_inactive_fg",
    "header_bg", "header_fg", "footer_bg", "footer_fg", "footer_key_bg", "footer_key_fg",
    "selection_highlight",
)


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple for CSS. Uses fallback if invalid."""
    hex_color = (hex_color or "").strip().lstrip("#")
    if len(hex_color) != 6:
        return (0, 0, 0)
    try:
        return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def is_valid_hex(value: str) -> bool:
    """True if ``value`` is a 6-digit hex colour, with or without a leading '#'."""
    return bool(_HEX_RE.match((value or "").strip()))


def normalize_hex(value: str) -> Optional[str]:
    """Return ``value`` as ``#RRGGBB`` (uppercase), or None if it isn't a hex colour."""
    value = (value or "").strip()
    if not is_valid_hex(value):
        return None
    return "#" + value.lstrip("#").upper()


def _read_config() -> Dict:
    """Return the parsed config file, or an empty dict if missing/corrupt."""
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
        return config if isinstance(config, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_config(config: Dict) -> bool:
    """Write the config file, creating its directory. False on any failure."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        return True
    except OSError:
        return False


def get_theme_tokens(colors: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Return a dict of CSS-ready token values (rgb(...)) from the theme.

    Single source of truth for app CSS: no hardcoded colors in the app.
    Missing keys are filled from _FALLBACK_DEFAULT_COLORS. Use header_fg and
    tab_active_fg for text on accent (default text_on_accent) so header/tabs
    stay readable.
    """
    c = dict(_FALLBACK_DEFAULT_COLORS)
    if colors:
        c.update(colors)

    def rgb(key: str, default_hex: str = "#000000") -> str:
        h = c.get(key, default_hex)
        r, g, b = hex_to_rgb(h)
        return f"rgb({r}, {g}, {b})"

    def rgb_hex(hex_str: str) -> str:
        r, g, b = hex_to_rgb(hex_str or "#000000")
        return f"rgb({r}, {g}, {b})"

    panel_hex = c.get("panel", "#1E1E1E")
    pr, pg, pb = hex_to_rgb(panel_hex)
    panel_dim = f"rgb({pr}, {pg}, {pb}) 60%"

    # Single text color everywhere; text on accent (header, active tab, footer keys) uses text_on_accent.
    text = rgb("text", "#E0E0E0")
    text_on_accent_hex = c.get("tab_active_fg") or c.get("text_on_accent") or "#000000"
    return {
        "bg": rgb("background", "#0D0D0D"),
        "surface": rgb("surface", "#1A1A1A"),
        "panel": rgb("panel", "#1E1E1E"),
        "panel_dim": panel_dim,
        "border": rgb("border", "#13A10E"),
        "text": text,
        "text_on_accent": rgb_hex(text_on_accent_hex),
        "accent": rgb("accent", "#13A10E"),
        "tab_active_bg": rgb("tab_active_bg", "#13A10E"),
        "tab_active_fg": rgb_hex(text_on_accent_hex),
        "tab_inactive_bg": rgb("tab_inactive_bg", "#1A1A1A"),
        "tab_inactive_fg": rgb_hex(c.get("tab_inactive_fg") or c.get("text") or "#E0E0E0"),
        "header_bg": rgb("header_bg", "#13A10E"),
        "header_fg": rgb_hex(c.get("header_fg") or c.get("text_on_accent") or "#000000"),
        "footer_bg": rgb("footer_bg", "#1A1A1A"),
        "footer_fg": rgb_hex(c.get("footer_fg") or c.get("text") or "#E0E0E0"),
        "footer_key_bg": rgb("footer_key_bg", "#13A10E"),
        "footer_key_fg": rgb_hex(c.get("footer_key_fg") or c.get("text_on_accent") or "#000000"),
        "selection": rgb("selection_highlight", "#13A10E"),
        # Signal-button backgrounds (GPU process rows); their label uses text_on_accent
        # so it flips with the theme instead of being a hardcoded white/black.
        "danger": rgb("high_value", "#FF0000"),
        "warn": rgb("temp_hot", "#FF8C00"),
        "caution": rgb("temp_warm", "#FFFF00"),
    }


def hex_to_rich_color(hex_color: str) -> str:
    """
    Convert hex color to Rich color name if available, otherwise return hex.
    
    Args:
        hex_color: Hex color string (e.g., "#FF0000")
        
    Returns:
        Rich color name or hex color string
    """
    hex_color = hex_color.upper()
    return HEX_TO_RICH.get(hex_color, hex_color)


def load_colors() -> Dict[str, str]:
    """
    Load colors from config file, merging with defaults.
    
    Returns:
        Dictionary of color names to hex values
    """
    colors = DEFAULT_COLORS.copy()
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                if "colors" in config and isinstance(config["colors"], dict):
                    # Merge user colors with defaults
                    colors.update(config["colors"])
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    
    return colors


def get_color(color_name: str, default: Optional[str] = None) -> str:
    """
    Get a color value by name from config.
    
    Args:
        color_name: Name of the color to retrieve
        default: Default hex color if not found (uses DEFAULT_COLORS if None)
        
    Returns:
        Hex color string
    """
    colors = load_colors()
    if color_name in colors:
        return colors[color_name]
    if default:
        return default
    return DEFAULT_COLORS.get(color_name, "#000000")


def get_rich_color(color_name: str, default: Optional[str] = None) -> str:
    """
    Get a Rich color name by color name from config.
    
    Args:
        color_name: Name of the color to retrieve
        default: Default hex color if not found
        
    Returns:
        Rich color name or hex color string
    """
    hex_color = get_color(color_name, default)
    return hex_to_rich_color(hex_color)


def slugify_theme_name(name: str) -> str:
    """
    Turn a user-typed theme name into a safe filename stem.

    Lowercases, collapses runs of non-alphanumerics into single hyphens and
    trims leading/trailing hyphens, so "My Cool Theme!" -> "my-cool-theme".

    Args:
        name: Raw name as typed by the user.

    Returns:
        The slug, or "" if nothing usable remained.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip()).strip("-").lower()
    return slug


def _theme_path(theme_name: str, user: bool) -> Path:
    """Path a theme with this name would live at, in the user or builtin dir."""
    directory = USER_THEMES_DIR if user else THEMES_DIR
    return directory / f"{theme_name}.json"


def is_builtin_theme(theme_name: str) -> bool:
    """True if a theme of this name ships with the package."""
    return _theme_path(theme_name, user=False).is_file()


def is_user_theme(theme_name: str) -> bool:
    """True if a theme of this name exists in the user themes directory."""
    return _theme_path(theme_name, user=True).is_file()


def load_theme(theme_name: str) -> Optional[Dict[str, str]]:
    """
    Load a theme by name, preferring the user's themes over the builtins.

    Args:
        theme_name: Name of the theme (without .json extension)

    Returns:
        Dictionary of color names to hex values, or None if theme not found
    """
    for user in (True, False):
        theme_file = _theme_path(theme_name, user)
        if theme_file.is_file():
            try:
                with open(theme_file, "r") as f:
                    colors = json.load(f)
                return colors if isinstance(colors, dict) else None
            except (json.JSONDecodeError, IOError):
                return None
    return None


def save_theme(theme_name: str, colors: Dict[str, str]) -> Tuple[bool, str]:
    """
    Save a palette as a user theme.

    Builtin names are refused rather than shadowed: silently overriding a
    shipped theme would make `gc theme --nord` mean something different per
    machine with no way to tell from the UI.

    Args:
        theme_name: Desired name; slugified before use.
        colors: Palette to write. Missing keys are filled from DEFAULT_COLORS
            so every saved theme is complete.

    Returns:
        (True, slug) on success, or (False, error message) on failure.
    """
    slug = slugify_theme_name(theme_name)
    if not slug:
        return False, "Theme name must contain a letter or digit"
    if slug in RESERVED_THEME_NAMES:
        return False, f"'{slug}' is a reserved name"
    if is_builtin_theme(slug):
        return False, f"'{slug}' is a builtin theme — pick another name"

    payload = {key: colors.get(key, DEFAULT_COLORS.get(key, "#000000")) for key in COLOR_KEYS}
    try:
        USER_THEMES_DIR.mkdir(parents=True, exist_ok=True)
        with open(_theme_path(slug, user=True), "w") as f:
            json.dump(payload, f, indent=4)
    except OSError as exc:
        return False, f"Could not write theme: {exc}"
    return True, slug


def delete_theme(theme_name: str) -> Tuple[bool, str]:
    """
    Delete a user theme. Builtins cannot be deleted.

    Returns:
        (True, name) on success, or (False, error message) on failure.
    """
    if not is_user_theme(theme_name):
        if is_builtin_theme(theme_name):
            return False, f"'{theme_name}' is a builtin theme and cannot be deleted"
        return False, f"No custom theme named '{theme_name}'"
    try:
        _theme_path(theme_name, user=True).unlink()
    except OSError as exc:
        return False, f"Could not delete theme: {exc}"
    return True, theme_name

# Load default colors from classic theme if available
def _load_default_colors() -> Dict[str, str]:
    """Load default colors from classic theme, fallback to hardcoded defaults."""
    classic_theme = load_theme("classic")
    if classic_theme:
        return classic_theme
    return _FALLBACK_DEFAULT_COLORS.copy()

# Update DEFAULT_COLORS with theme if available
DEFAULT_COLORS = _load_default_colors()


def get_user_themes() -> List[str]:
    """Names of themes saved by the user (sorted)."""
    if not USER_THEMES_DIR.is_dir():
        return []
    return sorted(f.stem for f in USER_THEMES_DIR.glob("*.json"))


def get_available_themes() -> List[str]:
    """
    Get list of available theme names: builtins plus the user's own.

    Returns:
        Sorted list of theme names (without .json extension), deduplicated —
        a user theme shadowing a builtin appears once.
    """
    themes = set()
    if THEMES_DIR.is_dir():
        themes.update(f.stem for f in THEMES_DIR.glob("*.json"))
    themes.update(get_user_themes())
    return sorted(themes)


def apply_theme(theme_name: str) -> bool:
    """
    Apply a theme to the config file.

    Records the theme's *name* alongside its colours so a later single-colour
    edit can still say which theme it started from.

    Args:
        theme_name: Name of the theme to apply

    Returns:
        True if theme was applied successfully, False otherwise
    """
    theme_colors = load_theme(theme_name)
    if theme_colors is None:
        return False

    config = _read_config()
    config["colors"] = theme_colors.copy()
    config["theme"] = theme_name
    config["theme_modified"] = False
    return _write_config(config)


def get_active_theme() -> Tuple[Optional[str], bool]:
    """
    Identify the theme the current config came from.

    Returns:
        ``(name, modified)``. ``name`` is None when the palette matches no
        known theme; ``modified`` is True when the config's colours have been
        edited away from that theme's file.

    Configs written before the ``theme`` key existed have no recorded name, so
    fall back to matching the palette against every theme.
    """
    config = _read_config()
    current_colors = config.get("colors", {})
    if not isinstance(current_colors, dict):
        current_colors = {}

    name = config.get("theme")
    if isinstance(name, str) and name in get_available_themes():
        return name, (load_theme(name) or {}) != current_colors

    for candidate in get_available_themes():
        if (load_theme(candidate) or {}) == current_colors:
            return candidate, False
    return None, bool(current_colors)


def set_color(color_key: str, hex_value: str) -> bool:
    """
    Change a single colour in the config, leaving everything else intact.

    Args:
        color_key: One of COLOR_KEYS.
        hex_value: A hex colour, with or without the leading '#'.

    Returns:
        True if the config was updated, False on bad input or write failure.
    """
    normalized = normalize_hex(hex_value)
    if color_key not in COLOR_KEYS or normalized is None:
        return False

    config = _read_config()
    colors = config.get("colors")
    if not isinstance(colors, dict):
        colors = DEFAULT_COLORS.copy()
    colors[color_key] = normalized
    config["colors"] = colors

    # The palette no longer matches the theme it came from — unless the edit
    # happens to restore the original value.
    name = config.get("theme")
    theme_colors = load_theme(name) if isinstance(name, str) else None
    config["theme_modified"] = theme_colors != colors if theme_colors else True
    return _write_config(config)


def ensure_colors_in_config():
    """
    Ensure colors section exists in config file with defaults.
    This is called when creating a new config file.
    """
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        else:
            config = {}
        
        if "colors" not in config or not isinstance(config["colors"], dict):
            config["colors"] = DEFAULT_COLORS.copy()
            # DEFAULT_COLORS is the classic theme, so name it as such rather
            # than leaving the palette anonymous.
            config.setdefault("theme", "classic")
            config["theme_modified"] = False
            # Ensure config directory exists
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
        else:
            # Merge any missing default colors
            updated = False
            for key, value in DEFAULT_COLORS.items():
                if key not in config["colors"]:
                    config["colors"][key] = value
                    updated = True
            # Configs written before themes were tracked by name: recover the
            # name by matching the palette, so a later single-colour edit still
            # knows which theme it started from.
            if "theme" not in config:
                for candidate in get_available_themes():
                    if (load_theme(candidate) or {}) == config["colors"]:
                        config["theme"] = candidate
                        config["theme_modified"] = False
                        break
                updated = True
            if updated:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(config, f, indent=4)
    except Exception:
        pass  # Silently fail if config can't be written


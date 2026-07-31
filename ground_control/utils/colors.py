"""
Color configuration utility for loading and managing colors from config file.
Converts hex colors to Rich color names or hex format as needed.
"""
import json
import os
from typing import Dict, Optional, List
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


def load_theme(theme_name: str) -> Optional[Dict[str, str]]:
    """
    Load a theme from the themes directory.
    
    Args:
        theme_name: Name of the theme (without .json extension)
        
    Returns:
        Dictionary of color names to hex values, or None if theme not found
    """
    theme_file = THEMES_DIR / f"{theme_name}.json"
    if theme_file.exists():
        try:
            with open(theme_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None

# Load default colors from classic theme if available
def _load_default_colors() -> Dict[str, str]:
    """Load default colors from classic theme, fallback to hardcoded defaults."""
    classic_theme = load_theme("classic")
    if classic_theme:
        return classic_theme
    return _FALLBACK_DEFAULT_COLORS.copy()

# Update DEFAULT_COLORS with theme if available
DEFAULT_COLORS = _load_default_colors()


def get_available_themes() -> List[str]:
    """
    Get list of available theme names.
    
    Returns:
        List of theme names (without .json extension)
    """
    themes = []
    if THEMES_DIR.exists():
        for theme_file in THEMES_DIR.glob("*.json"):
            themes.append(theme_file.stem)
    return sorted(themes)


def apply_theme(theme_name: str) -> bool:
    """
    Apply a theme to the config file.
    
    Args:
        theme_name: Name of the theme to apply
        
    Returns:
        True if theme was applied successfully, False otherwise
    """
    theme_colors = load_theme(theme_name)
    if theme_colors is None:
        return False
    
    try:
        # Load existing config or create new one
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        else:
            config = {}
        
        # Update colors section with theme
        config["colors"] = theme_colors.copy()
        
        # Ensure config directory exists
        os.makedirs(CONFIG_DIR, exist_ok=True)
        
        # Write updated config
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        
        return True
    except Exception:
        return False


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
            if updated:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(config, f, indent=4)
    except Exception:
        pass  # Silently fail if config can't be written


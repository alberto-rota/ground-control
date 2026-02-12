import re

def ansi2rich(text: str) -> str:
    """Replace ANSI color sequences with Rich markup."""
    # Define a mapping of ANSI codes to Rich markup colors or styles
    color_map = {
        '12': 'blue',
        '10': 'green',
        '9': 'magenta',
        '2': 'brown',
        '13': 'red',
        '7': 'bold',
        
        # Add more mappings as needed
    }
    
    # Regular expression to match ANSI escape sequences (foreground 38;5;<code>)
    ansi_pattern = re.compile(r'\x1b\[38;5;(\d+)m(.*?)\x1b\[0m')
    
    def replace_ansi_with_rich(match):
        ansi_code = match.group(1)
        text_content = match.group(2)
        rich_color = color_map.get(ansi_code, None)
        if rich_color:
            return f"[{rich_color}]{text_content}[/]"
        else:
            # If the ANSI code is not in the map, return the text without formatting
            return text_content
    
    # Apply the replacement
    text = ansi_pattern.sub(replace_ansi_with_rich, text)
    
    # Clean up any remaining unsupported or stray ANSI sequences
    # Matches all ANSI escape sequences
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    
    return text


def align(input_str, max_length, alignment):
    if alignment == "left":
        # Trim the string from the right side if it exceeds the max_length
        input_str = input_str[:max_length]
        return input_str.ljust(max_length)
    elif alignment == "right":
        # Trim the string from the left side if it exceeds the max_length
        input_str = input_str[-max_length:]
        return input_str.rjust(max_length)
    elif alignment == "center":
        # For center alignment, take characters from the middle if trimming is needed
        if len(input_str) > max_length:
            start = (len(input_str) - max_length) // 2
            input_str = input_str[start : start + max_length]
        return input_str.center(max_length)
    else:
        raise ValueError("Alignment must be 'left', 'right', or 'center'.")


def format_size(value, in_gb: bool = False) -> str:
    """Format a size (bytes or GB) as a string with the appropriate unit (KB, MB, GB, TB).
    Values are rounded to integers; no decimal points are used.

    Args:
        value: Size either in bytes (if in_gb=False) or in gigabytes (if in_gb=True).
        in_gb: If True, value is in GB (float); if False, value is in bytes (int/float).

    Returns:
        String like "512 MB", "1 GB", "2 TB".
    """
    if in_gb:
        size_gb = float(value)
    else:
        size_gb = float(value) / (1024 ** 3)
    size_gb = max(0.0, size_gb)
    if size_gb >= 1024:
        return f"{round(size_gb / 1024)} TB"
    if size_gb >= 1:
        return f"{round(size_gb)} GB"
    if size_gb >= 1 / 1024:  # >= 1 MB
        size_mb = size_gb * 1024
        return f"{round(size_mb)} MB"
    if size_gb >= 1 / (1024 ** 2):  # >= 1 KB
        size_kb = size_gb * 1024 * 1024
        return f"{round(size_kb)} KB"
    return f"{round(size_gb * 1024 ** 3)} B"


def format_throughput(value_mb_per_s: float) -> str:
    """Format a throughput value (given in MB/s) with the appropriate unit (KB/s, MB/s, GB/s).
    Values are rounded to integers; no decimal points are used.

    Args:
        value_mb_per_s: Throughput in MB/s (can be float).

    Returns:
        String like "512 KB/s", "1 MB/s", "2 GB/s".
    """
    v = max(0.0, float(value_mb_per_s))
    if v >= 1024:
        return f"{round(v / 1024)} GB/s"
    if v >= 1:
        return f"{round(v)} MB/s"
    if v >= 0.001:
        v_kb = v * 1024
        return f"{round(v_kb)} KB/s"
    return f"{round(v)} MB/s"


# Regex to match plotext bottom border: └ followed by horizontal line chars, then ┘
# Matches Unicode horizontal (─) and ASCII hyphen (-); length is taken from the match.
_PLOT_BOTTOM_BORDER_RE = re.compile(r"└([─\-]+)┘")


def _timeframe_inner_content(history_size: int, inner_len: int) -> str:
    """Build the inner part of the timeframe line (0, half, size) with length inner_len.
    Uses time unit: 's' for seconds when size < 60, 'm' for minutes when size >= 60 (no space)."""
    if inner_len <= 0:
        return ""
    arr = ["─"] * inner_len
    size = max(1, int(history_size))
    half = size // 2
    if size > 60:
        unit = "m"
        left_val, mid_val, right_val = 0, half // 60, size // 60
    else:
        unit = "s"
        left_val, mid_val, right_val = 0, half, size
    s0 = f"{left_val}{unit}"
    half_str = f"{mid_val}{unit}"
    size_str = f"{right_val}{unit}"
    for i, c in enumerate(s0):
        if i < inner_len:
            arr[i] = c
    mid_start = max(0, (inner_len - len(half_str)) // 2)
    for i, c in enumerate(half_str):
        if mid_start + i < inner_len:
            arr[mid_start + i] = c
    right_start = inner_len - len(size_str)
    for i, c in enumerate(size_str):
        if right_start + i >= 0 and right_start + i < inner_len:
            arr[right_start + i] = c
    return "".join(arr)


def substitute_plot_timeframe(plot_text: str, history_size: int) -> str:
    """Replace the plotext bottom border with a timeframe line (0, half, full history) using regex.

    Finds the bottom border via regex (└ + horizontal line + ┘), then replaces it with
    a line of the same length showing 0 at left, half at center, full size at right.
    Robust to plotext's actual width and any variation in the horizontal character.

    Args:
        plot_text: The built plot string (e.g. from plt.build() then ansi2rich()).
        history_size: Number of samples in the plot window (e.g. 120).

    Returns:
        plot_text with the first matching bottom border replaced by the timeframe line.
    """
    def repl(match: re.Match) -> str:
        inner = match.group(1)
        n = len(inner)
        return "└" + _timeframe_inner_content(history_size, n) + "┘"
    return _PLOT_BOTTOM_BORDER_RE.sub(repl, plot_text, count=1)
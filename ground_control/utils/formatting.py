import re

def ansi2rich(text: str) -> str:
    """Replace ANSI color sequences with Rich markup."""
    color_map = {
        '12': 'blue',
        '10': 'green',
        '7': 'bold',
    }
    
    for ansi_code, rich_color in color_map.items():
        pattern = fr'\x1b\[38;5;{ansi_code}m(.*?)\x1b\[0m'
        text = re.sub(pattern, fr'[{rich_color}]\1[/]', text)
    return text

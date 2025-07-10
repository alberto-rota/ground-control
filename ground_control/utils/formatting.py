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
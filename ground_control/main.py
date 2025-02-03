from .app import GroundControl
import os
import subprocess
import sys
import json
from pathlib import Path

def get_default_editor():
    """Get the default text editor from environment variables."""
    if sys.platform.startswith('win'):
        return os.environ.get('EDITOR', 'notepad')
    return os.environ.get('EDITOR', os.environ.get('VISUAL', 'nano'))

def ensure_config_exists():
    """Ensure the config directory and file exist, creating them if necessary."""
    config_dir = Path.home() / '.config' / 'ground-control'
    config_file = config_dir / 'config.json'
    
    # Create directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # Create default config file if it doesn't exist
    if not config_file.exists():
        default_config = {
            # Add your default configuration settings here
            "example_setting": "default_value"
        }
        with open(config_file, 'w') as f:
            json.dump(default_config, f, indent=4)
    
    return config_file

def edit_config():
    """Open the configuration file in the default text editor."""
    try:
        config_file = ensure_config_exists()
        editor = get_default_editor()
        
        # Open the editor with the config file
        if sys.platform.startswith('win'):
            os.startfile(str(config_file))
        else:
            subprocess.call([editor, str(config_file)])
            
        return True
    except Exception as e:
        print(f"Error opening config file: {e}", file=sys.stderr)
        return False

def config_command():
    """Handle the 'groundcontrol config' command."""
    success = edit_config()
    sys.exit(0 if success else 1)


def main():
    appl = GroundControl()
    appl.run()
    
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        config_command()

    main()

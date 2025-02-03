import os
import subprocess
import sys
import json
from pathlib import Path
import click
from platformdirs import user_config_dir
from .app import GroundControl

# Set up the user-specific config file path
CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

@click.group()
def cli():
    pass

@cli.command()
def config():
    # Create config file if it doesn't exist
    config_dir = os.path.dirname(CONFIG_FILE)
    os.makedirs(config_dir, exist_ok=True)
    if not os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            f.write("{}")  # Initialize with empty JSON

    # Open config file in default editor
    if sys.platform.startswith('darwin'):
        subprocess.call(('open', CONFIG_FILE))
    elif os.name == 'nt':
        os.startfile(CONFIG_FILE)
    elif os.name == 'posix':
        editor = os.environ.get("EDITOR")
        if editor:
            subprocess.call((editor, CONFIG_FILE))
        else:
            subprocess.call(('nano', CONFIG_FILE))

def main():
    appl = GroundControl()
    appl.run()

def entry():
    if len(sys.argv) == 1:
        main()
    else:
        cli()

if __name__ == "__main__":
    entry()

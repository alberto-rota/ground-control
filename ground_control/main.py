import os
import subprocess
import sys
import json
import logging
from pathlib import Path
import click
from platformdirs import user_config_dir
from .app import GroundControl
from .utils.colors import DEFAULT_COLORS, apply_theme, get_available_themes

# Set up the user-specific config file path
CONFIG_DIR = user_config_dir("ground-control")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

def setup_logging(also_log_to_cwd=False):
    """Set up logging to config directory, optionally also to current working directory."""
    # Ensure config directory exists
    os.makedirs(CONFIG_DIR, exist_ok=True)
    
    # Log file in config directory (always used)
    config_log_file = os.path.join(CONFIG_DIR, "ground_control.log")
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Handler for config directory (always)
    config_handler = logging.FileHandler(config_log_file)
    config_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    config_handler.setFormatter(formatter)
    logger.addHandler(config_handler)
    
    # Handler for current working directory (if --log flag is set)
    if also_log_to_cwd:
        cwd_log_file = os.path.join(os.getcwd(), "ground_control.log")
        cwd_handler = logging.FileHandler(cwd_log_file)
        cwd_handler.setLevel(logging.DEBUG)
        cwd_handler.setFormatter(formatter)
        logger.addHandler(cwd_handler)

def format_help(ctx, formatter):
    """Custom help formatter that includes subcommand options."""
    # Write the main help
    with formatter.section('Usage'):
        formatter.write_text(ctx.get_usage())
    
    with formatter.section('Description'):
        formatter.write_text(ctx.command.help or ctx.command.short_help or '')
    
    # Write main command options
    opts = []
    for param in ctx.command.params:
        if isinstance(param, click.Option) and not param.hidden:
            opts.append(param)
    
    if opts:
        with formatter.section('Options'):
            formatter.write_dl([(opt.get_help_record(ctx)[0], opt.get_help_record(ctx)[1]) for opt in opts])
    
    # Write subcommands with their options
    commands = []
    for name, cmd in ctx.command.commands.items():
        if not cmd.hidden:
            commands.append((name, cmd))
    
    if commands:
        with formatter.section('Commands'):
            for name, cmd in sorted(commands):
                # Get subcommand help
                sub_ctx = click.Context(cmd, info_name=name, parent=ctx)
                help_text = cmd.help or cmd.short_help or ''
                if help_text:
                    # Extract first line of help text for brief description
                    brief_help = help_text.split('\n')[0].strip()
                else:
                    brief_help = ''
                
                # Get subcommand options (excluding hidden ones)
                sub_opts = []
                for param in cmd.params:
                    if isinstance(param, click.Option) and not param.hidden:
                        sub_opts.append(param)
                
                # Format subcommand with its options
                if brief_help:
                    formatter.write_text(f'{name}: {brief_help}')
                else:
                    formatter.write_text(f'{name}')
                
                if sub_opts:
                    for opt in sub_opts:
                        opt_record = opt.get_help_record(sub_ctx)
                        formatter.write_text(f'  {opt_record[0]}')
                        if opt_record[1]:
                            formatter.write_text(f'    {opt_record[1]}')
                formatter.write_text('')  # Empty line between commands

@click.group(invoke_without_command=True)
@click.option('--log', is_flag=True, help='Also save log file in current working directory')
@click.option('--cpu', '-c', is_flag=True, help='Show CPU widgets')
@click.option('--gpu', '-g', is_flag=True, help='Show GPU widgets')
@click.option('--ram', '-r', is_flag=True, help='Show Memory widgets')
@click.option('--disk', '-d', is_flag=True, help='Show Disk widgets')
@click.option('--net', '-n', is_flag=True, help='Show Network widgets')
@click.option('--temp', '-t', is_flag=True, help='Show Temperature widgets')
@click.pass_context
def cli(ctx, log, cpu, gpu, ram, disk, net, temp):
    """Ground Control - Terminal System Monitor"""
    if ctx.invoked_subcommand is None:
        # No subcommand specified, run the app
        setup_logging(also_log_to_cwd=log)
        
        allowed_types = set()
        if cpu: allowed_types.add('cpu')
        if gpu: allowed_types.add('gpu')
        if ram: allowed_types.add('ram')
        if disk: allowed_types.add('disk')
        if net: allowed_types.add('net')
        if temp: allowed_types.add('temp')
        
        # If no specific flags are set, pass None (allow all)
        if not allowed_types:
            allowed_types = None
            
        appl = GroundControl(allowed_types=allowed_types)
        appl.run()
    else:
        # Store log flag in context for subcommands if needed
        ctx.ensure_object(dict)
        ctx.obj['log'] = log

# Override the format_help method
cli.format_help = lambda ctx, formatter: format_help(ctx, formatter)

def get_default_config():
    """Generate default configuration with all default values including colors."""
    return {
        "selected": {},
        "layout": "grid",
        "refresh_rate": 1.0,
        "history_size": 120,
        "colors": DEFAULT_COLORS.copy()
    }

@cli.command()
@click.option('--reset', is_flag=True, help='Reset configuration to default values')
@click.option('--path', is_flag=True, help='Display the path of the config file')
def config(reset, path):
    """Manage configuration file.
    
    By default, opens the configuration file in your default editor.
    
    Options:
        --reset: Reset configuration to default values
        --path: Display the path of the config file in the console
    """
    if path:
        click.echo(CONFIG_FILE)
        return
    
    config_dir = os.path.dirname(CONFIG_FILE)
    os.makedirs(config_dir, exist_ok=True)
    
    if reset:
        # Reset config to defaults
        default_config = get_default_config()
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)
        click.echo(f"Configuration reset to defaults. Config file: {CONFIG_FILE}")
        return
    
    # Create config file if it doesn't exist
    if not os.path.isfile(CONFIG_FILE):
        default_config = get_default_config()
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)

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

# Create theme command with dynamic options
# We need to add options for each theme at command definition time
available_themes_at_init = get_available_themes()

# Build the theme command decorator dynamically
theme_options = [click.option('--list', 'list_themes', is_flag=True, help='List all available themes')]
for theme_name in available_themes_at_init:
    theme_options.append(
        click.option(f'--{theme_name}', f'{theme_name.replace("-", "_")}_flag', 
                    is_flag=True, help=f'Apply {theme_name.capitalize()} theme')
    )

def apply_theme_decorators(func):
    """Apply all theme option decorators."""
    for decorator in reversed(theme_options):
        func = decorator(func)
    return func

@cli.command()
@apply_theme_decorators
def theme(list_themes, **kwargs):
    """Manage color themes.
    
    Apply a theme to change the color scheme of Ground Control.
    
    Examples:
        gc theme --list              # List all available themes
        gc theme --monokai           # Apply monokai theme
        gc theme --classic            # Apply classic theme
    """
    available_themes = get_available_themes()
    
    if list_themes:
        click.echo("Available themes:")
        for name in available_themes:
            click.echo(f"  - {name}")
        return
    
    # Find which theme flag was set
    theme_name = None
    for key, value in kwargs.items():
        if key.endswith('_flag') and value:
            theme_name = key[:-5].replace('_', '-')  # Remove '_flag' suffix and convert _ to -
            break
    
    if not theme_name:
        click.echo("No theme specified. Use --list to see available themes.")
        click.echo("Usage: gc theme --<theme-name>")
        click.echo(f"Example: gc theme --{available_themes[0] if available_themes else 'monokai'}")
        return
    
    if theme_name in available_themes:
        if apply_theme(theme_name):
            click.echo(f"Theme '{theme_name}' applied successfully!")
            click.echo(f"Config file updated: {CONFIG_FILE}")
            click.echo("Restart Ground Control to see the changes.")
        else:
            click.echo(f"Error: Failed to apply theme '{theme_name}'.", err=True)
    else:
        click.echo(f"Error: Theme '{theme_name}' not found.", err=True)
        click.echo(f"Available themes: {', '.join(available_themes)}")

def entry():
    cli()

if __name__ == "__main__":
    entry()

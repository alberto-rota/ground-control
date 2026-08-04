import os
import subprocess
import sys
import json
import logging
from pathlib import Path
import click
from platformdirs import user_config_dir
from .app import DEFAULT_DISK_IGNORE_PREFIXES, GroundControl
from .utils.alerts import DEFAULT_THRESHOLDS, merge_thresholds
from .utils.colors import (
    DEFAULT_COLORS,
    USER_THEMES_DIR,
    apply_theme,
    delete_theme,
    get_available_themes,
    get_user_themes,
    load_colors,
    save_theme,
)

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
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
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

def _parse_gpu_indices(value):
    """Parse --gpu-index string into list of ints. E.g. '0' -> [0], '0,1,2' -> [0,1,2]. Raises BadParameter on error."""
    if not value or not value.strip():
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        return None
    indices = []
    for p in parts:
        try:
            indices.append(int(p))
        except ValueError:
            from click import BadParameter
            raise BadParameter(f"Invalid GPU index {p!r}; expected integers like 0 or 0,1,2")
    return indices


def _load_threshold_config():
    """Read just the alert settings out of the config file, tolerating absence."""
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    raw = config.get("disk_ignore_prefixes", ", ".join(DEFAULT_DISK_IGNORE_PREFIXES))
    if isinstance(raw, list):
        prefixes = [str(p).strip() for p in raw if str(p).strip()]
    else:
        prefixes = [p.strip() for p in str(raw).split(",") if p.strip()]
    return (merge_thresholds(config.get("thresholds")),
            config.get("alerts_enabled", True),
            prefixes)


def run_once(as_json: bool, check: bool, interval: float, all_gpus: bool,
             debug: bool, all_mounts: bool = False) -> int:
    """
    Collect one snapshot, print it, and return the process exit code.

    Runs entirely outside Textual so it is safe in cron, CI and health checks.
    Errors go to stderr and produce exit code 3, leaving stdout either valid
    JSON or empty -- a consumer piping into `jq` never sees a half-written
    document.
    """
    # Imported lazily: starting a TUI-less snapshot should not pay for the
    # widget/plotting imports the app pulls in.
    from .utils.snapshot import build_snapshot, exit_code_for, render_text, sample_twice
    from .utils.system_metrics import SystemMetrics

    try:
        thresholds, alerts_enabled, ignore_prefixes = _load_threshold_config()
        system_metrics = SystemMetrics(all_gpus=all_gpus)
        sample_twice(system_metrics, interval=interval)
        snapshot = build_snapshot(
            system_metrics, thresholds=thresholds,
            disk_ignore_prefixes=[] if all_mounts else ignore_prefixes)

        if not alerts_enabled:
            # Alerting is off for this user: report the readings without verdicts.
            snapshot["alerts"] = []
            snapshot["status"] = "ok"

        if as_json:
            click.echo(json.dumps(snapshot, indent=2, default=str))
        else:
            click.echo(render_text(snapshot))

        return exit_code_for(snapshot.get("status", "ok")) if check else 0
    except Exception as e:  # noqa: BLE001 - a snapshot must not dump a traceback
        if debug:
            raise
        click.echo(f"Error: could not collect metrics: {e}", err=True)
        return 3


def run_stream(interval: float, all_gpus: bool, debug: bool,
               all_mounts: bool = False, max_seconds: float = 3600.0) -> int:
    """
    Emit one JSON snapshot per line, forever, until told to stop.

    This is the collector half of job monitoring. ``gc`` on a login node starts
    one of these *inside* the job's allocation and reads its lines; all the
    psutil/NVML work then happens on the compute node, once per line, and the
    login node only parses JSON. Compare with ``--once``, where every sample
    pays for a new Slurm job step plus a fresh interpreter (seconds, not
    milliseconds).

    The output is newline-delimited JSON (one compact object per line, flushed
    immediately) rather than a single pretty document, so a reader can consume it
    incrementally with ``readline``. Diagnostics go to stderr, keeping stdout a
    clean stream.

    Termination is deliberately over-determined, because this process runs in
    someone's allocation: SIGTERM/SIGINT exit cleanly, a closed stdout (reader
    gone) exits cleanly, and ``max_seconds`` expires the stream even if no signal
    ever arrives.
    """
    import signal

    from .utils.snapshot import iter_snapshots
    from .utils.system_metrics import SystemMetrics

    stop = {"flag": False}

    def _request_stop(_signum, _frame):
        # Flag rather than exit: finishing the current line keeps stdout valid.
        stop["flag"] = True

    for signame in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, signame, None)
        if sig is not None:
            try:
                signal.signal(sig, _request_stop)
            except (ValueError, OSError):
                pass  # not the main thread, or platform lacks it

    try:
        thresholds, alerts_enabled, ignore_prefixes = _load_threshold_config()
        system_metrics = SystemMetrics(all_gpus=all_gpus)
        snapshots = iter_snapshots(
            system_metrics, thresholds=thresholds,
            disk_ignore_prefixes=[] if all_mounts else ignore_prefixes,
            interval=interval,
            max_seconds=max_seconds if max_seconds and max_seconds > 0 else None,
        )
        for snapshot in snapshots:
            if not alerts_enabled:
                snapshot["alerts"] = []
                snapshot["status"] = "ok"
            # separators= keeps the line compact; a stream is parsed, not read.
            sys.stdout.write(json.dumps(snapshot, default=str,
                                        separators=(",", ":")) + "\n")
            sys.stdout.flush()
            if stop["flag"]:
                break
        return 0
    except (BrokenPipeError, KeyboardInterrupt):
        # The reader went away. Nothing to report and nowhere to report it.
        return 0
    except Exception as e:  # noqa: BLE001 - a collector must not dump a traceback
        if debug:
            raise
        click.echo(f"Error: could not stream metrics: {e}", err=True)
        return 3


@click.group(invoke_without_command=True)
@click.option('--log', is_flag=True, help='Also save log file in current working directory')
@click.option('--debug', is_flag=True, help='Run in debug mode (do not catch errors).')
@click.option('--cpu', '-c', is_flag=True, help='Show CPU widgets')
@click.option('--gpu', '-g', is_flag=True, help='Show GPU widgets (all GPUs by default).')
@click.option('--gpu-index', type=str, default=None, metavar='IDX',
             help='Filter to specific GPU(s): 0, 0,1, etc. Use with -g (e.g. gc -g --gpu-index 0).')
@click.option('--all-gpus', is_flag=True,
             help='Show every physical GPU, ignoring CUDA_VISIBLE_DEVICES / Slurm allocation.')
@click.option('--squeue', is_flag=True,
             help='Add a Slurm panel and prompt for which of your running jobs to monitor.')
@click.option('--ram', '-r', is_flag=True, help='Show Memory widgets')
@click.option('--disk', '-d', is_flag=True, help='Show Disk widgets')
@click.option('--net', '-n', is_flag=True, help='Show Network widgets')
@click.option('--temp', '-t', is_flag=True, help='Show Temperature widgets')
@click.option('--once', is_flag=True,
             help='Print one metrics snapshot and exit, instead of running the TUI.')
@click.option('--json', 'as_json', is_flag=True,
             help='With --once, emit JSON instead of a human-readable summary.')
@click.option('--check', is_flag=True,
             help='With --once, exit 1 on a warning and 2 on a critical threshold breach.')
@click.option('--interval', type=float, default=0.5, metavar='SECONDS',
             help='With --once, gap between the priming and reported sample (default 0.5).')
@click.option('--all-mounts', is_flag=True,
             help='With --once, include mounts normally hidden (snap, /boot/efi).')
@click.option('--stream', is_flag=True,
             help='Emit one JSON snapshot per line every --interval seconds until stopped.')
@click.option('--stream-max-seconds', type=float, default=3600.0, metavar='SECONDS',
             help='With --stream, stop after this long (0 = never; default 3600).')
@click.pass_context
def cli(ctx, log, debug, cpu, gpu, gpu_index, all_gpus, squeue, ram, disk, net, temp,
        once, as_json, check, interval, all_mounts, stream, stream_max_seconds):
    """Ground Control - Terminal System Monitor"""
    if ctx.invoked_subcommand is None:
        if stream:
            # --interval means "gap between samples" here, not the priming gap;
            # the one-shot default of 0.5s would hammer the machine, so a bare
            # --stream ticks once a second like the dashboard does.
            raise SystemExit(run_stream(
                interval=interval if interval != 0.5 else 1.0,
                all_gpus=all_gpus, debug=debug, all_mounts=all_mounts,
                max_seconds=stream_max_seconds))
        if once or as_json or check:
            raise SystemExit(run_once(as_json=as_json, check=check, interval=interval,
                                      all_gpus=all_gpus, debug=debug,
                                      all_mounts=all_mounts))

        # No subcommand specified, run the app
        setup_logging(also_log_to_cwd=log)

        allowed_types = set()
        if cpu: allowed_types.add('cpu')
        if gpu or gpu_index is not None:
            allowed_types.add('gpu')
        if ram: allowed_types.add('ram')
        if disk: allowed_types.add('disk')
        if net: allowed_types.add('net')
        if temp: allowed_types.add('temp')

        # If no specific flags are set, pass None (allow all)
        if not allowed_types:
            allowed_types = None

        # GPU filter: -g alone -> all; --gpu-index 0 or 0,1 -> filter to those indices
        gpu_indices = _parse_gpu_indices(gpu_index) if gpu_index else None

        appl = GroundControl(allowed_types=allowed_types, gpu_indices=gpu_indices,
                             debug=debug, all_gpus=all_gpus, squeue=squeue)
        appl.run()
    else:
        # Store log flag in context for subcommands if needed
        ctx.ensure_object(dict)
        ctx.obj['log'] = log

# Override the format_help method
cli.format_help = lambda ctx, formatter: format_help(ctx, formatter)

def get_default_config():
    """Generate default configuration with all default values including colors.

    Returns:
        dict: A complete default configuration dictionary containing every
        persisted setting (selected widgets, layout, refresh rate, history
        size, widget tab states, and the full color palette).
    """
    return {
        "selected": {},
        "layout": "grid",
        "grid_weights": {},
        "refresh_rate": 1.0,
        "history_size": 120,
        "widget_tabs": {},
        "disk_ignore_prefixes": ", ".join(DEFAULT_DISK_IGNORE_PREFIXES),
        "alerts_enabled": True,
        "alert_sticky_seconds": 30.0,
        "thresholds": {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()},
        "colors": DEFAULT_COLORS.copy(),
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
theme_options = [
    click.option('--list', 'list_themes', is_flag=True, help='List all available themes'),
    click.option('--save-as', 'save_as', metavar='NAME',
                 help='Save the current colors as a custom theme'),
    click.option('--delete', 'delete_name', metavar='NAME',
                 help='Delete a custom theme'),
    click.argument('theme_arg', required=False),
]
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
def theme(list_themes, save_as, delete_name, theme_arg, **kwargs):
    """Manage color themes.

    Apply a theme to change the color scheme of Ground Control. Custom themes
    live in the user config directory and can be created here or edited
    interactively in the Settings tab.

    Examples:
        gc theme --list                 # List all available themes
        gc theme --monokai              # Apply monokai theme
        gc theme my-theme               # Apply a theme by name
        gc theme --save-as my-theme     # Save current colors as a custom theme
        gc theme --delete my-theme      # Delete a custom theme
    """
    available_themes = get_available_themes()

    if list_themes:
        user_themes = set(get_user_themes())
        click.echo("Available themes:")
        for name in available_themes:
            suffix = "  (custom)" if name in user_themes else ""
            click.echo(f"  - {name}{suffix}")
        if user_themes:
            click.echo(f"\nCustom themes directory: {USER_THEMES_DIR}")
        return

    if save_as:
        ok, result = save_theme(save_as, load_colors())
        if not ok:
            click.echo(f"Error: {result}", err=True)
            raise SystemExit(1)
        apply_theme(result)
        click.echo(f"Saved current colors as theme '{result}'.")
        click.echo(f"Theme file: {USER_THEMES_DIR / (result + '.json')}")
        return

    if delete_name:
        ok, result = delete_theme(delete_name)
        if not ok:
            click.echo(f"Error: {result}", err=True)
            raise SystemExit(1)
        click.echo(f"Deleted custom theme '{result}'.")
        return

    # Find which theme flag was set; a positional name works too.
    theme_name = theme_arg
    for key, value in kwargs.items():
        if key.endswith('_flag') and value:
            theme_name = key[:-5].replace('_', '-')  # Remove '_flag' suffix and convert _ to -
            break

    if not theme_name:
        click.echo("No theme specified. Use --list to see available themes.")
        click.echo("Usage: gc theme <theme-name>")
        click.echo(f"Example: gc theme {available_themes[0] if available_themes else 'monokai'}")
        return

    if theme_name in available_themes:
        if apply_theme(theme_name):
            click.echo(f"Theme '{theme_name}' applied successfully!")
            click.echo(f"Config file updated: {CONFIG_FILE}")
            click.echo("Restart Ground Control to see the changes.")
        else:
            click.echo(f"Error: Failed to apply theme '{theme_name}'.", err=True)
            raise SystemExit(1)
    else:
        click.echo(f"Error: Theme '{theme_name}' not found.", err=True)
        click.echo(f"Available themes: {', '.join(available_themes)}", err=True)
        raise SystemExit(1)

def entry():
    cli()

if __name__ == "__main__":
    entry()

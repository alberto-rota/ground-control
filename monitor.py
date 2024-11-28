import time
import psutil
from collections import deque
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

# Try to import pynvml for GPU stats
try:
    import pynvml

    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except:
    NVML_AVAILABLE = False

# Initialize console
console = Console()

# History length for sliding graphs (30 seconds)
HISTORY_LENGTH = 30

# Initialize history for GPU metrics
history = {
    "gpu": {},
}


def get_cpu_usage_per_core():
    return psutil.cpu_percent(percpu=True)


def get_ram_usage():
    mem = psutil.virtual_memory()
    return mem.used, mem.total


def get_disk_usage():
    disk = psutil.disk_usage("/")
    return disk.used, disk.total


def get_disk_io():
    io_counters = psutil.disk_io_counters()
    return io_counters.read_bytes, io_counters.write_bytes


def get_gpu_stats():
    if not NVML_AVAILABLE:
        return []
    gpus = []
    device_count = pynvml.nvmlDeviceGetCount()
    for i in range(device_count):
        handle = pynvml.nvmlDeviceGetHandleByIndex(i)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        meminfo = pynvml.nvmlDeviceGetMemoryInfo(handle)
        name = pynvml.nvmlDeviceGetName(handle)
        gpus.append(
            {
                "name": name,
                "gpu_util": util.gpu,
                "mem_used": meminfo.used / (1024**2),  # in MB
                "mem_total": meminfo.total / (1024**2),  # in MB
            }
        )
    return gpus


def get_bar(percentage, width=20, color="green"):
    filled_length = int(width * percentage // 100)
    bar = "█" * filled_length + " " * (width - filled_length)
    return f"[{color}]{bar}[/{color}]"


def get_graph(data, max_value=100, width=30, color="bright_cyan"):
    data = list(data)
    data = data[-width:]
    if len(data) < width:
        data = [0] * (width - len(data)) + data
    
    # Box drawing characters
    TOP_LEFT = "┌"
    TOP_RIGHT = "┐"
    BOTTOM_LEFT = "└"
    BOTTOM_RIGHT = "┘"
    HORIZONTAL = "─"
    VERTICAL = "│"
    
    # Create top and bottom rows
    top_row = ""
    bottom_row = ""
    
    # Create box top
    box_top = TOP_LEFT + HORIZONTAL * width + TOP_RIGHT + "\n"
    box_bottom = BOTTOM_LEFT + HORIZONTAL * width + BOTTOM_RIGHT
    
    # Use a single dot character for a cleaner look
    DOT = "•"
    EMPTY = " "
    
    for value in data:
        # Scale value to 8 levels (4 levels * 2 rows)
        height = int(value / max_value * 8)
        
        # Top row (levels 4-7)
        top_char = DOT if height > 4 else EMPTY
        top_row += f"[{color}]{top_char}[/{color}]"
        
        # Bottom row (levels 0-3)
        bottom_char = DOT if height > 0 and height <= 4 else EMPTY
        bottom_row += f"[{color}]{bottom_char}[/{color}]"
    
    # Combine all elements with box
    return (f"{box_top}"
            f"{VERTICAL}{top_row}{VERTICAL}\n"
            f"{VERTICAL}{bottom_row}{VERTICAL}\n"
            f"{box_bottom}")




def main():
    prev_read_bytes, prev_write_bytes = get_disk_io()
    time.sleep(1)  # Wait a second to get accurate disk I/O
    with Live(refresh_per_second=4, screen=True) as live:
        while True:
            # CPU Usage per Core
            cpu_percentages = get_cpu_usage_per_core()
            cpu_output = "\n".join(
                f"Core {idx}: {get_bar(percent, color='cyan')}"
                for idx, percent in enumerate(cpu_percentages)
            )

            # RAM Usage
            ram_used, ram_total = get_ram_usage()
            ram_percent = ram_used / ram_total * 100
            ram_output = (
                f"RAM: {ram_used / (1024 ** 3):.2f}/{ram_total / (1024 ** 3):.2f} GB\n"
                + f"{get_bar(ram_percent, color='magenta')}"
            )

            # Disk Usage and I/O
            disk_used, disk_total = get_disk_usage()
            disk_percent = disk_used / disk_total * 100
            read_bytes, write_bytes = get_disk_io()
            read_speed = (read_bytes - prev_read_bytes) / (1024**2)  # in MB/s
            write_speed = (write_bytes - prev_write_bytes) / (1024**2)  # in MB/s
            prev_read_bytes, prev_write_bytes = read_bytes, write_bytes
            disk_output = (
                f"Disk: {disk_used / (1024 ** 3):.2f}/{disk_total / (1024 ** 3):.2f} GB\n"
                + f"{get_bar(disk_percent, color='yellow')}\n"
                + f"Read: {read_speed:.2f} MB/s | Write: {write_speed:.2f} MB/s"
            )

            # GPU Stats
            gpu_output = ""
            if NVML_AVAILABLE:
                gpu_stats = get_gpu_stats()
                for gpu in gpu_stats:
                    name = gpu["name"]
                    if name not in history["gpu"]:
                        history["gpu"][name] = {
                            "util": deque(maxlen=HISTORY_LENGTH),
                            "mem": deque(maxlen=HISTORY_LENGTH),
                        }
                    history["gpu"][name]["util"].append(gpu["gpu_util"])
                    mem_percent = gpu["mem_used"] / gpu["mem_total"] * 100
                    history["gpu"][name]["mem"].append(mem_percent)
        
                    # Calculate the maximum width needed for the metrics names
                    name_width = 10  # Adjust this value based on your longest metric name
                    value_width = 15  # Adjust this value based on your longest value
        
                    # Print GPU name
                    gpu_output += f"{name}\n"
        
                    # Print Utilization with aligned values and taller graph
                    util_bar = get_bar(gpu['gpu_util'], color='green')
                    util_value = f"{gpu['gpu_util']}%"
                    gpu_output += f"{'Util:':<{name_width}}{util_bar}{util_value:>{value_width}}\n"
                    util_graph = get_graph(history['gpu'][name]['util'], width=HISTORY_LENGTH, color='green')
                    gpu_output += f"{'History:':<{name_width}}{util_graph}\n\n"
        
                    # Print Memory with aligned values and taller graph
                    mem_bar = get_bar(mem_percent, color='blue')
                    mem_value = f"{gpu['mem_used']:.1f} MB ({mem_percent:.1f}%)"
                    gpu_output += f"{'Memory:':<{name_width}}{mem_bar}{mem_value:>{value_width}}\n"
                    mem_graph = get_graph(history['gpu'][name]['mem'], width=HISTORY_LENGTH, color='blue')
                    gpu_output += f"History:\n{mem_graph}\n\n"
            else:
                gpu_output = "NVML not available"

            # Combine all outputs
            output = Panel(
                f"[bold cyan]CPU Usage:[/bold cyan]\n{cpu_output}\n\n"
                + f"[bold magenta]RAM Usage:[/bold magenta]\n{ram_output}\n\n"
                + f"[bold yellow]Disk Usage:[/bold yellow]\n{disk_output}\n\n"
                + f"[bold green]GPU Usage:[/bold green]\n{gpu_output}",
                title="[bold]System Monitor[/bold]",
                border_style="bold white",
            )
            live.update(output)
            time.sleep(1)


if __name__ == "__main__":
    main()

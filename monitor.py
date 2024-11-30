import time
import psutil
from collections import deque
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich import print
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


def get_graph(data, y_min=None, y_max=None, width=30, color="bright_cyan", height=4, filled=True):
    data = list(data)
    data = data[-width:]
    if len(data) < width:
        data = [0] * (width - len(data)) + data

    # Set y-axis limits
    if y_min is None:
        y_min = min(data)
    if y_max is None:
        y_max = max(data)
    if y_max == y_min:
        y_max += 1  # Avoid division by zero

    total_levels = height * 4  # Each row represents 4 levels (dots)

    # Initialize rows
    rows = ['' for _ in range(height)]

    # Prepare the left labels
    label_width = max(len(str(y_min)), len(str(y_max)))
    y_max_label = str(y_max).rjust(label_width)
    y_min_label = str(y_min).rjust(label_width)

    # Mapping of levels to Braille dots (bottom to top)
    level_in_row_to_dots = {
        0: [7, 8],  # Bottom dots
        1: [3, 6],
        2: [2, 5],
        3: [1, 4],  # Top dots
    }

    for idx in range(len(data)):
        value = data[idx]
        # Scale value to total levels
        scaled_value = int((value - y_min) / (y_max - y_min) * total_levels + 0.5)
        scaled_value = max(0, min(scaled_value, total_levels))

        # Initialize dots per row for this data point
        dots_per_row = [set() for _ in range(height)]
        if filled:
            # Fill all levels up to scaled_value
            for level in range(scaled_value):
                row_index = level // 4
                level_in_row = level % 4
                dots_in_level = level_in_row_to_dots[level_in_row]
                dots_per_row[row_index].update(dots_in_level)
        else:
            # Only set the dots at the topmost level
            if scaled_value > 0:
                level = scaled_value - 1
                row_index = level // 4
                level_in_row = level % 4
                dots_in_level = level_in_row_to_dots[level_in_row]
                dots_per_row[row_index].update(dots_in_level)

        # Construct the Braille characters for each row
        for row_index in range(height):
            dots_in_row = dots_per_row[row_index]
            if dots_in_row:
                code_point = 0x2800 + sum(1 << (dot_number - 1) for dot_number in dots_in_row)
                braille_char = chr(code_point)
            else:
                braille_char = ' '
            # Append to the corresponding row (top-down)
            rows[height - row_index - 1] += f"[{color}]{braille_char}[/{color}]"

    # Build the top and bottom borders
    TOP_LEFT = "┌"
    TOP_RIGHT = "┐"
    BOTTOM_LEFT = "└"
    BOTTOM_RIGHT = "┘"
    HORIZONTAL = "─"
    VERTICAL = "│"

    # Adjust the box width to include label width and spacing
    box_width = width + 1  # +1 for space between label and graph
    box_top = TOP_LEFT + HORIZONTAL * box_width + TOP_RIGHT + "\n"
    box_bottom = BOTTOM_LEFT + HORIZONTAL * box_width + BOTTOM_RIGHT

    # Assemble the graph with labels
    graph = box_top
    for i, row in enumerate(rows):
        if i == 0:
            label = y_max_label
        elif i == height - 1:
            label = y_min_label
        else:
            label = ' ' * label_width
        graph += VERTICAL + row + " " +VERTICAL + label + "\n"
    graph += box_bottom

    return graph







def main():
    # data = [10, 20, 50, 70, 90, 100, 10,80, 60, 40, 20, 10]
    # print(get_graph(data, width=30))
    # print(get_graph(data, width=30,filled=False))
    # print(get_graph(data, height=10,width=30))
    # print(get_graph(data, height=10,width=50, y_min=9, y_max=200,color="bright_red"))
    
    # return
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

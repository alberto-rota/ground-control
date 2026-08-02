import psutil
import platform
import time
import os
import glob
import subprocess
import random
import math
import logging
from typing import List, Tuple, Union, Dict, Optional

logger = logging.getLogger("ground-control.metrics")

# NVML (nvidia-ml-py) and nvitop are optional. On login nodes, CPU-only nodes,
# machines without an NVIDIA driver, or where the driver/NVML mismatches, these
# imports or nvmlInit() can fail. Treat GPU support as entirely best-effort so
# the rest of the monitor keeps working.
try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except Exception as _nvml_err:  # noqa: BLE001 - any failure means "no NVML"
    pynvml = None
    NVML_AVAILABLE = False
    logger.info("NVML unavailable, GPU metrics disabled: %s", _nvml_err)

try:
    import nvitop
    from nvitop import Device, MigDevice, NA
    NVITOP_AVAILABLE = True
except Exception as _nvitop_err:  # noqa: BLE001
    nvitop = None
    Device = MigDevice = None
    NA = None  # sentinel; `x is not NA` then behaves like `x is not None`
    NVITOP_AVAILABLE = False
    logger.info("nvitop unavailable, GPU metrics disabled: %s", _nvitop_err)

import multiprocessing


# --------------------------------------------------------------------- CPU telemetry
#
# Everything below is Linux procfs/sysfs and entirely best-effort: on macOS,
# Windows, or a kernel built without PSI these files simply are not there. Each
# reader returns None rather than raising, and the widget omits what is missing
# — the same contract the GPU telemetry uses for NVML's NA sentinel.

CGROUP_ROOT = "/sys/fs/cgroup"


def _read_text(path: str) -> Optional[str]:
    """File contents, or None if it does not exist / cannot be read."""
    try:
        with open(path, "r") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _read_psi_cpu() -> Optional[float]:
    """``some avg10`` from /proc/pressure/cpu — % of wall time tasks stalled.

    Strictly more informative than load average: it measures the time runnable
    tasks spent *waiting* for a CPU, so it is a saturation signal rather than a
    queue-length proxy. Requires a PSI-enabled kernel (most distro kernels
    since 4.20); None everywhere else.
    """
    text = _read_text("/proc/pressure/cpu")
    if not text:
        return None
    for line in text.splitlines():
        if not line.startswith("some "):
            continue
        for field in line.split():
            if field.startswith("avg10="):
                try:
                    return float(field.split("=", 1)[1])
                except ValueError:
                    return None
    return None


def _read_proc_counts() -> Tuple[Optional[int], Optional[int]]:
    """(runnable, total) task counts from the 4th field of /proc/loadavg."""
    text = _read_text("/proc/loadavg")
    if not text:
        return None, None
    fields = text.split()
    if len(fields) < 4 or "/" not in fields[3]:
        return None, None
    running, _, total = fields[3].partition("/")
    try:
        return int(running), int(total)
    except ValueError:
        return None, None


def _cgroup_dirs() -> List[str]:
    """Candidate cgroup directories for this process, most specific first.

    Inside a container with its own cgroup namespace the controller files sit
    directly at /sys/fs/cgroup; on a host they live under the path named in
    /proc/self/cgroup. Try both rather than guessing which world we are in.
    """
    dirs = []
    text = _read_text("/proc/self/cgroup") or ""
    for line in text.splitlines():
        parts = line.split(":", 2)
        # v2 lines look like "0::/user.slice/...".
        if len(parts) == 3 and parts[0] == "0" and parts[2].startswith("/"):
            rel = parts[2].strip()
            if rel != "/":
                dirs.append(os.path.join(CGROUP_ROOT, rel.lstrip("/")))
    dirs.append(CGROUP_ROOT)
    return dirs


def _read_cgroup_cpu() -> Dict[str, Optional[float]]:
    """CFS quota and throttling counters for this process's cgroup.

    Returns ``quota_cores`` (the CPU budget in whole cores, None when
    unlimited) plus the cumulative ``nr_periods``/``nr_throttled`` counters.
    A container held at its quota is the classic "40% CPU but everything is
    slow" report, and nothing else in the dashboard would show it.
    """
    result: Dict[str, Optional[float]] = {
        "quota_cores": None, "nr_periods": None, "nr_throttled": None,
    }
    for base in _cgroup_dirs():
        # cgroup v2: "cpu.max" holds "<quota|max> <period>".
        raw = _read_text(os.path.join(base, "cpu.max"))
        stat = _read_text(os.path.join(base, "cpu.stat"))
        if raw is None and stat is None:
            continue
        if raw:
            fields = raw.split()
            if len(fields) == 2 and fields[0] != "max":
                try:
                    quota, period = float(fields[0]), float(fields[1])
                    if period > 0:
                        result["quota_cores"] = quota / period
                except ValueError:
                    pass
        if stat:
            for line in stat.splitlines():
                key, _, value = line.partition(" ")
                if key in ("nr_periods", "nr_throttled"):
                    try:
                        result[key] = float(value)
                    except ValueError:
                        pass
        if result["quota_cores"] is not None or result["nr_periods"] is not None:
            return result

    # cgroup v1 fallback: still the default on older Kubernetes nodes.
    quota = _read_text(os.path.join(CGROUP_ROOT, "cpu", "cpu.cfs_quota_us"))
    period = _read_text(os.path.join(CGROUP_ROOT, "cpu", "cpu.cfs_period_us"))
    if quota and period:
        try:
            quota_v, period_v = float(quota.strip()), float(period.strip())
            if quota_v > 0 and period_v > 0:
                result["quota_cores"] = quota_v / period_v
        except ValueError:
            pass
    stat = _read_text(os.path.join(CGROUP_ROOT, "cpu", "cpu.stat"))
    if stat:
        for line in stat.splitlines():
            key, _, value = line.partition(" ")
            if key in ("nr_periods", "nr_throttled"):
                try:
                    result[key] = float(value)
                except ValueError:
                    pass
    return result


def _parse_visible_gpu_spec(spec: Optional[str]) -> Optional[object]:
    """Parse a CUDA_VISIBLE_DEVICES-style string into a filter.

    Returns one of:
      * ``None``  -> no restriction (caller should show all detected GPUs)
      * ``"none"`` -> an explicit "no GPUs are visible" marker (empty / -1 /
                       NoDevFiles), caller should expose zero GPUs
      * a ``dict`` ``{"indices": set[int], "uuids": set[str], "order": list}``
        describing which physical GPU indices and/or UUIDs are allocated.

    Slurm and CUDA accept either integer ordinals (``"0,1,3"``) or UUIDs
    (``"GPU-abc..."`` / ``"MIG-..."``). An empty string, ``-1`` or the literal
    ``NoDevFiles`` (set by Slurm when a job is allocated no GPUs) all mean
    "no GPUs".
    """
    if spec is None:
        return None
    spec = spec.strip()
    if spec == "":
        return "none"
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if not parts:
        return "none"
    # Sentinels that mean "no GPUs visible".
    if any(p.lower() in ("nodevfiles", "-1") for p in parts):
        return "none"
    indices: set = set()
    uuids: set = set()
    order: list = []
    for p in parts:
        if p.lstrip("+-").isdigit():
            try:
                idx = int(p)
            except ValueError:
                continue
            if idx < 0:
                return "none"
            indices.add(idx)
            order.append(("index", idx))
        else:
            # UUID form (GPU-..., MIG-...); compare case-insensitively
            uuids.add(p.lower())
            order.append(("uuid", p.lower()))
    if not indices and not uuids:
        return "none"
    return {"indices": indices, "uuids": uuids, "order": order}

class SystemMetrics:
    def __init__(self, all_gpus: bool = False):
        """Collect system metrics.

        Args:
            all_gpus: When True, ignore ``CUDA_VISIBLE_DEVICES`` / Slurm GPU
                allocation env vars and enumerate every physical GPU NVML can
                see. By default (False) the monitor only shows the GPUs the
                current job/session is actually allocated, which is the correct
                behaviour on Slurm compute nodes and login nodes.
        """
        self.all_gpus = all_gpus
        self.prev_read_bytes = 0
        self.prev_write_bytes = 0
        self.prev_net_bytes_recv = 0
        self.prev_net_bytes_sent = 0
        self.prev_disk_time = time.time()
        self.prev_net_time = time.time()
        self.prev_disk_io = {}  # Store previous disk IO counters per disk
        # CPU rate counters. Context switches, interrupts and CFS-throttle
        # counts are cumulative since boot, so only their delta over a tick
        # says anything; keep the previous reading the way disk/net do.
        self.prev_cpu_stats = None
        self.prev_cpu_stats_time = time.time()
        self.prev_cgroup_cpu = None
        self._initialize_counters()
        self.devices = self._get_all_gpu_devices() if (NVML_AVAILABLE and NVITOP_AVAILABLE) else []
        
        # Initialize memory I/O counters
        self.prev_memory_io = self._get_memory_io_counters()
        self.prev_memory_time = time.time()
        
        # Initialize memory history for stacked bar plot
        self.memory_history = {
            'timestamps': [],
            'used': [],
            'free': [],
            'cached': [],
            'buffers': [],
            'shared': [],
            'total': 0
        }
        self.max_history_points = 10  # Maximum number of history points to keep
        
        # Initialize temperature sensors
        self._temperature_sensors = self._discover_temperature_sensors()
        
        # Initialize random memory simulation parameters
        self._init_random_memory_simulation()

    def _init_random_memory_simulation(self):
        """Initialize parameters for random memory simulation."""
        # Get actual system memory for realistic baseline
        actual_memory = psutil.virtual_memory()
        actual_swap = psutil.swap_memory()
        
        # Use actual total sizes as baseline, but make them configurable
        self.sim_ram_total = actual_memory.total  # Keep actual total RAM
        self.sim_swap_total = max(actual_swap.total, 8 * 1024**3)  # At least 8GB swap for demo
        
        # Random simulation state
        self.sim_time_offset = random.uniform(0, 2 * math.pi)  # Random phase offset
        self.sim_ram_base = 0.3  # Base RAM usage (30%)
        self.sim_ram_amplitude = 0.4  # Amplitude of RAM usage oscillation
        self.sim_swap_base = 0.1  # Base SWAP usage (10%)
        self.sim_swap_amplitude = 0.2  # Amplitude of SWAP usage oscillation
        
        # Different frequencies for RAM and SWAP to make it more interesting
        self.sim_ram_freq = 0.5  # RAM oscillation frequency
        self.sim_swap_freq = 0.3  # SWAP oscillation frequency
        
        # Add some noise parameters
        self.sim_noise_scale = 0.05  # 5% noise
        
        # Track simulation start time
        self.sim_start_time = time.time()

    def _generate_random_memory_values(self):
        """Generate sensible random RAM and SWAP values that change over time."""
        current_time = time.time()
        elapsed_time = current_time - self.sim_start_time
        
        # Generate smooth oscillating patterns with different frequencies
        ram_cycle = math.sin(elapsed_time * self.sim_ram_freq + self.sim_time_offset)
        swap_cycle = math.sin(elapsed_time * self.sim_swap_freq + self.sim_time_offset * 1.5)
        
        # Add some noise for realism
        ram_noise = random.uniform(-self.sim_noise_scale, self.sim_noise_scale)
        swap_noise = random.uniform(-self.sim_noise_scale, self.sim_noise_scale)
        
        # Calculate usage percentages
        ram_usage_percent = max(0.1, min(0.9, 
            self.sim_ram_base + self.sim_ram_amplitude * ram_cycle + ram_noise))
        swap_usage_percent = max(0.0, min(0.8, 
            self.sim_swap_base + self.sim_swap_amplitude * swap_cycle + swap_noise))
        
        # Convert to bytes
        ram_used = int(self.sim_ram_total * ram_usage_percent)
        ram_available = self.sim_ram_total - ram_used
        swap_used = int(self.sim_swap_total * swap_usage_percent)
        swap_free = self.sim_swap_total - swap_used
        
        # Create mock memory objects that mimic psutil structure
        class MockMemoryInfo:
            def __init__(self, total, used, available):
                self.total = total
                self.used = used
                self.available = available
                self.percent = (used / total) * 100 if total > 0 else 0
                # Add some additional realistic fields
                self.free = available
                self.cached = int(total * 0.1)  # 10% cached
                self.buffers = int(total * 0.05)  # 5% buffers  
                self.shared = int(total * 0.02)  # 2% shared
        
        class MockSwapInfo:
            def __init__(self, total, used, free):
                self.total = total
                self.used = used
                self.free = free
                self.percent = (used / total) * 100 if total > 0 else 0
                # Add swap I/O counters (static for simulation)
                self.sin = 0
                self.sout = 0
        
        return (
            MockMemoryInfo(self.sim_ram_total, ram_used, ram_available),
            MockSwapInfo(self.sim_swap_total, swap_used, swap_free)
        )

    def _discover_temperature_sensors(self) -> Dict[str, str]:
        """Discover available temperature sensors on the system."""
        sensors = {}
        
        if platform.system() == "Linux":
            # Check thermal zones
            try:
                thermal_zones = glob.glob('/sys/class/thermal/thermal_zone*/type')
                for zone_type_file in thermal_zones:
                    zone_dir = os.path.dirname(zone_type_file)
                    temp_file = os.path.join(zone_dir, 'temp')
                    
                    if os.path.exists(temp_file):
                        try:
                            with open(zone_type_file, 'r') as f:
                                sensor_type = f.read().strip()
                            
                            # Don't skip any sensors for now - let's see what we have
                            # if sensor_type.lower() in ['acpi', 'iwlwifi', 'bluetooth', 'pch_']:
                            #     continue
                            
                            # Test reading temperature
                            with open(temp_file, 'r') as f:
                                temp_raw = int(f.read().strip())
                                if temp_raw > 0:  # Valid temperature reading
                                    sensors[sensor_type] = temp_file
                        except (IOError, ValueError, OSError) as e:
                            continue
            except Exception as e:
                pass
            
            # Check hwmon sensors
            try:
                hwmon_dirs = glob.glob('/sys/class/hwmon/hwmon*/temp*_input')
                for temp_file in hwmon_dirs:
                    hwmon_dir = os.path.dirname(temp_file)
                    temp_id = os.path.basename(temp_file).replace('_input', '')
                    
                    # Try to get label for this sensor
                    label_file = os.path.join(hwmon_dir, f"{temp_id}_label")
                    name_file = os.path.join(hwmon_dir, "name")
                    
                    sensor_name = "Unknown"
                    try:
                        if os.path.exists(label_file):
                            with open(label_file, 'r') as f:
                                sensor_name = f.read().strip()
                        elif os.path.exists(name_file):
                            with open(name_file, 'r') as f:
                                base_name = f.read().strip()
                            sensor_name = f"{base_name}_{temp_id}"
                        else:
                            sensor_name = f"temp_{temp_id}"
                        
                        # Test reading temperature
                        with open(temp_file, 'r') as f:
                            temp_raw = int(f.read().strip())
                            if temp_raw > 0:  # Valid temperature reading
                                sensors[sensor_name] = temp_file
                    except (IOError, ValueError, OSError):
                        continue
            except Exception as e:
                pass
        
        elif platform.system() == "Darwin":  # macOS
            try:
                # Try to use sensors command if available
                result = subprocess.run(['sensors', '-A'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # Parse sensors output (basic implementation)
                    for line in result.stdout.split('\n'):
                        if '°C' in line and ':' in line:
                            parts = line.split(':')
                            if len(parts) >= 2:
                                sensor_name = parts[0].strip()
                                sensors[sensor_name] = 'sensors_cmd'
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
                pass
        
        elif platform.system() == "Windows":
            # Windows temperature monitoring would require additional libraries
            # For now, we'll just check if any are available via WMI
            try:
                import wmi
                c = wmi.WMI()
                for temp in c.Win32_TemperatureProbe():
                    if temp.CurrentReading:
                        sensors[f"Sensor_{temp.Name}"] = f"wmi_{temp.Name}"
            except ImportError:
                pass
        
        return sensors

    def get_temperature_metrics(self) -> Optional[Dict]:
        """Get temperature metrics from available sensors."""
        if not self._temperature_sensors:
            return None
        
        temperatures = {}
        
        for sensor_name, sensor_path in self._temperature_sensors.items():
            try:
                if platform.system() == "Linux":
                    with open(sensor_path, 'r') as f:
                        temp_raw = int(f.read().strip())
                        # Convert from millidegrees to degrees Celsius
                        temp_celsius = temp_raw / 1000.0
                        
                        # Provide more user-friendly names
                        friendly_name = self._get_friendly_sensor_name(sensor_name, sensor_path)
                        temperatures[friendly_name] = temp_celsius
                        
                elif platform.system() == "Darwin" and sensor_path == 'sensors_cmd':
                    # For macOS, we'd need to parse sensors command output
                    # This is a simplified approach
                    result = subprocess.run(['sensors', '-A'], capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        for line in result.stdout.split('\n'):
                            if sensor_name in line and '°C' in line:
                                # Extract temperature value
                                import re
                                match = re.search(r'(\d+\.?\d*)\s*°C', line)
                                if match:
                                    temperatures[sensor_name] = float(match.group(1))
                                    break
                
                elif platform.system() == "Windows":
                    # Windows WMI approach would go here
                    pass
                    
            except (IOError, ValueError, OSError, subprocess.TimeoutExpired):
                continue
        
        # Add GPU temperatures if available
        if NVML_AVAILABLE:
            for device in self.devices:
                try:
                    with device.oneshot():
                        gpu_temp = device.temperature()
                        if gpu_temp is not NA:
                            gpu_name = f"GPU {device.index if not isinstance(device.index, tuple) else device.index[0]}"
                            temperatures[gpu_name] = float(gpu_temp)
                except:
                    continue
        
        return temperatures if temperatures else None

    def _get_friendly_sensor_name(self, sensor_name: str, sensor_path: str) -> str:
        """Convert technical sensor names to user-friendly names."""
        # Handle thermal zone sensors
        if 'thermal_zone' in sensor_path:
            if sensor_name == 'acpitz':
                return 'System/Motherboard'
            elif sensor_name == 'x86_pkg_temp':
                return 'CPU Package'
            elif 'pch' in sensor_name.lower():
                return 'Platform Controller Hub'
            elif 'wifi' in sensor_name.lower() or 'iwl' in sensor_name.lower():
                return 'WiFi Module'
            elif 'bluetooth' in sensor_name.lower():
                return 'Bluetooth Module'
        
        # Handle hwmon sensors
        if 'hwmon' in sensor_path:
            # Get the hwmon directory to understand the sensor type
            hwmon_dir = sensor_path.split('/temp')[0]
            
            try:
                # Check if there's a name file to identify the sensor type
                name_file = f"{hwmon_dir}/name"
                if os.path.exists(name_file):
                    with open(name_file, 'r') as f:
                        hwmon_name = f.read().strip()
                    
                    if hwmon_name == 'coretemp':
                        # For coretemp, try to get the specific core label
                        label_file = sensor_path.replace('_input', '_label')
                        if os.path.exists(label_file):
                            with open(label_file, 'r') as f:
                                label = f.read().strip()
                                return f"CPU {label}"
                        else:
                            # Fall back to using the temp ID
                            temp_id = os.path.basename(sensor_path).replace('_input', '')
                            if temp_id == 'temp1':
                                return 'CPU Package'
                            else:
                                core_num = temp_id.replace('temp', '')
                                return f"CPU Core {int(core_num) - 1}" if core_num.isdigit() else f"CPU {temp_id}"
                    
                    elif hwmon_name == 'nvme':
                        return 'NVMe SSD'
                    
                    elif hwmon_name == 'acpitz':
                        return 'System/Motherboard'
                    
                    elif 'gpu' in hwmon_name.lower() or 'radeon' in hwmon_name.lower() or 'amdgpu' in hwmon_name.lower():
                        return 'GPU'
                    
                    else:
                        # Use the hwmon name with temp ID
                        temp_id = os.path.basename(sensor_path).replace('_input', '')
                        return f"{hwmon_name.title()} {temp_id}"
                        
            except (IOError, OSError):
                pass
        
        # Handle special cases for common sensor names
        if sensor_name.lower() == 'composite':
            return 'NVMe SSD'
        elif 'core' in sensor_name.lower() and any(char.isdigit() for char in sensor_name):
            return sensor_name.replace('Core', 'CPU Core')
        elif 'package' in sensor_name.lower():
            return 'CPU Package'
        elif sensor_name.startswith('temp') and sensor_name[4:].isdigit():
            return f"Sensor {sensor_name[4:]}"
        
        # Default: clean up the sensor name
        return sensor_name.replace('_', ' ').title()

    def _initialize_counters(self):
        now = time.time()
        self.prev_disk_time = now
        self.prev_net_time = now
        io_counters = psutil.net_io_counters()
        self.prev_net_bytes_recv = io_counters.bytes_recv
        self.prev_net_bytes_sent = io_counters.bytes_sent
        disk_io = psutil.disk_io_counters()
        self.prev_read_bytes = disk_io.read_bytes
        self.prev_write_bytes = disk_io.write_bytes

        # Initialize per-disk counters
        try:
            per_disk_io = psutil.disk_io_counters(perdisk=True)
            for disk_name, io_data in per_disk_io.items():
                self.prev_disk_io[disk_name] = {
                    'read_bytes': io_data.read_bytes,
                    'write_bytes': io_data.write_bytes,
                    'time': now
                }
        except Exception:
            pass

    def get_cpu_info(self):
        system = platform.system()
        cpu_models = []
        # Total logical cores on the machine.
        try:
            core_count = multiprocessing.cpu_count()
        except (NotImplementedError, Exception):  # noqa: BLE001
            core_count = psutil.cpu_count() or 1
        # On a Slurm/cgroup allocation the job may only own a subset of cores;
        # surface that so the label reflects what the user actually has.
        alloc_count = None
        try:
            if hasattr(os, "sched_getaffinity"):
                alloc_count = len(os.sched_getaffinity(0))
        except Exception:  # noqa: BLE001
            alloc_count = None

        if system == "Windows":
            cpu_models = [platform.processor()]

        elif system == "Linux":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    models = [line.split(":", 1)[1].strip()
                              for line in f if line.lower().startswith("model name")]
                cpu_models = list(dict.fromkeys(models)) or ["CPU"]
            except Exception:  # noqa: BLE001
                cpu_models = ["CPU"]

        elif system == "Darwin":
            try:
                model = subprocess.check_output("sysctl -n machdep.cpu.brand_string", shell=True).decode().strip()
                cpu_models = [model]
            except:
                cpu_models = ["CPU"]

        else:
            cpu_models = ["CPU"]

        if alloc_count is not None and alloc_count < core_count:
            core_label = f"{alloc_count}/{core_count} cores"
        else:
            core_label = f"{core_count} cores"
        return f"{', '.join(cpu_models)} [{core_label}]"


    def get_cpu_telemetry(self, cpu_freqs, n_cores: int) -> Dict[str, Optional[float]]:
        """Saturation and stall figures that raw utilisation percentages hide.

        Utilisation alone lies in the same way it does for a GPU: a box can sit
        at 40% and still be unable to make progress because it is waiting on
        I/O, being descheduled by a hypervisor, or held at a CFS quota. Every
        field is optional -- callers render what is present and omit the rest.
        """
        t: Dict[str, Optional[float]] = {}

        # Where the time actually went. psutil compares against its own
        # previous call, so this is the breakdown over the last tick.
        try:
            times = psutil.cpu_times_percent()
            t["user_percent"] = float(times.user)
            t["system_percent"] = float(times.system)
            # iowait/steal only exist on Linux; getattr keeps this portable.
            for key, attr in (("iowait_percent", "iowait"), ("steal_percent", "steal")):
                value = getattr(times, attr, None)
                t[key] = float(value) if value is not None else None
        except Exception:  # noqa: BLE001
            pass

        try:
            load1, load5, load15 = psutil.getloadavg()
            t["load_1"], t["load_5"], t["load_15"] = load1, load5, load15
            # Per-core is the portable reading: >1.0 means the run queue is
            # longer than the machine is wide, whatever the machine's size.
            t["load_per_core"] = load1 / n_cores if n_cores else None
        except (OSError, AttributeError, ValueError):
            pass

        t["psi_some_avg10"] = _read_psi_cpu()
        running, total = _read_proc_counts()
        t["procs_running"], t["procs_total"] = running, total

        # Clock as a fraction of maximum: the CPU-side counterpart of the GPU's
        # SM clock, and the first place a thermally-limited box shows itself.
        # Heterogeneous machines (P/E cores, big.LITTLE) have per-core maxima,
        # so average the current clocks and take the highest ceiling.
        currents = [f.current for f in (cpu_freqs or []) if getattr(f, "current", 0)]
        maxima = [f.max for f in (cpu_freqs or []) if getattr(f, "max", 0)]
        t["freq_mhz"] = sum(currents) / len(currents) if currents else None
        t["freq_max_mhz"] = max(maxima) if maxima else None

        now = time.time()
        elapsed = max(now - self.prev_cpu_stats_time, 1e-6)
        try:
            stats = psutil.cpu_stats()
            if self.prev_cpu_stats is not None:
                # A context-switch rate far above the core count is the
                # signature of thread oversubscription -- the classic
                # OMP_NUM_THREADS x dataloader-workers blow-up.
                t["ctx_switches_per_s"] = max(
                    0.0, (stats.ctx_switches - self.prev_cpu_stats.ctx_switches) / elapsed
                )
                t["interrupts_per_s"] = max(
                    0.0, (stats.interrupts - self.prev_cpu_stats.interrupts) / elapsed
                )
            self.prev_cpu_stats = stats
            self.prev_cpu_stats_time = now
        except Exception:  # noqa: BLE001
            pass

        cgroup = _read_cgroup_cpu()
        t["cgroup_quota_cores"] = cgroup.get("quota_cores")
        periods, throttled = cgroup.get("nr_periods"), cgroup.get("nr_throttled")
        if periods is not None and throttled is not None:
            prev = self.prev_cgroup_cpu
            if prev is not None:
                d_periods = periods - (prev.get("nr_periods") or 0)
                d_throttled = throttled - (prev.get("nr_throttled") or 0)
                # Share of scheduling periods in which the cgroup ran out of
                # quota. The cumulative counter only ever grows, so a rate is
                # the only form of this number that means anything live.
                if d_periods > 0:
                    t["cgroup_throttled_percent"] = max(
                        0.0, min(100.0, d_throttled / d_periods * 100.0)
                    )
            self.prev_cgroup_cpu = cgroup
        return t

    def get_cpu_metrics(self):
        try:
            cpu_percentages = psutil.cpu_percent(percpu=True)
        except Exception:  # noqa: BLE001
            cpu_percentages = []
        # cpu_freq is frequently unavailable in containers / cgroup-limited
        # Slurm allocations / some VMs (returns None, [], or raises).
        try:
            cpu_freqs = psutil.cpu_freq(percpu=True)
            if cpu_freqs is None:
                cpu_freqs = []
        except Exception:  # noqa: BLE001
            cpu_freqs = []
        try:
            mem_percent = psutil.virtual_memory().percent
        except Exception:  # noqa: BLE001
            mem_percent = 0.0
        # Telemetry is decoration on top of the core percentages: a failure
        # here must never cost the panel its bar chart.
        try:
            telemetry = self.get_cpu_telemetry(cpu_freqs, len(cpu_percentages))
        except Exception as err:  # noqa: BLE001
            logger.debug("CPU telemetry unavailable: %s", err)
            telemetry = {}
        return {
            'cpu_percentages': cpu_percentages,
            'cpu_freqs': cpu_freqs,
            'mem_percent': mem_percent,
            'cpu_name': self.get_cpu_info(),
            'cpu_telemetry': telemetry,
        }

    def get_disk_metrics(self):
        current_time = time.time()
        disk_time_delta = max(current_time - self.prev_disk_time, 1e-6)

        # Get IO counters for all disks if available
        try:
            per_disk_io = psutil.disk_io_counters(perdisk=True)
        except Exception:
            per_disk_io = {}

        # Get total IO counters. disk_io_counters() returns None on systems with
        # no measurable disks (some containers / diskless compute nodes).
        try:
            total_io = psutil.disk_io_counters()
        except Exception:  # noqa: BLE001
            total_io = None

        if total_io is not None:
            # Calculate total read/write speeds with a smooth factor
            total_read_speed = (total_io.read_bytes - self.prev_read_bytes) / (1024**2) / disk_time_delta
            total_write_speed = (total_io.write_bytes - self.prev_write_bytes) / (1024**2) / disk_time_delta

            # Apply smoothing and prevent negative values
            total_read_speed = max(0, total_read_speed)
            total_write_speed = max(0, total_write_speed)

            # Debug check - ensure we're not zeroing out valid read values
            if total_read_speed < 0.01 and total_io.read_bytes > self.prev_read_bytes:
                total_read_speed = 0.01  # Set a minimum value if there was positive activity

            # Update previous values for total IO
            self.prev_read_bytes = total_io.read_bytes
            self.prev_write_bytes = total_io.write_bytes
        else:
            total_read_speed = 0
            total_write_speed = 0
        self.prev_disk_time = current_time

        # Get all mounted partitions
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception:  # noqa: BLE001
            partitions = []

        # Precompute total space once (avoids O(n^2) disk_usage() calls, which
        # are slow and can block on networked / stale HPC mounts).
        partition_total = {}
        for p in partitions:
            try:
                partition_total[p.mountpoint] = psutil.disk_usage(p.mountpoint).total
            except Exception:  # noqa: BLE001 - stale NFS, perms, etc.
                continue
        all_partitions_total = sum(partition_total.values())
    
        # Prepare result structure
        disks = []
        total_used = 0
        total_space = 0
    
        # Process each partition
        for partition in partitions:
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                disk_name = partition.device.split('/')[-1] if '/' in partition.device else partition.device.split('\\')[-1]
            
                # Try to get per-disk IO if available
                if disk_name in per_disk_io:
                    disk_io = per_disk_io[disk_name]
                    
                    # Calculate per-disk IO with proper previous values
                    if disk_name in self.prev_disk_io:
                        prev_data = self.prev_disk_io[disk_name]
                        disk_time_delta = max(current_time - prev_data['time'], 1e-6)
                        
                        read_speed = (disk_io.read_bytes - prev_data['read_bytes']) / (1024**2) / disk_time_delta
                        write_speed = (disk_io.write_bytes - prev_data['write_bytes']) / (1024**2) / disk_time_delta
                        
                        # Prevent negative values and apply smoothing
                        read_speed = max(0, read_speed)
                        write_speed = max(0, write_speed)
                        
                        # Don't zero out small but real read activity
                        if read_speed < 0.01 and disk_io.read_bytes > prev_data['read_bytes']:
                            read_speed = 0.01  # Set a minimum visible value
                            
                        # Apply an additional threshold to eliminate noise only for zero activity
                        if read_speed < 0.01 and disk_io.read_bytes == prev_data['read_bytes']:
                            read_speed = 0
                        if write_speed < 0.01 and disk_io.write_bytes == prev_data['write_bytes']:
                            write_speed = 0
                    
                    # Update previous values for this disk
                    self.prev_disk_io[disk_name] = {
                        'read_bytes': disk_io.read_bytes,
                        'write_bytes': disk_io.write_bytes,
                        'time': current_time
                    }
                else:
                    # Distribute total IO proportionally based on disk size ratio
                    total_disk_space = all_partitions_total - partition_total.get(partition.mountpoint, 0)
                    if total_disk_space > 0:
                        size_ratio = usage.total / total_disk_space
                        read_speed = total_read_speed * size_ratio
                        write_speed = total_write_speed * size_ratio
                    else:
                        read_speed = 0
                        write_speed = 0
            
                disks.append({
                    'mountpoint': partition.mountpoint,
                    'disk_used': usage.used,
                    'disk_total': usage.total,
                    'read_speed': read_speed,
                    'write_speed': write_speed
                })
            
                total_used += usage.used
                total_space += usage.total
            except (PermissionError, FileNotFoundError, OSError):
                # Skip partitions we can't access (perms, stale NFS handle,
                # disconnected network mount that raises OSError on statvfs).
                continue
            except Exception:  # noqa: BLE001 - never let one bad mount kill the panel
                continue

        return {
            'disks': disks,
            'total_disk_used': total_used,
            'total_disk_total': total_space,
            'read_speed': total_read_speed,
            'write_speed': total_write_speed
        }

    def get_network_metrics(self):
        current_time = time.time()
        try:
            net_io_counters = psutil.net_io_counters()
        except Exception:  # noqa: BLE001
            net_io_counters = None
        if net_io_counters is None:
            self.prev_net_time = current_time
            return {'download_speed': 0, 'upload_speed': 0}

        time_delta = max(current_time - self.prev_net_time, 1e-6)

        download_speed = (net_io_counters.bytes_recv - self.prev_net_bytes_recv) / (1024 ** 2) / time_delta
        upload_speed = (net_io_counters.bytes_sent - self.prev_net_bytes_sent) / (1024 ** 2) / time_delta

        self.prev_net_bytes_recv = net_io_counters.bytes_recv
        self.prev_net_bytes_sent = net_io_counters.bytes_sent
        self.prev_net_time = current_time

        return {
            'download_speed': download_speed,
            'upload_speed': upload_speed
        }

    def _get_pids_on_device(self, device) -> set:
        """Return set of PIDs running on this GPU device using pynvml (authoritative per-device list)."""
        if not NVML_AVAILABLE:
            return None  # no filter
        try:
            # Prefer nvitop's NVML handle so we support both Physical and MIG devices
            handle = getattr(device, 'handle', None) or getattr(device, '_handle', None)
            if handle is None and isinstance(getattr(device, 'index', None), int):
                handle = pynvml.nvmlDeviceGetHandleByIndex(device.index)
            if handle is None:
                return None
            procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
            return {p.pid for p in procs} if procs else set()
        except Exception:
            return None

    @staticmethod
    def _safe_metric(fn, default):
        """Call a device accessor, returning default on NA/exception."""
        try:
            val = fn()
            return default if (val is None or val is NA) else val
        except Exception:  # noqa: BLE001
            return default

    @staticmethod
    def _num(fn, scale: float = 1.0):
        """Numeric device accessor -> float (scaled) or None when unavailable.

        NVML reports plenty of fields as the NA sentinel rather than raising --
        consumer cards have no power limit, Grace-Blackwell has no discrete
        memory clock, and so on. Callers get None and decide what to show.
        """
        try:
            val = fn()
            if val is None or val is NA:
                return None
            return float(val) * scale
        except Exception:  # noqa: BLE001
            return None

    # NVML clock-throttle bits worth surfacing, most severe first. GpuIdle is
    # deliberately excluded: an idle GPU is not a throttled one, and flagging it
    # would make the common case look like a fault.
    _THROTTLE_BITS = (
        ("HwThermalSlowdown", "thermal", True),
        ("SwThermalSlowdown", "thermal (sw)", True),
        ("HwPowerBrakeSlowdown", "power brake", True),
        ("HwSlowdown", "hw slowdown", True),
        ("SwPowerCap", "power cap", False),
        ("SyncBoost", "sync boost", False),
        ("ApplicationsClocksSetting", "app clocks", False),
        ("DisplayClockSetting", "display clocks", False),
    )

    def _throttle_reasons(self, device):
        """Active clock-throttle reasons for a device.

        Returns ``(labels, severe)`` -- short human labels, and whether any of
        them indicates hardware distress (thermal or power-brake slowdown)
        rather than ordinary governed behaviour. A card sitting at its software
        power cap under load is normal; a card in hardware thermal slowdown is
        losing throughput and wants attention.

        Empty list when the driver does not implement the query, which is not
        the same as "not throttled" -- callers must not infer health from it.
        """
        try:
            import pynvml
        except ImportError:
            return [], False
        handle = getattr(device, "handle", None)
        if handle is None:
            return [], False

        # Renamed in newer NVML; keep working against both spellings.
        getter = (getattr(pynvml, "nvmlDeviceGetCurrentClocksThrottleReasons", None)
                  or getattr(pynvml, "nvmlDeviceGetCurrentClocksEventReasons", None))
        if getter is None:
            return [], False
        try:
            mask = int(getter(handle))
        except Exception:  # noqa: BLE001
            return [], False
        if not mask:
            return [], False

        labels, severe = [], False
        for suffix, label, is_severe in self._THROTTLE_BITS:
            bit = (getattr(pynvml, f"nvmlClocksThrottleReason{suffix}", None)
                   or getattr(pynvml, f"nvmlClocksEventReason{suffix}", None))
            if bit is not None and mask & int(bit):
                labels.append(label)
                severe = severe or is_severe
        return labels, severe

    def get_gpu_metrics(self):
        """Return per-GPU metrics including running processes on each device.

        Each device is collected independently: a device that becomes
        inaccessible (driver hiccup, job preemption, permission change) is
        skipped rather than aborting collection for every GPU.
        """
        gpu_metrics = []
        for device in self.devices:
            try:
                gpu_metrics.append(self._collect_one_gpu(device))
            except Exception as err:  # noqa: BLE001
                logger.warning("GPU collection failed for %r: %s",
                               getattr(device, "index", "?"), err)
                continue
        return gpu_metrics

    def _collect_one_gpu(self, device):
        """Collect metrics for a single GPU device (raises only on total failure)."""
        with device.oneshot():
                processes = []
                pids_on_this_device = self._get_pids_on_device(device)
                try:
                    procs = device.processes()
                    if procs is not None:
                        for proc in procs.values():
                            try:
                                if pids_on_this_device is not None and proc.pid not in pids_on_this_device:
                                    continue
                                name = proc.name() if proc.name() is not NA else str(proc.pid)
                                gpu_mem = proc.gpu_memory_human() if proc.gpu_memory_human() is not NA else "N/A"
                                try:
                                    username = proc.username()
                                except Exception:
                                    username = ""
                                command = ""
                                script = ""
                                try:
                                    cmdline = proc.cmdline()
                                    if cmdline:
                                        raw_cmd = proc.command()
                                        if raw_cmd is not NA and isinstance(raw_cmd, str):
                                            command = raw_cmd
                                        else:
                                            command = " ".join(str(x) for x in cmdline[:20])
                                        # For Python, show script name (first .py arg or -c "code")
                                        name_lower = (name or "").lower()
                                        first_arg = (cmdline[0] or "").lower() if cmdline else ""
                                        if "python" in name_lower or "python" in first_arg:
                                            for idx, arg in enumerate(cmdline[1:], start=1):
                                                s = str(arg) if arg is not None else ""
                                                if s.endswith(".py"):
                                                    script = s
                                                    break
                                                if s == "-c" and idx + 1 < len(cmdline):
                                                    code = str(cmdline[idx + 1] or "")
                                                    script = f'-c "{code[:50]}..."' if len(code) > 50 else f'-c "{code}"'
                                                    break
                                            if not script and len(cmdline) > 1:
                                                script = str(cmdline[1] or "")[:60]
                                except Exception:
                                    pass
                                cpu_pct = None
                                mem_str = ""
                                try:
                                    host = proc.host
                                    if host is not NA and host is not None:
                                        cp = host.cpu_percent()
                                        cpu_pct = f"{cp:.1f}%" if cp is not NA and cp is not None else "N/A"
                                        try:
                                            rss = host.memory_info().rss
                                            mem_mib = rss / (1024 * 1024)
                                            mem_str = f"{mem_mib:.0f}MiB" if mem_mib >= 0.1 else "<0.1MiB"
                                        except Exception:
                                            try:
                                                mp = host.memory_percent()
                                                mem_str = f"{mp:.1f}%" if mp is not None else "N/A"
                                            except Exception:
                                                mem_str = "N/A"
                                except Exception:
                                    cpu_pct = "N/A"
                                    mem_str = "N/A"
                                processes.append({
                                    'pid': proc.pid,
                                    'name': name,
                                    'gpu_memory': gpu_mem,
                                    'username': username,
                                    'command': command or "",
                                    'script': script or command[:80] if command else "",
                                    'cpu_percent': cpu_pct,
                                    'memory': mem_str,
                                })
                            except Exception:
                                continue
                except Exception:
                    pass
                try:
                    idx = device.index
                    idx_label = list(idx) if isinstance(idx, tuple) else [idx]
                except Exception:  # noqa: BLE001
                    idx_label = ["?"]
                name = self._safe_metric(device.name, "GPU")
                util = self._safe_metric(device.gpu_utilization, -1)
                mem_used = self._safe_metric(device.memory_used, None)
                mem_total = self._safe_metric(device.memory_total, None)
                throttle_labels, throttle_severe = self._throttle_reasons(device)
                return {
                    'gpu_name': f"{idx_label} {name}",
                    'gpu_util': util if util is not None else -1,
                    'mem_used': mem_used / (1000**3) if mem_used is not None else -1,
                    'mem_total': mem_total / (1000**3) if mem_total is not None else -1,
                    'processes': processes,
                    # Telemetry below is best-effort: every field is None when the
                    # driver or the card does not expose it, and the widget drops
                    # whatever is missing rather than printing "N/A" noise.
                    'power_w': self._num(device.power_draw, 1e-3),
                    'power_limit_w': self._num(device.power_limit, 1e-3),
                    'temperature_c': self._num(device.temperature),
                    'fan_percent': self._num(device.fan_speed),
                    'sm_clock_mhz': self._num(device.sm_clock),
                    'max_sm_clock_mhz': self._num(device.max_sm_clock),
                    # Fraction of time the memory interface was busy -- NOT how
                    # full the VRAM is. Read next to gpu_util it separates "doing
                    # work" from "a kernel is resident but starved".
                    'mem_bw_percent': self._num(device.memory_utilization),
                    'enc_percent': self._num(device.encoder_utilization),
                    'dec_percent': self._num(device.decoder_utilization),
                    'perf_state': self._safe_metric(device.performance_state, None),
                    'throttle_reasons': throttle_labels,
                    'throttle_severe': throttle_severe,
                }

    def get_memory_metrics(self):
        """
        Get detailed memory metrics including RAM and swap information.
        Now generates random values for visualization purposes.
        
        Returns:
            dict: A dictionary containing comprehensive memory information
        """
        # Generate random memory and swap values
        memory_info = psutil.virtual_memory()  # Shape: namedtuple with total, used, available, percent, free, cached, buffers, shared
        swap_info = psutil.swap_memory()  # Shape: namedtuple with total, used, free, percent, sin, sout
        
        # Get memory I/O metrics (keep real I/O for now, could be randomized too)
        current_time = time.time()
        memory_io = self._get_memory_io_counters()
        time_delta = max(current_time - self.prev_memory_time, 1e-6)
        
        # Calculate I/O rates (per second)
        memory_io_rates = {}
        for key, value in memory_io.items():
            prev_value = self.prev_memory_io.get(key, 0)
            rate = (value - prev_value) / time_delta
            memory_io_rates[f"{key}_rate"] = rate
        
        # Update previous counters
        self.prev_memory_io = memory_io
        self.prev_memory_time = current_time
        
        # Update memory history for stacked bar plot
        self._update_memory_history(memory_info)
        
        # Get additional system-wide memory metrics
        memory_data = {
            'memory_info': memory_info,
            'swap_info': swap_info,
            'memory_io': memory_io,
            'memory_io_rates': memory_io_rates,
            'memory_history': self.memory_history
        }
        
        # Generate some mock meminfo data for Linux-like behavior
        try:
            # Create realistic meminfo dict with random values
            total_kb = memory_info.total // 1024
            used_kb = memory_info.used // 1024
            available_kb = memory_info.available // 1024
            
            meminfo_dict = {
                'MemTotal': f'{total_kb} kB',
                'MemFree': f'{available_kb} kB',
                'MemAvailable': f'{available_kb} kB',
                'Buffers': f'{memory_info.buffers // 1024} kB',
                'Cached': f'{memory_info.cached // 1024} kB',
                'SwapTotal': f'{swap_info.total // 1024} kB',
                'SwapFree': f'{swap_info.free // 1024} kB',
                'CommitLimit': f'{total_kb + swap_info.total // 1024} kB',
                'Committed_AS': f'{int((total_kb + swap_info.total // 1024) * 0.6)} kB',
            }
            
            memory_data['meminfo'] = meminfo_dict
            
            # Calculate memory commit ratio
            commit_limit = total_kb + swap_info.total // 1024
            committed_as = int(commit_limit * 0.6)  # 60% committed
            memory_data['commit_ratio'] = committed_as / commit_limit if commit_limit > 0 else 0
        except:
            pass
            
        # Generate some mock top processes for demonstration
        try:
            # Create realistic process names and memory usage
            mock_processes = [
                {'pid': 1234, 'name': 'chrome', 'memory_percent': random.uniform(15, 25)},
                {'pid': 5678, 'name': 'firefox', 'memory_percent': random.uniform(10, 20)},
                {'pid': 9012, 'name': 'code', 'memory_percent': random.uniform(8, 15)},
                {'pid': 3456, 'name': 'python', 'memory_percent': random.uniform(5, 12)},
                {'pid': 7890, 'name': 'docker', 'memory_percent': random.uniform(3, 8)},
                {'pid': 2345, 'name': 'nodejs', 'memory_percent': random.uniform(2, 6)},
                {'pid': 6789, 'name': 'mysql', 'memory_percent': random.uniform(1, 4)},
                {'pid': 1357, 'name': 'nginx', 'memory_percent': random.uniform(0.5, 2)},
            ]
            
            # Add RSS and VMS values based on percentages
            for proc in mock_processes:
                proc['memory_rss'] = int(memory_info.total * proc['memory_percent'] / 100)
                proc['memory_vms'] = int(proc['memory_rss'] * 1.5)  # VMS typically larger than RSS
            
            # Sort by memory percent and take top 10
            memory_data['top_processes'] = sorted(
                mock_processes, 
                key=lambda x: x['memory_percent'], 
                reverse=True
            )[:10]
        except:
            memory_data['top_processes'] = []
        
        return memory_data

    def _get_visible_gpu_filter(self):
        """Resolve which GPUs the current job/session may use.

        Honours (in order) ``CUDA_VISIBLE_DEVICES``, then the Slurm-provided
        ``SLURM_STEP_GPUS`` / ``SLURM_JOB_GPUS``, then ``GPU_DEVICE_ORDINAL``.
        Returns the parsed filter from :func:`_parse_visible_gpu_spec`
        (``None`` = all, ``"none"`` = none, or a dict of indices/uuids).

        When ``self.all_gpus`` is set, restriction is disabled (returns None).
        """
        if self.all_gpus:
            return None
        for var in ("CUDA_VISIBLE_DEVICES", "SLURM_STEP_GPUS", "SLURM_JOB_GPUS", "GPU_DEVICE_ORDINAL"):
            if var in os.environ:
                parsed = _parse_visible_gpu_spec(os.environ.get(var))
                logger.info("GPU visibility from %s=%r -> %r", var, os.environ.get(var), parsed)
                return parsed
        return None

    @staticmethod
    def _device_uuid(device) -> Optional[str]:
        """Best-effort lowercase UUID for a device; None if unavailable."""
        try:
            uuid = device.uuid()
            if uuid is not None and uuid is not NA:
                return str(uuid).lower()
        except Exception:
            pass
        return None

    def _device_is_visible(self, device, phys_index, gpu_filter) -> bool:
        """Decide whether a physical device passes the CUDA/Slurm visibility filter."""
        if gpu_filter is None:
            return True
        if gpu_filter == "none":
            return False
        if isinstance(phys_index, int) and phys_index in gpu_filter["indices"]:
            return True
        uuid = self._device_uuid(device)
        if uuid is not None:
            # CUDA_VISIBLE_DEVICES UUIDs may be a prefix (e.g. "GPU-abc")
            for want in gpu_filter["uuids"]:
                if uuid == want or uuid.startswith(want) or want.startswith(uuid):
                    return True
        return False

    @staticmethod
    def _device_accessible(device) -> bool:
        """Probe a device with a cheap query. NVML can list GPUs that the
        current user/job cannot actually read (e.g. cgroup device isolation on
        Slurm), which raises NVMLError or returns NA. Such devices are skipped.
        """
        try:
            name = device.name()
            if name is None or name is NA:
                # Some drivers return NA for name but still expose memory.
                mem = device.memory_total()
                return mem is not None and mem is not NA
            return True
        except Exception as err:  # noqa: BLE001
            logger.info("Skipping inaccessible GPU %r: %s", getattr(device, "index", "?"), err)
            return False

    def _get_all_gpu_devices(self) -> list:
        """
        Combine Physical Devices and MIG Devices into a single list, filtered to
        the GPUs the current job/session is allowed to use and that are actually
        accessible.

        If a PhysicalDevice has MIGs, include the MIGs instead of the
        PhysicalDevice. If not, include the PhysicalDevice itself.

        Robust to enumeration failures (returns whatever could be collected),
        which matters on Slurm/login nodes with partial or restricted access.

        Returns:
            List of GPU devices (PhysicalDevice or MigDevice)
        """
        if not NVITOP_AVAILABLE:
            return []
        try:
            gpu_filter = self._get_visible_gpu_filter()
        except Exception:  # noqa: BLE001
            gpu_filter = None
        if gpu_filter == "none":
            logger.info("No GPUs visible to this session (CUDA_VISIBLE_DEVICES/Slurm allocation is empty)")
            return []

        try:
            physical_devices = list(nvitop.Device.all())
        except Exception as err:  # noqa: BLE001
            logger.warning("nvitop.Device.all() failed: %s", err)
            return []

        try:
            mig_devices = list(nvitop.MigDevice.all())
        except Exception as err:  # noqa: BLE001
            logger.info("nvitop.MigDevice.all() failed (no MIG support?): %s", err)
            mig_devices = []

        # Map physical index -> its MIG devices (index is a (phys, mig) tuple).
        mig_map: Dict[int, list] = {}
        for mig in mig_devices:
            try:
                idx = mig.index
                phys_idx = idx[0] if isinstance(idx, tuple) else idx
            except Exception:  # noqa: BLE001
                continue
            mig_map.setdefault(phys_idx, []).append(mig)

        combined_devices: list = []
        for phys_dev in physical_devices:
            try:
                phys_idx = phys_dev.index
            except Exception:  # noqa: BLE001
                phys_idx = None
            # Apply CUDA/Slurm visibility filter at the physical-device level.
            if not self._device_is_visible(phys_dev, phys_idx, gpu_filter):
                continue
            if phys_idx in mig_map:
                # Physical device runs in MIG mode: expose its MIG instances.
                for mig in mig_map[phys_idx]:
                    if self._device_accessible(mig):
                        combined_devices.append(mig)
            else:
                if self._device_accessible(phys_dev):
                    combined_devices.append(phys_dev)

        logger.info("Detected %d usable GPU device(s)%s",
                    len(combined_devices),
                    "" if gpu_filter is None else " (filtered by allocation)")
        return combined_devices

    def _get_memory_io_counters(self):
        """Get memory I/O counters from the system."""
        counters = {
            'pgpgin': 0,     # KB paged in
            'pgpgout': 0,    # KB paged out
            'pswpin': 0,     # pages swapped in
            'pswpout': 0,    # pages swapped out
            'pgfault': 0,    # page faults
            'pgmajfault': 0  # major page faults
        }
        
        try:
            # Try to get Linux-specific memory I/O stats
            if platform.system() == 'Linux':
                with open('/proc/vmstat', 'r') as f:
                    vmstat = f.read()
                    for line in vmstat.split('\n'):
                        parts = line.split()
                        if len(parts) >= 2:
                            key = parts[0]
                            value = int(parts[1])
                            
                            if key in counters:
                                counters[key] = value
            
            # On non-Linux systems, try to use swap info as a proxy
            swap = psutil.swap_memory()
            if hasattr(swap, 'sin') and hasattr(swap, 'sout'):
                counters['pswpin'] = swap.sin
                counters['pswpout'] = swap.sout
        except:
            pass
            
        return counters

    def _update_memory_history(self, memory_info):
        """Update the memory history for stacked bar plot visualization."""
        current_time = time.time()
        
        # Add current memory data to history
        self.memory_history['timestamps'].append(current_time)
        self.memory_history['used'].append(memory_info.used / (1024 ** 3))  # Convert to GB
        self.memory_history['free'].append(memory_info.available / (1024 ** 3))  # Convert to GB
        
        # Get cached and buffers if available
        cached = memory_info.cached / (1024 ** 3) if hasattr(memory_info, 'cached') else 0
        buffers = memory_info.buffers / (1024 ** 3) if hasattr(memory_info, 'buffers') else 0
        shared = memory_info.shared / (1024 ** 3) if hasattr(memory_info, 'shared') else 0
        total = memory_info.total / (1024 ** 3) if hasattr(memory_info, 'total') else 0
        
        self.memory_history['cached'].append(cached)
        self.memory_history['buffers'].append(buffers)
        self.memory_history['shared'].append(shared)
        self.memory_history['total'] = total
        # Trim history if it exceeds the maximum number of points
        if len(self.memory_history['timestamps']) > self.max_history_points:
            for key in self.memory_history:
                # Skip 'total' as it's a single value, not a list
                if key != 'total':
                    self.memory_history[key] = self.memory_history[key][-self.max_history_points:]
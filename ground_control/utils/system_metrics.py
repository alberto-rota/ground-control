import psutil
import platform
import time
import os
import glob
import subprocess
import random
import math
try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except:
    NVML_AVAILABLE = False
from nvitop import Device, MigDevice,NA
from typing import List, Union, Dict, Optional
import nvitop  # Ensure nvitop is installed: pip install nvitop

import platform
import subprocess
import multiprocessing

class SystemMetrics:
    def __init__(self):
        self.prev_read_bytes = 0
        self.prev_write_bytes = 0
        self.prev_net_bytes_recv = 0
        self.prev_net_bytes_sent = 0
        self.prev_time = time.time()
        self.prev_disk_io = {}  # Store previous disk IO counters per disk
        self._initialize_counters()
        self.devices = self._get_all_gpu_devices() if NVML_AVAILABLE else []
        
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
                    'time': time.time()
                }
        except:
            pass

    def get_cpu_info(self):
        system = platform.system()
        cpu_models = []
        core_count = multiprocessing.cpu_count()  # Get number of cores

        if system == "Windows":
            cpu_models = [platform.processor()]

        elif system == "Linux":
            try:
                output = subprocess.check_output("cat /proc/cpuinfo | grep 'model name'", shell=True).decode().strip()
                cpu_models = list(set(line.split(":")[1].strip() for line in output.split("\n")))
            except:
                cpu_models = ["CPU"]

        elif system == "Darwin":
            try:
                model = subprocess.check_output("sysctl -n machdep.cpu.brand_string", shell=True).decode().strip()
                cpu_models = [model]
            except:
                cpu_models = ["CPU"]

        else:
            cpu_models = ["CPU"]

        return f"{', '.join(cpu_models)} [{core_count} cores]"


    def get_cpu_metrics(self):
        return {
            'cpu_percentages': psutil.cpu_percent(percpu=True),
            'cpu_freqs': psutil.cpu_freq(percpu=True),
            'mem_percent': psutil.virtual_memory().percent,
            'cpu_name': self.get_cpu_info(),
        }

    def get_disk_metrics(self):
        current_time = time.time()
        disk_time_delta = max(current_time - self.prev_time, 1e-6)
    
        # Get IO counters for all disks if available
        try:
            per_disk_io = psutil.disk_io_counters(perdisk=True)
        except:
            per_disk_io = {}
    
        # Get total IO counters
        total_io = psutil.disk_io_counters()
    
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
        self.prev_time = current_time
    
        # Get all mounted partitions
        partitions = psutil.disk_partitions(all=False)
    
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
                    total_disk_space = sum(psutil.disk_usage(p.mountpoint).total for p in partitions if p.mountpoint != partition.mountpoint)
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
            except (PermissionError, FileNotFoundError):
                # Skip partitions we can't access
                pass
    
        return {
            'disks': disks,
            'total_disk_used': total_used,
            'total_disk_total': total_space,
            'read_speed': total_read_speed,
            'write_speed': total_write_speed
        }

    def get_network_metrics(self):
        current_time = time.time()
        net_io_counters = psutil.net_io_counters()
        
        time_delta = max(current_time - self.prev_time, 1e-6)
        
        download_speed = (net_io_counters.bytes_recv - self.prev_net_bytes_recv) / (1024 ** 2) / time_delta
        upload_speed = (net_io_counters.bytes_sent - self.prev_net_bytes_sent) / (1024 ** 2) / time_delta
        
        self.prev_net_bytes_recv = net_io_counters.bytes_recv
        self.prev_net_bytes_sent = net_io_counters.bytes_sent
        self.prev_time = current_time
        
        return {
            'download_speed': download_speed,
            'upload_speed': upload_speed
        }

    def get_gpu_metrics(self):
        gpu_metrics = []
        for device in self.devices:
            with device.oneshot():
                gpu_metrics.append({
                    'gpu_name': f"{list(device.index) if isinstance(device.index,tuple) else [device.index]} {device.name()}",
                    'gpu_util': device.gpu_utilization() if device.gpu_utilization() is not NA else -1,
                    'mem_used': device.memory_used() / (1000**3) if device.memory_used() is not NA else -1,
                    'mem_total': device.memory_total() / (1000**3) if device.memory_total() is not NA else -1,
                    # 'temperature': device.temperature() if device.temperature() is not NA else -1,
                    # 'fan_speed': device.fan_speed() if device.fan_speed() is not NA else -1,
                })
            
        return gpu_metrics

    def get_memory_metrics(self):
        """
        Get detailed memory metrics including RAM and swap information.
        Now generates random values for visualization purposes.
        
        Returns:
            dict: A dictionary containing comprehensive memory information
        """
        # Generate random memory and swap values
        memory_info, swap_info = self._generate_random_memory_values()
        
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

    def _get_all_gpu_devices(self) -> List[Union[nvitop.Device, nvitop.MigDevice]]:
        """
        Combine Physical Devices and MIG Devices into a single list.
        If a PhysicalDevice has MIGs, include the MIGs instead of the PhysicalDevice.
        If not, include the PhysicalDevice itself.
    
        Returns:
            List of GPU devices (PhysicalDevice or MigDevice)
        """
        physical_devices = nvitop.Device.all()
        mig_devices = nvitop.MigDevice.all()
    
        # Create a mapping from PhysicalDevice index to its MigDevices
        mig_map = {}
        for mig in mig_devices:
            phys_idx, mig_idx = mig.index  # Assuming index is a tuple (physical_idx, mig_idx)
            if phys_idx not in mig_map:
                mig_map[phys_idx] = []
            mig_map[phys_idx].append(mig)
    
        # Build the combined device list
        combined_devices = []
        for phys_dev in physical_devices:
            if phys_dev.index in mig_map:
                # If PhysicalDevice has MIGs, include all its MIGs
                combined_devices.extend(mig_map[phys_dev.index])
            else:
                # If no MIGs, include the PhysicalDevice itself
                combined_devices.append(phys_dev)
    
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
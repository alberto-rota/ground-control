import psutil
import platform
import time
try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except:
    NVML_AVAILABLE = False
from nvitop import Device, MigDevice,NA
from typing import List, Union
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
                        
                        # Apply an additional threshold to eliminate noise
                        if read_speed < 0.01:
                            read_speed = 0
                        if write_speed < 0.01:
                            write_speed = 0
                    else:
                        # No previous data, estimate based on total
                        read_speed = 0
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
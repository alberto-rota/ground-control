import psutil
import time
try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except:
    NVML_AVAILABLE = False

class SystemMetrics:
    def __init__(self):
        self.prev_read_bytes = 0
        self.prev_write_bytes = 0
        self.prev_net_bytes_recv = 0
        self.prev_net_bytes_sent = 0
        self.prev_time = time.time()
        self._initialize_counters()

    def _initialize_counters(self):
        io_counters = psutil.net_io_counters()
        self.prev_net_bytes_recv = io_counters.bytes_recv
        self.prev_net_bytes_sent = io_counters.bytes_sent
        disk_io = psutil.disk_io_counters()
        self.prev_read_bytes = disk_io.read_bytes
        self.prev_write_bytes = disk_io.write_bytes

    def get_cpu_metrics(self):
        return {
            'cpu_percentages': psutil.cpu_percent(percpu=True),
            'cpu_freqs': psutil.cpu_freq(percpu=True),
            'mem_percent': psutil.virtual_memory().percent
        }

    def get_disk_metrics(self):
        current_time = time.time()
        io_counters = psutil.disk_io_counters()
        disk_usage = psutil.disk_usage('/')
        
        time_delta = max(current_time - self.prev_time, 1e-6)
        
        read_speed = (io_counters.read_bytes - self.prev_read_bytes) / (1024**2) / time_delta
        write_speed = (io_counters.write_bytes - self.prev_write_bytes) / (1024**2) / time_delta
        
        self.prev_read_bytes = io_counters.read_bytes
        self.prev_write_bytes = io_counters.write_bytes
        self.prev_time = current_time
        
        return {
            'read_speed': read_speed,
            'write_speed': write_speed,
            'disk_used': disk_usage.used,
            'disk_total': disk_usage.total
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
        if not NVML_AVAILABLE:
            return None
            
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        meminfo = pynvml.nvmlDeviceGetMemoryInfo(handle)
        
        return {
            'gpu_util': util.gpu,
            'mem_used': meminfo.used / (1024**3),
            'mem_total': meminfo.total / (1024**3)
        }

# File: ground_control/cli/process_list.py
from ..utils.system_metrics import SystemMetrics
import pwd
import os

def main():
    system_metrics = SystemMetrics()
    processes = system_metrics.get_user_top_processes()
    
    if processes:
        print(f"Top-level processes for user {pwd.getpwuid(os.getuid()).pw_name}:")
        print("PID\tName")
        print("-" * 30)
        for pid, name in processes:
            print(f"{pid}\t{name}")
    else:
        print("No top-level processes found or an error occurred.")

if __name__ == "__main__":
    main()
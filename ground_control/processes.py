import os
import psutil
import pwd

def get_user_top_processes():
    """
    Get all top-level processes for the current user.
    Returns a list of tuples containing (pid, name).
    """
    current_user = pwd.getpwuid(os.getuid()).pw_name
    top_processes = []
    
    try:
        for proc in psutil.process_iter(['pid', 'name', 'username', 'ppid']):
            try:
                pinfo = proc.info
                # Check if process belongs to current user and is a top-level process (parent is systemd or init)
                if (pinfo['username'] == current_user and 
                    (pinfo['ppid'] == 1 or  # systemd/init
                     pinfo['ppid'] == 0)):  # kernel processes
                    top_processes.append((pinfo['pid'], pinfo['name']))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        print(f"Error occurred: {e}")
        return []
    
    return sorted(top_processes)

if __name__ == "__main__":
    processes = get_user_top_processes()
    if processes:
        print(f"Top-level processes for user {pwd.getpwuid(os.getuid()).pw_name}:")
        print("PID\tName")
        print("-" * 30)
        for pid, name in processes:
            print(f"{pid}\t{name}")
    else:
        print("No top-level processes found or an error occurred.")

# tagpup_gui.py
import os
import sys
import logging
import webbrowser

# Set up logging with colors for warnings and errors
class ColorFormatter(logging.Formatter):
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"
    def format(self, record):
        orig_levelname = record.levelname
        if record.levelno >= logging.ERROR:
            record.levelname = f"{self.RED}{orig_levelname}{self.RESET}"
        elif record.levelno == logging.WARNING:
            record.levelname = f"{self.YELLOW}{orig_levelname}{self.RESET}"
        val = super().format(record)
        record.levelname = orig_levelname
        return val

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColorFormatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    handlers=[handler]
)
logger = logging.getLogger("tagpup_gui")

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
from tagpup_server import create_library, start_server
import localserver
from tagpup import config as tagpup_config
from tagpup import logs as tagpup_logs

def cleanup_zombie_processes():
    """Finds and terminates any other running python processes that are executing tagpup_gui.py or tagpup_server.py."""
    import subprocess
    import json
    try:
        # Run PowerShell command to get python processes with command lines
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
            "Select-Object ProcessId, CommandLine | ConvertTo-Json"
        ]
        output = subprocess.check_output(
            cmd, 
            stderr=subprocess.DEVNULL, 
            creationflags=0x08000000
        ).decode("utf-8", errors="ignore").strip()
        
        if not output:
            return
            
        try:
            processes = json.loads(output)
        except json.JSONDecodeError:
            return
            
        if isinstance(processes, dict):
            processes = [processes]
            
        my_pid = os.getpid()
        my_ppid = os.getppid() if hasattr(os, "getppid") else None
        
        for proc in processes:
            pid = proc.get("ProcessId")
            cmdline = proc.get("CommandLine") or ""
            
            if pid and pid != my_pid and pid != my_ppid:
                cmdline_lower = cmdline.lower()
                # Check if it is running tagpup
                if ("tagpup_gui.py" in cmdline_lower or "tagpup_server.py" in cmdline_lower):
                    logger.info(f"Found existing TagPup process (PID {pid}, cmdline: '{cmdline}'). Cleaning it up...")
                    try:
                        subprocess.Popen(
                            ["taskkill", "/F", "/PID", str(pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=0x08000000
                        )
                    except Exception:
                        pass
    except Exception:
        pass

def main():
    if not os.environ.get("TAGPUP_RELOADED_CHILD"):
        cleanup_zombie_processes()
    else:
        # The serving process writes the log. The reloader's supervisor only restarts
        # it, and two processes rotating one file fail on Windows.
        logger.info("Logging to %s", tagpup_logs.to_file("tagpup"))

    logger.info("Initializing TagPup GUI...")
    db_name = tagpup_config.default_db()
    if len(sys.argv) > 1 and sys.argv[1].endswith(".db"):
        db_name = sys.argv[1]
    db_path = tagpup_config.library_path(db_name)
    
    if not os.path.exists(db_path):
        logger.info(f"Database not found at {db_path}. Initializing empty database with default categories...")
        create_library(db_path)
        logger.info("Database initialized successfully.")
        
    port_env = os.environ.get("TAGPUP_PORT")
    if port_env:
        port = int(port_env)
    else:
        port = localserver.find_available_port(localserver.TAGPUP_PORT)
        os.environ["TAGPUP_PORT"] = str(port)
    url = f"http://localhost:{port}/"
    
    # Open the browser once, from the process that actually serves.
    #
    # The reloader runs a parent supervisor and a child; the child is the one
    # with the server. This guard was on TAGPUP_RELOADED alone, which is unset
    # in both on a first start -- so the supervisor opened a tab and the child
    # opened a second. It looked right after a reload, where the parent sets
    # TAGPUP_RELOADED on the child, which is why it only ever showed at startup.
    #
    # _CHILD means "I am the server"; _RELOADED means "this is a restart, they
    # already have a tab open".
    serving = bool(os.environ.get("TAGPUP_RELOADED_CHILD"))
    restarting = bool(os.environ.get("TAGPUP_RELOADED"))
    if serving and not restarting:
        import threading
        def open_browser_when_ready(port):
            import socket
            import time
            for _ in range(100):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.connect(("127.0.0.1", port))
                        logger.info("TagPup server is ready. Opening browser...")
                        webbrowser.open(f"http://localhost:{port}/")
                        return
                    except (ConnectionRefusedError, OSError):
                        time.sleep(0.1)
            webbrowser.open(f"http://localhost:{port}/")

        threading.Thread(target=open_browser_when_ready, args=(port,), daemon=True).start()
        
    from reloader import start_reloader_thread
    start_reloader_thread("TAGPUP_RELOADED")
    
    try:
        start_server(
            port=port,
            db_path=db_path,
            gui_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_tagpup")
        )
    except KeyboardInterrupt:
        pass
    logger.info(f"TagPup GUI shut down cleanly. (PID: {os.getpid()})")
    os._exit(0)

if __name__ == "__main__":
    main()

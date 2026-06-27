# scripts/reloader.py
import os
import sys
import time
import threading
import logging

logger = logging.getLogger("reloader")

def start_reloader_thread(env_var_name: str = "TAGPUP_RELOADED"):
    """Starts a background thread that monitors python files in the workspace and scripts directory.
    If changes are detected, it restarts the python process in-place using os.execv."""
    
    def watch_files():
        # Setup directories to watch
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        watch_dirs = [
            base_dir,
            os.path.join(base_dir, "scripts")
        ]
        
        def get_all_py_files():
            files = []
            for d in watch_dirs:
                if os.path.exists(d):
                    for root, _, fs in os.walk(d):
                        if ".venv" in root or "__pycache__" in root or ".git" in root:
                            continue
                        for f in fs:
                            if f.endswith(".py"):
                                files.append(os.path.join(root, f))
            return files

        # Populate initial modification times
        py_files = {}
        for f in get_all_py_files():
            try:
                py_files[f] = os.path.getmtime(f)
            except OSError:
                pass

        logger.info("Auto-reloader active. Watching for changes in Python source files...")

        while True:
            time.sleep(1.0)
            current_files = get_all_py_files()
            changed = False
            for f in current_files:
                try:
                    mtime = os.path.getmtime(f)
                    if f not in py_files or py_files[f] != mtime:
                        changed = True
                        break
                except OSError:
                    pass
            if changed:
                logger.info("Python source file modification detected. Reloading server process...")
                os.environ[env_var_name] = "1"
                try:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                except Exception as e:
                    logger.error(f"Failed to execv reload: {e}")
                    sys.exit(1)

    t = threading.Thread(target=watch_files, daemon=True)
    t.start()

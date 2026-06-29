# scripts/reloader.py
import os
import sys
import time
import threading
import logging
import subprocess

logger = logging.getLogger("reloader")

# Exit code used to signal a reload request from child to parent wrapper
RELOAD_EXIT_CODE = 3

def start_reloader_thread(env_var_name: str = "TAGPUP_RELOADED"):
    """Starts a background thread that monitors python files in the workspace and scripts directory.
    To prevent zombie processes on Windows, it uses a parent wrapper and child process model."""
    
    child_env_var = env_var_name + "_CHILD"
    
    # Parent supervisor process
    if not os.environ.get(child_env_var):
        logger.info("Auto-reloader active. Watching for changes in Python source files...")
        
        child_env = os.environ.copy()
        child_env[child_env_var] = "1"
        
        while True:
            cmd = [sys.executable] + sys.argv
            try:
                p = subprocess.Popen(cmd, env=child_env, stdout=sys.stdout, stderr=sys.stderr)
            except Exception as e:
                logger.error(f"Failed to spawn child process: {e}")
                sys.exit(1)
            
            try:
                p.wait()
            except KeyboardInterrupt:
                logger.info(f"[Reloader Parent] KeyboardInterrupt caught. Terminating child process (PID {p.pid})...")
                try:
                    p.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    p.kill()
                logger.info(f"[Reloader Parent] Parent supervisor process (PID {os.getpid()}) exiting...")
                os._exit(0)
                
            if p.returncode == RELOAD_EXIT_CODE:
                logger.info("[Reloader Parent] Child requested reload. Restarting...")
                child_env[env_var_name] = "1"
                continue
            else:
                logger.info(f"[Reloader Parent] Child exited with non-reload code. Exiting parent with {p.returncode}")
                os._exit(p.returncode or 0)
                
    # Child worker process
    def watch_files():
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

        pending_reload = False
        logged_delay = False

        while True:
            time.sleep(1.0)
            current_files = get_all_py_files()
            changed = False
            for f in current_files:
                try:
                    mtime = os.path.getmtime(f)
                    if f not in py_files or py_files[f] != mtime:
                        py_files[f] = mtime
                        changed = True
                except OSError:
                    pass
            
            if changed:
                pending_reload = True
                logged_delay = False
                
            if pending_reload:
                busy = False
                for thread in threading.enumerate():
                    if thread.name in ("FolderSuggestionsThread", "ReclusterThread") and thread.is_alive():
                        busy = True
                        break
                if busy:
                    if not logged_delay:
                        logger.info("Python source file modification detected, but background tasks (suggestions/clustering) are running. Reload will happen once they complete.")
                        logged_delay = True
                    continue

                logger.info("Python source file modification detected. Exiting process to reload...")
                os._exit(RELOAD_EXIT_CODE)

    t = threading.Thread(target=watch_files, daemon=True)
    t.start()

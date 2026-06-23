# tagtuner.py
import os
import sys
import logging
import configparser
import webbrowser
import threading
import time

# Set up logging to print to console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("tagtuner")

# Add scripts directory to path to load tuner_server
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
from tuner_server import start_server

def get_config():
    """Load configuration parameters from config.ini."""
    config = configparser.ConfigParser(interpolation=None)
    config_path = os.path.join(os.path.dirname(__file__), "config.ini")
    
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
    else:
        # Defaults
        config.add_section("paths")
        config.set("paths", "data_dir", "data")
    return config

def find_available_port(start_port=8080):
    import socket
    port = start_port
    while True:
        # Check if we can connect to the port (something is listening)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect(("127.0.0.1", port))
                port += 1
                continue
            except (ConnectionRefusedError, OSError):
                pass
        
        # Double check by trying to bind to it
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                port += 1

def main():
    logger.info("Initializing TagTuner...")
    config = get_config()
    
    # Resolve DB path
    data_dir = config.get("paths", "data_dir", fallback="data")
    db_path = os.path.join(data_dir, "photo_index.db")
    
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}! Please run 'python tagpup.py index <dir>' first to index your photos.")
        sys.exit(1)
        
    port = find_available_port(8080)
    url = f"http://localhost:{port}/"
    
    logger.info(f"Opening TagTuner UI in browser at: {url}")
    webbrowser.open(url)
    
    # Start server in the main thread blockingly to allow clean Ctrl+C / Ctrl+Break shutdown
    try:
        start_server(
            port=port,
            db_path=db_path,
            gui_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui")
        )
    except KeyboardInterrupt:
        pass
    logger.info("TagTuner shut down cleanly.")

if __name__ == "__main__":
    main()

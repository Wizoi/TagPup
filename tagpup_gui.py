# tagpup_gui.py
import os
import sys
import logging
import configparser
import webbrowser
import socket

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("tagpup_gui")

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
from tagpup_server import start_server

def get_config():
    """Load configuration parameters from config.ini."""
    config = configparser.ConfigParser(interpolation=None)
    config_path = os.path.join(os.path.dirname(__file__), "config.ini")
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
    else:
        config.add_section("paths")
        config.set("paths", "data_dir", "data")
    return config

def find_available_port(start_port=8090):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect(("127.0.0.1", port))
                port += 1
                continue
            except (ConnectionRefusedError, OSError):
                pass
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                port += 1

def main():
    logger.info("Initializing TagPup GUI...")
    config = get_config()
    
    data_dir = config.get("paths", "data_dir", fallback="data")
    db_path = os.path.join(data_dir, "photo_index.db")
    
    if not os.path.exists(db_path):
        logger.info(f"Database not found at {db_path}. Initializing empty database with default categories...")
        from index import PhotoIndex
        from taxonomy import seed_taxonomy_from_db
        photo_index = PhotoIndex(db_path=db_path)
        photo_index.load()
        seed_taxonomy_from_db(db_path)
        logger.info("Database initialized successfully.")
        
    port = find_available_port(8090)
    url = f"http://localhost:{port}/"
    
    if not os.environ.get("TAGPUP_RELOADED"):
        logger.info(f"Opening TagPup GUI in browser at: {url}")
        webbrowser.open(url)
        
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
    logger.info("TagPup GUI shut down cleanly.")

if __name__ == "__main__":
    main()

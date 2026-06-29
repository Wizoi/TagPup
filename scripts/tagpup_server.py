# tagpup_server.py
import os
import json
import sqlite3
import urllib.parse
import io
import logging
import re
import threading
import subprocess
import configparser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingTCPServer
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = 500000000
import numpy as np

logger = logging.getLogger("tagpup.server")

def normalize_path(path):
    if not path:
        return ""
    return os.path.abspath(path).lower().replace("\\", "/")

def to_db_path(path):
    if not path:
        return ""
    return os.path.abspath(path).replace("\\", "/")

def make_json_serializable(obj):
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(x) for x in obj]
    elif isinstance(obj, (np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, np.ndarray):
        return make_json_serializable(obj.tolist())
    return obj

# Date parsing helpers
YEAR_RE = re.compile(r"^(\d{4})")
DATE_KEYS = [
    "EXIF:DateTimeOriginal", "DateTimeOriginal",
    "XMP:DateTimeOriginal",
    "EXIF:CreateDate", "CreateDate",
    "XMP:CreateDate",
    "EXIF:ModifyDate", "ModifyDate",
    "XMP:ModifyDate"
]

def parse_year_from_raw_metadata(raw_meta):
    if not raw_meta:
        return None
    for key in DATE_KEYS:
        val = raw_meta.get(key)
        if val:
            if isinstance(val, list) and val:
                val = val[0]
            val_str = str(val).strip()
            match = YEAR_RE.match(val_str)
            if match:
                try:
                    year = int(match.group(1))
                    if 1800 <= year <= 2100:
                        return year
                except ValueError:
                    pass
    return None

def get_year_from_mtime_or_meta(mtime, raw_meta_json, path=None):
    parsed_year = None
    if raw_meta_json:
        try:
            if isinstance(raw_meta_json, str):
                raw_meta = json.loads(raw_meta_json)
            else:
                raw_meta = raw_meta_json
            parsed_year = parse_year_from_raw_metadata(raw_meta)
        except Exception:
            pass
                
    if not parsed_year and path:
        norm_path = path.replace("\\", "/")
        parts = norm_path.split("/")
        if parts:
            filename = parts[-1]
            matches = re.findall(r'\d{4}', filename)
            for m in matches:
                val = int(m)
                if 1800 <= val <= 2100:
                    parsed_year = val
                    break
        if not parsed_year and len(parts) > 1:
            for folder in reversed(parts[:-1]):
                if not folder:
                    continue
                matches = re.findall(r'\d{4}', folder)
                for m in matches:
                    val = int(m)
                    if 1800 <= val <= 2100:
                        parsed_year = val
                        break
                if parsed_year:
                    break
                    
    return parsed_year if parsed_year else "Unknown"

class TagPupHTTPRequestHandler(BaseHTTPRequestHandler):
    db_path = "data/photo_index.db"
    gui_dir = "gui_tagpup"
    
    # Static Class-level caches
    model_lock = threading.Lock()
    shared_embedder = None
    folder_cache = {}          # folder_path -> { photo_path: metadata_dict }
    suggest_status = {}        # folder_path -> { status, completed, total, suggestions }
    suggest_threads = {}       # folder_path -> Thread

    def log_message(self, format, *args):
        pass # suppress request logs

    def validate_request_origin(self) -> bool:
        # Validate Host header to prevent DNS rebinding
        host = self.headers.get("Host", "")
        host_clean = host.split(":")[0].lower()
        if host_clean not in ("localhost", "127.0.0.1", "[::1]"):
            self.send_error(403, "Forbidden: Invalid Host Header")
            return False

        # Validate Origin header to prevent CSRF from external websites
        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urllib.parse.urlparse(origin)
            origin_host = parsed_origin.netloc.split(":")[0].lower()
            if origin_host not in ("localhost", "127.0.0.1", "[::1]"):
                self.send_error(403, "Forbidden: Cross-Origin Requests Denied")
                return False
        return True

    def do_GET(self):
        if not self.validate_request_origin():
            return
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # Static assets
        if path == "/" or path == "/index.html":
            self.serve_static_file("index.html", "text/html")
        elif path == "/style.css":
            self.serve_static_file("style.css", "text/css")
        elif path == "/app.js":
            self.serve_static_file("app.js", "application/javascript")
        
        # API Endpoints
        elif path == "/api/browse-folder":
            self.handle_get_browse_folder()
        elif path == "/api/autocomplete-folder":
            self.handle_get_autocomplete_folder(query)
        elif path == "/api/folder/scan":
            self.handle_get_folder_scan(query)
        elif path == "/api/folder/suggest-status":
            self.handle_get_folder_suggest_status(query)
        elif path == "/api/photo-file":
            self.handle_serve_photo_file(query)
        elif path == "/api/tags":
            self.handle_get_tags()
        elif path == "/api/people":
            self.handle_get_people()
        elif path == "/api/taxonomy/tree":
            self.handle_get_taxonomy_tree()
        else:
            self.send_error(404, "File Not Found")

    def do_POST(self):
        if not self.validate_request_origin():
            return
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/folder/suggest-start":
            self.handle_post_folder_suggest_start()
        elif path == "/api/photo/rotate":
            self.handle_post_photo_rotate()
        elif path == "/api/photo/open-explorer":
            self.handle_post_photo_open_explorer()
        elif path == "/api/photo/save-metadata":
            self.handle_post_photo_save_metadata()
        elif path == "/api/photos/bulk-tags":
            self.handle_post_photos_bulk_tags()
        elif path == "/api/folder/auto-apply":
            self.handle_post_folder_auto_apply()
        elif path == "/api/folder/time-shift":
            self.handle_post_folder_time_shift()
        elif path == "/api/folder/rename-photos":
            self.handle_post_folder_rename_photos()
        elif path == "/api/taxonomy/create":
            self.handle_post_taxonomy_create()
        elif path == "/api/taxonomy/update":
            self.handle_post_taxonomy_update()
        elif path == "/api/taxonomy/delete-check":
            self.handle_post_taxonomy_delete_check()
        elif path == "/api/taxonomy/delete-confirm":
            self.handle_post_taxonomy_delete_confirm()
        elif path == "/api/taxonomy/rename":
            self.handle_post_taxonomy_rename()
        else:
            self.send_error(404, "Endpoint Not Found")

    def serve_static_file(self, filename, content_type):
        filepath = os.path.join(self.gui_dir, filename)
        if not os.path.exists(filepath):
            self.send_error(404, f"File {filename} not found")
            return
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error: {e}")

    def send_json(self, data):
        data = make_json_serializable(data)
        content = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

    def send_json_error(self, status_code, message):
        content = json.dumps({"success": False, "error": message}).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

    def get_exiftool_path(self):
        import configparser
        config = configparser.ConfigParser(interpolation=None)
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.ini")
        default_path = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Username"), r"AppData\Local\Programs\ExifTool\exiftool.exe")
        if os.path.exists(config_path):
            try:
                config.read(config_path, encoding='utf-8')
                path = config.get("paths", "exiftool", fallback=default_path)
                return os.path.expandvars(path)
            except Exception:
                pass
        return default_path

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        return json.loads(post_data.decode("utf-8"))

    def handle_get_browse_folder(self):
        try:
            import sys
            python_cmd = (
                "import tkinter as tk; "
                "from tkinter import filedialog; "
                "root = tk.Tk(); "
                "root.withdraw(); "
                "root.lift(); "
                "root.focus_force(); "
                "root.attributes('-topmost', True); "
                "print(filedialog.askdirectory(title='Select Image Folder'))"
            )
            cmd = [sys.executable, "-c", python_cmd]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
            path = res.stdout.strip()
            self.send_json({"path": path})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_get_autocomplete_folder(self, query):
        path_list = query.get("path")
        if not path_list:
            self.send_json([])
            return
        typed_path = urllib.parse.unquote(path_list[0]).strip()
        
        # If empty, return standard drives on Windows
        if not typed_path:
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append(drive)
            self.send_json(drives)
            return
            
        # Clean paths (normalizing slashes)
        typed_path = os.path.expandvars(typed_path)
        
        # Handle simple drive letter typing (e.g., "C", "C:")
        if re.match(r'^[a-zA-Z]$', typed_path):
            self.send_json([f"{typed_path.upper()}:\\"])
            return
        if re.match(r'^[a-zA-Z]:$', typed_path):
            self.send_json([f"{typed_path.upper()}\\"])
            return
            
        norm_path = os.path.normpath(typed_path)
        ends_with_sep = typed_path.endswith(("\\", "/"))
        
        if ends_with_sep:
            base_dir = norm_path
            prefix = ""
        else:
            base_dir = os.path.dirname(norm_path)
            prefix = os.path.basename(norm_path).lower()
            
        suggestions = []
        try:
            if os.path.isdir(base_dir):
                for name in os.listdir(base_dir):
                    full_path = os.path.join(base_dir, name)
                    if os.path.isdir(full_path):
                        if not prefix or name.lower().startswith(prefix):
                            suggestions.append(full_path)
        except Exception:
            pass
            
        self.send_json(suggestions[:15])

    def handle_get_tags(self):
        try:
            db_tags = set()
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tags FROM photos WHERE tags IS NOT NULL")
            for row in cursor.fetchall():
                try:
                    tags_list = json.loads(row[0])
                    for t in tags_list:
                        db_tags.add(t)
                except Exception:
                    pass
            conn.close()

            # Also load from taxonomy file
            from taxonomy import TagTaxonomy
            tax_path = os.path.splitext(self.db_path)[0] + "_taxonomy.json"
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            for p in taxonomy.paths:
                db_tags.add(p)

            # Filter out hidden tags
            hidden_tags = set()
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")
                for row in cursor.fetchall():
                    hidden_tags.add(row[0])
            conn.close()

            def is_tag_hidden(tag):
                normalized = TagTaxonomy.normalize_tag(tag)
                if not normalized:
                    return False
                parts = normalized.split("/")
                for i in range(1, len(parts) + 1):
                    ancestor = "/".join(parts[:i])
                    if ancestor in hidden_tags:
                        return True
                return False

            final_tags = [t for t in db_tags if not is_tag_hidden(t)]
            self.send_json(sorted(final_tags))
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_get_people(self):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL ORDER BY name")
            people = [row[0] for row in cursor.fetchall()]

            # Filter out people hidden from autocomplete
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")
                hidden_tags = {row[0] for row in cursor.fetchall()}

                from taxonomy import TagTaxonomy
                def is_tag_hidden(tag):
                    normalized = TagTaxonomy.normalize_tag(tag)
                    if not normalized:
                        return False
                    parts = normalized.split("/")
                    for i in range(1, len(parts) + 1):
                        ancestor = "/".join(parts[:i])
                        if ancestor in hidden_tags:
                            return True
                    return False

                filtered_people = []
                for p in people:
                    cursor.execute("SELECT tag FROM tag_taxonomy WHERE name = ?", (p,))
                    paths = [r[0] for r in cursor.fetchall()]
                    hidden = False
                    for path in paths:
                        if is_tag_hidden(path):
                            hidden = True
                            break
                    if not hidden:
                        filtered_people.append(p)
                people = filtered_people

            self.send_json(people)
        except Exception as e:
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_get_folder_scan(self, query):
        folder_path_list = query.get("path")
        if not folder_path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
            
        folder_path = urllib.parse.unquote(folder_path_list[0])
        force_refresh = query.get("force", ["false"])[0].lower() == "true"
        
        if not os.path.isdir(folder_path):
            self.send_json_error(400, f"Path is not a valid directory: {folder_path}")
            return
            
        folder_path = os.path.abspath(folder_path)
        folder_path_norm = normalize_path(folder_path)
        
        # Check cache
        if folder_path_norm in TagPupHTTPRequestHandler.folder_cache and not force_refresh:
            cached_data = list(TagPupHTTPRequestHandler.folder_cache[folder_path_norm].values())
            def get_date_taken_str(meta):
                raw_meta = meta.get("raw_metadata", {})
                for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                    val = raw_meta.get(k)
                    if val:
                        if isinstance(val, list) and val:
                            val = val[0]
                        return str(val).strip()
                return f"mtime_{meta.get('mtime', 0.0)}"
            cached_data.sort(key=get_date_taken_str)
            self.send_json(cached_data)
            return
            
        # Scan folder for image files
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
                    
        if not image_files:
            self.send_json([])
            return
            
        # Query existing metadata from SQLite DB to avoid running ExifTool on unchanged files
        db_records = {}
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT path, mtime, size, tags, people, captions, raw_metadata FROM photos WHERE path LIKE ?",
                (to_db_path(folder_path) + "/%",)
            )
            for row in cursor.fetchall():
                p, mt, sz, t_json, pe_json, c_json, raw_json = row
                db_records[normalize_path(p)] = {
                    "path": p,
                    "mtime": mt,
                    "size": sz,
                    "tags": json.loads(t_json) if t_json else [],
                    "people": json.loads(pe_json) if pe_json else [],
                    "captions": json.loads(c_json) if c_json else [],
                    "raw_metadata": json.loads(raw_json) if raw_json else {}
                }
            conn.close()
        except Exception as db_err:
            logger.warning(f"Failed to query index DB for folder scan cache: {db_err}")
            
        # Resolve file metadata
        folder_map = {}
        files_to_read = []
        
        for file in image_files:
            file_norm = normalize_path(file)
            try:
                stat = os.stat(file)
                mtime = stat.st_mtime
                size = stat.st_size
            except Exception:
                continue
                
            cached = db_records.get(file_norm)
            if cached and abs(cached["mtime"] - mtime) < 0.1 and cached["size"] == size:
                from metadata import build_photo_ui_record
                folder_map[file_norm] = build_photo_ui_record(cached["path"], cached, mtime, size)
            else:
                files_to_read.append((file, mtime, size))
                
        # For new or modified files, run ExifTool
        if files_to_read:
            logger.info(f"Scan found {len(files_to_read)} new/modified files in {folder_path}. Running ExifTool...")
            try:
                from metadata import MetadataExtractor, build_photo_ui_record
                extractor = MetadataExtractor(exiftool_path=self.get_exiftool_path())
                batch_size = 500
                for i in range(0, len(files_to_read), batch_size):
                    batch = files_to_read[i:i+batch_size]
                    batch_paths = [b[0] for b in batch]
                    batch_meta = extractor.batch_read(batch_paths)
                    for (file, mtime, size), meta in zip(batch, batch_meta):
                        file_norm = normalize_path(file)
                        folder_map[file_norm] = build_photo_ui_record(file, meta, mtime, size)
            except Exception as e:
                logger.error(f"Error running ExifTool during scan: {e}")
                
        TagPupHTTPRequestHandler.folder_cache[folder_path_norm] = folder_map
        
        response_list = list(folder_map.values())
        def get_date_taken_str(meta):
            raw_meta = meta.get("raw_metadata", {})
            for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                val = raw_meta.get(k)
                if val:
                    if isinstance(val, list) and val:
                        val = val[0]
                    return str(val).strip()
            return f"mtime_{meta.get('mtime', 0.0)}"
        response_list.sort(key=get_date_taken_str)
        self.send_json(response_list)

    def handle_get_folder_suggest_status(self, query):
        folder_path_list = query.get("path")
        if not folder_path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
        folder_path = normalize_path(urllib.parse.unquote(folder_path_list[0]))
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path, {"status": "idle"})
        self.send_json(status_info)

    def handle_post_folder_suggest_start(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        folder_path = os.path.abspath(folder_path)
        folder_path_norm = normalize_path(folder_path)
        
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path_norm)
        if status_info and status_info["status"] in ("preparing", "running"):
            self.send_json({"success": True, "status": status_info["status"]})
            return
            
        existing_suggestions = {}
        if status_info:
            existing_suggestions = status_info.get("suggestions", {})
            
        TagPupHTTPRequestHandler.suggest_status[folder_path_norm] = {
            "status": "preparing",
            "completed": len(existing_suggestions),
            "total": 0,
            "suggestions": existing_suggestions
        }
        
        t = threading.Thread(
            target=TagPupHTTPRequestHandler.run_folder_suggestions_thread,
            args=(folder_path, self.db_path),
            name="FolderSuggestionsThread",
            daemon=True
        )
        TagPupHTTPRequestHandler.suggest_threads[folder_path_norm] = t
        t.start()
        
        self.send_json({"success": True, "status": "running"})

    @classmethod
    def load_suggestions_cache(cls, db_path):
        cache_path = os.path.join(os.path.dirname(db_path), "gui_suggestions_cache.json")
        if os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Clean up any active/running statuses to "idle"
                for folder, status in data.items():
                    if status.get("status") in ("running", "preparing"):
                        status["status"] = "idle"
                with cls.model_lock:
                    cls.suggest_status.update(data)
                logger.info(f"Loaded suggestions cache from {cache_path} with {len(data)} folders.")
            except Exception as e:
                logger.error(f"Error loading suggestions cache: {e}")

    @classmethod
    def save_suggestions_cache(cls, db_path):
        cache_path = os.path.join(os.path.dirname(db_path), "gui_suggestions_cache.json")
        try:
            import json
            with cls.model_lock:
                serializable_data = make_json_serializable(cls.suggest_status)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(serializable_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving suggestions cache: {e}")

    @classmethod
    def rescan_folder_to_cache_classmethod(cls, folder_path):
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
        if not image_files:
            cls.folder_cache[normalize_path(folder_path)] = {}
            return
            
        import configparser
        config = configparser.ConfigParser(interpolation=None)
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.ini")
        exiftool_path = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Username"), r"AppData\Local\Programs\ExifTool\exiftool.exe")
        if os.path.exists(config_path):
            try:
                config.read(config_path, encoding='utf-8')
                path = config.get("paths", "exiftool", fallback=exiftool_path)
                exiftool_path = os.path.expandvars(path)
            except Exception:
                pass

        from metadata import MetadataExtractor, build_photo_ui_record
        extractor = MetadataExtractor(exiftool_path=exiftool_path)
        batch_size = 500
        results = []
        for i in range(0, len(image_files), batch_size):
            batch = image_files[i:i+batch_size]
            batch_meta = extractor.batch_read(batch)
            results.extend(batch_meta)
            
        folder_map = {}
        for meta in results:
            path = meta["path"]
            folder_map[normalize_path(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
            
        cls.folder_cache[normalize_path(folder_path)] = folder_map

    @classmethod
    def run_folder_suggestions_thread(cls, folder_path, db_path):
        folder_path = os.path.abspath(folder_path)
        folder_path_norm = normalize_path(folder_path)
        try:
            import configparser
            import concurrent.futures
            photos_dict = cls.folder_cache.get(folder_path_norm, {})
            logger.info(f"run_folder_suggestions_thread started for {folder_path}. Found {len(photos_dict)} cached photos.")
            if not photos_dict:
                logger.info(f"Folder cache empty for {folder_path}. Performing on-the-fly scan to populate cache...")
                cls.rescan_folder_to_cache_classmethod(folder_path)
                photos_dict = cls.folder_cache.get(folder_path_norm, {})
                logger.info(f"On-the-fly scan completed. Found {len(photos_dict)} photos.")
                
            if not photos_dict:
                logger.warning(f"No photos found in {folder_path} after scan. Returning early.")
                if folder_path_norm in cls.suggest_status:
                    cls.suggest_status[folder_path_norm]["status"] = "error"
                return
                
            photo_paths = list(photos_dict.keys())
            existing_suggs = cls.suggest_status[folder_path_norm].get("suggestions", {})
            unprocessed_paths = [p for p in photo_paths if photos_dict[p]["path"] not in existing_suggs]
            
            cls.suggest_status[folder_path_norm]["total"] = len(photo_paths)
            cls.suggest_status[folder_path_norm]["completed"] = len(photo_paths) - len(unprocessed_paths)
            cls.suggest_status[folder_path_norm]["status"] = "preparing"
            
            cls.save_suggestions_cache(db_path)
            
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.ini")
            config = configparser.ConfigParser(interpolation=None)
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
                
            cache_dir = config.get("paths", "embedding_cache_dir", fallback="data/embedding_cache")
            model_name = config.get("model", "name", fallback="ViT-B-32")
            pretrained = config.get("model", "pretrained", fallback="laion2b_s34b_b79k")
            candidate_str = config.get("candidates", "tags", fallback="")
            candidate_tags = [t.strip() for t in candidate_str.split(",") if t.strip()]
            
            from index import PhotoIndex
            from taxonomy import TagTaxonomy
            from suggester import TagSuggester
            from embedder import ClipEmbedder
            
            tax_path = os.path.splitext(db_path)[0] + "_taxonomy.json"
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            
            # Merge non-people taxonomy tags into candidates
            for path in taxonomy.paths:
                parts = path.split("/")
                if parts and parts[0].lower() in ["family", "friends", "pets"]:
                    continue
                leaf = parts[-1].strip()
                if leaf and leaf.lower() not in [t.lower() for t in candidate_tags]:
                    candidate_tags.append(leaf)
            
            preserve_full_frame = config.getboolean("model", "preserve_full_frame", fallback=False)
            max_aspect_ratio = config.getfloat("model", "max_aspect_ratio", fallback=2.0)
            force_image_size = config.get("model", "force_image_size", fallback=None)
            force_image_size = int(force_image_size) if force_image_size else None
            
            if hasattr(cls, "shared_embedder") and cls.shared_embedder is not None:
                embedder = cls.shared_embedder
                photo_index = embedder.photo_index
            else:
                photo_index = PhotoIndex(db_path=db_path)
                photo_index.load()
                embedder = ClipEmbedder(
                    model_name=model_name,
                    pretrained=pretrained,
                    cache_dir=cache_dir,
                    preserve_full_frame=preserve_full_frame,
                    max_aspect_ratio=max_aspect_ratio,
                    force_image_size=force_image_size,
                    photo_index=photo_index
                )
            
            suggester = TagSuggester(photo_index, taxonomy, embedder=embedder, candidate_tags=candidate_tags)
            # Precompute candidate text embeddings sequentially so they are cached before the parallel loop
            suggester._precompute_candidates()
            
            # Transition to running state as we begin processing the images
            with cls.model_lock:
                cls.suggest_status[folder_path_norm]["status"] = "running"
            cls.save_suggestions_cache(db_path)
            
            suggestions_list = []
            
            def process_single_photo(path):
                if folder_path_norm not in cls.suggest_status:
                    return None
                try:
                    meta = photos_dict[path]
                    orig_path = meta["path"]
                    emb = embedder.embed_image(orig_path)
                    sugg = suggester.suggest_for_photo(orig_path, emb, k=15, min_sim=0.35, target_metadata=meta)
                    
                    suggested_tags = []
                    suggested_people = []
                    for item in sugg.get("suggested_tags", []):
                        score = item.get("score", 0.0)
                        if score >= 0.6:
                            if item.get("has_face_match"):
                                suggested_people.append({"name": item["tag"], "score": score})
                            else:
                                suggested_tags.append({"tag": item["tag"], "score": score})
                                
                    all_sugg_tags = [t["tag"] for t in suggested_tags] + [p["name"] for p in suggested_people]
                    from writer import derive_caption_from_tags
                    suggested_title = derive_caption_from_tags(all_sugg_tags)
                    
                    with cls.model_lock:
                        cls.suggest_status[folder_path_norm]["suggestions"][orig_path] = {
                            "tags": suggested_tags,
                            "people": suggested_people,
                            "title": suggested_title,
                            "raw_suggestions": sugg
                        }
                        cls.suggest_status[folder_path_norm]["completed"] += 1
                    cls.save_suggestions_cache(db_path)
                    return sugg
                except Exception as e:
                    logger.error(f"Error suggesting for {path}: {e}")
                    meta = photos_dict.get(path, {})
                    orig_path = meta.get("path", path)
                    with cls.model_lock:
                        cls.suggest_status[folder_path_norm]["suggestions"][orig_path] = {
                            "tags": [],
                            "people": [],
                            "title": None,
                            "raw_suggestions": {"suggested_tags": []}
                        }
                        cls.suggest_status[folder_path_norm]["completed"] += 1
                    cls.save_suggestions_cache(db_path)
                    return None

            max_workers = min(4, os.cpu_count() or 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(process_single_photo, p) for p in unprocessed_paths]
                for fut in concurrent.futures.as_completed(futures):
                    res = fut.result()
                    if res is not None:
                        suggestions_list.append(res)
                
            cls.suggest_status[folder_path_norm]["status"] = "completed"
            cls.save_suggestions_cache(db_path)
            
            # Apply folder consensus
            if len(suggestions_list) > 1 and folder_path_norm in cls.suggest_status:
                try:
                    consensus_suggestions = suggester.apply_folder_consensus(suggestions_list)
                    for sugg in consensus_suggestions:
                        path = sugg["path"]
                        suggested_tags = []
                        suggested_people = []
                        for item in sugg.get("suggested_tags", []):
                            score = item.get("score", 0.0)
                            if score >= 0.6:
                                if item.get("has_face_match"):
                                    suggested_people.append({"name": item["tag"], "score": score})
                                else:
                                    suggested_tags.append({"tag": item["tag"], "score": score})
                                    
                        all_sugg_tags = [t["tag"] for t in suggested_tags] + [p["name"] for p in suggested_people]
                        from writer import derive_caption_from_tags
                        suggested_title = derive_caption_from_tags(all_sugg_tags)
                        
                        if path in cls.suggest_status[folder_path_norm]["suggestions"]:
                            cls.suggest_status[folder_path_norm]["suggestions"][path]["tags"] = suggested_tags
                            cls.suggest_status[folder_path_norm]["suggestions"][path]["people"] = suggested_people
                            cls.suggest_status[folder_path_norm]["suggestions"][path]["title"] = suggested_title
                            cls.suggest_status[folder_path_norm]["suggestions"][path]["raw_suggestions"] = sugg
                except Exception as e:
                    logger.error(f"Error folder consensus: {e}")
                    
            cls.suggest_status[folder_path_norm]["status"] = "completed"
            cls.save_suggestions_cache(db_path)
        except Exception as e:
            logger.error(f"Error running suggestions thread: {e}")
            if folder_path_norm in cls.suggest_status:
                cls.suggest_status[folder_path_norm]["status"] = "error"
                cls.save_suggestions_cache(db_path)

    def handle_post_photo_open_explorer(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_path = data.get("path")
        if not photo_path or not os.path.exists(photo_path):
            self.send_json_error(400, "Invalid file path")
            return
            
        try:
            import subprocess
            norm_path = os.path.normpath(photo_path)
            subprocess.Popen(["explorer.exe", f"/select,{norm_path}"])
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error opening explorer for {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photo_rotate(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_path = data.get("path")
        direction = data.get("direction")
        
        if not photo_path or not os.path.exists(photo_path):
            self.send_json_error(400, "Invalid file path")
            return
            
        try:
            from metadata import rotate_image_file
            executable = self.get_exiftool_path()
            rotate_image_file(photo_path, direction, executable)
                
            # Update cache file stats
            folder_path = normalize_path(os.path.dirname(photo_path))
            if folder_path in TagPupHTTPRequestHandler.folder_cache:
                stat = os.stat(photo_path)
                photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].get(normalize_path(photo_path))
                if photo_entry:
                    photo_entry["mtime"] = stat.st_mtime
                    photo_entry["size"] = stat.st_size
                    
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error rotating image {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photo_save_metadata(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_path = data.get("path")
        title = data.get("title")
        tags = data.get("tags", [])
        date_taken = data.get("date_taken")
        
        if not photo_path or not os.path.exists(photo_path):
            self.send_json_error(400, "Invalid file path")
            return
            
        try:
            new_flat_tags = []
            new_hierarchical_tags = []
            for tag in tags:
                new_flat_tags.append(tag)
                if "/" in tag:
                    new_hierarchical_tags.append(tag)
                    for part in tag.split("/"):
                        new_flat_tags.append(part)
            
            new_flat_tags = list(set(new_flat_tags))
            new_hierarchical_tags = list(set(new_hierarchical_tags))
            
            params = {}
            if new_flat_tags:
                params["XMP:Subject"] = new_flat_tags
                params["IPTC:Keywords"] = new_flat_tags
                params["EXIF:XPKeywords"] = ";".join(new_flat_tags)
            else:
                params["XMP:Subject"] = []
                params["IPTC:Keywords"] = []
                params["EXIF:XPKeywords"] = ""
                
            if new_hierarchical_tags:
                params["XMP:HierarchicalSubject"] = new_hierarchical_tags
            else:
                params["XMP:HierarchicalSubject"] = []
                
            if title:
                params["XMP:Description"] = title
                params["IPTC:Caption-Abstract"] = title
                params["EXIF:ImageDescription"] = title
                params["EXIF:XPComment"] = title
            else:
                params["XMP:Description"] = ""
                params["IPTC:Caption-Abstract"] = ""
                params["EXIF:ImageDescription"] = ""
                params["EXIF:XPComment"] = ""
                
            if date_taken:
                # Normalize ISO T separator to space, and replace dash in date with colon
                date_cleaned = str(date_taken).replace("T", " ").replace("-", ":").strip()
                params["EXIF:DateTimeOriginal"] = date_cleaned
                params["XMP:DateTimeOriginal"] = date_cleaned
                params["EXIF:CreateDate"] = date_cleaned
                
                # Write subseconds explicitly if present (e.g. .123)
                subsec_parts = date_cleaned.split(".")
                if len(subsec_parts) > 1:
                    subsec = subsec_parts[1]
                    subsec_digits = ""
                    for char in subsec:
                        if char.isdigit():
                            subsec_digits += char
                        else:
                            break
                    if subsec_digits:
                        params["EXIF:SubSecTimeOriginal"] = subsec_digits
                        params["EXIF:SubSecTimeDigitized"] = subsec_digits
                        params["EXIF:SubSecTime"] = subsec_digits

            executable = self.get_exiftool_path()
            import exiftool
            with exiftool.ExifToolHelper(executable=executable) as et:
                et.set_tags([photo_path], tags=params, params=["-overwrite_original"])
                
            from metadata import sync_title_to_filename, METADATA_FIELDS
            new_path = sync_title_to_filename(photo_path, title, executable)
            
            # Update SQLite database
            try:
                # Get new file stats on disk
                stat = os.stat(new_path)
                mtime = stat.st_mtime
                size = stat.st_size
                
                # Fetch new raw metadata from ExifTool
                with exiftool.ExifToolHelper(executable=executable) as et:
                    fresh_meta_list = et.get_tags([new_path], tags=METADATA_FIELDS)
                    fresh_meta = fresh_meta_list[0] if fresh_meta_list else {}
                    
                # Clean metadata
                from metadata import clean_metadata_value, extract_tags, extract_people
                cleaned_meta = {k: clean_metadata_value(v) for k, v in fresh_meta.items()}
                db_tags = extract_tags(cleaned_meta)
                db_people = extract_people(cleaned_meta, db_tags, db_path=self.db_path)
                db_captions = [title] if title else []
                
                conn = sqlite3.connect(self.db_path, timeout=10.0)
                cursor = conn.cursor()
                
                # If renamed, delete old and insert new (preserving embedding if present)
                if normalize_path(new_path) != normalize_path(photo_path):
                    cursor.execute("SELECT embedding FROM photos WHERE path = ?", (to_db_path(photo_path),))
                    row = cursor.fetchone()
                    emb = row[0] if row else None
                    
                    cursor.execute("DELETE FROM photos WHERE path = ?", (to_db_path(photo_path),))
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (to_db_path(new_path), mtime, size, json.dumps(db_tags), json.dumps(db_people), json.dumps(db_captions), json.dumps(cleaned_meta), emb)
                    )
                    cursor.execute("UPDATE faces SET photo_path = ? WHERE photo_path = ?", (to_db_path(new_path), to_db_path(photo_path)))
                else:
                    cursor.execute(
                        """
                        UPDATE photos 
                        SET mtime = ?, size = ?, tags = ?, people = ?, captions = ?, raw_metadata = ?
                        WHERE path = ?
                        """,
                        (mtime, size, json.dumps(db_tags), json.dumps(db_people), json.dumps(db_captions), json.dumps(cleaned_meta), to_db_path(new_path))
                    )
                conn.commit()
                conn.close()
            except Exception as db_err:
                logger.warning(f"Failed to update SQLite database metadata for {new_path}: {db_err}")

            # Update in-memory cache
            folder_path = normalize_path(os.path.dirname(new_path))
            if folder_path in TagPupHTTPRequestHandler.folder_cache:
                if normalize_path(new_path) != normalize_path(photo_path):
                    photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].pop(normalize_path(photo_path), None)
                    if photo_entry:
                        photo_entry["path"] = new_path
                        photo_entry["filename"] = os.path.basename(new_path)
                        TagPupHTTPRequestHandler.folder_cache[folder_path][normalize_path(new_path)] = photo_entry
                else:
                    photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].get(normalize_path(photo_path))
                    
                if photo_entry:
                    from metadata import extract_tags
                    # Update raw_metadata tags
                    photo_entry["raw_metadata"]["XMP:Subject"] = new_flat_tags
                    photo_entry["raw_metadata"]["XMP:HierarchicalSubject"] = new_hierarchical_tags
                    if date_taken:
                        date_cleaned = str(date_taken).replace("T", " ").replace("-", ":").strip()
                        photo_entry["raw_metadata"]["EXIF:DateTimeOriginal"] = date_cleaned
                        photo_entry["raw_metadata"]["XMP:DateTimeOriginal"] = date_cleaned
                        photo_entry["raw_metadata"]["EXIF:CreateDate"] = date_cleaned
                    photo_entry["tags"] = extract_tags(photo_entry["raw_metadata"])
                    photo_entry["captions"] = [title] if title else []
                    photo_entry["title"] = title
                    from metadata import extract_people
                    photo_entry["people"] = extract_people(photo_entry["raw_metadata"], tags)
                    
            self.send_json({"success": True, "new_path": new_path})
        except Exception as e:
            logger.error(f"Error saving metadata for {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photos_bulk_tags(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        paths = data.get("paths", [])
        add_tags = data.get("add_tags", [])
        remove_tags = data.get("remove_tags", [])
        
        if not paths:
            self.send_json_error(400, "Missing paths list")
            return
            
        executable = self.get_exiftool_path()
        import exiftool
        from metadata import extract_people
        
        try:
            with exiftool.ExifToolHelper(executable=executable) as et:
                for path in paths:
                    folder_path = normalize_path(os.path.dirname(path))
                    photo_entry = None
                    if folder_path in TagPupHTTPRequestHandler.folder_cache:
                        photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].get(normalize_path(path))
                        
                    current_tags = photo_entry["tags"] if photo_entry else []
                    new_tags_set = set(current_tags)
                    for t in add_tags:
                        new_tags_set.add(t)
                    for t in remove_tags:
                        new_tags_set.discard(t)
                        
                    new_tags = list(new_tags_set)
                    
                    new_flat_tags = []
                    for tag in new_tags:
                        if "/" in tag:
                            new_flat_tags.append(tag.split("/")[-1].strip())
                        else:
                            new_flat_tags.append(tag.strip())
                            
                    new_flat_tags = list(set(new_flat_tags))
                    
                    params = {}
                    if new_flat_tags:
                        params["XMP:Subject"] = new_flat_tags
                        params["IPTC:Keywords"] = new_flat_tags
                        params["EXIF:XPKeywords"] = ";".join(new_flat_tags)
                    else:
                        params["XMP:Subject"] = []
                        params["IPTC:Keywords"] = []
                        params["EXIF:XPKeywords"] = ""
                        
                    params["XMP:HierarchicalSubject"] = []
                        
                    et.set_tags([path], tags=params, params=["-overwrite_original"])
                    
                    if photo_entry:
                        photo_entry["tags"] = new_flat_tags
                        photo_entry["people"] = extract_people(photo_entry.get("raw_metadata", {}), new_flat_tags)
                        
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in bulk tags write: {e}")
            self.send_json_error(500, str(e))

    def handle_post_folder_auto_apply(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        threshold = data.get("threshold", 0.75)
        
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        folder_path = normalize_path(folder_path)
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path)
        if not status_info or "suggestions" not in status_info:
            self.send_json_error(400, "No suggestions found for this folder")
            return
            
        suggestions_map = status_info["suggestions"]
        photo_paths = data.get("photo_paths")
        if photo_paths:
            photo_paths = [os.path.normpath(p).replace("\\", "/") for p in photo_paths]
            suggestions_map = {k: v for k, v in suggestions_map.items() if os.path.normpath(k).replace("\\", "/") in photo_paths}
        executable = self.get_exiftool_path()
        import exiftool
        from metadata import extract_people
        from writer import derive_caption_from_tags
        
        try:
            with exiftool.ExifToolHelper(executable=executable) as et:
                for path, sugg_info in suggestions_map.items():
                    raw_sugg = sugg_info.get("raw_suggestions", {})
                    suggested_tags = raw_sugg.get("suggested_tags", [])
                    
                    apply_tags = [t["tag"] for t in suggested_tags if t.get("score", 0.0) >= threshold]
                    if not apply_tags:
                        continue
                        
                    folder_path_dir = os.path.dirname(path)
                    photo_entry = None
                    if folder_path_dir in TagPupHTTPRequestHandler.folder_cache:
                        photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path_dir].get(path)
                        
                    current_tags = photo_entry["tags"] if photo_entry else []
                    new_tags = list(set(current_tags + apply_tags))
                    
                    new_flat_tags = []
                    for tag in new_tags:
                        if "/" in tag:
                            new_flat_tags.append(tag.split("/")[-1].strip())
                        else:
                            new_flat_tags.append(tag.strip())
                            
                    new_flat_tags = list(set(new_flat_tags))
                    
                    params = {}
                    if new_flat_tags:
                        params["XMP:Subject"] = new_flat_tags
                        params["IPTC:Keywords"] = new_flat_tags
                        params["EXIF:XPKeywords"] = ";".join(new_flat_tags)
                    else:
                        params["XMP:Subject"] = []
                        params["IPTC:Keywords"] = []
                        params["EXIF:XPKeywords"] = ""
                        
                    params["XMP:HierarchicalSubject"] = []
                        
                    et.set_tags([path], tags=params, params=["-overwrite_original"])
                    
                    if photo_entry:
                        photo_entry["tags"] = new_flat_tags
                        photo_entry["people"] = extract_people(photo_entry.get("raw_metadata", {}), new_flat_tags)
                            
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error auto-applying suggestions: {e}")
            self.send_json_error(500, str(e))

    def handle_post_folder_time_shift(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        camera_model = data.get("camera_model")
        shift_minutes = data.get("shift_minutes", 0)
        
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        if shift_minutes == 0:
            self.send_json({"success": True, "message": "No shift applied (0 minutes)"})
            return
            
        folder_path = normalize_path(folder_path)
        
        # Load from cache, or scan on the fly if missing
        if folder_path not in TagPupHTTPRequestHandler.folder_cache:
            try:
                from metadata import MetadataExtractor
                executable = self.get_exiftool_path()
                extractor = MetadataExtractor(exiftool_path=executable)
                
                valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
                image_files = []
                for root, _, files in os.walk(folder_path):
                    for file in files:
                        ext = os.path.splitext(file)[1].lower()
                        if ext in valid_exts:
                            image_files.append(os.path.join(root, file))
                            
                from metadata import build_photo_ui_record
                results = extractor.batch_read(image_files)
                folder_map = {}
                for meta in results:
                    path = meta["path"]
                    folder_map[path] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
                TagPupHTTPRequestHandler.folder_cache[folder_path] = folder_map
            except Exception as scan_err:
                logger.error(f"Error scanning folder on the fly for time shift: {scan_err}")
                self.send_json_error(500, f"Folder must be scanned first, and scan fallback failed: {scan_err}")
                return
            
        photos_map = TagPupHTTPRequestHandler.folder_cache[folder_path]
        
        # Filter photos by camera model
        target_paths = []
        for path, entry in photos_map.items():
            raw = entry.get("raw_metadata", {})
            model = raw.get("EXIF:Model") or raw.get("Model") or raw.get("EXIF:Make") or raw.get("Make") or "Unknown Camera"
            if camera_model == "All Cameras" or model == camera_model:
                target_paths.append(path)
                
        if not target_paths:
            self.send_json({"success": True, "message": "No photos matched the camera model"})
            return
            
        executable = self.get_exiftool_path()
        import exiftool
        
        sign = "+" if shift_minutes >= 0 else "-"
        abs_minutes = abs(shift_minutes)
        
        shift_dto = f"-DateTimeOriginal{sign}=0:0:0 0:{abs_minutes}:0"
        shift_cd = f"-CreateDate{sign}=0:0:0 0:{abs_minutes}:0"
        
        try:
            with exiftool.ExifTool(executable=executable) as et:
                batch_size = 50
                for i in range(0, len(target_paths), batch_size):
                    batch = target_paths[i:i+batch_size]
                    args = [shift_dto, shift_cd, "-overwrite_original"] + batch
                    et.execute(*args)
                    
            # Re-read metadata for updated photos to refresh cache
            from metadata import MetadataExtractor
            extractor = MetadataExtractor(exiftool_path=executable)
            updated_entries = extractor.batch_read(target_paths)
            
            from metadata import build_photo_ui_record
            for entry in updated_entries:
                p = entry["path"]
                if p in photos_map:
                    photos_map[p] = build_photo_ui_record(p, entry, photos_map[p].get("mtime", 0.0), photos_map[p].get("size", 0))
                    
            updated_photos = list(photos_map.values())
            self.send_json({"success": True, "updated_photos": updated_photos})
        except Exception as e:
            logger.error(f"Error applying time shift to {folder_path}: {e}")
            self.send_json_error(500, str(e))
    def rescan_folder_to_cache(self, folder_path):
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
        if not image_files:
            TagPupHTTPRequestHandler.folder_cache[normalize_path(folder_path)] = {}
            return
            
        from metadata import MetadataExtractor, build_photo_ui_record
        extractor = MetadataExtractor(exiftool_path=self.get_exiftool_path())
        batch_size = 500
        results = []
        for i in range(0, len(image_files), batch_size):
            batch = image_files[i:i+batch_size]
            batch_meta = extractor.batch_read(batch)
            results.extend(batch_meta)
            
        folder_map = {}
        for meta in results:
            path = meta["path"]
            folder_map[normalize_path(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
            
        TagPupHTTPRequestHandler.folder_cache[normalize_path(folder_path)] = folder_map

    def handle_post_folder_rename_photos(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        photo_paths = data.get("photo_paths", [])
        grouping = data.get("grouping", "").strip()
        
        folder_path = to_db_path(folder_path)
        if photo_paths:
            photo_paths = [to_db_path(p) for p in photo_paths]
            
        if not folder_path or not os.path.exists(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        if not photo_paths:
            self.send_json_error(400, "No photos selected for renaming")
            return
            
        try:
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.ini")
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
            
            if not config.has_section("renaming"):
                config.add_section("renaming")
            if not config.has_option("renaming", "format"):
                config.set("renaming", "format", "{grouping} - {index} - {caption}")
                with open(config_path, "w", encoding='utf-8') as f:
                    config.write(f)
                    
            format_pattern = config.get("renaming", "format")
            
            # Sort the selected photo paths chronologically by Date Taken
            cache = TagPupHTTPRequestHandler.folder_cache.get(normalize_path(folder_path), {})
            cache = {normalize_path(k): v for k, v in cache.items()}
            
            def get_date_taken_sort_key(p_path):
                entry = cache.get(p_path)
                if entry:
                    raw = entry.get("raw_metadata", {})
                    for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                        val = raw.get(k)
                        if val:
                            if isinstance(val, list) and val:
                                val = val[0]
                            return str(val).strip()
                    return f"mtime_{entry.get('mtime', 0.0)}"
                try:
                    return f"mtime_{os.path.getmtime(p_path)}"
                except OSError:
                    return "9999"
                    
            sorted_paths = sorted(photo_paths, key=get_date_taken_sort_key)
            
            # Calculate target path for each selected file
            N = len(sorted_paths)
            index_len = len(str(N))
            
            import exiftool
            from metadata import sanitize_filename
            executable = self.get_exiftool_path()
            
            selected_renames = {}
            
            for idx, old_path in enumerate(sorted_paths, start=1):
                if not os.path.exists(old_path):
                    continue
                    
                with exiftool.ExifToolHelper(executable=executable) as et:
                    meta = et.get_tags([old_path], tags=[
                        "XMP-xmpMM:PreservedFileName", "XMP:PreservedFileName",
                        "XMP:Title", "Title", "XMP:Description", "Description",
                        "IPTC:Caption-Abstract", "Caption-Abstract"
                    ])
                    meta_dict = meta[0] if meta else {}
                    
                preserved = None
                for k, v in meta_dict.items():
                    base = k.split(":")[-1] if ":" in k else k
                    if base == "PreservedFileName":
                        preserved = str(v).strip()
                        break
                        
                if not preserved:
                    orig_name = os.path.basename(old_path)
                    with exiftool.ExifToolHelper(executable=executable) as et:
                        et.set_tags([old_path], tags={"XMP-xmpMM:PreservedFileName": orig_name}, params=["-overwrite_original"])
                        
                title = ""
                for k, v in meta_dict.items():
                    base = k.split(":")[-1] if ":" in k else k
                    if base in ["Description", "Caption-Abstract", "Title"]:
                        if v:
                            if isinstance(v, list) and v:
                                title = str(v[0]).strip()
                            else:
                                title = str(v).strip()
                            if title:
                                break
                title = title.strip()
                
                index_str = str(idx).zfill(index_len)
                new_base = format_pattern.replace("{grouping}", grouping).replace("{index}", index_str)
                if title:
                    new_base = new_base.replace("{caption}", title)
                else:
                    new_base = new_base.replace(" - {caption}", "").replace("- {caption}", "").replace("{caption}", "")
                    
                new_base = sanitize_filename(new_base)
                ext = os.path.splitext(old_path)[1]
                new_name = new_base + ext
                new_path = os.path.join(folder_path, new_name).replace("\\", "/")
                
                selected_renames[old_path] = new_path

            # Identify and resolve external conflicts on disk
            for old_path, target_path in selected_renames.items():
                if os.path.exists(target_path) and target_path not in selected_renames:
                    dir_name = os.path.dirname(target_path)
                    base, ext = os.path.splitext(os.path.basename(target_path))
                    counter = 1
                    safe_path = os.path.join(dir_name, f"{base}_conflict_{counter}{ext}").replace("\\", "/")
                    while os.path.exists(safe_path) or safe_path in selected_renames.values():
                        counter += 1
                        safe_path = os.path.join(dir_name, f"{base}_conflict_{counter}{ext}").replace("\\", "/")
                    os.rename(target_path, safe_path)

            # Two-pass rename sequence to avoid self-overwrite conflicts in the selection range
            temp_renames = {}
            import time
            for old_path, target_path in selected_renames.items():
                if old_path != target_path:
                    dir_name = os.path.dirname(old_path)
                    ext = os.path.splitext(old_path)[1]
                    temp_path = os.path.join(dir_name, f"tmp_rename_{hash(old_path)}_{time.time()}{ext}").replace("\\", "/")
                    os.rename(old_path, temp_path)
                    temp_renames[temp_path] = target_path
                else:
                    temp_renames[old_path] = target_path

            updated_paths_map = {}
            for temp_path, target_path in temp_renames.items():
                if temp_path != target_path:
                    os.rename(temp_path, target_path)
                    orig_old_path = next(k for k, v in selected_renames.items() if v == target_path)
                    updated_paths_map[orig_old_path] = target_path
                else:
                    updated_paths_map[target_path] = target_path
                    
            # Clear old and scan new cache entries
            if normalize_path(folder_path) in TagPupHTTPRequestHandler.folder_cache:
                del TagPupHTTPRequestHandler.folder_cache[normalize_path(folder_path)]
                
            self.rescan_folder_to_cache(folder_path)
            
            # Send updated photos sorted chronologically
            updated_list = list(TagPupHTTPRequestHandler.folder_cache.get(normalize_path(folder_path), {}).values())
            
            def get_date_taken_str(meta):
                raw_meta = meta.get("raw_metadata", {})
                for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                    val = raw_meta.get(k)
                    if val:
                        if isinstance(val, list) and val:
                            val = val[0]
                        return str(val).strip()
                return f"mtime_{meta.get('mtime', 0.0)}"
            updated_list.sort(key=get_date_taken_str)
            
            self.send_json({
                "success": True,
                "updated_paths": updated_paths_map,
                "updated_photos": updated_list
            })
            
        except Exception as e:
            logger.error(f"Error smart renaming photos: {e}", exc_info=True)
            self.send_json_error(500, str(e))

    def handle_serve_photo_file(self, query):
        photo_path_list = query.get("path")
        if not photo_path_list:
            self.send_error(400, "Missing 'path' parameter")
            return
        photo_path = urllib.parse.unquote(photo_path_list[0])
        
        # Security check: Restrict serving to only standard image extensions
        VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif", ".heic", ".heif"}
        _, ext = os.path.splitext(photo_path.lower())
        if ext not in VALID_IMAGE_EXTS:
            self.send_error(400, "Forbidden: Invalid file type requested")
            return

        if not os.path.exists(photo_path):
            self.send_error(404, f"Photo file not found: {photo_path}")
            return
        try:
            size_param = query.get("size")
            content_type = "image/jpeg"
            if size_param:
                try:
                    max_size = int(size_param[0])
                    with Image.open(photo_path) as img:
                        # Exif transpose so preview is rotated properly in UI
                        img = ImageOps.exif_transpose(img)
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                        
                        out_io = io.BytesIO()
                        img.save(out_io, format="JPEG", quality=85)
                        content = out_io.getvalue()
                except Exception as e:
                    logger.warning(f"Could not resize thumbnail for {photo_path}: {e}")
                    with open(photo_path, "rb") as f:
                        content = f.read()
            else:
                with open(photo_path, "rb") as f:
                    content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "max-age=86400") # Cache local thumbnails
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Internal error serving image: {e}")

    def handle_get_taxonomy_tree(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if not cursor.fetchone():
                from taxonomy import seed_taxonomy_from_db
                seed_taxonomy_from_db(self.db_path)
            
            cursor.execute("SELECT id, tag, parent_id, name, has_face, hidden_from_autocomplete FROM tag_taxonomy ORDER BY tag")
            rows = cursor.fetchall()
            conn.close()
            
            counts = get_tag_usage_counts(self.db_path)
            
            tree_nodes = []
            for row in rows:
                node = {
                    "id": row[0],
                    "tag": row[1],
                    "parent_id": row[2],
                    "name": row[3],
                    "has_face": row[4],
                    "hidden_from_autocomplete": row[5],
                    "usage_count": counts.get(row[1], 0)
                }
                tree_nodes.append(node)
                
            self.send_json(tree_nodes)
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_create(self):
        try:
            data = self.read_json_body()
            name = data.get("name", "").strip()
            parent_id = data.get("parent_id")
            has_face = data.get("has_face", 0)
            
            if not name:
                self.send_json_error(400, "Tag name cannot be empty")
                return
                
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            
            if parent_id:
                cursor.execute("SELECT tag, has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                parent_row = cursor.fetchone()
                if not parent_row:
                    conn.close()
                    self.send_json_error(404, "Parent tag not found")
                    return
                parent_path, parent_has_face = parent_row
                tag_path = parent_path + "/" + name
                has_face = parent_has_face
            else:
                tag_path = name
                
            from taxonomy import TagTaxonomy
            tag_path = TagTaxonomy.normalize_tag(tag_path)
            
            cursor.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (tag_path,))
            existing = cursor.fetchone()
            if existing:
                conn.close()
                self.send_json({"success": True, "id": existing[0], "message": "Tag already exists"})
                return
                
            cursor.execute(
                "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                (tag_path, parent_id, name, has_face)
            )
            new_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            taxonomy = TagTaxonomy(db_path=self.db_path)
            taxonomy.load()
            taxonomy.add_tag(tag_path)
            taxonomy.save()
            
            self.send_json({"success": True, "id": new_id, "tag": tag_path})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_update(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("id")
            has_face = data.get("has_face")
            hidden_from_autocomplete = data.get("hidden_from_autocomplete")
            
            if tag_id is None:
                self.send_json_error(400, "Missing 'id' parameter")
                return
                
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            
            cursor.execute("SELECT tag, parent_id FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            tag_path, parent_id = row
            
            if has_face is not None:
                cursor.execute("UPDATE tag_taxonomy SET has_face = ? WHERE id = ?", (has_face, tag_id))
                cursor.execute(
                    "UPDATE tag_taxonomy SET has_face = ? WHERE tag = ? OR tag LIKE ?",
                    (has_face, tag_path, tag_path + "/%")
                )
                
            if hidden_from_autocomplete is not None:
                cursor.execute("UPDATE tag_taxonomy SET hidden_from_autocomplete = ? WHERE id = ?", (hidden_from_autocomplete, tag_id))
                cursor.execute(
                    "UPDATE tag_taxonomy SET hidden_from_autocomplete = ? WHERE tag = ? OR tag LIKE ?",
                    (hidden_from_autocomplete, tag_path, tag_path + "/%")
                )
                
            conn.commit()
            conn.close()
            self.send_json({"success": True})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_delete_check(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("tag_id")
            if tag_id is None:
                self.send_json_error(400, "Missing 'tag_id' parameter")
                return
                
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            tag_path = row[0]
            conn.close()
            
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL")
            affected_photos = []
            for path, tags_json in cursor.fetchall():
                try:
                    tags_list = json.loads(tags_json)
                    for tag in tags_list:
                        from taxonomy import TagTaxonomy
                        normalized = TagTaxonomy.normalize_tag(tag)
                        if normalized == tag_path or normalized.startswith(tag_path + "/"):
                            affected_photos.append(path)
                            break
                except Exception:
                    pass
            conn.close()
            
            self.send_json({
                "success": True,
                "tag": tag_path,
                "used": len(affected_photos) > 0,
                "count": len(affected_photos),
                "affected_photos": affected_photos[:100]
            })
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_delete_confirm(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("tag_id")
            action = data.get("action")
            target_tag = data.get("target_tag")
            
            if tag_id is None or not action:
                self.send_json_error(400, "Missing parameters")
                return
                
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            tag_path = row[0]
            conn.close()
            
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL")
            affected_photos = []
            for path, tags_json in cursor.fetchall():
                try:
                    tags_list = json.loads(tags_json)
                    for tag in tags_list:
                        from taxonomy import TagTaxonomy
                        normalized = TagTaxonomy.normalize_tag(tag)
                        if normalized == tag_path or normalized.startswith(tag_path + "/"):
                            affected_photos.append(path)
                            break
                except Exception:
                    pass
            conn.close()
            
            if affected_photos:
                executable = self.get_exiftool_path()
                if action == "move":
                    if not target_tag:
                        self.send_json_error(400, "Target tag path is required for move action")
                        return
                    from taxonomy import TagTaxonomy
                    target_tag = TagTaxonomy.normalize_tag(target_tag)
                    conn = sqlite3.connect(self.db_path, timeout=10.0)
                    cursor = conn.cursor()
                    insert_tag_path_to_db(cursor, target_tag)
                    conn.commit()
                    conn.close()
                    
                    update_photo_metadata_tags(self.db_path, executable, affected_photos, tag_path, target_tag)
                else:
                    update_photo_metadata_tags(self.db_path, executable, affected_photos, tag_path, None)
                    
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("DELETE FROM tag_taxonomy WHERE id = ?", (tag_id,))
            conn.commit()
            conn.close()
            
            from taxonomy import TagTaxonomy
            taxonomy = TagTaxonomy(db_path=self.db_path)
            taxonomy.load()
            paths_to_remove = [p for p in taxonomy.paths if p == tag_path or p.startswith(tag_path + "/")]
            for p in paths_to_remove:
                taxonomy.paths.discard(p)
            taxonomy.save()
            
            TagPupHTTPRequestHandler.folder_cache.clear()
            self.send_json({"success": True})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_rename(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("tag_id")
            new_name = data.get("new_name", "").strip()
            
            if tag_id is None or not new_name:
                self.send_json_error(400, "Missing parameters")
                return
                
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tag, parent_id, name FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            old_tag_path, parent_id, current_name = row
            
            if current_name == new_name:
                conn.close()
                self.send_json({"success": True})
                return
                
            # Compute new path
            if parent_id is not None:
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (parent_id,))
                parent_row = cursor.fetchone()
                if not parent_row:
                    conn.close()
                    self.send_json_error(500, "Parent tag not found in DB")
                    return
                new_tag_path = parent_row[0] + "/" + new_name
            else:
                new_tag_path = new_name
                
            from taxonomy import TagTaxonomy
            new_tag_path = TagTaxonomy.normalize_tag(new_tag_path)
            
            # Check for conflict
            cursor.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (new_tag_path,))
            conflict = cursor.fetchone()
            if conflict:
                conn.close()
                self.send_json_error(400, f"A tag with path '{new_tag_path}' already exists.")
                return
                
            # Retrieve descendants
            cursor.execute("SELECT id, tag FROM tag_taxonomy WHERE tag LIKE ?", (old_tag_path + "/%",))
            descendants = cursor.fetchall()
            
            # Update the node itself
            cursor.execute("UPDATE tag_taxonomy SET name = ?, tag = ? WHERE id = ?", (new_name, new_tag_path, tag_id))
            
            # Update descendants paths
            for desc_id, desc_tag in descendants:
                new_desc_tag = new_tag_path + desc_tag[len(old_tag_path):]
                cursor.execute("UPDATE tag_taxonomy SET tag = ? WHERE id = ?", (new_desc_tag, desc_id))
                
            conn.commit()
            conn.close()
            
            # Find and update affected photos
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL")
            affected_photos = []
            for path, tags_json in cursor.fetchall():
                try:
                    tags_list = json.loads(tags_json)
                    for tag in tags_list:
                        normalized = TagTaxonomy.normalize_tag(tag)
                        if normalized == old_tag_path or normalized.startswith(old_tag_path + "/"):
                            affected_photos.append(path)
                            break
                except Exception:
                    pass
            conn.close()
            
            if affected_photos:
                executable = self.get_exiftool_path()
                update_photo_metadata_tags(self.db_path, executable, affected_photos, old_tag_path, new_tag_path)
                
            # Update taxonomy fallback JSON
            taxonomy = TagTaxonomy(db_path=self.db_path)
            taxonomy.load()
            
            # Remove old paths
            paths_to_remove = [p for p in taxonomy.paths if p == old_tag_path or p.startswith(old_tag_path + "/")]
            for p in paths_to_remove:
                taxonomy.paths.discard(p)
                
            # Add new paths
            taxonomy.paths.add(new_tag_path)
            for desc_id, desc_tag in descendants:
                new_desc_tag = new_tag_path + desc_tag[len(old_tag_path):]
                taxonomy.paths.add(new_desc_tag)
                
            taxonomy.save()
            
            TagPupHTTPRequestHandler.folder_cache.clear()
            self.send_json({"success": True})
        except Exception as e:
            self.send_json_error(500, str(e))

from typing import List, Optional
def get_tag_usage_counts(db_path):
    counts = {}
    if not os.path.exists(db_path):
        return counts
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        cursor = conn.cursor()
        cursor.execute("SELECT tags FROM photos WHERE tags IS NOT NULL")
        for row in cursor.fetchall():
            try:
                tags_list = json.loads(row[0])
                for tag in tags_list:
                    from taxonomy import TagTaxonomy
                    normalized = TagTaxonomy.normalize_tag(tag)
                    if not normalized:
                        continue
                    parts = normalized.split("/")
                    for i in range(1, len(parts) + 1):
                        ancestor = "/".join(parts[:i])
                        counts[ancestor] = counts.get(ancestor, 0) + 1
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return counts

def insert_tag_path_to_db(cursor, path: str, has_face_root: bool = False) -> int:
    from taxonomy import TagTaxonomy
    normalized = TagTaxonomy.normalize_tag(path)
    if not normalized:
        return None
    
    parts = normalized.split("/")
    parent_id = None
    accumulated_path = ""
    
    for i, part in enumerate(parts):
        if i == 0:
            accumulated_path = part
        else:
            accumulated_path += "/" + part
            
        cursor.execute("SELECT id, has_face FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
        row = cursor.fetchone()
        if row:
            parent_id = row[0]
            current_has_face = row[1]
            if i == 0 and has_face_root and not current_has_face:
                cursor.execute("UPDATE tag_taxonomy SET has_face = 1 WHERE id = ?", (parent_id,))
        else:
            is_p = 0
            if i == 0:
                if has_face_root or part.lower() in ["people", "family", "friends", "pets"]:
                    is_p = 1
            else:
                if parent_id is not None:
                    cursor.execute("SELECT has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                    p_row = cursor.fetchone()
                    if p_row:
                        is_p = p_row[0]
            
            cursor.execute(
                "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                (accumulated_path, parent_id, part, is_p)
            )
            parent_id = cursor.lastrowid
            
    return parent_id

def update_photo_metadata_tags(db_path: str, exiftool_path: str, photo_paths: List[str], tag_to_remove: str, tag_to_add: Optional[str] = None):
    import sqlite3
    import json
    import exiftool
    from metadata import extract_people, extract_tags
    from taxonomy import TagTaxonomy
    
    conn = sqlite3.connect(db_path, timeout=30.0)
    cursor = conn.cursor()
    
    batch_size = 50
    with exiftool.ExifToolHelper(executable=exiftool_path) as et:
        for i in range(0, len(photo_paths), batch_size):
            batch = photo_paths[i:i+batch_size]
            for path in batch:
                cursor.execute("SELECT tags, raw_metadata FROM photos WHERE path = ?", (path,))
                row = cursor.fetchone()
                if not row:
                    continue
                try:
                    current_tags = json.loads(row[0]) if row[0] else []
                    raw_meta = json.loads(row[1]) if row[1] else {}
                except Exception:
                    continue
                
                new_tags = []
                changed = False
                for tag in current_tags:
                    normalized = TagTaxonomy.normalize_tag(tag)
                    if normalized == tag_to_remove or normalized.startswith(tag_to_remove + "/"):
                        changed = True
                        if tag_to_add:
                            suffix = normalized[len(tag_to_remove):]
                            new_tag = tag_to_add + suffix
                            new_tags.append(new_tag)
                    else:
                        new_tags.append(tag)
                        
                if not changed:
                    continue
                    
                new_flat_tags = []
                new_hierarchical_tags = []
                for tag in new_tags:
                    new_flat_tags.append(tag)
                    if "/" in tag:
                        new_hierarchical_tags.append(tag)
                        for part in tag.split("/"):
                            new_flat_tags.append(part)
                            
                new_flat_tags = list(set(new_flat_tags))
                new_hierarchical_tags = list(set(new_hierarchical_tags))
                
                params = {}
                if new_flat_tags:
                    params["XMP:Subject"] = new_flat_tags
                    params["IPTC:Keywords"] = new_flat_tags
                    params["EXIF:XPKeywords"] = ";".join(new_flat_tags)
                else:
                    params["XMP:Subject"] = []
                    params["IPTC:Keywords"] = []
                    params["EXIF:XPKeywords"] = ""
                    
                if new_hierarchical_tags:
                    params["XMP:HierarchicalSubject"] = new_hierarchical_tags
                else:
                    params["XMP:HierarchicalSubject"] = []
                    
                try:
                    et.set_tags([path], tags=params, params=["-overwrite_original"])
                    
                    raw_meta["XMP:Subject"] = new_flat_tags
                    raw_meta["XMP:HierarchicalSubject"] = new_hierarchical_tags
                    
                    updated_tags = extract_tags(raw_meta)
                    updated_people = extract_people(raw_meta, updated_tags, db_path=db_path)
                    
                    cursor.execute(
                        "UPDATE photos SET tags = ?, people = ?, raw_metadata = ? WHERE path = ?",
                        (json.dumps(updated_tags), json.dumps(updated_people), json.dumps(raw_meta), path)
                    )
                except Exception as err:
                    logger.error(f"Failed to update metadata on disk/db for {path}: {err}")
                    
    conn.commit()
    conn.close()

class ThreadedHTTPServer(ThreadingTCPServer):
    allow_reuse_address = True

def warmup_embedder_thread(embedder):
    logger.info("Background thread starting CLIP model warmup...")
    try:
        embedder._init_model()
        embedder.embed_text("warmup")
        logger.info("Background CLIP model warmup completed successfully.")
    except Exception as e:
        logger.error(f"Error warming up CLIP model: {e}")

    try:
        logger.info("Background thread starting Face model warmup...")
        from faces import FaceProcessor
        import suggester
        with suggester._face_processor_lock:
            if suggester._global_face_processor is None:
                suggester._global_face_processor = FaceProcessor()
        logger.info("Background Face model warmup completed successfully.")
    except Exception as e:
        logger.error(f"Error warming up Face models: {e}")

def start_server(port=8090, db_path="data/photo_index.db", gui_dir="gui_tagpup"):
    TagPupHTTPRequestHandler.db_path = db_path
    TagPupHTTPRequestHandler.gui_dir = gui_dir

    # Instantiate the shared embedder and start background warmup in a background thread
    def init_embedder_in_background():
        try:
            from index import PhotoIndex
            from embedder import ClipEmbedder
            import configparser
            
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.ini")
            config = configparser.ConfigParser(interpolation=None)
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
                
            cache_dir = config.get("paths", "embedding_cache_dir", fallback="data/embedding_cache")
            model_name = config.get("model", "name", fallback="ViT-B-32")
            pretrained = config.get("model", "pretrained", fallback="laion2b_s34b_b79k")
            preserve_full_frame = config.getboolean("model", "preserve_full_frame", fallback=False)
            max_aspect_ratio = config.getfloat("model", "max_aspect_ratio", fallback=2.0)
            force_image_size = config.get("model", "force_image_size", fallback=None)
            force_image_size = int(force_image_size) if force_image_size else None
            
            photo_index = PhotoIndex(db_path=db_path)
            # Load index asynchronously in the background so the HTTP server can bind instantly
            threading.Thread(
                target=photo_index.load,
                name="LoadIndexThread",
                daemon=True
            ).start()
            
            shared_embedder = ClipEmbedder(
                model_name=model_name,
                pretrained=pretrained,
                cache_dir=cache_dir,
                preserve_full_frame=preserve_full_frame,
                max_aspect_ratio=max_aspect_ratio,
                force_image_size=force_image_size,
                photo_index=photo_index
            )
            TagPupHTTPRequestHandler.shared_embedder = shared_embedder
            # Load suggestions cache
            TagPupHTTPRequestHandler.load_suggestions_cache(db_path)
            
            warmup_thread = threading.Thread(
                target=warmup_embedder_thread,
                args=(shared_embedder,),
                name="WarmupEmbedderThread",
                daemon=True
            )
            warmup_thread.start()
        except Exception as e:
            logger.error(f"Failed to initialize shared embedder for warmup: {e}")

    threading.Thread(
        target=init_embedder_in_background,
        name="InitEmbedderThread",
        daemon=True
    ).start()

    server_address = ("", port)
    server = None
    import time
    for attempt in range(5):
        try:
            server = ThreadedHTTPServer(server_address, TagPupHTTPRequestHandler)
            break
        except OSError as e:
            if attempt == 4:
                raise e
            logger.info(f"Port {port} is busy, retrying in 0.5s (attempt {attempt + 1}/5)...")
            time.sleep(0.5)

    logger.info(f"TagPup server started on port {port} using DB {db_path}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info(f"Server shutting down... (PID: {os.getpid()})")
        server.shutdown()
        server.server_close()

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
Image.MAX_IMAGE_PIXELS = None
import numpy as np

logger = logging.getLogger("tagpup.server")

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
    folder_cache = {}          # folder_path -> { photo_path: metadata_dict }
    suggest_status = {}        # folder_path -> { status, completed, total, suggestions }
    suggest_threads = {}       # folder_path -> Thread

    def log_message(self, format, *args):
        pass # suppress request logs

    def do_GET(self):
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
        else:
            self.send_error(404, "File Not Found")

    def do_POST(self):
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
            ps_cmd = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Select Image Folder'; "
                "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }"
            )
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd]
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
            if os.path.exists(tax_path):
                taxonomy = TagTaxonomy(file_path=tax_path)
                taxonomy.load()
                for p in taxonomy.paths:
                    db_tags.add(p)
                    
            self.send_json(sorted(list(db_tags)))
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_get_people(self):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL ORDER BY name")
            people = [row[0] for row in cursor.fetchall()]
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
        
        # Check cache
        if folder_path in TagPupHTTPRequestHandler.folder_cache and not force_refresh:
            cached_data = list(TagPupHTTPRequestHandler.folder_cache[folder_path].values())
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
            
        try:
            from metadata import MetadataExtractor
            extractor = MetadataExtractor(exiftool_path=self.get_exiftool_path())
            batch_size = 500
            results = []
            for i in range(0, len(image_files), batch_size):
                batch = image_files[i:i+batch_size]
                batch_meta = extractor.batch_read(batch)
                results.extend(batch_meta)
                
            from metadata import build_photo_ui_record
            folder_map = {}
            for meta in results:
                path = meta["path"]
                folder_map[path] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
                
            TagPupHTTPRequestHandler.folder_cache[folder_path] = folder_map
            
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
            
        except Exception as e:
            logger.error(f"Error scanning folder {folder_path}: {e}", exc_info=True)
            self.send_json_error(500, str(e))

    def handle_get_folder_suggest_status(self, query):
        folder_path_list = query.get("path")
        if not folder_path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
        folder_path = os.path.abspath(urllib.parse.unquote(folder_path_list[0]))
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
        
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path)
        if status_info and status_info["status"] == "running":
            self.send_json({"success": True, "status": "running"})
            return
            
        TagPupHTTPRequestHandler.suggest_status[folder_path] = {
            "status": "running",
            "completed": 0,
            "total": 0,
            "suggestions": {}
        }
        
        t = threading.Thread(
            target=TagPupHTTPRequestHandler.run_folder_suggestions_thread,
            args=(folder_path, self.db_path),
            daemon=True
        )
        TagPupHTTPRequestHandler.suggest_threads[folder_path] = t
        t.start()
        
        self.send_json({"success": True, "status": "running"})

    @classmethod
    def run_folder_suggestions_thread(cls, folder_path, db_path):
        try:
            import configparser
            photos_dict = cls.folder_cache.get(folder_path, {})
            if not photos_dict:
                return
                
            photo_paths = list(photos_dict.keys())
            cls.suggest_status[folder_path]["total"] = len(photo_paths)
            
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
            
            photo_index = PhotoIndex(db_path=db_path)
            photo_index.load()
            
            tax_path = os.path.splitext(db_path)[0] + "_taxonomy.json"
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            
            preserve_full_frame = config.getboolean("model", "preserve_full_frame", fallback=False)
            max_aspect_ratio = config.getfloat("model", "max_aspect_ratio", fallback=2.0)
            force_image_size = config.get("model", "force_image_size", fallback=None)
            force_image_size = int(force_image_size) if force_image_size else None
            
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
            
            suggestions_list = []
            for path in photo_paths:
                if folder_path not in cls.suggest_status:
                    break
                try:
                    with cls.model_lock:
                        emb = embedder.embed_image(path)
                        meta = photos_dict[path]
                        sugg = suggester.suggest_for_photo(path, emb, k=15, min_sim=0.35, target_metadata=meta)
                    
                    suggestions_list.append(sugg)
                    
                    suggested_tags = []
                    suggested_people = []
                    for item in sugg.get("suggested_tags", []):
                        score = item.get("score", 0.0)
                        if score >= 0.6:
                            if item.get("is_face_match"):
                                suggested_people.append({"name": item["tag"], "score": score})
                            else:
                                suggested_tags.append({"tag": item["tag"], "score": score})
                                
                    all_sugg_tags = [t["tag"] for t in suggested_tags] + [p["name"] for p in suggested_people]
                    from writer import derive_caption_from_tags
                    suggested_title = derive_caption_from_tags(all_sugg_tags)
                    
                    cls.suggest_status[folder_path]["suggestions"][path] = {
                        "tags": suggested_tags,
                        "people": suggested_people,
                        "title": suggested_title,
                        "raw_suggestions": sugg
                    }
                except Exception as e:
                    logger.error(f"Error suggesting for {path}: {e}")
                    cls.suggest_status[folder_path]["suggestions"][path] = {
                        "tags": [],
                        "people": [],
                        "title": None,
                        "raw_suggestions": {"suggested_tags": []}
                    }
                cls.suggest_status[folder_path]["completed"] += 1
                
            # Apply folder consensus
            if len(suggestions_list) > 1 and folder_path in cls.suggest_status:
                try:
                    consensus_suggestions = suggester.apply_folder_consensus(suggestions_list)
                    for sugg in consensus_suggestions:
                        path = sugg["path"]
                        suggested_tags = []
                        suggested_people = []
                        for item in sugg.get("suggested_tags", []):
                            score = item.get("score", 0.0)
                            if score >= 0.6:
                                if item.get("is_face_match"):
                                    suggested_people.append({"name": item["tag"], "score": score})
                                else:
                                    suggested_tags.append({"tag": item["tag"], "score": score})
                                    
                        all_sugg_tags = [t["tag"] for t in suggested_tags] + [p["name"] for p in suggested_people]
                        from writer import derive_caption_from_tags
                        suggested_title = derive_caption_from_tags(all_sugg_tags)
                        
                        if path in cls.suggest_status[folder_path]["suggestions"]:
                            cls.suggest_status[folder_path]["suggestions"][path]["tags"] = suggested_tags
                            cls.suggest_status[folder_path]["suggestions"][path]["people"] = suggested_people
                            cls.suggest_status[folder_path]["suggestions"][path]["title"] = suggested_title
                            cls.suggest_status[folder_path]["suggestions"][path]["raw_suggestions"] = sugg
                except Exception as e:
                    logger.error(f"Error folder consensus: {e}")
                    
            cls.suggest_status[folder_path]["status"] = "completed"
        except Exception as e:
            logger.error(f"Error running suggestions thread: {e}")
            if folder_path in cls.suggest_status:
                cls.suggest_status[folder_path]["status"] = "error"

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
            subprocess.Popen(f'explorer.exe /select,"{norm_path}"')
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
            folder_path = os.path.dirname(photo_path)
            if folder_path in TagPupHTTPRequestHandler.folder_cache:
                stat = os.stat(photo_path)
                photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].get(photo_path)
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
                
            executable = self.get_exiftool_path()
            import exiftool
            with exiftool.ExifToolHelper(executable=executable) as et:
                et.set_tags([photo_path], tags=params, params=["-overwrite_original"])
                
            from metadata import sync_title_to_filename
            new_path = sync_title_to_filename(photo_path, title, executable)
            
            # Update cache
            folder_path = os.path.dirname(new_path)
            if folder_path in TagPupHTTPRequestHandler.folder_cache:
                if new_path != photo_path:
                    photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].pop(photo_path, None)
                    if photo_entry:
                        photo_entry["path"] = new_path
                        photo_entry["filename"] = os.path.basename(new_path)
                        TagPupHTTPRequestHandler.folder_cache[folder_path][new_path] = photo_entry
                else:
                    photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].get(photo_path)
                    
                if photo_entry:
                    from metadata import extract_tags
                    # Update raw_metadata tags
                    photo_entry["raw_metadata"]["XMP:Subject"] = new_flat_tags
                    photo_entry["raw_metadata"]["XMP:HierarchicalSubject"] = new_hierarchical_tags
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
                    folder_path = os.path.dirname(path)
                    photo_entry = None
                    if folder_path in TagPupHTTPRequestHandler.folder_cache:
                        photo_entry = TagPupHTTPRequestHandler.folder_cache[folder_path].get(path)
                        
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
            
        folder_path = os.path.abspath(folder_path)
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
            
        folder_path = os.path.abspath(folder_path)
        
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
            TagPupHTTPRequestHandler.folder_cache[folder_path] = {}
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
            folder_map[path] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
            
        TagPupHTTPRequestHandler.folder_cache[folder_path] = folder_map

    def handle_post_folder_rename_photos(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        photo_paths = data.get("photo_paths", [])
        grouping = data.get("grouping", "").strip()
        
        if folder_path:
            folder_path = os.path.normpath(folder_path).replace("\\", "/")
        if photo_paths:
            photo_paths = [os.path.normpath(p).replace("\\", "/") for p in photo_paths]
            
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
            cache = TagPupHTTPRequestHandler.folder_cache.get(folder_path, {})
            cache = {os.path.normpath(k).replace("\\", "/"): v for k, v in cache.items()}
            
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
            if folder_path in TagPupHTTPRequestHandler.folder_cache:
                del TagPupHTTPRequestHandler.folder_cache[folder_path]
                
            self.rescan_folder_to_cache(folder_path)
            
            # Send updated photos sorted chronologically
            updated_list = list(TagPupHTTPRequestHandler.folder_cache.get(folder_path, {}).values())
            
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

class ThreadedHTTPServer(ThreadingTCPServer):
    allow_reuse_address = True

def start_server(port=8090, db_path="data/photo_index.db", gui_dir="gui_tagpup"):
    TagPupHTTPRequestHandler.db_path = db_path
    TagPupHTTPRequestHandler.gui_dir = gui_dir

    server_address = ("", port)
    server = ThreadedHTTPServer(server_address, TagPupHTTPRequestHandler)
    logger.info(f"TagPup server started on port {port} using DB {db_path}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
        server.shutdown()
        server.server_close()

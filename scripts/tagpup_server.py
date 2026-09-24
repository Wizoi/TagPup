# tagpup_server.py
import os
import json
try:
    from . import db as tagpup_db
    from . import localserver
    from . import paths
except ImportError:  # imported as a top-level module
    import db as tagpup_db
    import localserver
    import paths
import urllib.parse
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler
from PIL import Image
Image.MAX_IMAGE_PIXELS = 500000000
import numpy as np

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.core import dates, fields, renaming, vocabulary
from tagpup.core.library import Library
from tagpup.core import library as libraries
from tagpup.files import keywords as file_keywords
from tagpup.store.photos import move_rows as move_photo_rows  # noqa: F401  (saving, tests)
from tagpup.store.photos import record_tags as record_tags_in_index  # noqa: F401  (writers, tests)
from tagpup.store import taxonomy as store_taxonomy
from tagpup.core.result import NotFound
from tagpup.jobs import indexing as indexing_jobs
from tagpup.services import indexing
from tagpup.services import people as people_service
from tagpup.services import tags as tags_service
from tagpup.services import photos as photo_actions
from tagpup.services import tagging as tagging_actions
from tagpup.store.photos import record_file_stat as record_file_stat_in_index  # noqa: F401  (writer.py)
from tagpup.files.keywords import (  # noqa: F401  (imported from here by other scripts)
    TAG_SOURCE_FIELDS, caption_fields, expand_tag_fields, keyword_fields,
    record_keyword_fields, tags_in_file)

logger = logging.getLogger("tagpup.server")

# A photo path has two spellings, and both come from tagpup/core/paths.py: paths.key() for
# the in-memory caches, paths.stored() for the index, the browser and the disk. This
# file used to have its own pair, and the one for the index turned the native paths the
# indexer writes into forward slashes, so every lookup made through it on Windows
# matched nothing: tag writes never reached the index, renames orphaned their rows and
# faces, deleted photos kept theirs, and the folder scan never found its cached
# metadata. The tests seeded their rows through that same function, so they agreed with
# it and not with the data.


#: Who a bare name means, per library, read and cached by the store; the rule that
#: applies it is tagpup.core.vocabulary.resolve_people. Kept here under the old names,
#: which the writers and their tests call.
people_paths_for = store_taxonomy.people_paths
invalidate_people_cache = store_taxonomy.forget_people_paths


def resolve_people_tags(tags, db_path):
    """Give every person in `tags` the path they are filed under in the library at
    `db_path` (tagpup.core.vocabulary.resolve_people)."""
    return vocabulary.resolve_people(tags, people_paths_for(db_path))


def write_keyword_fields(et, path, tags, extra_params=None, db_path=None):
    """Write `tags` into a photo's keyword fields (tagpup.files.keywords).

    Pass `db_path` and a person named by a bare leaf is written as the tag they are
    filed under instead. Every write path through this function should pass it.
    """
    if db_path:
        tags = resolve_people_tags(tags, db_path)
    return file_keywords.write_keywords(et, path, tags, extra_params=extra_params)

def indexed_tags_for_photo(db_path, photo_path):
    """Tags recorded for a photo in the index, used when the folder cache is cold.

    Bulk writes overwrite a photo's whole keyword set, so starting from an empty list
    because nothing was cached would erase tags the photo already carries.
    """
    try:
        conn = tagpup_db.connect(db_path, timeout=10.0)
        try:
            where, where_params = paths.sql_equals("path", photo_path)
            row = conn.execute("SELECT tags FROM photos WHERE " + where, where_params).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        logger.warning(f"Could not read indexed tags for {photo_path}: {e}")
    return []


def zero_shot_candidates(taxonomy, configured):
    """The words CLIP is asked about: config.ini's, plus every leaf that is not a person.

    People are matched by their faces, not by asking CLIP whether a photo looks like
    "a photo of Rowan Thackeray". The roots skipped were written out as family,
    friends and pets, so everyone under People -- and under any face root a library
    made for itself -- was offered to CLIP by name. The library's own face roots are
    used now.
    """
    face_roots = taxonomy.people_roots() | {"pets"}
    candidates = list(configured)
    seen = {c.lower() for c in candidates}
    for path in sorted(taxonomy.paths):
        parts = vocabulary.segments(path)
        if not parts or parts[0].lower() in face_roots:
            continue
        leaf = parts[-1]
        if leaf.lower() not in seen:
            seen.add(leaf.lower())
            candidates.append(leaf)
    return candidates


#: The files this app treats as photos.
PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"})


def explorer_select_command(photo_path):
    """The command line that opens Explorer with this photo selected.

    Explorer parses its own command line and wants the path quoted after the
    switch -- /select,"D:\\a b\\c.jpg" -- not the whole switch quoted, which is what
    passing ["explorer.exe", "/select,<path>"] produced for any path with a space.
    A Windows path cannot contain a quote, so quoting it is safe.
    """
    return 'explorer.exe /select,"%s"' % paths.stored(photo_path)


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


def taken_order(record):
    """The folder view's order for a cached photo record: when it was taken, then file time."""
    return dates.date_taken_sort_key(record.get("raw_metadata", {}), record.get("mtime", 0.0))


_thread_local = threading.local()

def set_active_db_path(db_path):
    if db_path is None:
        if hasattr(_thread_local, "active_db_path"):
            delattr(_thread_local, "active_db_path")
    else:
        _thread_local.active_db_path = Library(db_path).key


def create_library(db_path):
    """Make an empty library at db_path: its tables, and the tag tree's starting roots.

    Both servers' Create, both launchers and the desktop runner each did this, and none
    closed the index opened for the tables. The new file stayed open until a garbage-
    collection pass, and on Windows could not be moved or deleted until then.
    """
    from index import PhotoIndex
    from taxonomy import seed_taxonomy_from_db

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    photo_index = PhotoIndex(db_path=db_path)
    try:
        photo_index.load()
    finally:
        photo_index.close()
    seed_taxonomy_from_db(db_path)


def resolve_library_from_url(handler, set_active):
    """Point `handler` at the library its URL names. False means it has been answered.

    The first part of the path names the library -- /kr-track/api/tags -- and is
    stripped from handler.path. With none, the request goes to the library the server
    started on, and a bare page request is redirected to its URL. Both apps had their
    own copy of this, word for word.

    A named library must exist. Requests used to go ahead against data/<name>.db
    either way, and the first handler to open it created it, so a typo in the address
    bar made an empty library that then sat in the list. Create makes libraries.
    `set_active` is the app's own set_active_db_path: each has its own thread-local.
    """
    parsed_url = urllib.parse.urlparse(handler.path)
    path = parsed_url.path

    data_dir = tagpup_config.data_dir()

    # Determine if we are in test mode based on startup database
    startup_db = os.path.basename(handler.__class__.db_path)
    test_mode = startup_db.startswith("test_")

    def library_file(db_name):
        # The startup library is the file the server was started on. Looking it up by
        # name in data_dir found the same file only while it lived there; one started
        # anywhere else was served -- and created -- as an empty namesake in data/.
        if db_name == startup_db:
            return os.path.abspath(handler.__class__.db_path).replace("\\", "/")  # not a path: a database file
        return os.path.join(data_dir, db_name).replace("\\", "/")  # not a path: a database file

    db_match = re.match(r"^/([^/]+)(/.*)?$", path)
    if db_match:
        potential_db = db_match.group(1)
        subpath = db_match.group(2) or "/"

        RESERVED_PATHS = {"api", "gui", "gui_tagpup", "index.html", "style.css", "app.js", "favicon.ico", ""}
        if potential_db not in RESERVED_PATHS and not potential_db.endswith((".css", ".js", ".html", ".png", ".jpg", ".jpeg", ".ico")):
            db_name = potential_db + ".db"

            if test_mode:
                if not db_name.startswith("test_"):
                    db_name = "test_" + db_name
            else:
                if db_name.startswith("test_"):
                    db_name = db_name[5:]

            resolved_db_path = library_file(db_name)
            if not os.path.exists(resolved_db_path) and db_name != startup_db:
                handler.send_error(404, "There is no library called %s" % potential_db)
                return False
            set_active(resolved_db_path)
            handler.db_path = resolved_db_path

            # Rewrite path
            if parsed_url.query:
                handler.path = subpath + "?" + parsed_url.query
            else:
                handler.path = subpath
            return True

    # If path does not contain database subfolder, default to startup database
    db_name = startup_db

    if path in ["/", "/index.html", "/style.css", "/app.js"]:
        clean_url_name = os.path.splitext(db_name)[0]
        if clean_url_name.startswith("test_"):
            clean_url_name = clean_url_name[5:]
        new_path = f"/{clean_url_name}{handler.path}"
        handler.send_response(302)
        handler.send_header("Location", new_path)
        handler.end_headers()
        return False

    resolved_db_path = library_file(db_name)
    set_active(resolved_db_path)
    handler.db_path = resolved_db_path
    return True

def get_active_db_path():
    active_db = getattr(_thread_local, "active_db_path", None)
    if active_db:
        return active_db
    handler_cls = globals().get("TagPupHTTPRequestHandler")
    if handler_cls:
        try:
            return Library(handler_cls.db_path).key
        except Exception:
            pass
    return "data/photo_index.db"

class DatabaseIsolatedDict(dict):
    def __init__(self, registry):
        super().__init__()
        self._registry = registry

    def _get_current_dict(self):
        active_db = get_active_db_path()
        # setdefault, not check-then-set: two threads creating the same library's
        # entry at once each made one, and one thread's entries were lost.
        return self._registry.setdefault(active_db, {})

    def __getitem__(self, key):
        return self._get_current_dict()[key]

    def __setitem__(self, key, value):
        self._get_current_dict()[key] = value

    def __delitem__(self, key):
        del self._get_current_dict()[key]

    def __contains__(self, key):
        return key in self._get_current_dict()

    def __len__(self):
        return len(self._get_current_dict())

    def __iter__(self):
        return iter(self._get_current_dict())

    def get(self, key, default=None):
        return self._get_current_dict().get(key, default)

    def pop(self, key, default=None):
        return self._get_current_dict().pop(key, default)

    def update(self, other):
        self._get_current_dict().update(other)

    def values(self):
        return self._get_current_dict().values()

    def keys(self):
        return self._get_current_dict().keys()

    def items(self):
        return self._get_current_dict().items()

    def clear(self):
        self._get_current_dict().clear()

class TagPupHTTPRequestHandlerMeta(type):
    _shared_embedders = {}

    @property
    def shared_embedder(cls):
        db_key = get_active_db_path()
        return cls._shared_embedders.get(db_key)

    @shared_embedder.setter
    def shared_embedder(cls, val):
        db_key = get_active_db_path()
        cls._shared_embedders[db_key] = val

class TagPupHTTPRequestHandler(localserver.RequestLog, BaseHTTPRequestHandler,
                               metaclass=TagPupHTTPRequestHandlerMeta):
    db_path = "data/photo_index.db"
    gui_dir = "gui_tagpup"
    
    # Static Class-level caches
    model_lock = threading.Lock()
    
    # Database-specific registries
    _db_folder_cache_registry = {}
    _db_suggest_status_registry = {}
    _db_suggest_threads_registry = {}

    folder_cache = DatabaseIsolatedDict(_db_folder_cache_registry)
    suggest_status = DatabaseIsolatedDict(_db_suggest_status_registry)
    suggest_threads = DatabaseIsolatedDict(_db_suggest_threads_registry)

    def log_message(self, format, *args):
        pass # suppress request logs

    @classmethod
    def cached_photo_entries(cls, photo_path):
        """Every folder-cache map holding this photo, as (folder map, record) pairs.

        A scan stores its whole recursive walk under the scanned folder's key, so a
        photo in a subfolder is filed under an ancestor, not under its own directory.
        The writers looked it up under paths.key(dirname(photo)) and never found it:
        its record went on showing what it held before the write. A photo can be in
        more than one map when a folder and a subfolder of it were both scanned; each
        is kept true.
        """
        photo_key = paths.key(photo_path)
        found = []
        for folder_key, folder_map in list(cls.folder_cache.items()):
            if not paths.is_under(photo_path, folder_key):
                continue
            entry = folder_map.get(photo_key)
            if entry is not None:
                found.append((folder_map, entry))
        return found

    def validate_request_origin(self) -> bool:
        # Only this machine, and only pages this server served. See localserver.
        return localserver.is_local_request(self)

    def resolve_db_from_url(self) -> bool:
        # See resolve_library_from_url.
        return resolve_library_from_url(self, set_active_db_path)

    def do_GET(self):
        if not self.resolve_db_from_url():
            return
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
        elif path == "/api/databases":
            self.handle_get_databases()
        elif path == "/api/browse-folder":
            self.handle_get_browse_folder()
        elif path == "/api/autocomplete-folder":
            self.handle_get_autocomplete_folder(query)
        elif path == "/api/folder/scan":
            self.handle_get_folder_scan(query)
        elif path == "/api/folder/suggest-status":
            self.handle_get_folder_suggest_status(query)
        elif path == "/api/folder/index-status":
            self.handle_get_folder_index_status(query)
        elif path == "/api/photo-faces":
            self.handle_get_photo_faces(query)
        elif path == "/api/face-crop":
            self.handle_serve_face_crop(query)
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
        if not self.resolve_db_from_url():
            return
        if not self.validate_request_origin():
            return
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/databases/select":
            self.handle_post_databases_select()
        elif path == "/api/databases/create":
            self.handle_post_databases_create()
        elif path == "/api/folder/suggest-start":
            self.handle_post_folder_suggest_start()
        elif path == "/api/folder/index-start":
            self.handle_post_folder_index_start()
        elif path == "/api/photo/rotate":
            self.handle_post_photo_rotate()
        elif path == "/api/photo/delete":
            self.handle_post_photo_delete()
        elif path == "/api/photo/open-explorer":
            self.handle_post_photo_open_explorer()
        elif path == "/api/photo/open":
            self.handle_post_photo_open()
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
        return tagpup_config.exiftool_path()

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        return json.loads(post_data.decode("utf-8"))

    def handle_get_databases(self):
        settings = tagpup_config.load()
        data_dir = tagpup_config.data_dir(settings)
        default_db = tagpup_config.default_db(settings)

        startup_db = os.path.basename(self.__class__.db_path)
        test_mode = startup_db.startswith("test_")
        
        files = os.listdir(data_dir) if os.path.exists(data_dir) else []
        databases = libraries.picker_names(files, test_mode)
        clean_default_db = libraries.picker_name(default_db)
            
        self.send_json({
            "databases": sorted(databases),
            "selected": clean_default_db
        })

    def handle_post_databases_select(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON body")
            return
            
        db_name = data.get("db_name")
        if not db_name:
            self.send_json_error(400, "Invalid database name")
            return
            
        db_name = libraries.file_name_for(db_name)
            
        try:
            tagpup_config.remember_library(db_name)
            self.send_json({"success": True})
        except Exception as e:
            self.send_json_error(500, f"Error saving default database: {e}")

    def handle_post_databases_create(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON body")
            return
            
        db_name = data.get("db_name")
        if not db_name:
            self.send_json_error(400, "Invalid database name")
            return
            
        db_name = libraries.file_name_for(db_name)
            
        problem = libraries.problem_with_new_name(db_name)
        if problem:
            self.send_json_error(400, problem)
            return
            
        startup_db = os.path.basename(self.__class__.db_path)
        test_mode = startup_db.startswith("test_")
        
        fs_db_name = libraries.TEST_PREFIX + db_name if test_mode else db_name
            
        db_path = tagpup_config.library_path(fs_db_name).replace("\\", "/")  # not a path: a database file

        try:
            if not os.path.exists(db_path):
                create_library(db_path)

            tagpup_config.remember_library(db_name)
            self.send_json({"success": True, "db_name": os.path.splitext(db_name)[0]})
        except Exception as e:
            self.send_json_error(500, f"Error creating database: {e}")

    def handle_get_folder_index_status(self, query):
        path_list = query.get("path")
        if not path_list:
            self.send_json_error(400, "Missing path parameter")
            return
        self.send_json(self.index_queue().status(urllib.parse.unquote(path_list[0])))

    def handle_post_folder_index_start(self):
        """Queue a folder to be added to this library (tagpup.jobs.indexing). The page no
        longer asks: adding folders is TagTuner's (docs/findings.md, #34)."""
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        # Clustering re-derives every face name in the library, not only this folder's,
        # and can discard manual corrections, so it is opt-in.
        result = self.index_queue().start([data.get("folder_path")], self.folder_indexer(),
                                          cluster=bool(data.get("cluster", False)))
        if result.refused:
            self.send_json_error(400, result.message())
            return
        self.send_json({"success": True, "status": "running", **result.details})

    def index_queue(self):
        """The folders waiting to be added to this request's library."""
        return indexing_jobs.queue_for(Library(self.db_path))

    def folder_indexer(self):
        """How this server adds a folder to this request's library: through the CLI
        (tagpup.services.indexing.index_folder). Then the folder's cached scan is
        dropped, since rows were written even when clustering failed afterwards."""
        db_path = self.db_path

        def index(folder, cluster, report):
            try:
                return indexing.index_folder(Library(db_path), folder, tagpup_config.CODE_ROOT,
                                             cluster=cluster, report=report)
            finally:
                # The queue's own thread: no request has said which library it is in.
                set_active_db_path(db_path)
                TagPupHTTPRequestHandler.folder_cache.pop(paths.key(folder), None)

        return index

    def handle_get_browse_folder(self):
        try:
            self.send_json({"path": localserver.ask_for_folder()})
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

    def handle_get_photo_faces(self, query):
        """Faces detected on one photo, with the best identity guess for each.

        TagPup previously ran face recognition invisibly: the suggester matched faces
        and surfaced only a name pill, so there was no way to see which face was
        unrecognised while tagging. This backs the face strip in the details panel.

        The similarity reported is to the nearest single resolved face of that person,
        which is the same measure TagTuner's suggestion list uses -- deliberately, so
        the two interfaces agree on how confident a match looks.
        """
        path_list = query.get("path")
        if not path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
        photo_path = urllib.parse.unquote(path_list[0])

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            on_photo, on_photo_params = paths.sql_equals("photo_path", photo_path)
            cursor.execute(
                "SELECT id, box, name, prob, embedding, excluded, excluded_reason"
                " FROM faces WHERE " + on_photo + " ORDER BY id",
                on_photo_params,
            )
            rows = cursor.fetchall()
            if not rows:
                self.send_json({"faces": [], "total": 0})
                return

            # Resolved faces elsewhere in the library, for suggesting a name.
            cursor.execute(
                "SELECT name, embedding FROM faces"
                " WHERE name IS NOT NULL AND excluded = 0 AND NOT (" + on_photo + ")",
                on_photo_params,
            )
            known_names, known_vectors = [], []
            for name, emb in cursor.fetchall():
                if not emb:
                    continue
                vec = np.frombuffer(emb, dtype=np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    known_names.append(name)
                    known_vectors.append(vec / norm)
            known_matrix = np.array(known_vectors, dtype=np.float32) if known_vectors else None

            faces = []
            for face_id, box_json, name, prob, emb, excluded, reason in rows:
                try:
                    box = json.loads(box_json) if box_json else []
                except Exception:
                    box = []

                # Below this the nearest name is not a suggestion, it is just the
                # least-bad of a bad set. Offering "Jane Doe? 4%" for a stranger is
                # worse than saying nothing: it invites a wrong click.
                SUGGESTION_FLOOR = 0.5

                suggestion, similarity = None, None
                if known_matrix is not None and emb and not excluded:
                    vec = np.frombuffer(emb, dtype=np.float32)
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        sims = known_matrix @ (vec / norm)
                        best = int(np.argmax(sims))
                        best_sim = float(sims[best])
                        if best_sim >= SUGGESTION_FLOOR:
                            suggestion = known_names[best]
                            similarity = round(best_sim, 4)

                area = (box[2] - box[0]) * (box[3] - box[1]) if len(box) >= 4 else 0
                faces.append({
                    "id": face_id,
                    "box": box,
                    "area": area,
                    "name": name,
                    "prob": prob,
                    "excluded": bool(excluded),
                    "excluded_reason": reason,
                    "suggestion": suggestion if name is None else None,
                    "similarity": similarity if name is None else None,
                })

            # Named first, then by how confident the guess is, then largest first: the
            # faces needing attention are the ones the eye should land on.
            faces.sort(key=lambda f: (
                f["name"] is None,
                -(f["similarity"] or 0.0),
                -f["area"],
            ))
            self.send_json({
                "faces": faces,
                "total": len(faces),
                "unmatched": sum(1 for f in faces if f["name"] is None and not f["excluded"]),
            })
        except Exception as e:
            logger.error("Error listing faces for %s: %s" % (photo_path, e))
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_serve_face_crop(self, query):
        """A face's crop (tagpup.services.photos.face_crop)."""
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_json_error(400, "Missing 'id' parameter")
            return
        try:
            face_id = int(face_id_list[0])
        except (ValueError, TypeError):
            self.send_json_error(400, "Invalid 'id' parameter")
            return
        try:
            crop = photo_actions.face_crop(Library(self.db_path), face_id)
        except NotFound as missing:
            self.send_json_error(404, str(missing))
            return
        except Exception as e:
            logger.error("Error serving face crop %s: %s" % (face_id, e))
            self.send_json_error(500, str(e))
            return
        localserver.send_image(self, crop, "image/jpeg")

    def handle_get_tags(self):
        try:
            db_tags = set()
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
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
            tax_path = Library(self.db_path).taxonomy_file
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            for p in taxonomy.paths:
                db_tags.add(p)

            # Filter out hidden tags
            hidden_tags = set()
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")
                for row in cursor.fetchall():
                    hidden_tags.add(row[0])
            conn.close()

            def is_tag_hidden(tag):
                return vocabulary.hidden_by(tag, hidden_tags)

            final_tags = [t for t in db_tags if not is_tag_hidden(t)]
            self.send_json(sorted(final_tags))
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_get_people(self):
        """The people offered while a name is typed (tagpup.services.people.names)."""
        try:
            self.send_json(people_service.names(Library(self.db_path)))
        except Exception as e:
            self.send_json_error(500, str(e))

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
            
        folder_path = paths.stored(folder_path)
        folder_path_norm = paths.key(folder_path)

        # Check cache
        if folder_path_norm in TagPupHTTPRequestHandler.folder_cache and not force_refresh:
            cached_data = list(TagPupHTTPRequestHandler.folder_cache[folder_path_norm].values())
            cached_data.sort(key=taken_order)
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
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            under, under_params = paths.sql_under("path", folder_path)
            cursor.execute(
                "SELECT path, mtime, size, tags, people, captions, raw_metadata FROM photos WHERE " + under,
                under_params,
            )
            for row in cursor.fetchall():
                p, mt, sz, t_json, pe_json, c_json, raw_json = row
                db_records[paths.key(p)] = {
                    "path": paths.stored(p),
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
            file_norm = paths.key(file)
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
                        file_norm = paths.key(file)
                        folder_map[file_norm] = build_photo_ui_record(file, meta, mtime, size)
            except Exception as e:
                logger.error(f"Error running ExifTool during scan: {e}")
                
        TagPupHTTPRequestHandler.folder_cache[folder_path_norm] = folder_map
        
        response_list = list(folder_map.values())
        response_list.sort(key=taken_order)
        self.send_json(response_list)

    def handle_get_folder_suggest_status(self, query):
        folder_path_list = query.get("path")
        if not folder_path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
        folder_path = paths.key(urllib.parse.unquote(folder_path_list[0]))
        self.ensure_suggestions_loaded(self.db_path)
        # A copy taken under the lock the workers write under. Serialising the live
        # dict while four workers added to it failed the poll with "dictionary
        # changed size during iteration".
        with TagPupHTTPRequestHandler.model_lock:
            status_info = make_json_serializable(
                TagPupHTTPRequestHandler.suggest_status.get(folder_path, {"status": "idle"}))
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
            
        folder_path = paths.stored(folder_path)
        folder_path_norm = paths.key(folder_path)

        self.ensure_suggestions_loaded(self.db_path)
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path_norm)
        if status_info and status_info["status"] in ("preparing", "running"):
            self.send_json({"success": True, "status": status_info["status"]})
            return
            
        existing_suggestions = {}
        if status_info:
            existing_suggestions = status_info.get("suggestions", {})

        TagPupHTTPRequestHandler.suggest_status[folder_path_norm] = {
            "status": "preparing",
            # A photo whose suggestion failed is tried again, so it is not done yet.
            "completed": sum(1 for s in existing_suggestions.values()
                             if isinstance(s, dict) and "error" not in s),
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

    #: Serialises writes of the suggestions cache file. Its own lock, not model_lock,
    #: so a slow disk never holds up the pool threads recording suggestions.
    _suggestions_file_lock = threading.Lock()
    #: When each cache file was last written, for the throttled saves during a run.
    _suggestions_last_saved = {}

    @staticmethod
    def _suggestions_cache_path(db_path):
        """The one suggestions cache file of a library, and the only place it is named.

        One file per database, next to it. The main library keeps the unsuffixed name
        it has always had, so the file already on disk goes on loading.
        """
        db_basename = os.path.splitext(os.path.basename(db_path))[0]
        if db_basename == "photo_index":
            return os.path.join(os.path.dirname(db_path), "gui_suggestions_cache.json")
        return os.path.join(os.path.dirname(db_path), f"gui_suggestions_cache_{db_basename}.json")

    @staticmethod
    def _rekey_saved_suggestions(data):
        """Saved suggestions, keyed the way this code looks them up.

        Files written before paths.py hold folder keys in the old lower-case,
        forward-slash form, which paths.key() does not produce; loading them as they
        were would keep every saved run where nothing ever asks for it. A folder saved
        under two spellings becomes one entry, its suggestions merged. The photos
        inside are keyed by the path the page was sent, which is paths.stored().
        """
        rekeyed = {}
        for folder, status in data.items():
            if not isinstance(status, dict):
                continue
            suggestions = status.get("suggestions")
            if isinstance(suggestions, dict):
                status["suggestions"] = {paths.stored(p): s for p, s in suggestions.items()}
            folder_key = paths.key(folder)
            existing = rekeyed.get(folder_key)
            if existing is None:
                rekeyed[folder_key] = status
            else:
                merged = existing.setdefault("suggestions", {})
                for p, s in (status.get("suggestions") or {}).items():
                    merged.setdefault(p, s)
        return rekeyed

    #: Libraries whose saved suggestions have been read into memory, by registry key.
    _suggestions_loaded = set()
    _suggestions_load_lock = threading.Lock()

    @classmethod
    def ensure_suggestions_loaded(cls, db_path):
        """Read a library's saved suggestions the first time anything uses them.

        They used to be read once, at startup, for the startup library only. Any
        other library's saved runs were never offered again, and the first save for
        it -- which writes everything in memory -- replaced its file with just this
        session's folders. Every reader of the suggestions and every save calls this,
        so no save can happen before the load.
        """
        set_active_db_path(db_path)
        registry_key = get_active_db_path()
        if registry_key in cls._suggestions_loaded:
            return
        with cls._suggestions_load_lock:
            if registry_key not in cls._suggestions_loaded:
                cls.load_suggestions_cache(db_path)

    @classmethod
    def load_suggestions_cache(cls, db_path):
        set_active_db_path(db_path)
        # Marked first: a file that fails to load is not tried again on every save.
        cls._suggestions_loaded.add(get_active_db_path())
        cache_path = cls._suggestions_cache_path(db_path)
        if os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Clean up any active/running statuses to "idle"
                for folder, status in data.items():
                    if status.get("status") in ("running", "preparing"):
                        status["status"] = "idle"
                data = cls._rekey_saved_suggestions(data)
                # Only folders nothing has touched yet. This runs in the background
                # at startup, so a folder chosen straight away can already have a run
                # in progress; overwriting it with the saved copy -- rewritten to
                # "idle" above -- told the page the run had stopped, and it stopped
                # asking while the server went on and finished.
                with cls.model_lock:
                    for folder, status in data.items():
                        if folder not in cls.suggest_status:
                            cls.suggest_status[folder] = status
                logger.info(f"Loaded suggestions cache from {cache_path} with {len(data)} folders.")
            except Exception as e:
                logger.error(f"Error loading suggestions cache: {e}")

    _library_embedder_lock = threading.Lock()

    @classmethod
    def library_embedder(cls, db_path, embedder_kwargs):
        """This library's embedder and index, made once and brought up to date.

        The startup library's was made at startup and never reloaded, so photos
        indexed and tags saved since never reached Suggest. Every other library had
        none, and loaded its whole index again on every run. Each library now keeps
        one, and a run reloads its index only when the photos table has changed.
        The CLIP model itself is shared by every embedder (ClipEmbedder._shared_model).
        Call with the library active on this thread.
        """
        from embedder import ClipEmbedder
        from index import PhotoIndex

        with cls._library_embedder_lock:
            embedder = cls.shared_embedder
            if embedder is None:
                photo_index = PhotoIndex(db_path=db_path)
                photo_index.load()
                embedder = ClipEmbedder(photo_index=photo_index, **embedder_kwargs)
                cls.shared_embedder = embedder
                return embedder
        embedder.photo_index.reload_if_changed()
        return embedder

    @classmethod
    def move_saved_suggestions(cls, db_path, renames):
        """File each renamed photo's saved suggestions under its new name, and save.

        `renames` maps old path to new, in any spelling. Returns how many moved. The
        page looks suggestions up by path, so a renamed photo showed none, the next
        Suggest ran it again from scratch, and the old entry stayed for ever.
        """
        by_key = {paths.key(old): paths.stored(new) for old, new in renames.items()}
        moved = 0
        with cls.model_lock:
            for status in cls.suggest_status.values():
                saved = status.get("suggestions") if isinstance(status, dict) else None
                if not saved:
                    continue
                # Taken out first, then put back, so names shuffled among themselves
                # never overwrite one another.
                leaving = {path: saved.pop(path) for path in list(saved)
                           if paths.key(path) in by_key}
                for old_path, entry in leaving.items():
                    new_path = by_key[paths.key(old_path)]
                    raw = entry.get("raw_suggestions") if isinstance(entry, dict) else None
                    if isinstance(raw, dict) and "path" in raw:
                        raw["path"] = new_path
                    saved[new_path] = entry
                    moved += 1
        if moved:
            cls.save_suggestions_cache(db_path)
        return moved

    @classmethod
    def save_suggestions_cache(cls, db_path, min_interval=0.0):
        """Write this library's saved suggestions. True if the file was written.

        The file is replaced, never rewritten in place. It used to be truncated and
        rewritten by four pool threads at once, after every photo: two saves
        overlapping, or the process stopping mid-write, left a file that did not
        parse, and a file that does not parse restores nothing for any folder.

        The snapshot is taken inside the file lock, so a save that started later can
        never be overwritten by one that started earlier.

        `min_interval` is for saves during a run: skip the write if this file was
        written less than that many seconds ago. The run's final save passes nothing,
        so what it finished with is always what is on disk.
        """
        import json
        import tempfile
        import time
        # What is written is everything in memory, so the saved folders must be in
        # memory first or this would replace them with only this session's.
        cls.ensure_suggestions_loaded(db_path)
        cache_path = cls._suggestions_cache_path(db_path)

        def too_soon():
            last = cls._suggestions_last_saved.get(cache_path)
            return bool(min_interval) and last is not None and time.monotonic() - last < min_interval

        if too_soon():
            return False
        with cls._suggestions_file_lock:
            if too_soon():
                return False
            tmp_path = None
            try:
                with cls.model_lock:
                    serializable_data = make_json_serializable(cls.suggest_status)
                directory = os.path.dirname(cache_path) or "."
                os.makedirs(directory, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(
                    prefix=os.path.basename(cache_path) + ".", suffix=".tmp", dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(serializable_data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                for attempt in range(40):
                    try:
                        os.replace(tmp_path, cache_path)
                        break
                    except PermissionError:
                        # Windows refuses while something has the file open to read.
                        if attempt == 39:
                            raise
                        time.sleep(0.05)
                tmp_path = None
                cls._suggestions_last_saved[cache_path] = time.monotonic()
                return True
            except Exception as e:
                logger.error(f"Error saving suggestions cache {cache_path}: {e}")
                return False
            finally:
                if tmp_path is not None:
                    try:
                        os.remove(tmp_path)
                    except OSError as e:
                        logger.warning(f"Could not remove {tmp_path}: {e}")

    @classmethod
    def rescan_folder_to_cache_classmethod(cls, folder_path):
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(paths.stored(folder_path)):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
        if not image_files:
            cls.folder_cache[paths.key(folder_path)] = {}
            return
            
        from metadata import MetadataExtractor, build_photo_ui_record
        extractor = MetadataExtractor(exiftool_path=tagpup_config.exiftool_path())
        batch_size = 500
        results = []
        for i in range(0, len(image_files), batch_size):
            batch = image_files[i:i+batch_size]
            batch_meta = extractor.batch_read(batch)
            results.extend(batch_meta)
            
        folder_map = {}
        for meta in results:
            path = meta["path"]
            folder_map[paths.key(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
            
        cls.folder_cache[paths.key(folder_path)] = folder_map

    @classmethod
    def run_folder_suggestions_thread(cls, folder_path, db_path):
        # Restore the active database in this worker thread (see run_folder_index_thread).
        set_active_db_path(db_path)
        folder_path = paths.stored(folder_path)
        folder_path_norm = paths.key(folder_path)
        try:
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
                entry = cls.suggest_status.get(folder_path_norm) or {
                    "completed": 0, "total": 0, "suggestions": {}
                }
                entry["status"] = "error"
                entry["message"] = "No images found in this folder."
                cls.suggest_status[folder_path_norm] = entry
                return
                
            photo_paths = list(photos_dict.keys())
            if folder_path_norm not in cls.suggest_status:
                cls.suggest_status[folder_path_norm] = {
                    "status": "preparing", "completed": 0, "total": 0, "suggestions": {}
                }
            existing_suggs = cls.suggest_status[folder_path_norm].get("suggestions", {})

            def already_suggested(p):
                # A photo whose suggestion failed has an entry too, marked "error";
                # counting it as done meant it was never tried again.
                entry = existing_suggs.get(paths.stored(photos_dict[p]["path"]))
                return isinstance(entry, dict) and "error" not in entry

            unprocessed_paths = [p for p in photo_paths if not already_suggested(p)]
            
            cls.suggest_status[folder_path_norm]["total"] = len(photo_paths)
            cls.suggest_status[folder_path_norm]["completed"] = len(photo_paths) - len(unprocessed_paths)
            cls.suggest_status[folder_path_norm]["status"] = "preparing"
            
            cls.save_suggestions_cache(db_path)
            
            settings = tagpup_config.load()
            candidate_tags = tagpup_config.candidate_tags(settings)

            from taxonomy import TagTaxonomy
            from suggester import TagSuggester

            tax_path = Library(db_path).taxonomy_file
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            
            candidate_tags = zero_shot_candidates(taxonomy, candidate_tags)

            embedder = cls.library_embedder(db_path, tagpup_config.embedder_settings(settings))
            photo_index = embedder.photo_index

            suggester = TagSuggester(photo_index, taxonomy, embedder=embedder, candidate_tags=candidate_tags)
            # Precompute candidate text embeddings sequentially so they are cached before the parallel loop
            suggester._precompute_candidates()
            
            # Transition to running state as we begin processing the images
            with cls.model_lock:
                cls.suggest_status[folder_path_norm]["status"] = "running"
            cls.save_suggestions_cache(db_path)
            
            suggestions_list = []

            def offered(sugg):
                """What the panel shows for one suggestion: tags, people, title."""
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
                return suggested_tags, suggested_people, derive_caption_from_tags(all_sugg_tags)

            #: Saving after every photo rewrote every folder every photo; during the
            #: run the file is brought up to date at most this often.
            save_interval = 2.0

            def process_single_photo(path):
                # Pool threads are separate threads again, so re-bind the active database.
                set_active_db_path(db_path)
                if folder_path_norm not in cls.suggest_status:
                    return None
                try:
                    meta = photos_dict[path]
                    orig_path = paths.stored(meta["path"])
                    emb = embedder.embed_image(orig_path)
                    sugg = suggester.suggest_for_photo(orig_path, emb, k=15, min_sim=0.35, target_metadata=meta)
                    suggested_tags, suggested_people, suggested_title = offered(sugg)

                    with cls.model_lock:
                        cls.suggest_status[folder_path_norm]["suggestions"][orig_path] = {
                            "tags": suggested_tags,
                            "people": suggested_people,
                            "title": suggested_title,
                            # Kept as the suggester produced it, so consensus can be
                            # taken again over the whole folder when more photos
                            # arrive. Entries saved before this flag hold scores
                            # consensus already adjusted, and are left out of it.
                            "raw_suggestions": sugg,
                            "raw_before_consensus": True,
                        }
                        cls.suggest_status[folder_path_norm]["completed"] += 1
                    cls.save_suggestions_cache(db_path, min_interval=save_interval)
                    return sugg
                except Exception as e:
                    logger.error(f"Error suggesting for {path}: {e}")
                    meta = photos_dict.get(path, {})
                    orig_path = paths.stored(meta.get("path", path))
                    with cls.model_lock:
                        # Marked as a failure, not stored as an empty suggestion: the
                        # next run retries it instead of skipping it for good.
                        cls.suggest_status[folder_path_norm]["suggestions"][orig_path] = {
                            "tags": [],
                            "people": [],
                            "title": None,
                            "raw_suggestions": {"suggested_tags": []},
                            "error": str(e) or type(e).__name__,
                        }
                        cls.suggest_status[folder_path_norm]["completed"] += 1
                    cls.save_suggestions_cache(db_path, min_interval=save_interval)
                    return None

            max_workers = min(4, os.cpu_count() or 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(process_single_photo, p) for p in unprocessed_paths]
                for fut in concurrent.futures.as_completed(futures):
                    res = fut.result()
                    if res is not None:
                        suggestions_list.append(res)

            # Folder consensus, before the run says "completed": the page stops polling
            # on "completed" and keeps what it fetched then, which used to be the
            # scores consensus was about to change.
            #
            # Taken over every photo of the folder that has a suggestion of its own,
            # not only this run's, or resuming a folder with two photos left judged
            # what the folder agrees on from those two. It runs on copies of the
            # suggester's own output, so the stored suggestions are never adjusted
            # twice, nor mutated in place while a status request is reading them.
            if suggestions_list and folder_path_norm in cls.suggest_status:
                try:
                    import copy
                    in_folder = {paths.stored(meta["path"]) for meta in photos_dict.values()}
                    with cls.model_lock:
                        saved = cls.suggest_status[folder_path_norm]["suggestions"]
                        consensus_input = [
                            copy.deepcopy(entry["raw_suggestions"])
                            for photo, entry in saved.items()
                            if photo in in_folder
                            and isinstance(entry, dict)
                            and "error" not in entry
                            and entry.get("raw_before_consensus")
                            and isinstance(entry.get("raw_suggestions"), dict)
                            and entry["raw_suggestions"].get("path")
                        ]
                    if len(consensus_input) > 1:
                        adjusted = {}
                        for sugg in suggester.apply_folder_consensus(consensus_input):
                            adjusted[paths.stored(sugg["path"])] = offered(sugg)
                        with cls.model_lock:
                            for path, (tags, people, title) in adjusted.items():
                                entry = saved.get(path)
                                if entry is not None:
                                    saved[path] = {**entry, "tags": tags, "people": people, "title": title}
                except Exception as e:
                    logger.error(f"Error folder consensus: {e}")

            with cls.model_lock:
                cls.suggest_status[folder_path_norm]["status"] = "completed"
            cls.save_suggestions_cache(db_path)
        except Exception as e:
            logger.exception(f"Error running suggestions thread for {folder_path}: {e}")
            # Always surface the failure, even if the status entry is missing, so the UI
            # stops polling instead of spinning on "preparing" forever.
            entry = cls.suggest_status.get(folder_path_norm) or {
                "completed": 0, "total": 0, "suggestions": {}
            }
            entry["status"] = "error"
            entry["message"] = str(e)
            cls.suggest_status[folder_path_norm] = entry
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
            subprocess.Popen(explorer_select_command(photo_path))
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error opening explorer for {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photo_open(self):
        """Open a photo in the application Windows associates with its type."""
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return

        photo_path = paths.stored(data.get("path") or "")
        # startfile runs whatever it is handed; this route only ever opens a photo.
        if (not photo_path or not os.path.isfile(photo_path)
                or os.path.splitext(photo_path)[1].lower() not in PHOTO_EXTENSIONS):
            self.send_json_error(400, "Not a photo file")
            return
        try:
            os.startfile(photo_path)
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error opening {photo_path}: {e}")
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

        # Reject anything but an explicit direction rather than silently treating an
        # unrecognised value as a right turn.
        if direction not in ("left", "right"):
            self.send_json_error(400, "Direction must be 'left' or 'right'")
            return

        try:
            photo_path = paths.stored(photo_path)
            result = photo_actions.rotate(Library(self.db_path), photo_path, direction,
                                          self.get_exiftool_path())
            if not result.ok:
                logger.error("Error rotating image %s: %s", photo_path, result.message())
                self.send_json_error(500, result.message())
                return

            for _folder_map, photo_entry in self.cached_photo_entries(photo_path):
                photo_entry["mtime"] = result.details["mtime"]
                photo_entry["size"] = result.details["size"]

            # The new mtime, which versions the page's image URLs: thumbnails are
            # cached for a day, so without a new URL the grid kept the old turn.
            self.send_json({"success": True, "mtime": result.details["mtime"]})
        except Exception as e:
            logger.error(f"Error rotating image {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photo_delete(self):
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
            result = photo_actions.delete(Library(self.db_path), photo_path)
            if not result.ok:
                self.send_json_error(500, result.message())
                return

            # Remove from every folder-cache map that holds it.
            for folder_map, _entry in self.cached_photo_entries(photo_path):
                folder_map.pop(paths.key(photo_path), None)

            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error deleting image {photo_path}: {e}")
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
        photo_path = paths.stored(photo_path)

        try:
            result = tagging_actions.save_photo(
                Library(self.db_path), photo_path, title, tags, date_taken,
                self.get_exiftool_path(), tagpup_config.rename_format())
            if result.refused:
                self.send_json_error(400, result.refused)
                return
            new_path = result.details["new_path"]
            renamed = result.details["renamed"]
            tags = result.details["tags"]

            # Update in-memory cache: every folder map holding the photo, found under
            # the name it had (a rename stays in the same directory).
            for folder_map, photo_entry in self.cached_photo_entries(photo_path):
                if renamed:
                    folder_map.pop(paths.key(photo_path), None)
                    photo_entry["path"] = new_path
                    photo_entry["filename"] = os.path.basename(new_path)
                    folder_map[paths.key(new_path)] = photo_entry

                from metadata import extract_tags, photo_people
                raw_meta = photo_entry.setdefault("raw_metadata", {})
                # Every keyword field, not just the XMP pair: tags are re-derived
                # from this on the next line, and a stale IPTC:Keywords brought a
                # removed tag straight back.
                record_keyword_fields(raw_meta, result.details["flat"], result.details["hierarchical"])
                if date_taken:
                    fields.record_date_taken(raw_meta, date_taken)
                photo_entry["tags"] = extract_tags(raw_meta)
                photo_entry["captions"] = [title] if title else []
                photo_entry["title"] = title
                photo_entry["people"] = photo_people(raw_meta, tags, new_path, db_path=self.db_path)

            reply = {"success": True, "new_path": new_path}
            if result.details["index_warning"]:
                reply["index_warning"] = result.details["index_warning"]
            self.send_json(reply)
        except Exception as e:
            logger.error(f"Error saving metadata for {photo_path}: {e}")
            self.send_json_error(500, str(e))
    def handle_post_photos_bulk_tags(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_list = data.get("paths", [])
        add_tags = data.get("add_tags", [])
        remove_tags = data.get("remove_tags", [])
        
        if not photo_list:
            self.send_json_error(400, "Missing paths list")
            return
        # What is added, not what is removed: taking a bad tag off must stay possible.
        problem = vocabulary.problem_with_tags(add_tags)
        if problem:
            self.send_json_error(400, problem)
            return
        add_tags = [vocabulary.normalize(t) for t in add_tags]

        from metadata import photo_people

        try:
            result = tagging_actions.change_tags(
                Library(self.db_path), photo_list, add_tags, remove_tags, self.get_exiftool_path())
            # The photos written before any failure are written; the page's copy of
            # them has to say so either way.
            for path, (new_tags, flat, hierarchical) in result.details["written"].items():
                for _folder_map, photo_entry in self.cached_photo_entries(path):
                    photo_entry["tags"] = new_tags
                    raw_meta = record_keyword_fields(
                        photo_entry.setdefault("raw_metadata", {}), flat, hierarchical)
                    photo_entry["people"] = photo_people(raw_meta, new_tags, path, db_path=self.db_path)
            if not result.ok:
                raise RuntimeError(result.message())

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
        # No floor of its own. The list this writes has already been filtered to what
        # the page was shown; a second threshold here could only take away some of
        # what was offered, which is the behaviour that made Apply All ambiguous.
        threshold = data.get("threshold", 0.0)
        
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        folder_path = paths.key(folder_path)
        self.ensure_suggestions_loaded(self.db_path)
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path)
        if not status_info or "suggestions" not in status_info:
            self.send_json_error(400, "No suggestions found for this folder")
            return
            
        suggestions_map = status_info["suggestions"]
        photo_paths = data.get("photo_paths")
        if photo_paths:
            wanted = {paths.key(p) for p in photo_paths}
            suggestions_map = {k: v for k, v in suggestions_map.items() if paths.key(k) in wanted}
        from metadata import photo_people

        # Apply exactly what the panel offered.
        #
        # This used to read `raw_suggestions`, which is everything the suggester
        # produced down to its own floor, while the panel shows only what scored 0.6 or
        # better. The two lists were built in different places and drifted: a photo came
        # back from Apply All carrying two people the panel had never mentioned, and the
        # suggestions it *had* listed were still sitting there unapplied.
        #
        # One list, two consumers. `tags` and `people` are what the page was shown, so
        # they are what gets written.
        additions = {}
        for path, sugg_info in suggestions_map.items():
            offered = list(sugg_info.get("tags") or [])
            offered_people = list(sugg_info.get("people") or [])
            apply_tags = [t["tag"] for t in offered if t.get("score", 0.0) >= threshold]
            apply_tags += [p["name"] for p in offered_people if p.get("score", 0.0) >= threshold]
            additions[path] = apply_tags

        try:
            result = tagging_actions.add_tags(Library(self.db_path), additions, self.get_exiftool_path())
            for path, (new_tags, flat, hierarchical) in result.details["written"].items():
                for _folder_map, photo_entry in self.cached_photo_entries(path):
                    photo_entry["tags"] = new_tags
                    raw_meta = record_keyword_fields(
                        photo_entry.setdefault("raw_metadata", {}), flat, hierarchical)
                    photo_entry["people"] = photo_people(raw_meta, new_tags, path, db_path=self.db_path)
            if not result.ok:
                raise RuntimeError(result.message())

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
            
        # Walked and written in the stored form, looked up by the key. This used to
        # walk the lower-cased key itself, so every path it found -- and every path
        # it sent back to the page -- was lower case, and the cache it built was keyed
        # by those instead of by paths.key().
        folder_path = paths.stored(folder_path)
        folder_key = paths.key(folder_path)

        # Load from cache, or scan on the fly if missing
        if folder_key not in TagPupHTTPRequestHandler.folder_cache:
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
                    path = paths.stored(meta["path"])
                    folder_map[paths.key(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
                TagPupHTTPRequestHandler.folder_cache[folder_key] = folder_map
            except Exception as scan_err:
                logger.error(f"Error scanning folder on the fly for time shift: {scan_err}")
                self.send_json_error(500, f"Folder must be scanned first, and scan fallback failed: {scan_err}")
                return

        photos_map = TagPupHTTPRequestHandler.folder_cache[folder_key]

        # Filter photos by camera model
        target_paths = []
        for entry in photos_map.values():
            raw = entry.get("raw_metadata", {})
            model = raw.get("EXIF:Model") or raw.get("Model") or raw.get("EXIF:Make") or raw.get("Make") or "Unknown Camera"
            if camera_model == "All Cameras" or model == camera_model:
                target_paths.append(paths.stored(entry["path"]))
                
        if not target_paths:
            self.send_json({"success": True, "message": "No photos matched the camera model"})
            return
            
        try:
            result = photo_actions.shift_date_taken(
                Library(self.db_path), target_paths, shift_minutes, self.get_exiftool_path())
            if not result.ok:
                raise RuntimeError(result.message())

            from metadata import build_photo_ui_record
            for entry in result.details["records"]:
                p = paths.stored(entry["path"])
                # Every map holding the photo: this folder's, and an ancestor's scan
                # that walked into it.
                for folder_map, previous in self.cached_photo_entries(p):
                    folder_map[paths.key(p)] = build_photo_ui_record(
                        p, entry, entry.get("mtime", previous.get("mtime", 0.0)),
                        entry.get("size", previous.get("size", 0)))
                    
            updated_photos = list(photos_map.values())
            self.send_json({"success": True, "updated_photos": updated_photos,
                            "updated_count": result.changed,
                            "requested_count": result.attempted})
        except Exception as e:
            logger.error(f"Error applying time shift to {folder_path}: {e}")
            self.send_json_error(500, str(e))
    def rescan_folder_to_cache(self, folder_path):
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(paths.stored(folder_path)):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
        if not image_files:
            TagPupHTTPRequestHandler.folder_cache[paths.key(folder_path)] = {}
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
            folder_map[paths.key(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
            
        TagPupHTTPRequestHandler.folder_cache[paths.key(folder_path)] = folder_map

    def handle_post_folder_rename_photos(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        photo_paths = data.get("photo_paths", [])
        grouping = data.get("grouping", "").strip()
        
        folder_path = paths.stored(folder_path)
        if photo_paths:
            photo_paths = [paths.stored(p) for p in photo_paths]
            
        if not folder_path or not os.path.exists(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        if not photo_paths:
            self.send_json_error(400, "No photos selected for renaming")
            return
        problem = renaming.problem_with_grouping(grouping)
        if problem:
            self.send_json_error(400, problem)
            return

        try:
            format_pattern = tagpup_config.rename_format()

            # Sort the selected photo paths chronologically by Date Taken
            cache = TagPupHTTPRequestHandler.folder_cache.get(paths.key(folder_path), {})
            cache = {paths.key(k): v for k, v in cache.items()}

            def get_date_taken_sort_key(p_path):
                # The cache is keyed by paths.key; looking it up by the path itself
                # never matched, so every photo sorted by its file time instead.
                entry = cache.get(paths.key(p_path))
                if entry:
                    return taken_order(entry)
                try:
                    return f"mtime_{os.path.getmtime(p_path)}"
                except OSError:
                    return "9999"
                    
            sorted_paths = sorted(photo_paths, key=get_date_taken_sort_key)
            result = photo_actions.smart_rename(
                Library(self.db_path), sorted_paths, grouping, format_pattern,
                self.get_exiftool_path())
            if not result.ok:
                self.send_json_error(500, result.message())
                return

            # The files moved whatever the index did, so their suggestions follow.
            moves = {**result.details["moved_aside"], **result.details["renamed"]}
            if moves:
                try:
                    TagPupHTTPRequestHandler.move_saved_suggestions(self.db_path, moves)
                except Exception as e:
                    logger.error("Renamed %d photo(s) but could not move their saved "
                                 "suggestions: %s", len(result.details["renamed"]), e)

            # Clear old and scan new cache entries
            if paths.key(folder_path) in TagPupHTTPRequestHandler.folder_cache:
                del TagPupHTTPRequestHandler.folder_cache[paths.key(folder_path)]
                
            self.rescan_folder_to_cache(folder_path)
            
            # Send updated photos sorted chronologically
            updated_list = list(TagPupHTTPRequestHandler.folder_cache.get(paths.key(folder_path), {}).values())
            updated_list.sort(key=taken_order)
            
            self.send_json({
                "success": True,
                "updated_paths": result.details["updated_paths"],
                "updated_photos": updated_list,
                "index_rows_moved": result.details["index_rows_moved"],
                "index_skipped": [new for _, new in result.details["index_skipped"]],
            })
            
        except Exception as e:
            logger.error(f"Error smart renaming photos: {e}", exc_info=True)
            self.send_json_error(500, str(e))

    def handle_serve_photo_file(self, query):
        # Turned upright, and kept by the browser for a day: the page puts the photo's
        # mtime in the URL, so a rotated photo is asked for again.
        localserver.serve_photo_file(self, query.get("path"), query.get("size"),
                                     upright=True, cache_seconds=86400)

    def handle_get_taxonomy_tree(self):
        try:
            self.send_json(tags_service.tree(Library(self.db_path)))
        except Exception as e:
            self.send_json_error(500, str(e))

    def _tree_edit(self, edit):
        """Run an edit of the tag tree (tagpup.services.tags) on this request's library,
        and answer what went wrong: 404 for a node that is not there, 400 for a request
        refused, 500 for anything else. Returns its Result, or None once answered."""
        try:
            result = edit(Library(self.db_path))
        except NotFound as missing:
            self.send_json_error(404, str(missing))
            return None
        except Exception as e:
            self.send_json_error(500, str(e))
            return None
        if result.refused:
            self.send_json_error(400, result.refused)
            return None
        return result

    def handle_post_taxonomy_create(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        result = self._tree_edit(lambda library: tags_service.create(
            library, data.get("name", ""), data.get("parent_id"), data.get("has_face", 0)))
        if result:
            self.send_json({"success": True, "id": result.details["id"], "tag": result.details["tag"]})

    def handle_post_taxonomy_update(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        tag_id = data.get("id")
        if tag_id is None:
            self.send_json_error(400, "Missing 'id' parameter")
            return
        if self._tree_edit(lambda library: tags_service.set_flags(
                library, tag_id, data.get("has_face"), data.get("hidden_from_autocomplete"))):
            self.send_json({"success": True})

    def handle_post_taxonomy_delete_check(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        tag_id = data.get("tag_id")
        if tag_id is None:
            self.send_json_error(400, "Missing 'tag_id' parameter")
            return
        try:
            usage = tags_service.usage(Library(self.db_path), tag_id)
        except NotFound as missing:
            self.send_json_error(404, str(missing))
            return
        except Exception as e:
            self.send_json_error(500, str(e))
            return
        self.send_json(dict(usage, success=True))

    def handle_post_taxonomy_delete_confirm(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        tag_id, action = data.get("tag_id"), data.get("action")
        if tag_id is None or not action:
            self.send_json_error(400, "Missing parameters")
            return
        result = self._tree_edit(lambda library: tags_service.delete(
            library, tag_id, action, data.get("target_tag"), self.get_exiftool_path()))
        if result is None:
            return
        # The photos rewritten are no longer what their cached scans say.
        TagPupHTTPRequestHandler.folder_cache.clear()
        reply = {"success": result.ok, "photos_affected": result.details["photos_affected"],
                 "photos_rewritten": result.details["photos_rewritten"]}
        if not result.ok:
            reply["error"] = result.message()
        self.send_json(reply)

    def handle_post_taxonomy_rename(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        tag_id, new_name = data.get("tag_id"), str(data.get("new_name") or "").strip()
        if tag_id is None or not new_name:
            self.send_json_error(400, "Missing parameters")
            return
        result = self._tree_edit(lambda library: tags_service.rename(
            library, tag_id, new_name, self.get_exiftool_path()))
        if result is None:
            return
        TagPupHTTPRequestHandler.folder_cache.clear()
        reply = {"success": True, "photos_affected": result.details["photos_affected"],
                 "photos_rewritten": result.details["photos_rewritten"]}
        # The tree has the new name; a photo that could not be rewritten keeps the old.
        if not result.ok:
            reply["warning"] = result.message()
        self.send_json(reply)


#: Listens on IPv4 and IPv6 alike -- see scripts/localserver.py for why that is
#: worth two seconds on every click.
ThreadedHTTPServer = localserver.ThreadedHTTPServer

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
        # Constructing the processor loads nothing; the models load on first use. So
        # this reported the face models warm while the first Suggest still paid for
        # loading them.
        suggester._global_face_processor._init_models()
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

            photo_index = PhotoIndex(db_path=db_path)
            # Load index asynchronously in the background so the HTTP server can bind instantly
            threading.Thread(
                target=photo_index.load,
                name="LoadIndexThread",
                daemon=True
            ).start()
            
            shared_embedder = ClipEmbedder(photo_index=photo_index,
                                           **tagpup_config.embedder_settings())
            TagPupHTTPRequestHandler.shared_embedder = shared_embedder
            # The startup library's saved suggestions, ahead of the first request;
            # any other library's are read the first time it is used.
            TagPupHTTPRequestHandler.ensure_suggestions_loaded(db_path)
            
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

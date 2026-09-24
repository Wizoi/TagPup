# tuner_server.py
import collections
import os
import time
import threading
import subprocess
import json
import sqlite3
try:
    from . import db as tagpup_db
    from . import localserver
    from . import paths
except ImportError:  # imported as a top-level module
    import db as tagpup_db
    import localserver
    import paths
import urllib.parse
import io
import logging
from http.server import BaseHTTPRequestHandler
from PIL import Image
Image.MAX_IMAGE_PIXELS = 500000000
import numpy as np
from scipy import sparse
from sklearn.cluster import DBSCAN
from sklearn.neighbors import sort_graph_by_row_values

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.core import dates, vocabulary
from tagpup.core.library import Library
from tagpup.core import library as libraries
from tagpup.services import tagging as tagging_actions

logger = logging.getLogger("tagtuner.server")


def compute_geometric_median(X, eps=1e-5, max_iter=20):
    if len(X) == 0:
        return None
    if len(X) <= 2:
        return np.mean(X, axis=0)
    y = np.mean(X, axis=0)
    for _ in range(max_iter):
        distances = np.linalg.norm(X - y, axis=1)
        zero_mask = distances < 1e-10
        if np.any(zero_mask):
            distances = np.where(zero_mask, 1e-10, distances)
        weights = 1.0 / distances
        weights_sum = np.sum(weights)
        next_y = np.sum(X * weights[:, np.newaxis], axis=0) / weights_sum
        if np.linalg.norm(next_y - y) < eps:
            break
        y = next_y
    return y

_metadata_year_cache = {}
_path_year_cache = {}

#: Every nameless face still in play, for the Identify Faces queue.
#:
#: `LENGTH(f.embedding)` rather than `f.embedding`: the queue groups faces by the tags
#: their photo carries and counts them. It never compares a vector to anything -- it
#: only needs to know a face HAS one, because a face without an embedding cannot take
#: part in identifying and must not be counted as waiting. Selecting the column itself
#: read 380 MB of BLOB and built 189,000 numpy arrays to answer a question about
#: integers. Clustering is the per-person view's job, and it selects the embeddings
#: there, where they are actually used.
IDENTIFY_CANDIDATES_SQL = """
    SELECT f.id, f.photo_path, p.people, LENGTH(f.embedding)
    FROM faces f
    LEFT JOIN photos p ON p.path = f.photo_path
    WHERE f.name IS NULL AND f.excluded = 0
"""

#: How many unclustered faces a person's grid shows at once.
#:
#: They are capped because a person in many group photos can have tens of thousands of
#: unclustered candidates, and rendering a card for each one locks up the browser. The
#: cap keeps them reachable a screenful at a time; `has_more` tells the client there
#: are further faces behind it.
UNCLUSTERED_LIMIT = 500

#: How alike two faces must be to be neighbours, as a distance between unit vectors.
#: The clustering below and the DBSCAN call it replaced use the same number.
CLUSTER_EPS = 0.48

#: How much of one row-block of the similarity matrix to hold at once, in bytes. The
#: block is (rows x every face) float32, so this is what decides the block size -- and
#: with it, how often progress can be reported. 96 MB is about 240 rows against a
#: hundred thousand faces, which is a few hundred updates over the whole pass.
CLUSTER_BLOCK_BYTES = 96 * 1024 * 1024


def cluster_candidates(embeddings, on_progress=None):
    """Group faces that resemble each other, a block of rows at a time.

    This is DBSCAN, and it returns what `DBSCAN(eps=CLUSTER_EPS, min_samples=2,
    metric="euclidean")` returns -- verified against it on the real library: 79,662
    faces, 4,210 clusters both ways, the same partition face for face and the same
    63,175 left as noise.

    The reason for doing it here rather than in one sklearn call is that one call is
    one call: it takes the better part of a minute on a real pool and says nothing
    until it is finished, so the screen could only sit there. Building the neighbour
    graph a block at a time gives a number to report, and hands DBSCAN a graph it
    resolves in about half a second.

    It is not slower. Measured on that pool: 25.6s against sklearn's 29.3s, because
    the blocks are one BLAS matrix multiply each and a ball tree in 512 dimensions
    degenerates to the same comparisons with more bookkeeping.

    The vectors are unit length, so ||a - b||^2 = 2 - 2(a.b) and a distance threshold
    is a similarity threshold; comparing similarities lets the whole block be
    thresholded at once.

    `on_progress` is called with (rows_done, rows_total) as each block lands.
    """
    count = len(embeddings)
    if count < 2:
        return np.full(count, -1, dtype=int)

    similarity_floor = 1.0 - (CLUSTER_EPS * CLUSTER_EPS) / 2.0
    block_rows = int(CLUSTER_BLOCK_BYTES / (4 * max(count, 1)))
    block_rows = max(1, min(block_rows, 4096, count))

    neighbour_rows, neighbour_cols, distances = [], [], []
    for start in range(0, count, block_rows):
        stop = min(start + block_rows, count)
        block = embeddings[start:stop] @ embeddings.T
        rows, cols = np.nonzero(block >= similarity_floor)
        neighbour_rows.append(rows + start)
        neighbour_cols.append(cols)
        # Back to a distance for DBSCAN. Clamped because floating point can put a
        # face a hair over 1.0 similar to itself, and a negative square root is not
        # a distance.
        distances.append(
            np.sqrt(np.maximum(0.0, 2.0 - 2.0 * block[rows, cols])))
        if on_progress:
            on_progress(stop, count)

    rows = np.concatenate(neighbour_rows)
    cols = np.concatenate(neighbour_cols)
    # A face is its own neighbour at distance zero, and min_samples counts it. Sparse
    # storage drops an explicit zero, which would lose that, so the floor keeps it.
    values = np.maximum(np.concatenate(distances), 1e-9)

    graph = sparse.csr_matrix((values, (rows, cols)), shape=(count, count))
    sort_graph_by_row_values(graph, warn_when_not_sorted=False)
    return DBSCAN(eps=CLUSTER_EPS, min_samples=2, metric="precomputed").fit_predict(graph)


def get_year_from_mtime_or_meta(mtime, raw_meta_json, path=None):
    """The year to file a photo under, or "Unknown": tagpup.core.dates, cached.

    Cached by path and by metadata text, since the Identify views ask for thousands at
    once. This read ModifyDate as well -- when the file was last edited, not when the
    photo was taken; it now reads the same Date Taken as everything else.
    """
    path_key = paths.key(path)
    if path_key and path_key in _path_year_cache:
        return _path_year_cache[path_key]

    parsed_year = None
    if raw_meta_json:
        if raw_meta_json in _metadata_year_cache:
            parsed_year = _metadata_year_cache[raw_meta_json]
        else:
            try:
                if isinstance(raw_meta_json, str):
                    raw_meta = json.loads(raw_meta_json)
                else:
                    raw_meta = raw_meta_json
                parsed_year = dates.year_taken(raw_meta)
                _metadata_year_cache[raw_meta_json] = parsed_year
            except Exception:
                pass

    if not parsed_year and path:
        parsed_year = dates.year_in_name(paths.stored(path))

    year_str = parsed_year if parsed_year else "Unknown"
    if path_key:
        _path_year_cache[path_key] = year_str
    return year_str


_thread_local = threading.local()

def set_active_db_path(db_path):
    if db_path is None:
        if hasattr(_thread_local, "active_db_path"):
            delattr(_thread_local, "active_db_path")
    else:
        _thread_local.active_db_path = Library(db_path).key

def get_active_db_path():
    active_db = getattr(_thread_local, "active_db_path", None)
    if active_db:
        return active_db
    handler_cls = globals().get("TunerHTTPRequestHandler")
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


class TunerHTTPRequestHandlerMeta(type):
    _clustering_in_progress = set()

    @property
    def clustering_in_progress(cls):
        db_key = get_active_db_path()
        return db_key in cls._clustering_in_progress

    @clustering_in_progress.setter
    def clustering_in_progress(cls, val):
        db_key = get_active_db_path()
        if val:
            cls._clustering_in_progress.add(db_key)
        else:
            cls._clustering_in_progress.discard(db_key)


class TunerHTTPRequestHandler(localserver.RequestLog, BaseHTTPRequestHandler,
                              metaclass=TunerHTTPRequestHandlerMeta):
    db_path = "data/photo_index.db"
    gui_dir = "gui"
    
    # State cache and lock for folder tagging
    model_lock = threading.Lock()
    shared_embedder = None
    
    # Database-specific registries
    _db_folder_cache_registry = {}
    _db_suggest_threads_registry = {}
    _db_identify_cache_registry = {}
    _db_identify_progress_registry = {}
    _db_index_status_registry = {}
    _db_index_threads_registry = {}
    _db_index_queue_registry = {}

    folder_cache = DatabaseIsolatedDict(_db_folder_cache_registry)
    # Cache for the Identify Faces views. Clustering a person's unmatched candidates is
    # expensive (tens of thousands of 512-dimensional vectors for a large library), and
    # the answer only changes when faces are added or named, so it is cached against a
    # cheap fingerprint of the faces table rather than recomputed per request.
    identify_cache = DatabaseIsolatedDict(_db_identify_cache_registry)
    # How far along a grid that is being built has got, so the screen can say so
    # instead of sitting blank for a minute. Written by the request doing the work and
    # read by a status request on another thread, which is what the threaded server is
    # for. Keyed by person, because two people's grids can be built at once.
    identify_progress = DatabaseIsolatedDict(_db_identify_progress_registry)
    index_status = DatabaseIsolatedDict(_db_index_status_registry)
    index_threads = DatabaseIsolatedDict(_db_index_threads_registry)
    # Folders waiting their turn, under the key "pending", and the single worker
    # draining them under "runner". Indexing is GPU-bound, so folders are worked
    # through one at a time rather than in parallel -- but asking for ten of them
    # should not mean standing over the machine to start each one.
    index_queue = DatabaseIsolatedDict(_db_index_queue_registry)
    # Every read-modify-write of "pending" and "runner" happens under this. Start,
    # cancel and the runner each rewrote the list without one, so a job popped by the
    # runner could be written back by a start and indexed twice; and a runner that had
    # found the list empty still looked alive to a start in the moment before it
    # cleared itself, leaving that start's job queued with nothing to run it.
    _index_queue_lock = threading.RLock()
    suggest_threads = DatabaseIsolatedDict(_db_suggest_threads_registry)

    # The suggestions cache file belongs to TagPup, which runs the suggestions and is
    # the only process that reads or writes it (TagPupHTTPRequestHandler.
    # _suggestions_cache_path). TagTuner used to carry its own copy of the naming and
    # its own load and save; nothing called either, and a second writer of the same
    # file from a stale in-memory copy would have overwritten TagPup's.

    def resolve_db_from_url(self) -> bool:
        # See resolve_library_from_url.
        from tagpup_server import resolve_library_from_url
        return resolve_library_from_url(self, set_active_db_path)

    def log_message(self, format, *args):
        # Suppress request spam logging in console unless error
        pass

    def validate_request_origin(self) -> bool:
        # Only this machine, and only pages this server served. See localserver.
        return localserver.is_local_request(self)

    def handle_get_databases(self):
        # List all .db files in the data directory
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
            
        db_path = tagpup_config.library_path(fs_db_name).replace("\\", "/")  # not a path: a database file, spelled as the other db paths here are

        try:
            if not os.path.exists(db_path):
                from tagpup_server import create_library
                create_library(db_path)

            tagpup_config.remember_library(db_name)
            self.send_json({"success": True, "db_name": os.path.splitext(db_name)[0]})
        except Exception as e:
            self.send_json_error(500, f"Error creating database: {e}")

    def do_GET(self):
        if not self.resolve_db_from_url():
            return
        if not self.validate_request_origin():
            return
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # Static files serving
        if path == "/" or path == "/index.html":
            self.serve_static_file("index.html", "text/html")
        elif path == "/style.css":
            self.serve_static_file("style.css", "text/css")
        elif path == "/app.js":
            self.serve_static_file("app.js", "application/javascript")
            
        # API Endpoints
        elif path == "/api/databases":
            self.handle_get_databases()
            
        # API: get list of photos with unmatched faces
        elif path == "/api/photos":
            self.handle_get_photos(query)
            
        # API: get details of a specific photo
        elif path == "/api/photo-details":
            self.handle_get_photo_details(query)
            
        # API: serve original photo file
        elif path == "/api/photo-file":
            self.handle_serve_photo_file(query)
            
        # API: serve cropped face image on-the-fly
        elif path == "/api/face-crop":
            self.handle_serve_face_crop(query)

        # API: get list of all known people
        elif path == "/api/people":
            self.handle_get_people(query)

        # API: get list of all known people with face counts
        elif path == "/api/tags/list":
            self.handle_get_tags_list(query)
        elif path == "/api/tags/photos":
            self.handle_get_tag_photos(query)
        elif path == "/api/people-with-counts":
            self.handle_get_people_with_counts()

        # API: get faces for a specific name
        elif path == "/api/person-faces":
            self.handle_get_person_faces(query)

        # API: get top 5 similar matched faces for a selected face
        elif path == "/api/face-matches":
            self.handle_get_face_matches(query)

        # API: get list of high confident unmatched face matches for a selected face ID
        elif path == "/api/face-matches-unmatched":
            self.handle_get_face_matches_unmatched(query)
            
        elif path == "/api/folder/index-active":
            self.handle_get_folder_index_active()
        elif path == "/api/folder/subfolders":
            self.handle_get_folder_subfolders(query)
        elif path == "/api/folder/index-status":
            self.handle_get_folder_index_status(query)
        elif path == "/api/faces/excluded":
            self.handle_get_excluded_faces()
        elif path == "/api/unmatched-faces/people":
            self.handle_get_unmatched_faces_people()
        elif path == "/api/unmatched-faces/person-matches":
            self.handle_get_unmatched_faces_person_matches(query)
        elif path == "/api/unmatched-faces/build-status":
            self.handle_get_unmatched_faces_build_status(query)
        elif path == "/api/browse-folder":
            self.handle_get_browse_folder()
        else:
            self.send_error(404, "File Not Found")

    def do_POST(self):
        if not self.resolve_db_from_url():
            return
        if not self.validate_request_origin():
            return
        if TunerHTTPRequestHandler.clustering_in_progress:
            self.send_json_error(409, "Server is currently clustering faces. Please try again later.")
            return

        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/databases/select":
            self.handle_post_databases_select()
        elif path == "/api/databases/create":
            self.handle_post_databases_create()
        elif path == "/api/face/match":
            self.handle_post_match()
        elif path == "/api/face/unmatch":
            self.handle_post_unmatch()
        elif path == "/api/folder/index-start":
            self.handle_post_folder_index_start()
        elif path == "/api/folder/index-cancel":
            self.handle_post_folder_index_cancel()
        elif path == "/api/folder/remove":
            self.handle_post_folder_remove()
        elif path == "/api/faces/exclude":
            self.handle_post_faces_exclude()
        elif path == "/api/faces/restore":
            self.handle_post_faces_restore()
        elif path == "/api/faces/unmatch-bulk":
            self.handle_post_unmatch_bulk()
        elif path == "/api/faces/match-bulk":
            self.handle_post_match_bulk()
        elif path == "/api/tags/merge":
            self.handle_post_tags_merge()
        elif path == "/api/person/rename":
            self.handle_post_person_rename()
        elif path == "/api/photo/unmatch-all":
            self.handle_post_unmatch_all()
        elif path == "/api/photo/automatch":
            self.handle_post_automatch()
        elif path == "/api/folder/automatch":
            self.handle_post_folder_automatch()
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
            self.send_error(500, f"Internal server error: {e}")

    def handle_get_photos(self, query):
        mode = query.get("mode", ["folder-match"])[0]

        if not os.path.exists(self.db_path):
            self.send_json([])
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            if mode == "folder-match" or mode == "unmatched":
                cursor.execute("""
                    SELECT 
                        f.photo_path, 
                        SUM(CASE WHEN f.name IS NULL THEN 1 ELSE 0 END) as unmatched,
                        SUM(CASE WHEN f.name IS NOT NULL THEN 1 ELSE 0 END) as matched,
                        p.mtime, 
                        p.raw_metadata
                    FROM faces f
                    LEFT JOIN photos p ON p.path = f.photo_path
                    GROUP BY f.photo_path
                    HAVING unmatched > 0
                    ORDER BY p.mtime DESC
                """)
                rows = cursor.fetchall()
                photos = []
                for row in rows:
                    p_path = row[0]
                    unmatched_count = row[1]
                    matched_count = row[2]
                    mtime = row[3] if row[3] is not None else 0.0
                    raw_meta_json = row[4]
                    
                    year = get_year_from_mtime_or_meta(mtime, raw_meta_json, p_path)
                    
                    photos.append({
                        "path": p_path,
                        "filename": os.path.basename(p_path),
                        "unmatched_count": unmatched_count,
                        "matched_count": matched_count,
                        "mtime": mtime,
                        "year": year,
                        "folder": os.path.dirname(p_path)
                    })
                self.send_json(photos)
            else:
                self.send_json([])
        except Exception as e:
            logger.error(f"Error fetching photos: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_photo_details(self, query):
        photo_path_list = query.get("path")
        if not photo_path_list:
            self.send_error(400, "Missing 'path' parameter")
            return

        photo_path = urllib.parse.unquote(photo_path_list[0])

        if not os.path.exists(self.db_path):
            self.send_error(404, "Database not found")
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch metadata from photos table
            path_sql, path_args = paths.sql_equals("path", photo_path)
            cursor.execute("SELECT people, tags, captions, mtime, raw_metadata FROM photos WHERE " + path_sql, path_args)
            photo_row = cursor.fetchone()

            people = []
            tags = []
            caption = None
            mtime = 0.0
            raw_meta_json = None

            if photo_row:
                try:
                    people = json.loads(photo_row[0]) if photo_row[0] else []
                except Exception:
                    people = []
                try:
                    tags = json.loads(photo_row[1]) if photo_row[1] else []
                except Exception:
                    tags = []
                try:
                    captions = json.loads(photo_row[2]) if photo_row[2] else []
                    caption = captions[0] if captions else None
                except Exception:
                    caption = None
                
                mtime = photo_row[3] if photo_row[3] is not None else 0.0
                raw_meta_json = photo_row[4]

            year = get_year_from_mtime_or_meta(mtime, raw_meta_json, photo_path)

            # 2. Fetch face detections from faces table
            path_sql, path_args = paths.sql_equals("photo_path", photo_path)
            cursor.execute("SELECT id, box, name, embedding FROM faces WHERE " + path_sql, path_args)
            face_rows = cursor.fetchall()

            # Every named face, from the matrix shared with /api/face-matches. This
            # read all of them from SQLite on every click of a face card -- 35,826 rows,
            # half a second -- for the one number per face shown beside it.
            _known_ids, _known_names, known_matrix = self.named_face_matrix(
                conn, self.faces_fingerprint(conn))

            faces = []
            for f_row in face_rows:
                fid = f_row[0]
                box_str = f_row[1]
                fname = f_row[2]
                emb_bytes = f_row[3]
                
                try:
                    box = json.loads(box_str)
                except Exception:
                    # Clean brackets and split if stored directly
                    try:
                        box = [int(x) for x in box_str.replace('[', '').replace(']', '').split(',')]
                    except Exception:
                        box = [0, 0, 0, 0]
                
                max_sim = 0.0
                if emb_bytes is not None:
                    target_emb = np.frombuffer(emb_bytes, dtype=np.float32)
                    if known_matrix is not None:
                        sims = np.dot(known_matrix, target_emb)
                        if len(sims) > 0:
                            max_sim = float(np.max(sims))
                        
                faces.append({
                    "id": fid,
                    "box": box,
                    "name": fname,
                    "max_similarity": max_sim
                })

            # Compile details response
            details = {
                "path": photo_path,
                "filename": os.path.basename(photo_path),
                "people": people,
                "tags": tags,
                "caption": caption,
                "faces": faces,
                "year": year
            }
            self.send_json(details)

        except Exception as e:
            logger.error(f"Error fetching photo details for {photo_path}: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

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
            # Check if size parameter is present to resize dynamically and speed up loading
            size_param = query.get("size")
            content_type = "image/jpeg"
            
            if size_param:
                try:
                    max_size = int(size_param[0])
                    with Image.open(photo_path) as img:
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        
                        # Handle Pillow version compatibility for resampling filter
                        try:
                            resample = Image.Resampling.LANCZOS
                        except AttributeError:
                            try:
                                resample = Image.LANCZOS
                            except AttributeError:
                                resample = Image.ANTIALIAS
                                
                        img.thumbnail((max_size, max_size), resample)
                        buffer = io.BytesIO()
                        img.save(buffer, format="JPEG", quality=85)
                        content = buffer.getvalue()
                except Exception as resize_err:
                    logger.warning(f"Failed to resize image {photo_path}: {resize_err}. Falling back to original.")
                    with open(photo_path, "rb") as f:
                        content = f.read()
                    ext = os.path.splitext(photo_path)[1].lower()
                    if ext == ".png":
                        content_type = "image/png"
                    elif ext == ".webp":
                        content_type = "image/webp"
            else:
                # Guess content type based on extension
                ext = os.path.splitext(photo_path)[1].lower()
                if ext == ".png":
                    content_type = "image/png"
                elif ext == ".webp":
                    content_type = "image/webp"
                
                with open(photo_path, "rb") as f:
                    content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.error(f"Error serving file {photo_path}: {e}")
            self.send_error(500, f"Error serving file: {e}")

    def handle_serve_face_crop(self, query):
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_error(400, "Missing 'id' parameter")
            return

        try:
            face_id = int(face_id_list[0])
        except ValueError:
            self.send_error(400, "Invalid 'id' parameter")
            return

        if not os.path.exists(self.db_path):
            self.send_error(404, "Database not found")
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute("SELECT photo_path, box, crop_image FROM faces WHERE id = ?", (face_id,))
            row = cursor.fetchone()

            if not row:
                self.send_error(404, f"Face ID {face_id} not found in DB")
                return

            photo_path = row[0]
            box_str = row[1]
            crop_image = row[2]

            if crop_image is not None:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(crop_image)))
                self.end_headers()
                self.wfile.write(crop_image)
                return

            # Fallback if crop_image is None (older records)
            if not os.path.exists(photo_path):
                self.send_error(404, f"Original photo file not found: {photo_path}")
                return

            try:
                box = json.loads(box_str)
            except Exception:
                try:
                    box = [int(x) for x in box_str.replace('[', '').replace(']', '').split(',')]
                except Exception:
                    self.send_error(500, "Invalid bounding box format stored in DB")
                    return

            x1, y1, x2, y2 = box
            
            # Crop image on the fly using PIL
            with Image.open(photo_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                    
                width, height = img.size
                
                # Clamp coordinates to safety
                x1, y1 = max(0, int(x1)), max(0, int(y1))
                x2, y2 = min(width, int(x2)), min(height, int(y2))
                
                if (x2 - x1) <= 0 or (y2 - y1) <= 0:
                    # Return a fallback empty thumbnail if box coordinates are corrupt
                    crop_img = Image.new("RGB", (100, 100), color=(50, 50, 50))
                else:
                    crop_img = img.crop((x1, y1, x2, y2))
                
                # Downscale to max 256px if larger to match faces.py behavior
                if max(crop_img.size) > 256:
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        try:
                            resample = Image.LANCZOS
                        except AttributeError:
                            resample = Image.ANTIALIAS
                    crop_img.thumbnail((256, 256), resample)
                
                # Save crop to in-memory buffer
                buffer = io.BytesIO()
                crop_img.save(buffer, format="JPEG", quality=90)
                crop_bytes = buffer.getvalue()

            # Cache the crop image back into the DB
            try:
                cursor.execute("UPDATE faces SET crop_image = ? WHERE id = ?", (sqlite3.Binary(crop_bytes), face_id))
                conn.commit()
            except Exception as cache_err:
                logger.warning(f"Failed to cache face crop in database for face ID {face_id}: {cache_err}")

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(crop_bytes)))
            self.end_headers()
            self.wfile.write(crop_bytes)

        except Exception as e:
            logger.error(f"Error serving face crop for ID {face_id}: {e}")
            self.send_error(500, f"Error cropping face: {e}")
        finally:
            if conn:
                conn.close()


    # ---- Word tags -------------------------------------------------------------
    #
    # TagTuner curates faces because a wrong name spreads: a tagged photo is what the
    # suggester learns the next photo from. Word tags spread the same way and had no
    # view at all, so a misspelling could sit in the vocabulary for months, be
    # suggested, be applied, and become its own source. Finding one took SQL.
    #
    # These endpoints answer the two questions that were unanswerable: what is in the
    # vocabulary, and which photos does a given tag actually touch.

    def _tag_taxonomy_rows(self, cursor):
        """Every taxonomy entry, and which roots this library files people under."""
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
        if not cursor.fetchone():
            return [], {"people", "family", "friends", "pets"}

        roots = {"people", "family", "friends", "pets"}
        cursor.execute(
            "SELECT name FROM tag_taxonomy WHERE has_face = 1 AND tag NOT LIKE '%/%'")
        for row in cursor.fetchall():
            if row[0]:
                roots.add(row[0].strip().lower())

        cursor.execute("SELECT tag, has_face FROM tag_taxonomy")
        return cursor.fetchall(), roots

    def handle_get_tags_list(self, query):
        """Every tag this library knows, with what it touches.

        `people` decides which side of the face/word split to return: the point of
        this view is the tags TagTuner could not previously reach, so people are left
        out by default.
        """
        if not os.path.exists(self.db_path):
            self.send_json({"tags": [], "buckets": {}})
            return

        include_people = str(query.get("people", ["0"])[0]).lower() in ("1", "true", "yes")
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()

            taxonomy_rows, people_roots = self._tag_taxonomy_rows(cursor)
            in_taxonomy = {}
            for tag, has_face in taxonomy_rows:
                if tag:
                    in_taxonomy[tag] = bool(has_face)

            # A person the taxonomy files under a people root, by their leaf name.
            # A bare tag matching one is that person having lost their path, not a
            # word tag -- and saying so is the point, since that is the fault the
            # keyword convention forbids.
            person_leaves = {
                vocabulary.key(vocabulary.leaf_of(tag))
                for tag in in_taxonomy
                if "/" in tag and vocabulary.key(vocabulary.root_of(tag)) in people_roots
            }

            def is_person_tag(tag):
                if "/" in tag:
                    return vocabulary.key(vocabulary.root_of(tag)) in people_roots
                low = tag.strip().lower()
                return (low in people_roots
                        or low in person_leaves
                        or in_taxonomy.get(tag, False))

            def is_stray_person(tag):
                return "/" not in tag and tag.strip().lower() in person_leaves

            embedded = set()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_embeddings'")
            if cursor.fetchone():
                cursor.execute("SELECT DISTINCT tag FROM tag_embeddings")
                embedded = {row[0] for row in cursor.fetchall() if row[0]}

            counts = {}
            cursor.execute("SELECT tags FROM photos")
            for (tags_json,) in cursor.fetchall():
                try:
                    tags = json.loads(tags_json or "[]")
                except Exception:
                    continue
                for tag in tags:
                    if tag:
                        counts[tag] = counts.get(tag, 0) + 1

            every = set(counts) | set(in_taxonomy)
            out = []
            for tag in sorted(every):
                if is_person_tag(tag) and not include_people:
                    continue
                count = counts.get(tag, 0)
                out.append({
                    "tag": tag,
                    "leaf": vocabulary.leaf_of(tag),
                    "count": count,
                    "flat": "/" not in tag,
                    "in_taxonomy": tag in in_taxonomy,
                    "has_embedding": tag in embedded,
                    "is_person": is_person_tag(tag),
                    "person_without_path": is_stray_person(tag),
                })

            buckets = {
                # Tags with no path. Some are legitimately flat; some are people who
                # lost theirs, which is what the keyword convention forbids.
                "flat": sorted(t["tag"] for t in out if t["flat"] and t["count"]),
                # Where typos hide: a tag used once has never been confirmed by a
                # second photo agreeing with it.
                "used_once": sorted(t["tag"] for t in out if t["count"] == 1),
                # In the vocabulary, on no photo. These still feed zero-shot matching,
                # so they can be suggested without ever having described anything.
                "unused": sorted(t["tag"] for t in out if t["count"] == 0),
            }
            # Counted separately from the tags returned: a person who lost their path
            # is excluded from the word-tag list by `is_person_tag`, but it is exactly
            # what somebody opening this view wants told.
            strays = sorted(
                tag for tag in every
                if is_stray_person(tag) and counts.get(tag, 0) > 0
            )
            buckets["people_without_a_path"] = strays
            self.send_json({"tags": out, "buckets": buckets})
        except Exception as e:
            logger.error(f"Error listing tags: {e}")
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_get_tag_photos(self, query):
        """The photos carrying one tag, newest first."""
        tag = (query.get("tag", [""])[0] or "").strip()
        if not tag:
            self.send_json_error(400, "Missing tag")
            return
        if not os.path.exists(self.db_path):
            self.send_json({"tag": tag, "photos": [], "total": 0})
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT path, tags, mtime FROM photos")

            photos = []
            for path, tags_json, mtime in cursor.fetchall():
                try:
                    tags = json.loads(tags_json or "[]")
                except Exception:
                    continue
                if tag not in tags:
                    continue
                photos.append({
                    "path": path,
                    "filename": os.path.basename(path),
                    "mtime": mtime or 0,
                    "tags": tags,
                })

            photos.sort(key=lambda p: p["mtime"], reverse=True)
            self.send_json({"tag": tag, "photos": photos, "total": len(photos)})
        except Exception as e:
            logger.error(f"Error listing photos for tag {tag}: {e}")
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()


    def handle_post_tags_merge(self):
        """Rename a tag, or merge it into another, everywhere it lives.

        A tag is in five places and a rename has to reach all of them, or the old name
        comes back. That is not a guess: `Saskia Wrenn` was cleaned out of every photo
        file and still went on being suggested, because its cached CLIP embedding was
        never dropped and zero-shot matching reads from there.

            photo files      XMP:Subject, IPTC:Keywords, XMP:HierarchicalSubject
            photos.tags      the index's own copy, which goes stale silently
            tag_taxonomy     or the retired name stays offerable
            tag_embeddings   or zero-shot keeps matching it
            suggest_status   TagPup's in-memory cache, in another process

        The first four are handled here. The fifth cannot be reached from this process,
        so the reply says how many folders hold suggestions that predate the merge
        rather than pretending they were refreshed.

        Defaults to a dry run: `apply` must be sent explicitly, and without it nothing
        is written and the plan comes back. Every destructive script in this repo works
        that way, and it has caught real mistakes before they reached photos.
        """
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return

        source = (data.get("from") or "").strip()
        target = (data.get("into") or data.get("to") or "").strip()
        apply_it = bool(data.get("apply"))
        retire_only = bool(data.get("retire"))

        if not source:
            self.send_json_error(400, "Missing the tag to change")
            return
        if not target and not retire_only:
            self.send_json_error(400, "Missing the tag to merge into")
            return
        problem = target and vocabulary.problem_with_tag(target)
        if problem:
            self.send_json_error(400, problem)
            return
        # In its one spelling, as the tag tree holds it: "School / Kentridge" was
        # written into the files with its spaces.
        target = vocabulary.normalize(target)
        if target == source:
            self.send_json_error(400, "That tag is already called that")
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()

            affected = []
            already = 0
            cursor.execute("SELECT path, tags FROM photos")
            for photo_path, tags_json in cursor.fetchall():
                try:
                    tags = json.loads(tags_json or "[]")
                except Exception:
                    continue
                if source in tags:
                    affected.append(photo_path)
                    if target and target in tags:
                        already += 1

            embeddings = 0
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_embeddings'")
            has_embeddings = bool(cursor.fetchone())
            if has_embeddings:
                embeddings = cursor.execute(
                    "SELECT COUNT(*) FROM tag_embeddings WHERE tag = ?", (source,)).fetchone()[0]

            in_taxonomy = 0
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            has_taxonomy = bool(cursor.fetchone())
            if has_taxonomy:
                in_taxonomy = cursor.execute(
                    "SELECT COUNT(*) FROM tag_taxonomy WHERE tag = ?", (source,)).fetchone()[0]

            plan = {
                "from": source,
                "into": target or None,
                "retire_only": retire_only,
                "photos": len(affected),
                "photos_already_carrying_the_target": already,
                "embeddings_to_drop": embeddings,
                "taxonomy_rows_to_drop": in_taxonomy,
                "examples": [os.path.basename(p) for p in affected[:5]],
                "applied": False,
            }

            if not apply_it:
                conn.close()
                self.send_json(plan)
                return

            rewritten = 0
            if affected:
                rewritten = tagging_actions.replace_tag(
                    Library(self.db_path), affected, source, target or None,
                    self.get_exiftool_path()).changed
            plan["photos_rewritten"] = rewritten

            # A photo that could not be rewritten still carries the old tag, so it is
            # not retired: the tree and zero-shot matching still describe that photo.
            # This used to retire it regardless and report the photos it had planned.
            if rewritten < len(affected):
                conn.close()
                conn = None
                plan["error"] = ("%d of %d photo(s) could not be rewritten, so '%s' was kept; "
                                 "they still carry it." % (len(affected) - rewritten,
                                                           len(affected), source))
                logger.warning("Tag merge of %r: %s", source, plan["error"])
                self.send_json(plan)
                return

            def clean_up(write_conn):
                c = write_conn.cursor()
                if has_embeddings:
                    c.execute("DELETE FROM tag_embeddings WHERE tag = ?", (source,))
                if has_taxonomy:
                    c.execute("DELETE FROM tag_taxonomy WHERE tag = ?", (source,))
                return True

            conn.close()
            conn = None
            tagpup_db.write_with_connection(
                self.db_path, clean_up, label="retire tag %s" % source)

            plan["applied"] = True
            logger.info("Merged tag %r into %r across %d photo(s)",
                        source, target or "(nothing)", len(affected))
            self.send_json(plan)
        except Exception as e:
            logger.error(f"Error merging tag {source!r}: {e}")
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_get_people(self, query=None):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            # Get people from faces table
            cursor.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL")
            faces_names = {row[0] for row in cursor.fetchall()}
            
            # Get people from photos table
            cursor.execute("SELECT people FROM photos")
            photos_people = set()
            for row in cursor.fetchall():
                if row[0]:
                    try:
                        names = json.loads(row[0])
                        for name in names:
                            if name:
                                photos_people.add(name)
                    except Exception:
                        pass
                        
            all_people = faces_names.union(photos_people)

            # Filter out people hidden from autocomplete -- for the list offered while
            # typing. ?include_hidden=1 answers "does this person exist?", which a
            # hidden person does: asked of the filtered list, assigning one offered to
            # create them as someone new.
            include_hidden = (query or {}).get("include_hidden", ["0"])[0] == "1"
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if not include_hidden and cursor.fetchone():
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")
                hidden_tags = {row[0] for row in cursor.fetchall()}

                def is_tag_hidden(tag):
                    return vocabulary.hidden_by(tag, hidden_tags)

                filtered_people = []
                for p in all_people:
                    cursor.execute("SELECT tag FROM tag_taxonomy WHERE name = ? AND has_face = 1", (p,))
                    tag_paths = [r[0] for r in cursor.fetchall()]
                    if tag_paths:
                        hidden = all(is_tag_hidden(path) for path in tag_paths)
                    else:
                        hidden = False
                    if not hidden:
                        filtered_people.append(p)
                all_people = filtered_people

            self.send_json(sorted([p for p in all_people if p]))
        except Exception as e:
            logger.error(f"Error fetching people: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_face_matches(self, query):
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_error(400, "Missing 'id' parameter")
            return
        try:
            face_id = int(face_id_list[0])
        except ValueError:
            self.send_error(400, "Invalid 'id' parameter")
            return

        if not os.path.exists(self.db_path):
            self.send_json([])
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            # Fetch target embedding
            cursor.execute("SELECT embedding, name FROM faces WHERE id = ?", (face_id,))
            row = cursor.fetchone()
            if not row:
                self.send_error(404, "Face not found")
                return
                
            target_emb_bytes = row[0]
            target_emb = np.frombuffer(target_emb_bytes, dtype=np.float32)

            # Every named face, from the cache shared with the identify views, rather
            # than re-read from SQLite on every click. This endpoint fires each time a
            # card is selected, and rebuilding a 73 MB matrix to answer it cost half a
            # second of the click.
            known_ids, names, embeddings_matrix = self.named_face_matrix(
                conn, self.faces_fingerprint(conn))
            if embeddings_matrix is None:
                self.send_json([])
                return

            # Calculate similarities (dot product since they are L2 normalized)
            similarities = np.dot(embeddings_matrix, target_emb)

            # Sort indices descending
            sorted_indices = np.argsort(similarities)[::-1]

            # Extract top 5 unique names with similarity scores. The face itself is
            # skipped rather than excluded from the matrix, which is shared and cannot
            # be rebuilt per face; a face is not a suggestion for itself.
            top_matches = []
            seen = set()
            for idx in sorted_indices:
                if known_ids[idx] == face_id:
                    continue
                name = names[idx]
                if name in seen:
                    continue
                seen.add(name)
                top_matches.append({
                    "name": name,
                    "similarity": float(similarities[idx])
                })
                if len(top_matches) >= 5:
                    break

            self.send_json(top_matches)
            
        except Exception as e:
            logger.error(f"Error finding face matches: {e}")
            self.send_error(500, f"Error finding matches: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_face_matches_unmatched(self, query):
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_error(400, "Missing 'id' parameter")
            return
        try:
            face_id = int(face_id_list[0])
        except ValueError:
            self.send_error(400, "Invalid 'id' parameter")
            return

        if not os.path.exists(self.db_path):
            self.send_json({"matches": []})
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            # Fetch target embedding
            cursor.execute("SELECT embedding FROM faces WHERE id = ?", (face_id,))
            row = cursor.fetchone()
            if not row:
                self.send_error(404, "Face not found")
                return
                
            target_emb_bytes = row[0]
            target_emb = np.frombuffer(target_emb_bytes, dtype=np.float32)
            
            # Fetch all other unmatched faces
            cursor.execute("SELECT id, photo_path, box, embedding FROM faces WHERE name IS NULL AND excluded = 0 AND id != ?", (face_id,))
            faces_rows = cursor.fetchall()
            
            if not faces_rows:
                self.send_json({"matches": []})
                return
                
            face_ids = []
            photo_paths = []
            boxes = []
            embeddings_list = []
            for fid, photo_path, box_json, emb_bytes in faces_rows:
                face_ids.append(fid)
                photo_paths.append(photo_path)
                try:
                    box = json.loads(box_json) if box_json else []
                except Exception:
                    box = []
                boxes.append(box)
                embeddings_list.append(np.frombuffer(emb_bytes, dtype=np.float32))
                
            embeddings_matrix = np.array(embeddings_list, dtype=np.float32)
            
            # Calculate similarities
            similarities = np.dot(embeddings_matrix, target_emb)
            
            # Sort indices descending
            sorted_indices = np.argsort(similarities)[::-1]
            
            matches = []
            for idx in sorted_indices:
                sim = float(similarities[idx])
                if sim >= 0.8:
                    matches.append({
                        "id": face_ids[idx],
                        "photo_path": photo_paths[idx],
                        "filename": os.path.basename(photo_paths[idx]),
                        "box": boxes[idx],
                        "similarity": sim
                    })
                    
            self.send_json({"matches": matches})
            
        except Exception as e:
            logger.error(f"Error finding unmatched face matches: {e}")
            self.send_error(500, f"Error finding matches: {e}")
        finally:
            if conn:
                conn.close()

    def read_json_body(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        return json.loads(body.decode('utf-8'))

    def handle_post_match(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_id = data.get("face_id")
            person_name = data.get("person_name")
            
            if face_id is None or not person_name:
                self.send_error(400, "Missing face_id or person_name")
                return
                
            try:
                face_id = int(face_id)
                person_name = str(person_name).strip()
            except (ValueError, TypeError):
                self.send_error(400, "Invalid parameters")
                return
            problem = vocabulary.problem_with_name(person_name)
            if problem:
                self.send_json_error(400, problem)
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # The state the cached identify views were built against, read before this
            # write touches anything. Only an entry stamped with this can be carried
            # forward; anything older was built before something else changed the
            # table -- a folder removed, a batch of photos indexed -- and re-stamping
            # it would quietly revive a grid full of faces that no longer exist.
            fingerprint_before = self.begin_identify_write(conn)

            # 1. Fetch face details: photo_path and old name
            cursor.execute("SELECT photo_path, name, excluded FROM faces WHERE id = ?", (face_id,))
            face_row = cursor.fetchone()
            if not face_row:
                self.send_error(404, "Face ID not found")
                return

            photo_path, old_name, excluded = face_row

            # An excluded face has been ruled out of identity work; naming it leaves a
            # face that is both ruled out and claimed, which no view shows and no Undo
            # reaches. Restoring it first is the way to name it.
            if excluded:
                self.send_json_error(
                    409, "Cannot match: this face is excluded. Restore it first to name it.")
                return

            # If name is unchanged (case-insensitive), just return success
            if old_name and old_name.strip().lower() == person_name.lower():
                self.send_json({"success": True})
                return

            # Check for conflict: is person_name already tagged on another face in this photo?
            #
            # One lookup, by equality. There used to be a `photo_path LIKE ?` retry
            # whenever this found nothing -- which is the ordinary case, since finding
            # nothing is what "no conflict" looks like -- and it was wrong twice over.
            # LIKE cannot use idx_faces_photo_path, so it scanned every face row (0.38s
            # on this library, on every single assignment); and in LIKE an underscore
            # matches any character, so `IMG_1234.jpg` also matched `IMG-1234.jpg` and
            # refused a legitimate assignment because a different photo had that person.
            path_sql, path_args = paths.sql_equals("photo_path", photo_path)
            cursor.execute("SELECT id FROM faces WHERE " + path_sql + " AND name = ? AND id != ?", path_args + (person_name, face_id,))
            conflict_row = cursor.fetchone()

            if conflict_row:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": False,
                    "error": f"Cannot match: '{person_name}' is already tagged on another face in this photo."
                }).encode("utf-8"))
                return

            # 2. Update faces table
            # A person chose this, so record it as a manual decision: re-clustering
            # re-derives every name from scratch and must not discard it.
            cursor.execute("UPDATE faces SET name = ?, name_source = 'manual' WHERE id = ?", (person_name, face_id))

            # 3. Update photos table people list
            path_sql, path_args = paths.sql_equals("path", photo_path)
            cursor.execute("SELECT path, people FROM photos WHERE " + path_sql, path_args)
            photo_row = cursor.fetchone()
                
            actual_photo_path = photo_path
            people = []
            if photo_row:
                actual_photo_path = photo_row[0]
                if photo_row[1]:
                    try:
                        people = json.loads(photo_row[1])
                    except Exception:
                        people = []

            # Append the new person name if missing
            if person_name not in people:
                people.append(person_name)

            # Check if old name is no longer matched to any other faces in the photo
            if old_name and old_name != person_name:
                path_sql, path_args = paths.sql_equals("photo_path", photo_path)
                cursor.execute("SELECT count(*) FROM faces WHERE " + path_sql + " AND name = ? AND id != ?", path_args + (old_name, face_id,))
                count_row = cursor.fetchone()
                
                other_count = count_row[0] if count_row else 0
                if other_count == 0:
                    # Remove old name from people list
                    people = [p for p in people if p != old_name]

            # Save the updated people list
            people_json = json.dumps(people)
            path_sql, path_args = paths.sql_equals("path", actual_photo_path)
            cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (people_json,) + path_args)

            with tagpup_db.writing(self.db_path, label="name a face"):
                fingerprint_after = self.faces_fingerprint(conn)
                conn.commit()
                # This face has left the identify pool; take it out of the cached
                # views rather than making the next click rebuild them.
                self.identify_cache_forget_faces(conn, [face_id], fingerprint_before, fingerprint_after)
            self.send_json({"success": True})

        except Exception as e:
            logger.error(f"Error in handle_post_match: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            self.end_identify_write()
            if conn:
                conn.close()

    def handle_post_unmatch(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_id = data.get("face_id")
            
            if face_id is None:
                self.send_error(400, "Missing face_id")
                return
                
            try:
                face_id = int(face_id)
            except (ValueError, TypeError):
                self.send_error(400, "Invalid face_id")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch face details: photo_path and old name
            cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
            face_row = cursor.fetchone()
            if not face_row:
                self.send_error(404, "Face ID not found")
                return
                
            photo_path, old_name = face_row
            
            if old_name is None:
                # Already unmatched
                self.send_json({"success": True})
                return

            # 2. Update faces table
            # A person chose this, so record it as a manual decision: re-clustering
            # re-derives every name from scratch and must not discard it.
            cursor.execute("UPDATE faces SET name = NULL, name_source = 'manual' WHERE id = ?", (face_id,))

            # 3. Check if old name is no longer matched to any other faces in the photo
            path_sql, path_args = paths.sql_equals("photo_path", photo_path)
            cursor.execute("SELECT count(*) FROM faces WHERE " + path_sql + " AND name = ? AND id != ?", path_args + (old_name, face_id,))
            count_row = cursor.fetchone()
                
            other_count = count_row[0] if count_row else 0
            if other_count == 0:
                # Remove old name from people list in photos table
                path_sql, path_args = paths.sql_equals("path", photo_path)
                cursor.execute("SELECT path, people FROM photos WHERE " + path_sql, path_args)
                photo_row = cursor.fetchone()
                    
                actual_photo_path = photo_path
                people = []
                if photo_row:
                    actual_photo_path = photo_row[0]
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []
                        
                people = [p for p in people if p != old_name]
                people_json = json.dumps(people)
                
                path_sql, path_args = paths.sql_equals("path", actual_photo_path)
                cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (people_json,) + path_args)

            conn.commit()
            self.send_json({"success": True})
            
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_unmatch_all(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            photo_path = data.get("photo_path")
            if not photo_path:
                self.send_error(400, "Missing photo_path")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.conn.cursor() if hasattr(conn, 'conn') else conn.cursor()

            # 1. Fetch currently matched names for faces in this photo
            path_sql, path_args = paths.sql_equals("photo_path", photo_path)
            cursor.execute("SELECT DISTINCT name FROM faces WHERE " + path_sql + " AND name IS NOT NULL", path_args)
            matched_names = {row[0] for row in cursor.fetchall()}

            # 2. Update faces table: set name = NULL
            # A person chose this, so record it as a manual decision: re-clustering
            # re-derives every name from scratch and must not discard it.
            #
            # One equality, not an equality and then a LIKE as well: in LIKE an
            # underscore matches any character, so the LIKE pass also cleared every
            # name in a photo called IMG-1234.jpg when this one was IMG_1234.jpg.
            path_sql, path_args = paths.sql_equals("photo_path", photo_path)
            cursor.execute(
                "UPDATE faces SET name = NULL, name_source = 'manual' WHERE " + path_sql, path_args)

            # 3. Update photos table: remove the matched names from people metadata
            if matched_names:
                path_sql, path_args = paths.sql_equals("path", photo_path)
                cursor.execute("SELECT path, people FROM photos WHERE " + path_sql, path_args)
                photo_row = cursor.fetchone()
                
                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    # Filter out names that were matched
                    people = [p for p in people if p not in matched_names]
                    path_sql, path_args = paths.sql_equals("path", actual_photo_path)
                    cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (json.dumps(people),) + path_args)

            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch_all: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_automatch(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            photo_path = data.get("photo_path")
            if not photo_path:
                self.send_error(400, "Missing photo_path")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch unmatched faces in this photo
            path_sql, path_args = paths.sql_equals("photo_path", photo_path)
            cursor.execute("SELECT id, embedding FROM faces WHERE " + path_sql + " AND name IS NULL AND excluded = 0", path_args)
            unmatched_rows = cursor.fetchall()

            if not unmatched_rows:
                self.send_json({"success": True, "matched_count": 0})
                return

            # 2. Every named face, from the matrix shared with the rest of Identify
            # Faces rather than read from SQLite again on each automatch.
            _ids, resolved_names, resolved_matrix = self.named_face_matrix(
                conn, self.faces_fingerprint(conn))

            if resolved_matrix is None:
                self.send_json({"success": True, "matched_count": 0})
                return

            # Fetch names already resolved in this photo to avoid duplicate assignments
            path_sql, path_args = paths.sql_equals("photo_path", photo_path)
            cursor.execute("SELECT name FROM faces WHERE " + path_sql + " AND name IS NOT NULL", path_args)
            already_tagged_rows = cursor.fetchall()
            already_tagged_names = {row[0] for row in already_tagged_rows if row[0]}

            # 3. For each unmatched face, find the best candidate match
            proposed_matches = {}  # face_id -> matched_name
            for face_id, target_emb_bytes in unmatched_rows:
                target_emb = np.frombuffer(target_emb_bytes, dtype=np.float32)
                
                # Calculate similarities
                similarities = np.dot(resolved_matrix, target_emb)
                best_idx = np.argmax(similarities)
                best_sim = similarities[best_idx]

                # High confidence threshold for auto-matching (cosine similarity >= 0.8)
                if best_sim >= 0.8:
                    proposed_matches[face_id] = resolved_names[best_idx]

            # Detect conflicts (proposed same name for multiple faces, or name already tagged)
            proposed_counts = {}
            for name in proposed_matches.values():
                proposed_counts[name] = proposed_counts.get(name, 0) + 1

            conflicting_names = set()
            for name, count in proposed_counts.items():
                if count > 1 or name in already_tagged_names:
                    conflicting_names.add(name)

            # Apply only non-conflicting matches
            matched_count = 0
            newly_matched_names = set()
            for face_id, matched_name in proposed_matches.items():
                if matched_name not in conflicting_names:
                    # Automatch is a bulk guess, not a per-face human decision, so it is
                    # left as an automatic assignment that re-clustering may revise.
                    cursor.execute(
                        "UPDATE faces SET name = ? WHERE id = ? AND excluded = 0",
                        (matched_name, face_id))
                    if cursor.rowcount:
                        newly_matched_names.add(matched_name)
                        matched_count += cursor.rowcount

            # 4. Append newly matched names to photos table people list
            if newly_matched_names:
                path_sql, path_args = paths.sql_equals("path", photo_path)
                cursor.execute("SELECT path, people FROM photos WHERE " + path_sql, path_args)
                photo_row = cursor.fetchone()

                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    updated = False
                    for name in newly_matched_names:
                        if name and name not in people:
                            people.append(name)
                            updated = True

                    if updated:
                        path_sql, path_args = paths.sql_equals("path", actual_photo_path)
                        cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (json.dumps(people),) + path_args)

            conn.commit()
            self.send_json({"success": True, "matched_count": matched_count})
        except Exception as e:
            logger.error(f"Error in handle_post_automatch: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_folder_automatch(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            folder_path = data.get("folder_path")
            if not folder_path:
                self.send_error(400, "Missing folder_path")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch unmatched faces in this folder -- and, as ever, in the folders
            # under it. A prefix comparison rather than LIKE, whose "_" and "%" are
            # wildcards that also occur in folder names.
            under_sql, under_args = paths.sql_under("photo_path", folder_path)
            cursor.execute("""
                SELECT id, embedding, photo_path
                FROM faces
                WHERE """ + under_sql + """ AND name IS NULL AND excluded = 0
            """, under_args)
            unmatched_rows = cursor.fetchall()

            if not unmatched_rows:
                self.send_json({"success": True, "matched_count": 0})
                return

            # 2. Every named face, from the matrix shared with the rest of Identify
            # Faces rather than read from SQLite again on each automatch.
            _ids, resolved_names, resolved_matrix = self.named_face_matrix(
                conn, self.faces_fingerprint(conn))

            if resolved_matrix is None:
                self.send_json({"success": True, "matched_count": 0})
                return

            # Fetch all resolved names for photos in this folder to check for already-tagged conflicts
            cursor.execute("""
                SELECT name, photo_path
                FROM faces
                WHERE """ + under_sql + """ AND name IS NOT NULL
            """, under_args)
            already_tagged_rows = cursor.fetchall()

            already_tagged_by_photo = {}
            for name, p_path in already_tagged_rows:
                already_tagged_by_photo.setdefault(paths.key(p_path), set()).add(name)

            # 3. For each unmatched face, find the best candidate match, grouped by photo
            proposed_by_photo = {}  # photo_path -> list of (face_id, proposed_name)
            for face_id, target_emb_bytes, photo_path in unmatched_rows:
                target_emb = np.frombuffer(target_emb_bytes, dtype=np.float32)
                
                # Calculate similarities
                similarities = np.dot(resolved_matrix, target_emb)
                best_idx = np.argmax(similarities)
                best_sim = similarities[best_idx]

                # High confidence threshold for auto-matching (cosine similarity >= 0.8)
                if best_sim >= 0.8:
                    matched_name = resolved_names[best_idx]
                    if photo_path not in proposed_by_photo:
                        proposed_by_photo[photo_path] = []
                    proposed_by_photo[photo_path].append((face_id, matched_name))

            # Detect conflicts per photo and apply updates
            matched_count = 0
            photos_to_update = {}

            for photo_path, proposed_list in proposed_by_photo.items():
                already_tagged = already_tagged_by_photo.get(paths.key(photo_path), set())
                
                proposed_counts = {}
                for _, name in proposed_list:
                    proposed_counts[name] = proposed_counts.get(name, 0) + 1
                    
                conflicting_names = set()
                for name, count in proposed_counts.items():
                    if count > 1 or name in already_tagged:
                        conflicting_names.add(name)
                        
                for face_id, name in proposed_list:
                    if name not in conflicting_names:
                        cursor.execute(
                            "UPDATE faces SET name = ? WHERE id = ? AND excluded = 0",
                            (name, face_id))
                        if not cursor.rowcount:
                            continue
                        if photo_path not in photos_to_update:
                            photos_to_update[photo_path] = set()
                        photos_to_update[photo_path].add(name)
                        matched_count += cursor.rowcount

            # 4. Update photos table people lists for each affected photo
            for photo_path, newly_matched_names in photos_to_update.items():
                path_sql, path_args = paths.sql_equals("path", photo_path)
                cursor.execute("SELECT path, people FROM photos WHERE " + path_sql, path_args)
                photo_row = cursor.fetchone()

                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    updated = False
                    for name in newly_matched_names:
                        if name and name not in people:
                            people.append(name)
                            updated = True

                    if updated:
                        path_sql, path_args = paths.sql_equals("path", actual_photo_path)
                        cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (json.dumps(people),) + path_args)

            # Query the remaining unmatched counts for photos in this folder
            cursor.execute("""
                SELECT f.photo_path, COUNT(*)
                FROM faces f
                WHERE """ + under_sql + """ AND f.name IS NULL
                GROUP BY f.photo_path
            """, under_args)
            remaining_rows = cursor.fetchall()
            remaining_counts = {r[0]: r[1] for r in remaining_rows}

            conn.commit()
            self.send_json({
                "success": True, 
                "matched_count": matched_count,
                "remaining_counts": remaining_counts
            })
        except Exception as e:
            logger.error(f"Error in handle_post_folder_automatch: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_people_with_counts(self):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            # Fetch hidden tags
            hidden_tags = set()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")
                for row in cursor.fetchall():
                    hidden_tags.add(row[0])
            
            def is_tag_hidden(tag):
                return vocabulary.hidden_by(tag, hidden_tags)

            # Fetch people with matched counts
            cursor.execute("""
                SELECT name, COUNT(*) as count
                FROM faces
                WHERE name IS NOT NULL
                GROUP BY name
                ORDER BY count DESC
            """)
            rows = cursor.fetchall()
            
            filtered_rows = []
            for r in rows:
                p = r[0]
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE name = ? AND has_face = 1", (p,))
                tag_paths = [row[0] for row in cursor.fetchall()]
                if tag_paths:
                    hidden = all(is_tag_hidden(path) for path in tag_paths)
                else:
                    hidden = False
                if not hidden:
                    filtered_rows.append(r)
                    
            people_counts = [{"name": r[0], "count": r[1]} for r in filtered_rows]

            # This list is deliberately people only. An "Unmatched" pseudo-person used to
            # be pinned at the top, which dropped a flat dump of every nameless face into
            # a mode meant for auditing named people -- unordered and far too large to work
            # through. Nameless faces belong to the Identify Faces queue, which groups them
            # by the name their photo's tags suggest.
            self.send_json(people_counts)
        except Exception as e:
            logger.error(f"Error fetching people with counts: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_person_faces(self, query):
        name_list = query.get("name")
        if not name_list:
            self.send_error(400, "Missing 'name' parameter")
            return
        name = urllib.parse.unquote(name_list[0])

        limit = 100
        try:
            if "limit" in query:
                limit = int(query["limit"][0])
        except Exception:
            pass

        page = 1
        try:
            if "page" in query:
                page = int(query["page"][0])
        except Exception:
            pass
        offset = (page - 1) * limit

        if not os.path.exists(self.db_path):
            self.send_json({"faces": [], "total_count": 0, "has_more": False})
            return
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            faces = []
            
            if name == "Unmatched":
                cursor.execute("SELECT COUNT(*) FROM faces WHERE name IS NULL")
                total_count = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, f.name, p.raw_metadata
                    FROM faces f
                    LEFT JOIN photos p ON p.path = f.photo_path
                    WHERE f.name IS NULL AND f.excluded = 0
                    LIMIT ? OFFSET ?
                """, (limit, offset))
                rows = cursor.fetchall()
                for r in rows:
                    try:
                        box = json.loads(r[2]) if r[2] else []
                    except Exception:
                        box = []
                        
                    year = get_year_from_mtime_or_meta(r[4], r[6], r[1])
                    
                    faces.append({
                        "id": r[0],
                        "photo_path": r[1],
                        "filename": os.path.basename(r[1]),
                        "box": box,
                        "prob": r[3],
                        "mtime": r[4] if r[4] is not None else 0.0,
                        "year": year,
                        "similarity": 1.0,
                        "name": r[5]
                    })
            else:
                cursor.execute("SELECT COUNT(*) FROM faces WHERE name = ?", (name,))
                total_count = cursor.fetchone()[0]

                # Fetch all resolved faces with metadata for era-aware centroid calculation
                cursor.execute("""
                    SELECT f.embedding, p.mtime, p.raw_metadata, f.photo_path
                    FROM faces f
                    LEFT JOIN photos p ON p.path = f.photo_path
                    WHERE f.name = ? AND f.embedding IS NOT NULL
                """, (name,))
                all_matched_rows = cursor.fetchall()

                all_matched_faces = []
                for emb_bytes, mtime, raw_meta_json, photo_path in all_matched_rows:
                    if emb_bytes and len(emb_bytes) > 0:
                        year = get_year_from_mtime_or_meta(mtime, raw_meta_json, photo_path)
                        # Ensure year is parsed to int or None
                        try:
                            year_int = int(year) if year is not None else None
                        except (ValueError, TypeError):
                            year_int = None
                        emb = np.frombuffer(emb_bytes, dtype=np.float32)
                        emb_norm = np.linalg.norm(emb)
                        if emb_norm > 0:
                            emb = emb / emb_norm
                        all_matched_faces.append((emb, year_int))

                matched_years = [y for _, y in all_matched_faces if y is not None]
                y_min = min(matched_years) if matched_years else None

                def compute_era_centroid(target_year):
                    if not all_matched_faces:
                        return None
                    
                    try:
                        t_yr = int(target_year) if target_year is not None else None
                    except (ValueError, TypeError):
                        t_yr = None
                    
                    if y_min is not None and t_yr is not None:
                        age = t_yr - y_min
                    else:
                        age = 99
                        
                    if age <= 4:
                        w = 1
                    elif age <= 12:
                        w = 2
                    elif age <= 16:
                        w = 3
                    elif age <= 20:
                        w = 4
                    else:
                        w = 5
                        
                    window_embeddings = []
                    if t_yr is not None:
                        for emb, y in all_matched_faces:
                            if y is not None and (t_yr - w) <= y <= (t_yr + w):
                                window_embeddings.append(emb)
                                
                    current_w = w
                    while len(window_embeddings) < 5 and current_w < 5:
                        current_w += 1
                        window_embeddings = []
                        if t_yr is not None:
                            for emb, y in all_matched_faces:
                                if y is not None and (t_yr - current_w) <= y <= (t_yr + current_w):
                                    window_embeddings.append(emb)
                                    
                    if len(window_embeddings) < 5:
                        window_embeddings = [emb for emb, _ in all_matched_faces]
                        
                    if not window_embeddings:
                        return None
                        
                    centroid = compute_geometric_median(window_embeddings)
                    norm = np.linalg.norm(centroid)
                    if norm > 0:
                        centroid /= norm
                    return centroid

                era_centroid_cache = {}
                def get_era_centroid(target_year):
                    if target_year not in era_centroid_cache:
                        era_centroid_cache[target_year] = compute_era_centroid(target_year)
                    return era_centroid_cache[target_year]

                cursor.execute("""
                    SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, f.embedding, p.raw_metadata
                    FROM faces f
                    LEFT JOIN photos p ON p.path = f.photo_path
                    WHERE f.name = ?
                    LIMIT ? OFFSET ?
                """, (name, limit, offset))
                rows = cursor.fetchall()
                        
                for r in rows:
                    try:
                        box = json.loads(r[2]) if r[2] else []
                    except Exception:
                        box = []
                        
                    year = get_year_from_mtime_or_meta(r[4], r[6], r[1])
                    
                    similarity = 1.0
                    if r[5] is not None and len(r[5]) > 0:
                        centroid = get_era_centroid(year)
                        if centroid is not None:
                            emb = np.frombuffer(r[5], dtype=np.float32)
                            emb_norm = np.linalg.norm(emb)
                            if emb_norm > 0:
                                emb = emb / emb_norm
                            similarity = float(np.dot(emb, centroid))
                    
                    faces.append({
                        "id": r[0],
                        "photo_path": r[1],
                        "filename": os.path.basename(r[1]),
                        "box": box,
                        "prob": r[3],
                        "mtime": r[4] if r[4] is not None else 0.0,
                        "year": year,
                        "similarity": similarity
                    })

            has_more = False
            if limit >= 0:
                has_more = (offset + len(faces)) < total_count

            self.send_json({
                "faces": faces,
                "total_count": total_count,
                "has_more": has_more,
                "page": page,
                "limit": limit
            })
        except Exception as e:
            logger.error(f"Error fetching faces for person {name}: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_unmatch_bulk(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_ids = data.get("face_ids")
            if not face_ids or not isinstance(face_ids, list):
                self.send_error(400, "Missing or invalid face_ids")
                return

            try:
                face_ids = [int(fid) for fid in face_ids]
            except (ValueError, TypeError):
                self.send_error(400, "Invalid face_ids format")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            photos_to_check = {}

            for face_id in face_ids:
                cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
                row = cursor.fetchone()
                if row:
                    photo_path, old_name = row
                    if old_name:
                        if photo_path not in photos_to_check:
                            photos_to_check[photo_path] = set()
                        photos_to_check[photo_path].add(old_name)

            placeholders = ",".join("?" for _ in face_ids)
            # Unmatching is a decision -- "this is nobody" -- recorded as manual. Undoing
            # an assignment is not: it puts the faces back as they were, unreviewed, and
            # used to leave them marked as deliberately nobody instead.
            source = None if data.get("undo") else "manual"
            cursor.execute(f"UPDATE faces SET name = NULL, name_source = ? WHERE id IN ({placeholders})",
                           [source] + face_ids)

            for photo_path, old_names in photos_to_check.items():
                path_sql, path_args = paths.sql_equals("path", photo_path)
                cursor.execute("SELECT path, people FROM photos WHERE " + path_sql, path_args)
                photo_row = cursor.fetchone()

                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    updated_people = list(people)
                    for old_name in old_names:
                        path_sql, path_args = paths.sql_equals("photo_path", photo_path)
                        cursor.execute("SELECT count(*) FROM faces WHERE " + path_sql + " AND name = ?", path_args + (old_name,))
                        count_row = cursor.fetchone()

                        other_count = count_row[0] if count_row else 0
                        if other_count == 0:
                            updated_people = [p for p in updated_people if p != old_name]

                    if updated_people != people:
                        path_sql, path_args = paths.sql_equals("path", actual_photo_path)
                        cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (json.dumps(updated_people),) + path_args)

            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch_bulk: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_match_bulk(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_ids = data.get("face_ids")
            person_name = data.get("person_name")
            
            if not face_ids or not isinstance(face_ids, list) or not person_name:
                self.send_error(400, "Missing or invalid face_ids or person_name")
                return
                
            try:
                face_ids = [int(fid) for fid in face_ids]
                person_name = str(person_name).strip()
            except (ValueError, TypeError):
                self.send_error(400, "Invalid parameters format")
                return
            problem = vocabulary.problem_with_name(person_name)
            if problem:
                self.send_json_error(400, problem)
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # The state the cached identify views were built against, read before this
            # write touches anything. Only an entry stamped with this can be carried
            # forward; anything older was built before something else changed the
            # table -- a folder removed, a batch of photos indexed -- and re-stamping
            # it would quietly revive a grid full of faces that no longer exist.
            fingerprint_before = self.begin_identify_write(conn)

            # Read the selected faces once.
            #
            # Their path and current name were fetched three times over, one query per
            # face per pass: to drop the ones already named this person, to note the
            # names being displaced, and to group them by photo. A selection of fifty
            # was a hundred and fifty round trips for fifty rows.
            selected = {}
            excluded_ids = set()
            for start in range(0, len(face_ids), 500):
                chunk = face_ids[start:start + 500]
                cursor.execute(
                    "SELECT id, photo_path, name, excluded FROM faces WHERE id IN (%s)"
                    % ",".join("?" * len(chunk)), chunk)
                for row_id, photo_path, current_name, excluded in cursor.fetchall():
                    if excluded:
                        excluded_ids.add(row_id)
                    else:
                        selected[row_id] = (photo_path, current_name)

            # An excluded face is left alone. Naming one leaves a face both ruled out
            # and claimed -- a page acting on a stale list of faces did exactly that --
            # so it is skipped and reported, and the reply says which faces were named.
            skipped_excluded = [fid for fid in face_ids if fid in excluded_ids]

            # Faces already assigned to this person are nothing to do (case-insensitive),
            # and neither is an id that is not in the table.
            face_ids = [
                fid for fid in face_ids
                if fid in selected
                and not (selected[fid][1] or "").strip().lower() == person_name.lower()
            ]
            if not face_ids:
                self.send_json({"success": True, "matched": 0, "matched_ids": [],
                                "skipped_excluded": skipped_excluded})
                return

            photos_to_check = {}
            # Group selected face IDs by photo_path to detect duplicates and verify
            # existing matches, and note which names this assignment displaces.
            photo_to_selected_fids = {}
            for face_id in face_ids:
                if face_id not in selected:
                    continue
                photo_path, old_name = selected[face_id]
                photos_to_check.setdefault(photo_path, set())
                if old_name and old_name != person_name:
                    photos_to_check[photo_path].add(old_name)
                photo_to_selected_fids.setdefault(photo_path, []).append(face_id)

            # Check conflicts for each photo
            for photo_path, fids in photo_to_selected_fids.items():
                # Conflict 1: Multiple selected faces in the same photo are being assigned to this person
                if len(fids) > 1:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": False,
                        "error": f"Cannot match: Multiple selected faces in photo '{os.path.basename(photo_path)}' are being assigned to '{person_name}'."
                    }).encode("utf-8"))
                    return

                # Conflict 2: The person is already tagged on another face in this photo
                #
                # By equality only. The LIKE retry this used to fall back on scanned
                # every face row per selected face -- nineteen seconds for a selection
                # of fifty -- and treated an underscore in a filename as a wildcard,
                # so a lookalike name in an unrelated photo blocked the assignment.
                fid = fids[0]
                path_sql, path_args = paths.sql_equals("photo_path", photo_path)
                cursor.execute("SELECT id FROM faces WHERE " + path_sql + " AND name = ? AND id != ?", path_args + (person_name, fid,))
                conflict_row = cursor.fetchone()

                if conflict_row:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": False,
                        "error": f"Cannot match: '{person_name}' is already tagged on another face in photo '{os.path.basename(photo_path)}'."
                    }).encode("utf-8"))
                    return

            # 2. Update faces table in one transaction
            placeholders = ",".join("?" for _ in face_ids)
            cursor.execute(
                f"UPDATE faces SET name = ?, name_source = 'manual'"
                f" WHERE id IN ({placeholders}) AND excluded = 0",
                [person_name] + face_ids)
            # The rows this write named, which the reply reports rather than the
            # number asked for.
            matched = cursor.rowcount

            # 3. Update photos table people list for each affected photo
            for photo_path, old_names in photos_to_check.items():
                path_sql, path_args = paths.sql_equals("path", photo_path)
                cursor.execute("SELECT path, people FROM photos WHERE " + path_sql, path_args)
                photo_row = cursor.fetchone()

                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    # Append the new person name if missing
                    if person_name and person_name not in people:
                        people.append(person_name)

                    # Remove old names if they are no longer matched to any other faces in the photo
                    updated_people = list(people)
                    for old_name in old_names:
                        path_sql, path_args = paths.sql_equals("photo_path", photo_path)
                        cursor.execute("SELECT count(*) FROM faces WHERE " + path_sql + " AND name = ?", path_args + (old_name,))
                        count_row = cursor.fetchone()

                        other_count = count_row[0] if count_row else 0
                        if other_count == 0:
                            updated_people = [p for p in updated_people if p != old_name]

                    if updated_people != people:
                        path_sql, path_args = paths.sql_equals("path", actual_photo_path)
                        cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (json.dumps(updated_people),) + path_args)

            with tagpup_db.writing(self.db_path, label="name faces in bulk"):
                fingerprint_after = self.faces_fingerprint(conn)
                conn.commit()
                self.identify_cache_forget_faces(conn, face_ids, fingerprint_before, fingerprint_after)
            self.send_json({"success": True, "matched": matched, "matched_ids": face_ids,
                            "skipped_excluded": skipped_excluded})
        except Exception as e:
            logger.error(f"Error in handle_post_match_bulk: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            self.end_identify_write()
            if conn:
                conn.close()

    def _read_face_ids(self, data):
        """Accept either face_ids (list) or a single face_id, as ints."""
        face_ids = data.get("face_ids")
        if face_ids is None and data.get("face_id") is not None:
            face_ids = [data.get("face_id")]
        if not face_ids or not isinstance(face_ids, list):
            return None
        try:
            return [int(x) for x in face_ids]
        except (ValueError, TypeError):
            return None

    def handle_get_folder_index_active(self):
        """Whatever is indexing right now, and whatever is waiting behind it.

        The per-folder status endpoint can only answer about a folder you already know
        about. A page that has just loaded knows nothing, so without this it cannot tell
        that a job is in flight and shows an idle, enabled button over a busy server.
        With a queue there is a second thing it cannot otherwise know: how much is left.
        """
        active = []
        for folder_key, status in list(TunerHTTPRequestHandler.index_status.items()):
            if isinstance(status, dict) and status.get("status") == "running":
                # The status is filed under the folder's key, which is for comparing;
                # the folder itself, as a queued job shows it, travels in the status.
                folder = status.get("folder") or folder_key
                active.append({
                    "folder": folder,
                    "name": os.path.basename(folder),
                    "percent": status.get("percent", 0),
                    "message": status.get("message", ""),
                })

        pending = [
            {"folder": job["folder"],
             "name": os.path.basename(job["folder"])}
            for job in TunerHTTPRequestHandler.index_queue.get("pending", [])
        ]
        self.send_json({
            "active": active,
            "queued": pending,
            "busy": bool(active) or bool(pending),
            "remaining": len(active) + len(pending),
        })

    def handle_get_folder_subfolders(self, query):
        """The immediate subfolders of a folder, so a parent can be expanded.

        Indexing already recurses, so pointing it at a parent would work -- but as one
        opaque job with one progress bar for the lot. Listing the children lets each be
        queued on its own, which is what makes progress legible and lets one bad folder
        be left out rather than sinking the whole run.
        """
        path_list = query.get("path")
        if not path_list:
            self.send_json_error(400, "Missing path parameter")
            return
        # Stored form before anything is joined onto it: a parent typed with forward
        # slashes otherwise yields children spelled with both separators at once.
        parent = paths.stored(urllib.parse.unquote(path_list[0]))
        if not os.path.isdir(parent):
            self.send_json_error(400, "Not a folder: %s" % parent)
            return

        try:
            entries = sorted(
                e for e in os.listdir(parent)
                if os.path.isdir(os.path.join(parent, e))
            )
        except OSError as e:
            self.send_json_error(500, "Could not read %s: %s" % (parent, e))
            return

        indexed_by_folder = self._indexed_folder_counts()
        folders = []
        for name in entries:
            full = os.path.join(parent, name)
            images, subdirs = self._count_images(full)
            folders.append({
                "path": full,
                "name": name,
                "images": images,
                "has_subfolders": subdirs,
                # Counted like `images`, which is every photo under the folder. This
                # counted only photos directly in it, so a folder of subfolders, fully
                # indexed, read "8756 image(s)" as though new and was preselected.
                "indexed": sum(n for folder, n in indexed_by_folder.items()
                               if folder == paths.key(full) or paths.is_under(folder, full)),
            })

        own_images, own_subdirs = self._count_images(parent, recursive=False)
        self.send_json({
            "parent": parent,
            "folders": folders,
            "own_images": own_images,
            "own_indexed": indexed_by_folder.get(paths.key(parent), 0),
            "has_subfolders": own_subdirs,
        })

    @staticmethod
    def _count_images(folder, recursive=True):
        """How many indexable images are under a folder, and has it subfolders."""
        valid = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        count = 0
        has_subdirs = False
        try:
            if recursive:
                for root, dirs, files in os.walk(folder):
                    if root == folder and dirs:
                        has_subdirs = True
                    for f in files:
                        if os.path.splitext(f)[1].lower() in valid:
                            count += 1
            else:
                for entry in os.listdir(folder):
                    full = os.path.join(folder, entry)
                    if os.path.isdir(full):
                        has_subdirs = True
                    elif os.path.splitext(entry)[1].lower() in valid:
                        count += 1
        except OSError:
            pass
        return count, has_subdirs

    def _indexed_folder_counts(self):
        """How many photos this database already holds, per folder.

        Lets the picker show what is already in rather than offering it as if new.
        """
        counts = {}
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            for (photo_path,) in conn.execute("SELECT path FROM photos"):
                folder_key = paths.key(os.path.dirname(photo_path))
                counts[folder_key] = counts.get(folder_key, 0) + 1
            conn.close()
        except Exception:
            return {}
        return counts

    def handle_get_folder_index_status(self, query):
        path_list = query.get("path")
        if not path_list:
            self.send_json_error(400, "Missing path parameter")
            return
        folder_norm = paths.key(urllib.parse.unquote(path_list[0]))
        status = TunerHTTPRequestHandler.index_status.get(
            folder_norm, {"status": "completed", "percent": 100, "message": "Ready"}
        )
        self.send_json(status)

    def handle_post_folder_index_start(self):
        """Queue one or more folders to be indexed into this database.

        TagTuner is where identity work happens, so it needs to be able to bring new
        material in rather than requiring a trip through TagPup first. Clustering is
        opt-in for the same reason it is there: it rewrites every name in the database,
        not just the folder being added.

        Accepts `folder_path` (one) or `folder_paths` (several). Several are queued,
        not run together: indexing is GPU-bound, so two at once do not go twice as fast
        so much as make each other crawl. Asking for a season's worth of folders should
        still be one action rather than a wait beside the machine between each.
        """
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return

        requested = data.get("folder_paths")
        if requested is None:
            requested = [data.get("folder_path")] if data.get("folder_path") else []
        if not isinstance(requested, list):
            self.send_json_error(400, "folder_paths must be a list")
            return

        run_clustering = bool(data.get("cluster", False))

        valid, invalid = [], []
        seen = set()
        for raw in requested:
            if not raw or not isinstance(raw, str):
                invalid.append(str(raw))
                continue
            if not os.path.isdir(raw):
                invalid.append(raw)
                continue
            norm = paths.key(raw)
            if norm in seen:
                continue
            seen.add(norm)
            # The job carries the stored spelling: it is what the indexer is handed,
            # and what the page is shown for queued and running jobs alike.
            valid.append((paths.stored(raw), norm))

        if not valid:
            self.send_json_error(
                400,
                "No valid folder path" + (": %s" % ", ".join(invalid[:3]) if invalid else ""),
            )
            return

        with TunerHTTPRequestHandler._index_queue_lock:
            queue = list(TunerHTTPRequestHandler.index_queue.get("pending", []))
            queued_norms = {paths.key(job["folder"]) for job in queue}

            accepted, already = [], []
            for raw, norm in valid:
                status = TunerHTTPRequestHandler.index_status.get(norm)
                if status and status.get("status") == "running":
                    already.append(raw)
                    continue
                if norm in queued_norms:
                    already.append(raw)
                    continue
                queue.append({"folder": raw, "cluster": run_clustering})
                queued_norms.add(norm)
                TunerHTTPRequestHandler.index_status[norm] = {
                    "status": "queued", "percent": 0, "message": "Waiting to be indexed...",
                    "folder": raw,
                }
                accepted.append(raw)

            TunerHTTPRequestHandler.index_queue["pending"] = queue
            self._ensure_queue_runner()

        self.send_json({
            "success": True,
            "status": "running",
            "queued": accepted,
            "already_queued": already,
            "invalid": invalid,
            "pending": len(queue),
        })

    def handle_post_folder_index_cancel(self):
        """Drop folders that have not started yet.

        The folder already being indexed is left alone: it owns a subprocess partway
        through writing rows, and killing that is a different and riskier operation
        than forgetting something that has not begun.
        """
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return

        wanted = data.get("folder_paths")
        if wanted is None and data.get("folder_path"):
            wanted = [data.get("folder_path")]
        cancel_all = bool(data.get("all"))
        if not cancel_all and not wanted:
            self.send_json_error(400, "Nothing to cancel")
            return

        targets = {paths.key(p) for p in (wanted or []) if p}
        with TunerHTTPRequestHandler._index_queue_lock:
            queue = list(TunerHTTPRequestHandler.index_queue.get("pending", []))
            kept, dropped = [], []
            for job in queue:
                norm = paths.key(job["folder"])
                if cancel_all or norm in targets:
                    dropped.append(job["folder"])
                    # Said, not forgotten: with the entry gone, asking about the folder
                    # answered "completed".
                    TunerHTTPRequestHandler.index_status[norm] = {
                        "status": "cancelled", "percent": 0, "message": "Cancelled.",
                        "folder": job["folder"],
                    }
                else:
                    kept.append(job)

            TunerHTTPRequestHandler.index_queue["pending"] = kept
        self.send_json({"success": True, "cancelled": dropped, "pending": len(kept)})

    def _ensure_queue_runner(self):
        """Start the worker that drains the queue, unless one is already draining it."""
        with TunerHTTPRequestHandler._index_queue_lock:
            runner = TunerHTTPRequestHandler.index_queue.get("runner")
            if runner is not None and runner.is_alive():
                return
            t = threading.Thread(
                target=TunerHTTPRequestHandler.run_index_queue,
                args=(self.db_path,),
                name="TunerIndexQueueRunner",
                daemon=True,
            )
            TunerHTTPRequestHandler.index_queue["runner"] = t
            t.start()

    @classmethod
    def run_index_queue(cls, db_path):
        """Work through the queued folders, one at a time, until it is empty.

        Each folder is indexed by the same code path a single folder always used, so a
        failure is recorded against that folder and the rest of the queue still runs --
        one unreadable folder should not cost the other nine.
        """
        # A worker thread does not inherit the request's thread-local, and the
        # class-level fallback points at the startup database.
        set_active_db_path(db_path)
        try:
            while True:
                with cls._index_queue_lock:
                    queue = list(cls.index_queue.get("pending", []))
                    if not queue:
                        # Stop and say so in one step, so a start arriving now finds
                        # no runner and starts one.
                        if cls.index_queue.get("runner") is threading.current_thread():
                            cls.index_queue["runner"] = None
                        return
                    job = queue.pop(0)
                    cls.index_queue["pending"] = queue

                folder_norm = paths.key(job["folder"])
                cls.index_status[folder_norm] = {
                    "status": "running", "percent": 0,
                    "message": "Starting indexing...",
                    "folder": paths.stored(job["folder"]),
                }
                try:
                    cls.run_folder_index_thread(
                        job["folder"], db_path, job.get("cluster", False)
                    )
                except Exception as e:
                    logger.exception("Queued index of %s failed: %s" % (job["folder"], e))
                    cls.index_status[folder_norm] = {
                        "status": "failed", "percent": 0, "message": "Error: %s" % e,
                        "folder": paths.stored(job["folder"]),
                    }
        finally:
            # Only this runner's own entry: a newer one may have been started already.
            with cls._index_queue_lock:
                if cls.index_queue.get("runner") is threading.current_thread():
                    cls.index_queue["runner"] = None

    @classmethod
    def run_folder_index_thread(cls, folder_path, db_path, run_clustering=False):
        # Re-bind the active database: a worker thread does not inherit the request's
        # thread-local, and the class-level fallback points at the startup database.
        set_active_db_path(db_path)
        # The indexer writes rows in the spelling it is handed, so it is handed the
        # stored one: a folder typed with forward slashes otherwise produced rows with
        # both separators in one path, which no lookup matched.
        folder_path = paths.stored(folder_path)
        folder_norm = paths.key(folder_path)
        status = cls.index_status.get(folder_norm)
        if status is None:
            status = {"status": "running", "percent": 0, "message": "Starting indexing...",
                      "folder": folder_path}
            cls.index_status[folder_norm] = status
        try:
            # The same steps TagPup runs, which read both exit codes. The identify
            # queue is cached against a fingerprint of the faces table, which the new
            # rows change, so it recomputes on its own.
            from contextlib import contextmanager

            from tagpup_server import index_folder_with_cli

            # Assignments are refused while clustering runs, as during Recluster: it
            # rewrites the names they would set. This queue ran it unguarded.
            @contextmanager
            def holding_clustering():
                cls.clustering_in_progress = True
                try:
                    yield
                finally:
                    cls.clustering_in_progress = False

            index_folder_with_cli(folder_path, db_path, run_clustering, status,
                                  while_clustering=holding_clustering)
        except Exception as e:
            logger.exception("Error indexing folder %s: %s" % (folder_path, e))
            status["status"] = "failed"
            status["message"] = "Error: %s" % e
            status["percent"] = 0

    def handle_post_folder_remove(self):
        """Remove a folder's photos and faces from this database.

        Only the index is touched: the photo files themselves are never deleted. This
        does discard face work for those photos -- manual names and exclusions included
        -- because the rows holding them go away, so the caller is told what it cost.
        """
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return

        folder_path = data.get("folder_path")
        if not folder_path:
            self.send_json_error(400, "Missing folder_path")
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # Everything under the folder, at any depth, compared the way the
            # filesystem compares -- in SQL, rather than by reading every path in the
            # library into Python to filter it. Faces are matched on their own
            # photo_path rather than through the photo rows, so a face is removed
            # with its folder even where its photo row is missing or spelled apart.
            photos_sql, photos_args = paths.sql_under("path", folder_path)
            faces_sql, faces_args = paths.sql_under("photo_path", folder_path)

            manual_lost = cursor.execute(
                "SELECT COUNT(*) FROM faces WHERE " + faces_sql + " AND name_source = 'manual'",
                faces_args,
            ).fetchone()[0]
            excluded_lost = cursor.execute(
                "SELECT COUNT(*) FROM faces WHERE " + faces_sql + " AND excluded = 1",
                faces_args,
            ).fetchone()[0]

            # Delete faces explicitly rather than relying on the cascade, which is only
            # active when foreign keys are enabled on this particular connection. The
            # counts reported are what the deletes removed, not what was expected.
            faces_removed = cursor.execute(
                "DELETE FROM faces WHERE " + faces_sql, faces_args).rowcount
            photos_removed = cursor.execute(
                "DELETE FROM photos WHERE " + photos_sql, photos_args).rowcount
            conn.commit()

            logger.info(
                "Removed %d photo(s) and %d face(s) under %s"
                % (photos_removed, faces_removed, folder_path)
            )
            self.send_json({
                "success": True,
                "photos_removed": photos_removed,
                "faces_removed": faces_removed,
                "manual_lost": manual_lost,
                "excluded_lost": excluded_lost,
            })
        except Exception as e:
            logger.error("Error removing folder %s: %s" % (folder_path, e))
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_post_faces_exclude(self):
        """Mark faces as not-a-person so they stop influencing identity work.

        Crowd shots collect passers-by and a bad crop is not a person at all. Left in the
        database they cluster, vote, and drag centroids around. Excluding is reversible
        and keeps the row, so the face still exists on the photo -- it simply stops being
        a candidate for anyone.
        """
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, "Malformed JSON: %s" % json_err)
                return

            face_ids = self._read_face_ids(data)
            if face_ids is None:
                self.send_error(400, "Missing or invalid face_ids")
                return

            reason = data.get("reason") or "not a person"
            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()

            # The state the cached identify views were built against, read before this
            # write touches anything. Only an entry stamped with this can be carried
            # forward; anything older was built before something else changed the
            # table -- a folder removed, a batch of photos indexed -- and re-stamping
            # it would quietly revive a grid full of faces that no longer exist.
            fingerprint_before = self.begin_identify_write(conn)
            placeholders = ",".join("?" for _ in face_ids)

            cursor.execute(
                "SELECT id, photo_path, name FROM faces WHERE id IN (%s)" % placeholders,
                face_ids,
            )
            affected = cursor.fetchall()

            # Excluding retires any name the face carried. name_source stays 'manual'
            # so re-clustering cannot quietly re-assign it.
            cursor.execute(
                "UPDATE faces SET excluded = 1, excluded_reason = ?, name = NULL,"
                " name_source = 'manual' WHERE id IN (%s)" % placeholders,
                [reason] + face_ids,
            )
            excluded_count = cursor.rowcount

            # Drop the person from the photo when no other face of theirs remains there.
            for _face_id, photo_path, old_name in affected:
                if not old_name:
                    continue
                faces_sql, faces_args = paths.sql_equals("photo_path", photo_path)
                cursor.execute(
                    "SELECT COUNT(*) FROM faces WHERE " + faces_sql + " AND name = ? AND excluded = 0",
                    faces_args + (old_name,),
                )
                if cursor.fetchone()[0] == 0:
                    photo_sql, photo_args = paths.sql_equals("path", photo_path)
                    cursor.execute("SELECT people FROM photos WHERE " + photo_sql, photo_args)
                    row = cursor.fetchone()
                    if row and row[0]:
                        try:
                            people = [p for p in json.loads(row[0]) if p != old_name]
                            cursor.execute(
                                "UPDATE photos SET people = ? WHERE " + photo_sql,
                                (json.dumps(people),) + photo_args,
                            )
                        except Exception:
                            pass

            with tagpup_db.writing(self.db_path, label="exclude faces"):
                fingerprint_after = self.faces_fingerprint(conn)
                conn.commit()
                # Ignoring a cluster is the single most expensive thing to have
                # invalidated the grid, and it is pure removal.
                self.identify_cache_forget_faces(conn, face_ids, fingerprint_before, fingerprint_after)
            # The rows changed, not the ids sent: an id that is not in the table was
            # never excluded, and saying it was is how a write reports success on
            # nothing.
            self.send_json({"success": True, "excluded": excluded_count})
        except Exception as e:
            logger.error("Error excluding faces: %s" % e)
            self.send_error(500, "Internal error: %s" % e)
        finally:
            self.end_identify_write()
            if conn:
                conn.close()

    def handle_post_faces_restore(self):
        """Bring excluded faces back into identity work, unnamed and unclaimed."""
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, "Malformed JSON: %s" % json_err)
                return

            face_ids = self._read_face_ids(data)
            if face_ids is None:
                self.send_error(400, "Missing or invalid face_ids")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            placeholders = ",".join("?" for _ in face_ids)
            # Only faces that are excluded. Clearing name_source on any other face
            # would unpin a manual name that re-clustering must not revise -- and
            # Undo after Ignore Cluster sends whatever ids it was given.
            restored = conn.execute(
                "UPDATE faces SET excluded = 0, excluded_reason = NULL, name_source = NULL"
                " WHERE id IN (%s) AND excluded = 1" % placeholders,
                face_ids,
            ).rowcount
            conn.commit()
            self.send_json({"success": True, "restored": restored})
        except Exception as e:
            logger.error("Error restoring faces: %s" % e)
            self.send_error(500, "Internal error: %s" % e)
        finally:
            if conn:
                conn.close()

    def handle_get_excluded_faces(self):
        """List excluded faces so they can be reviewed and restored."""
        if not os.path.exists(self.db_path):
            self.send_json({"faces": [], "total_count": 0})
            return
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, p.raw_metadata,"
                " f.excluded_reason FROM faces f"
                " LEFT JOIN photos p ON p.path = f.photo_path"
                " WHERE f.excluded = 1 ORDER BY f.id DESC"
            )
            faces = []
            for r in cursor.fetchall():
                try:
                    box = json.loads(r[2]) if r[2] else []
                except Exception:
                    box = []
                faces.append({
                    "id": r[0],
                    "photo_path": r[1],
                    "filename": os.path.basename(r[1]) if r[1] else "",
                    "box": box,
                    "prob": r[3],
                    "mtime": r[4] if r[4] is not None else 0.0,
                    "year": get_year_from_mtime_or_meta(r[4], r[5], r[1]),
                    "reason": r[6] or "not a person",
                    "similarity": 0.0,
                })
            self.send_json({"faces": faces, "total_count": len(faces)})
        except Exception as e:
            logger.error("Error listing excluded faces: %s" % e)
            self.send_error(500, "Database error: %s" % e)
        finally:
            if conn:
                conn.close()

    def handle_post_person_rename(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            old_name = data.get("old_name")
            new_name = data.get("new_name")

            if not old_name or not new_name:
                self.send_error(400, "Missing old_name or new_name")
                return

            old_name = str(old_name).strip()
            new_name = str(new_name).strip()

            if old_name == new_name:
                self.send_json({"success": True})
                return

            if old_name == "Unmatched" or new_name == "Unmatched":
                self.send_error(400, "Cannot rename to/from 'Unmatched'")
                return
            problem = vocabulary.problem_with_name(new_name)
            if problem:
                self.send_json_error(400, problem)
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Update faces table
            cursor.execute("UPDATE faces SET name = ? WHERE name = ?", (new_name, old_name))
            cursor.execute("UPDATE faces SET name = ? WHERE LOWER(name) = LOWER(?)", (new_name, old_name))

            # 2. Find and update all photos containing the old name in their people metadata list
            cursor.execute("SELECT path, people FROM photos WHERE people LIKE ?", (f"%{old_name}%",))
            photo_rows = cursor.fetchall()

            for path, people_json in photo_rows:
                if not people_json:
                    continue
                try:
                    people = json.loads(people_json)
                except Exception:
                    continue
                
                updated_people = []
                changed = False
                for name in people:
                    if name.strip().lower() == old_name.lower():
                        if new_name not in updated_people:
                            updated_people.append(new_name)
                        changed = True
                    else:
                        if name not in updated_people:
                            updated_people.append(name)
                
                if changed and updated_people != people:
                    path_sql, path_args = paths.sql_equals("path", path)
                    cursor.execute("UPDATE photos SET people = ? WHERE " + path_sql, (json.dumps(updated_people),) + path_args)

            # Follow the rename into the tag taxonomy and the photo files themselves.
            # Previously this endpoint only touched the faces and photos tables, so the
            # taxonomy kept the old name and -- worse -- nothing was written to disk, so
            # the next rescan of that folder restored the old name from the file.
            cursor.execute(
                "SELECT id, tag FROM tag_taxonomy WHERE name = ? AND has_face = 1", (old_name,)
            )
            person_nodes = cursor.fetchall()
            # (node id, old path, new path, whether the new path already has a node)
            renamed_paths = []
            for node_id, node_tag in person_nodes:
                new_tag = vocabulary.with_leaf(node_tag, new_name)
                cursor.execute(
                    "SELECT id FROM tag_taxonomy WHERE tag = ? AND id != ?", (new_tag, node_id)
                )
                if cursor.fetchone():
                    # Two spellings of one person becoming one. The node for the new
                    # name is kept, and the old one goes once no photo carries it. This
                    # used to leave the tree and the files alone, so the files kept the
                    # old path and the next scan brought the old name back.
                    renamed_paths.append((node_id, node_tag, new_tag, True))
                    continue
                cursor.execute(
                    "UPDATE tag_taxonomy SET name = ?, tag = ? WHERE id = ?",
                    (new_name, new_tag, node_id),
                )
                renamed_paths.append((node_id, node_tag, new_tag, False))

            conn.commit()

            # Rewrite the keyword metadata on any photo carrying the old tag path, and
            # count what was rewritten rather than what was meant to be.
            affected_total = rewritten_total = 0
            write_error = None
            if renamed_paths:
                try:

                    executable = self.get_exiftool_path()
                    for node_id, old_tag, new_tag, merged in renamed_paths:
                        cursor.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL")
                        affected = []
                        for p_path, tags_json in cursor.fetchall():
                            try:
                                for t in json.loads(tags_json or "[]"):
                                    norm = t.replace("\\", "/").strip()  # not a path: a keyword's hierarchy separator
                                    if norm == old_tag or norm.startswith(old_tag + "/"):
                                        affected.append(p_path)
                                        break
                            except Exception:
                                continue
                        rewritten = 0
                        if affected:
                            rewritten = tagging_actions.replace_tag(
                                Library(self.db_path), affected, old_tag, new_tag, executable
                            ).changed
                        affected_total += len(affected)
                        rewritten_total += rewritten
                        if merged and rewritten == len(affected):
                            cursor.execute("DELETE FROM tag_taxonomy WHERE id = ?", (node_id,))
                            conn.commit()
                except Exception as write_err:
                    logger.error(f"Person rename: failed to update photo files: {write_err}")
                    write_error = str(write_err)

            reply = {"success": True, "photos_affected": affected_total,
                     "photos_rewritten": rewritten_total}
            if rewritten_total < affected_total or write_error:
                reply["warning"] = "%d of %d photo(s) could not be rewritten and still name %s%s." % (
                    affected_total - rewritten_total, affected_total, old_name,
                    " (%s)" % write_error if write_error else "")
            self.send_json(reply)
        except Exception as e:
            logger.error(f"Error in handle_post_person_rename: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def faces_fingerprint(self, conn):
        """Cheap signature of the faces table; changes whenever a face is added or named."""
        # SUM(excluded) matters: excluding an unnamed face changes what the queue should
        # show without changing the row count, the name count, or the maximum id, so
        # leaving it out serves a stale queue after every exclusion.
        row = conn.execute(
            "SELECT COUNT(*), COUNT(name), COALESCE(MAX(id), 0), COALESCE(SUM(excluded), 0) FROM faces"
        ).fetchone()
        # None of those counts moves when a person is renamed or a face is given to
        # someone else; the generation does. See index.ensure_faces_generation.
        from index import faces_generation
        return (tuple(row) if row else (0, 0, 0, 0)) + (faces_generation(conn),)

    def identify_cache_get(self, key, fingerprint):
        entry = TunerHTTPRequestHandler.identify_cache.get(key)
        if entry and entry.get("fingerprint") == fingerprint:
            return entry.get("value")
        return None

    def identify_cache_put(self, key, fingerprint, value):
        TunerHTTPRequestHandler.identify_cache[key] = {
            "fingerprint": fingerprint,
            "value": value,
        }

    #: The stages of building a person's grid, and roughly what share of the wait each
    #: one is. Taken from measuring the real library, where the whole pass is about a
    #: minute: reading the candidates is a second or two, grouping them is most of it,
    #: and ranking the leftovers against the person is the next biggest piece.
    #:
    #: They only have to be close. Their job is to keep the bar moving forward at a
    #: believable rate, not to predict the finish.
    BUILD_STAGES = (
        ("reading", 0.06),
        ("grouping", 0.70),
        ("suggesting", 0.04),
        ("ranking", 0.14),
        ("building", 0.06),
    )

    def build_progress(self, name, stage, fraction, message):
        """Say how far along this person's grid is, for anyone who asks.

        `fraction` is progress within the stage, 0 to 1. The overall figure comes from
        the stage weights above, so it only ever moves forward.
        """
        done = 0.0
        for stage_name, weight in self.BUILD_STAGES:
            if stage_name == stage:
                done += weight * max(0.0, min(1.0, fraction))
                break
            done += weight
        TunerHTTPRequestHandler.identify_progress[name] = {
            "name": name,
            "stage": stage,
            "message": message,
            "percent": int(round(done * 100)),
            "active": True,
            "updated": time.time(),
        }

    def build_progress_done(self, name):
        TunerHTTPRequestHandler.identify_progress.pop(name, None)

    def handle_get_unmatched_faces_build_status(self, query):
        """How far along is the grid somebody is waiting for?

        Deliberately cheap and deliberately not cached: it is polled while another
        thread does the slow work, and it touches no database at all.
        """
        name_list = query.get("name")
        name = urllib.parse.unquote(name_list[0]) if name_list else None
        if not name:
            self.send_error(400, "Missing 'name' parameter")
            return
        progress = TunerHTTPRequestHandler.identify_progress.get(name)
        self.send_json(progress or {"name": name, "active": False, "percent": 0})

    @staticmethod
    def _dissolve_stranded_clusters(faces):
        """A cluster that has lost all but one member is not a cluster any more.

        Grouping needs `min_samples` faces that resemble each other -- two, here. Take
        faces out of a group and the survivors can fall below that, and re-running the
        clustering is exactly what the cached payload exists to avoid. Measured on this
        library: taking a tenth of the clustered faces out stranded 139 groups this way.

        It matters because of what the screen offers. A group is drawn with its own
        heading and a button that assigns every face under it to one person in a click;
        the Unclustered pile is drawn with a warning that these resembled nothing and
        have to be handled one at a time. A survivor left flying its old group's colours
        would get the first treatment while being, by the rule that built the group, the
        second thing.

        Only shrinking is possible, which is what makes this safe to do by hand.
        Removing faces can lower a neighbour count but never raise one, so a face that
        was not dense enough to anchor a group cannot become dense enough -- groups
        split, shrink and dissolve, and never merge or gain a member. Measured across
        removals of 10%, 30% and 50% of the clustered faces: not one merge, and not one
        face pulled in from the unclustered pile.
        """
        survivors = collections.Counter(
            f.get("cluster_id") for f in faces if f.get("cluster_id") != -1)
        stranded = {cid for cid, count in survivors.items() if count < 2}
        if not stranded:
            return faces

        dissolved = []
        for face in faces:
            if face.get("cluster_id") in stranded:
                face = dict(face)
                face["cluster_id"] = -1
                face["cluster_name"] = "Unclustered"
                # Resemblance to the centroid of a group that no longer exists.
                face["similarity"] = 0.0
            dissolved.append(face)
        return dissolved

    def begin_identify_write(self, conn):
        """Hold this library's write locks until the commit; return the fingerprint now.

        Taking faces out of the cached grids needs the table's fingerprint before this
        write and after it, and the difference has to be this write alone. It was read
        before anything was locked and again after the commit, so a batch the indexer
        or TagPup committed in between was stamped as accounted for, and its faces
        never reached the grid. Both are now read inside one IMMEDIATE transaction:
        nobody else can write between them. The in-process lock is taken first, in the
        order every other writer here takes the two. end_identify_write releases it;
        closing the connection ends the transaction if the handler returns early.
        """
        lock = tagpup_db.lock_for(self.db_path)
        lock.acquire()
        self._identify_write_lock = lock
        conn.execute("BEGIN IMMEDIATE")
        return self.faces_fingerprint(conn)

    def end_identify_write(self):
        lock = getattr(self, "_identify_write_lock", None)
        if lock is not None:
            self._identify_write_lock = None
            lock.release()

    def identify_cache_forget_faces(self, conn, face_ids, expected_fingerprint,
                                    fingerprint_after=None):
        """Take faces out of the cached Identify Faces views instead of discarding them.

        The per-person grid costs about fifty seconds to build on a real library,
        almost all of it DBSCAN over a hundred thousand candidates. It is cached
        against a fingerprint of the faces table -- which moves the instant anything is
        named or excluded, so every assignment and every ignored cluster threw the
        whole grid away and the next click paid for it again. That is the wrong shape
        for what actually happened: naming ten faces does not change what the other
        hundred thousand look like, it removes ten cards.

        So a removal is applied to the cached payloads, which are then re-stamped with
        the fingerprint the table now carries. Removals only -- a restored or unnamed
        face comes back into the pool with no cluster to belong to, and working out
        where it lands is the clustering pass itself, so those still invalidate.

        Two things are deliberately left to rebuild on their own: the queue listing,
        now 1.4s, and the named-face matrix, which genuinely changes when somebody is
        named. The suggestions already on the remaining cards keep the scores they were
        drawn with until the next full build; they were computed against a set of named
        faces that has since grown by the handful just assigned, which moves a score in
        the third decimal and never changes which card is in front of you.

        Must be called with the write lock held, so the fingerprint read here cannot
        pick up another thread's write and stamp it as though it were accounted for.
        """
        removed = {int(fid) for fid in face_ids}
        if not removed:
            return

        # Read inside the write, when the caller has one (begin_identify_write).
        fingerprint = fingerprint_after or self.faces_fingerprint(conn)
        cache = TunerHTTPRequestHandler.identify_cache

        for key in list(cache.keys()):
            if not key.startswith("matches:"):
                continue
            entry = cache.get(key)
            value = entry.get("value") if entry else None
            if not isinstance(value, dict) or not isinstance(value.get("faces"), list):
                continue

            # Only an entry describing the table as it was a moment ago can be carried
            # forward. An older one was built before something this code knows nothing
            # about changed the pool -- a folder removed, a batch of photos indexed,
            # faces restored -- and those change what the clustering would say, not
            # merely which cards to drop. Left alone, it stays stamped with a
            # fingerprint that no longer matches and is rebuilt on the next request,
            # which is the right answer. Re-stamping it would revive it.
            if entry.get("fingerprint") != expected_fingerprint:
                continue

            faces = value["faces"]
            kept = [f for f in faces if f.get("id") not in removed]
            if len(kept) == len(faces):
                # This person's grid is untouched, but the table moved. Re-stamp it so
                # it stays usable rather than being rebuilt for somebody else's edit.
                cache[key] = {"fingerprint": fingerprint, "value": value}
                continue

            # Survivors of a group that has lost all but one member are no longer a
            # group, and the pile they belong in is the unclustered one.
            stranded_before = sum(1 for f in kept if f.get("cluster_id") == -1)
            kept = self._dissolve_stranded_clusters(kept)
            shown_unclustered = sum(1 for f in kept if f.get("cluster_id") == -1)
            newly_stranded = shown_unclustered - stranded_before

            gone_unclustered = sum(
                1 for f in faces
                if f.get("id") in removed and f.get("cluster_id") == -1)
            # Faces that just fell out of a group join the unclustered pool, so they
            # count towards its total as well as towards what is on screen.
            total_unclustered = max(
                0,
                int(value.get("unclustered_total") or 0)
                - gone_unclustered + newly_stranded)

            # Unclustered faces are capped, so there can be more waiting behind the
            # ones on screen, and thinning the visible end without bringing the next
            # ones forward shrinks the grid towards empty while faces still need a
            # name. Only a removal that actually took unclustered faces can do that --
            # ignoring a cluster leaves the tail exactly as it was -- and even then it
            # is worth letting the tail wear down before paying for a rebuild, because
            # the rebuild is the whole minute this exists to avoid.
            #
            # An earlier version of this rule asked only whether more faces were
            # waiting, which is true from the moment the grid is built, so every
            # removal rebuilt and the cache never once got used.
            wearing_thin = shown_unclustered < UNCLUSTERED_LIMIT // 2
            if gone_unclustered and total_unclustered > shown_unclustered and wearing_thin:
                cache.pop(key, None)
                continue

            updated = dict(value)
            updated["faces"] = kept
            updated["total_count"] = len(kept)
            updated["unclustered_total"] = total_unclustered
            updated["unclustered_shown"] = shown_unclustered
            updated["has_more"] = total_unclustered > shown_unclustered
            cache[key] = {"fingerprint": fingerprint, "value": updated}

    def named_face_matrix(self, conn, fingerprint):
        """Every face that carries a name, as unit vectors, with the names beside them.

        Not one averaged face per person: the diagnostics panel scores a candidate
        against the best single named face, and a suggestion that scored the same pair
        differently would be two numbers for one comparison on one screen. Averaging is
        also the more cautious of the two -- it drags down when somebody's named faces
        vary in light and angle, which at a cross-country meet they always do -- and
        that caution was costing real matches.

        Built once per state of the faces table rather than once per request. Clicking
        a face card asks who it resembles, and that question used to re-read all 35,758
        named embeddings from SQLite and rebuild a 73 MB matrix -- half a second and
        two allocations of it, on every click. Naming or excluding a face moves the
        fingerprint and the matrix is rebuilt; nothing else disturbs it.

        Returns (ids, names, matrix), with matrix None when nobody has been named yet.
        The ids are carried so a caller can leave a particular face out of its own
        answer -- the matrix is shared, so it cannot be rebuilt to exclude one row.
        """
        cached = self.identify_cache_get("named_matrix", fingerprint)
        if cached is not None:
            return cached

        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, embedding FROM faces "
            "WHERE name IS NOT NULL AND embedding IS NOT NULL AND excluded = 0"
        )
        ids, names, vecs = [], [], []
        for face_id, person, blob in cur.fetchall():
            try:
                vec = np.frombuffer(blob, dtype=np.float32)
            except Exception:
                # One damaged row is not a reason to refuse every comparison.
                continue
            norm = np.linalg.norm(vec)
            if norm == 0:
                continue
            ids.append(face_id)
            names.append(person)
            vecs.append(vec / norm)

        result = (ids, names, np.vstack(vecs) if vecs else None)
        self.identify_cache_put("named_matrix", fingerprint, result)
        return result

    def handle_get_unmatched_faces_people(self):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            fingerprint = self.faces_fingerprint(conn)
            cached = self.identify_cache_get("queue", fingerprint)
            if cached is not None:
                self.send_json(cached)
                return

            # 1. Fetch all unmatched faces with the tags their photos carry
            cursor.execute(IDENTIFY_CANDIDATES_SQL)
            unmatched_rows = cursor.fetchall()

            # 2. Fetch matched faces by photo to find already matched names
            cursor.execute("SELECT photo_path, name FROM faces WHERE name IS NOT NULL")
            matched_rows = cursor.fetchall()
            matched_by_photo = {}
            for p_path, name in matched_rows:
                if p_path not in matched_by_photo:
                    matched_by_photo[p_path] = set()
                matched_by_photo[p_path].add(name)

            # Group candidate faces by unmatched tag
            tag_candidates = {}
            unknown_candidates = []
            photo_unmatched_tags = {}

            for r in unmatched_rows:
                photo_path = r[1]
                people_json = r[2]
                embedding_length = r[3]

                # A face with no embedding cannot take part in identifying, so it is
                # not waiting for anybody and must not be counted as though it were.
                if not embedding_length:
                    continue

                people = []
                if people_json:
                    try:
                        people = json.loads(people_json)
                    except Exception:
                        pass

                matched_names = matched_by_photo.get(photo_path, set())
                unmatched_tags = [p for p in people if p not in matched_names]

                if unmatched_tags:
                    photo_unmatched_tags.setdefault(photo_path, set()).update(unmatched_tags)
                    for tag in unmatched_tags:
                        if tag not in tag_candidates:
                            tag_candidates[tag] = []
                        tag_candidates[tag].append(photo_path)
                else:
                    unknown_candidates.append(photo_path)

            # Counting only -- no clustering here.
            #
            # This endpoint used to run DBSCAN over every tag group and over the whole
            # unknown group on each request. On a real library that is tens of thousands
            # of 512-dimensional vectors per call (the same work a full cluster-faces run
            # does), which made the queue effectively unopenable. Clustering is what the
            # per-person view is for; the queue only needs to know who is waiting and how
            # many photos they are waiting in.
            tag_photos = {}
            for tag, candidates in tag_candidates.items():
                if len(candidates) >= 2:
                    tag_photos[tag] = set(candidates)

            # Tags with a single unmatched candidate cannot form a group of their own.
            # They used to vanish from the UI entirely; they are surfaced under
            # "Ungrouped" so that every nameless face stays reachable.
            #
            # A face only belongs here when EVERY unmatched tag on its photo is a
            # single-candidate tag -- otherwise it is already reachable under the tag
            # that does form a group. This matches the rule the detail view applies, so
            # the count shown in the queue is the number of faces it will actually open.
            single_candidate_tags = {t for t, c in tag_candidates.items() if len(c) == 1}
            ungrouped_photos = set()
            for tag in single_candidate_tags:
                photo_path = tag_candidates[tag][0]
                photo_tags = photo_unmatched_tags.get(photo_path, set())
                if photo_tags and photo_tags <= single_candidate_tags:
                    ungrouped_photos.add(photo_path)

            unknown_photos = set(unknown_candidates)

            # Format the counts.
            #
            # Identify Faces counts faces throughout, because a face is the unit of
            # work here: one photo of a start line holds thirty, and clearing it is
            # thirty decisions. The sidebar used to count photos for people and the
            # Ungrouped bucket, faces for Excluded, and photos for Unknown Faces --
            # three units in one list, so "710 photos" sat beside "4,739 faces" in the
            # panel and "1,554 photos" beside "1,554 ungrouped faces".
            people_counts = []
            for tag, candidates in tag_candidates.items():
                if len(tag_photos.get(tag, ())) > 0:
                    people_counts.append({
                        "name": tag,
                        "count": len(candidates),
                        "unit": "face",
                        "photos": len(tag_photos[tag]),
                    })

            # Sort descending by photo count
            people_counts.sort(key=lambda x: x["count"], reverse=True)

            # Put Unknown Faces first, then Ungrouped, as the two catch-all buckets.
            #
            # These count faces, not photos, and say so. A person's count is photos
            # because a photo is the unit of work for them -- open it, name who is in
            # it. In these buckets the unit is a face: one photo of a start line holds
            # thirty. Counting photos here put "710 photos" in the sidebar beside
            # "4,739 faces" in the panel, two true numbers that read as a contradiction.
            if len(unknown_candidates) > 0:
                people_counts.insert(0, {
                    "name": "Unknown Faces",
                    "count": len(unknown_candidates),
                    "unit": "face",
                    "photos": len(unknown_photos),
                })
            if len(ungrouped_photos) > 0:
                ungrouped_faces = sum(
                    len(tag_candidates[tag]) for tag in single_candidate_tags
                    if tag_candidates[tag][0] in ungrouped_photos)
                people_counts.append({
                    "name": "Ungrouped",
                    "count": ungrouped_faces or len(ungrouped_photos),
                    "unit": "face",
                    "photos": len(ungrouped_photos),
                })

            # Excluded faces take no part in identifying, but the bucket has to be
            # reachable from somewhere or an exclusion could never be reviewed or undone.
            cursor.execute("SELECT COUNT(*) FROM faces WHERE excluded = 1")
            excluded_count = cursor.fetchone()[0]
            if excluded_count:
                people_counts.append({
                    "name": "Excluded", "count": excluded_count, "unit": "face"})

            self.identify_cache_put("queue", fingerprint, people_counts)
            self.send_json(people_counts)
        except Exception as e:
            logger.error(f"Error fetching unmatched faces people: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_unmatched_faces_person_matches(self, query):
        name_list = query.get("name")
        if not name_list:
            self.send_error(400, "Missing 'name' parameter")
            return
        name = urllib.parse.unquote(name_list[0])

        if not os.path.exists(self.db_path):
            self.send_json({"faces": [], "total_count": 0, "has_more": False})
            return
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            fingerprint = self.faces_fingerprint(conn)
            cache_key = f"matches:{name}"
            cached = self.identify_cache_get(cache_key, fingerprint)
            if cached is not None:
                self.send_json(cached)
                return

            # Nothing was cached, so this is the slow path: reading every candidate and
            # grouping it. Say so, from here until the response goes out.
            self.build_progress(name, "reading", 0.0, "Reading the faces still unnamed")

            # 1. Fetch all unmatched faces in database
            cursor.execute("""
                SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, f.embedding, p.raw_metadata, p.people
                FROM faces f
                LEFT JOIN photos p ON p.path = f.photo_path
                WHERE f.name IS NULL AND f.excluded = 0
            """)
            unmatched_rows = cursor.fetchall()
            
            if not unmatched_rows:
                self.send_json({"faces": [], "total_count": 0, "has_more": False})
                return

            # 2. Fetch matched faces by photo to find already matched names
            cursor.execute("SELECT photo_path, name FROM faces WHERE name IS NOT NULL")
            matched_rows = cursor.fetchall()
            matched_by_photo = {}
            for p_path, m_name in matched_rows:
                if p_path not in matched_by_photo:
                    matched_by_photo[p_path] = set()
                matched_by_photo[p_path].add(m_name)

            # How many unmatched candidates each tag has library-wide, so "Ungrouped" can
            # recognise the tags that cannot form a group. Mirrors the queue listing.
            tag_candidate_counts = {}
            if name == "Ungrouped":
                for r in unmatched_rows:
                    try:
                        r_people = json.loads(r[7] or "[]")
                    except Exception:
                        continue
                    for tag in r_people:
                        if tag not in matched_by_photo.get(r[1], set()):
                            tag_candidate_counts[tag] = tag_candidate_counts.get(tag, 0) + 1

            # Filter candidate faces based on 'name'
            candidate_rows = []
            for r in unmatched_rows:
                photo_path = r[1]
                people_json = r[7]
                
                # Parse photo people tags
                people = []
                if people_json:
                    try:
                        people = json.loads(people_json)
                    except Exception:
                        pass
                
                matched_names = matched_by_photo.get(photo_path, set())
                unmatched_tags = [p for p in people if p not in matched_names]

                if name == "Unknown Faces":
                    # Photos with no unmatched tags
                    if not unmatched_tags:
                        candidate_rows.append(r)
                elif name == "Ungrouped":
                    # Faces whose photo names someone, but where that name has only this
                    # one unmatched candidate in the whole library, so it can never form a
                    # group of its own. Without this bucket these faces are unreachable.
                    if unmatched_tags and all(
                        tag_candidate_counts.get(tag, 0) <= 1 for tag in unmatched_tags
                    ):
                        candidate_rows.append(r)
                else:
                    # Photos where 'name' is an unmatched tag
                    if name in unmatched_tags:
                        candidate_rows.append(r)

            if not candidate_rows:
                self.send_json({"faces": [], "total_count": 0, "has_more": False})
                return

            #: Below this a suggestion is more distraction than help. Set at 0.70
            #: rather than higher because a weaker guess is still a shortlist of one,
            #: and confirming or rejecting it costs a glance -- which beats reading a
            #: nameless grid. The number is always shown, and a guess under
            #: SUGGEST_CONFIDENT is labelled as the weaker thing it is.
            SUGGEST_FLOOR = 0.70
            SUGGEST_CONFIDENT = 0.85
            self.build_progress(
                name, "reading", 0.7, "Reading the faces already named")
            _known_ids, known_names, known_matrix = self.named_face_matrix(conn, fingerprint)

            def reference_faces(person):
                """Every face already named as this person.

                None when nobody has been named yet -- the ordinary case for somebody
                being identified for the first time, and the reason this cannot simply
                replace the keyword queue.
                """
                if known_matrix is None:
                    return None
                rows = [i for i, n in enumerate(known_names) if n == person]
                return known_matrix[rows] if rows else None

            def suggest_for_all(centroids):
                """Who does each of these groups most resemble?

                Scored against the best single named face, matching the diagnostics
                panel exactly, so the badge on a card and the number in the panel
                cannot disagree.

                Answered for every group in one pass. The per-group version of this
                ran a (35,758 x 512) matrix against one vector at a time, once per
                cluster: on this library 6,151 of those, measured at 8.4s, to do
                arithmetic BLAS does in a fraction of a second when handed the whole
                batch. The numbers that come out are the same ones.

                Chunked, because the full product is groups x named faces and that is
                a matrix nobody needs all of at once -- only its row maxima.
                """
                results = [(None, 0.0)] * len(centroids)
                if known_matrix is None or not len(centroids):
                    return results

                block = np.asarray(centroids, dtype=np.float32)
                norms = np.linalg.norm(block, axis=1)
                usable = norms > 0
                block = np.where(usable[:, None], block / np.where(norms > 0, norms, 1)[:, None], block)

                CHUNK = 512
                for start in range(0, len(block), CHUNK):
                    stop = min(start + CHUNK, len(block))
                    sims = np.dot(block[start:stop], known_matrix.T)
                    best = np.argmax(sims, axis=1)
                    scores = sims[np.arange(stop - start), best]
                    for offset in range(stop - start):
                        i = start + offset
                        if not usable[i]:
                            continue
                        score = float(scores[offset])
                        results[i] = (
                            known_names[int(best[offset])] if score >= SUGGEST_FLOOR else None,
                            score,
                        )
                return results

            # Which other people each candidate's photo still has no face for. A photo
            # naming two unaccounted people offers both its faces under both names,
            # which is right but reads as noise until you are told why.
            def other_unaccounted_names(row):
                try:
                    people = json.loads(row[7] or "[]")
                except Exception:
                    return []
                matched = matched_by_photo.get(row[1], set())
                return [p for p in people if p not in matched and p != name]

            # Extract embeddings
            valid_rows = []
            embs = []
            for r in candidate_rows:
                if r[5] and len(r[5]) > 0:
                    emb = np.frombuffer(r[5], dtype=np.float32)
                    norm = np.linalg.norm(emb)
                    embs.append(emb / norm if norm > 0 else emb)
                    valid_rows.append(r)

            if not embs:
                self.send_json({"faces": [], "total_count": 0, "has_more": False})
                return

            embs = np.array(embs)
            self.build_progress(
                name, "grouping", 0.0,
                "Grouping %s faces that look alike" % format(len(embs), ","))

            # Group the candidates. The slow part of this screen by a wide margin, and
            # the reason it reports progress at all: see cluster_candidates.
            labels = cluster_candidates(
                embs,
                on_progress=lambda done, total: self.build_progress(
                    name, "grouping", done / total if total else 1.0,
                    "Grouping %s faces that look alike" % format(total, ",")))

            # Group faces by cluster label. Noise (label == -1) is kept rather than
            # discarded: those faces are real and still need a name, and dropping them
            # silently made them unreachable from anywhere in the UI. They are emitted
            # last, as singletons, so the confident groups stay at the top.
            cluster_groups = {}
            noise_indices = []
            for idx, label in enumerate(labels):
                if label == -1:
                    noise_indices.append(idx)
                    continue
                if label not in cluster_groups:
                    cluster_groups[label] = []
                cluster_groups[label].append(idx)

            # Sort cluster groups by size descending
            sorted_labels = sorted(cluster_groups.keys(), key=lambda l: len(cluster_groups[l]), reverse=True)

            # Each cluster's centre, and how much each of its members looks like it.
            cluster_centroids = []
            cluster_sims_by_label = {}
            for label in sorted_labels:
                cluster_embs = embs[cluster_groups[label]]
                centroid = np.mean(cluster_embs, axis=0)
                cnorm = np.linalg.norm(centroid)
                if cnorm > 0:
                    centroid = centroid / cnorm
                cluster_centroids.append(centroid)
                cluster_sims_by_label[label] = np.dot(cluster_embs, centroid)

            # Who each group looks like, from the faces already named, for every group
            # at once. The queue itself is keyword-driven and can only offer a name the
            # photo mentions, which is no help at all for a photo naming nobody.
            self.build_progress(
                name, "suggesting", 0.0,
                "Working out who %s groups resemble" % format(len(cluster_centroids), ","))
            cluster_suggestions = suggest_for_all(cluster_centroids)

            faces = []
            for cluster_idx, label in enumerate(sorted_labels):
                indices = cluster_groups[label]
                cluster_name = f"Cluster {cluster_idx + 1}"
                cluster_sims = cluster_sims_by_label[label]
                suggested_name, suggested_sim = cluster_suggestions[cluster_idx]

                for local_idx, global_idx in enumerate(indices):
                    r = valid_rows[global_idx]
                    similarity = float(cluster_sims[local_idx])
                    
                    try:
                        box = json.loads(r[2]) if r[2] else []
                    except Exception:
                        box = []
                        
                    year = get_year_from_mtime_or_meta(r[4], r[6], r[1])
                    faces.append({
                        "id": r[0],
                        "photo_path": r[1],
                        "filename": os.path.basename(r[1]),
                        "box": box,
                        "prob": r[3],
                        "mtime": r[4] if r[4] is not None else 0.0,
                        "year": year,
                        "similarity": similarity,
                        "cluster_id": int(label),
                        "cluster_name": cluster_name,
                        "other_names": other_unaccounted_names(r),
                        "suggested_name": suggested_name,
                        "suggested_similarity": round(suggested_sim, 3),
                        "suggestion_strength": (
                            "likely" if suggested_sim >= SUGGEST_CONFIDENT else "possible"
                        ) if suggested_name else None,
                        "_emb_idx": global_idx
                    })

            # Append the unclustered faces, flagged so the UI can rank them lowest.
            # Capped -- see UNCLUSTERED_LIMIT.
            unclustered_total = len(noise_indices)

            # The person being sought, needed before the cap rather than after it:
            # ranking has to see every candidate to choose the strongest 500.
            seeking_for_ranking = reference_faces(name)

            # Rank the whole set, then take the top of it.
            #
            # This used to slice the first 500 off an unordered list and sort those,
            # so with 4,739 unclustered faces the 500 on screen were an arbitrary
            # sample that happened to be sorted -- the strongest matches in the other
            # 4,239 were never shown, and no amount of clearing the queue reached them
            # because the next pass sliced the same way.
            #
            # Scoring every face first costs one matrix multiply against the named
            # faces, which is cheaper than building 4,739 cards and throwing most away.
            self.build_progress(
                name, "ranking", 0.0,
                "Ranking %s faces that grouped with nothing" % format(len(noise_indices), ","))
            ranked = list(noise_indices)
            if known_matrix is not None and len(ranked):
                block = embs[ranked]                          # (n, dim), already unit
                best_per_face = np.max(np.dot(block, known_matrix.T), axis=1)
                order = np.argsort(-best_per_face)
                ranked = [ranked[i] for i in order]
            if seeking_for_ranking is not None and len(ranked):
                block = embs[ranked]
                against_person = np.max(np.dot(block, seeking_for_ranking.T), axis=1)
                order = np.argsort(-against_person)
                ranked = [ranked[i] for i in order]

            self.build_progress(name, "building", 0.0, "Building the grid")

            # Each unclustered face is its own group of one, so its centroid is itself.
            shown_unclustered = ranked[:UNCLUSTERED_LIMIT]
            lone_suggestions = suggest_for_all(
                [embs[i] for i in shown_unclustered]) if shown_unclustered else []

            for position, global_idx in enumerate(shown_unclustered):
                r = valid_rows[global_idx]
                lone_name, lone_sim = lone_suggestions[position]
                try:
                    box = json.loads(r[2]) if r[2] else []
                except Exception:
                    box = []
                faces.append({
                    "id": r[0],
                    "photo_path": r[1],
                    "filename": os.path.basename(r[1]),
                    "box": box,
                    "prob": r[3],
                    "mtime": r[4] if r[4] is not None else 0.0,
                    "year": get_year_from_mtime_or_meta(r[4], r[6], r[1]),
                    "similarity": 0.0,
                    "cluster_id": -1,
                    "cluster_name": "Unclustered",
                    "other_names": other_unaccounted_names(r),
                    "suggested_name": lone_name,
                    "suggested_similarity": round(lone_sim, 3),
                    "suggestion_strength": (
                        "likely" if lone_sim >= SUGGEST_CONFIDENT else "possible"
                    ) if lone_name else None,
                    "_emb_idx": global_idx
                })

            # Rank against the person being sought, where there is anything to rank
            # against. Until now `similarity` meant similarity to a face's own cluster
            # centroid -- a number about the crowd it arrived with, not about this
            # person -- so a hundred candidates came back in no useful order.
            #
            # Ranked, not filtered. Two candidates can both be genuinely this person in
            # different photos (measured here: 0.826 and 0.822 for one), so a cutoff
            # would discard a real match to tidy the list.
            seeking = seeking_for_ranking
            if seeking is not None:
                for face in faces:
                    # Against the best of this person's faces, not their average: a
                    # candidate matching any one of them well is a candidate worth
                    # looking at, and the panel scores it the same way.
                    face["person_similarity"] = round(
                        float(np.max(np.dot(seeking, embs[face.pop("_emb_idx")]))), 3
                    )
                faces.sort(key=lambda x: x["person_similarity"], reverse=True)
            else:
                for face in faces:
                    face.pop("_emb_idx", None)
                faces.sort(key=lambda x: x["similarity"], reverse=True)

            payload = {
                "faces": faces,
                "total_count": len(faces),
                "unclustered_total": unclustered_total,
                "unclustered_shown": min(unclustered_total, UNCLUSTERED_LIMIT),
                "has_more": unclustered_total > UNCLUSTERED_LIMIT,
                "page": 1,
                "limit": -1
            }
            self.identify_cache_put(cache_key, fingerprint, payload)
            self.send_json(payload)

        except Exception as e:
            logger.error(f"Error fetching unmatched faces person matches: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            # However this ended, nothing is being built for this person any more.
            # Left behind, a stale entry would keep a progress bar on screen for good.
            self.build_progress_done(name)
            if conn:
                conn.close()

    def send_json(self, data):
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

    def handle_get_browse_folder(self):
        try:
            # Native Windows Vista-style Folder Browser Dialog via Python to avoid GUI blocks
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

    def handle_get_tags(self):
        try:
            from taxonomy import TagTaxonomy
            tax_path = Library(self.db_path).taxonomy_file
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            
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
                
            final_paths = [p for p in taxonomy.paths if not is_tag_hidden(p)]
            self.send_json(sorted(final_paths))
        except Exception as e:
            self.send_json_error(500, str(e))



    def rescan_folder_to_cache(self, folder_path):
        folder_path = paths.stored(folder_path)
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
        if not image_files:
            TunerHTTPRequestHandler.folder_cache[paths.key(folder_path)] = {}
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
            path = paths.stored(meta["path"])
            folder_map[paths.key(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))

        TunerHTTPRequestHandler.folder_cache[paths.key(folder_path)] = folder_map

#: Listens on IPv4 and IPv6 alike -- see scripts/localserver.py for why that is
#: worth two seconds on every click.
ThreadedHTTPServer = localserver.ThreadedHTTPServer

def start_server(port=8080, db_path="data/photo_index.db", gui_dir="gui"):
    TunerHTTPRequestHandler.db_path = db_path
    TunerHTTPRequestHandler.gui_dir = gui_dir

    # Automatically check and apply schema migration on startup
    conn = None
    try:
        conn = tagpup_db.connect(db_path, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(faces)")
        columns = [info[1] for info in cursor.fetchall()]
        if "crop_image" not in columns:
            logger.info("Migrating faces table: Adding crop_image column...")
            cursor.execute("ALTER TABLE faces ADD COLUMN crop_image BLOB")
            conn.commit()
        if "prob" not in columns:
            logger.info("Migrating faces table: Adding prob column...")
            cursor.execute("ALTER TABLE faces ADD COLUMN prob REAL")
            conn.commit()
        
        # Ensure faces(name) index exists
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_faces_name ON faces(name)")
        conn.commit()

        # And the one Identify Faces filters on. PhotoIndex.load() creates it too, but
        # opening TagTuner does not necessarily go through PhotoIndex, and this screen
        # is unusable without it on a large library: every count of the excluded bucket
        # scans the whole faces table, crops and embeddings included.
        if "excluded" in columns:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_faces_identify ON faces(excluded, name)")
            conn.commit()
            # So its cache sees renames and reassignments, for the same reason.
            from index import ensure_faces_generation, ensure_taxonomy_generation
            ensure_faces_generation(conn)
            # And so TagPup, another process, sees the tree edits made here.
            ensure_taxonomy_generation(conn)

        # ('Non Person' is migrated onto the excluded column by PhotoIndex.load,
        #  which every entry point calls; it is not duplicated here.)
        conn.commit()
    except Exception as e:
        logger.error(f"Error checking/migrating database schema: {e}")
    finally:
        if conn:
            conn.close()

    server_address = ("", port)
    server = None
    import time
    for attempt in range(5):
        try:
            server = ThreadedHTTPServer(server_address, TunerHTTPRequestHandler)
            break
        except OSError as e:
            if attempt == 4:
                raise e
            logger.info(f"Port {port} is busy, retrying in 0.5s (attempt {attempt + 1}/5)...")
            time.sleep(0.5)

    logger.info(f"TagTuner server started on port {port} using DB {db_path}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info(f"Server shutting down... (PID: {os.getpid()})")
        server.shutdown()
        server.server_close()

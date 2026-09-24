# tuner_server.py
import collections
from contextlib import contextmanager
import os
import time
import threading
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
from tagpup.core.result import Conflict, NotFound
from tagpup.jobs import indexing as indexing_jobs
from tagpup.services import faces as faces_service
from tagpup.store import faces as store_faces
from tagpup.store import schema
from tagpup.store import taxonomy as store_taxonomy
from tagpup.services import indexing
from tagpup.services import people as people_service
from tagpup.services import tags as tags_service
from tagpup.services import photos as photo_actions

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


@contextmanager
def clustering(db_path):
    """Refuse assignments in a library while this is held: clustering rewrites the names
    they would be setting. The library is named, not taken from the request, so it holds
    on a thread no request set up."""
    key = Library(db_path).key
    TunerHTTPRequestHandlerMeta._clustering_in_progress.add(key)
    try:
        yield
    finally:
        TunerHTTPRequestHandlerMeta._clustering_in_progress.discard(key)


class TunerHTTPRequestHandler(localserver.RequestLog, BaseHTTPRequestHandler,
                              metaclass=TunerHTTPRequestHandlerMeta):
    db_path = "data/photo_index.db"
    gui_dir = "gui"

    # Database-specific registries
    _db_identify_cache_registry = {}
    _db_identify_progress_registry = {}

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

    # The suggestions cache file belongs to TagPup, which runs the suggestions and is
    # the only process that reads or writes it (tagpup.jobs.suggestions.cache_file).
    # TagTuner used to carry its own copy of the naming and
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
        localserver.list_libraries(self)

    def handle_post_databases_select(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON body")
            return
        localserver.select_library(self, data.get("db_name"))

    def handle_post_databases_create(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON body")
            return
        from tagpup_server import create_library
        localserver.create_library(self, data.get("db_name"), create_library)

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
        # As stored, and not kept: the page draws face boxes over it in the stored
        # pixels' coordinates (localserver.serve_photo_file).
        localserver.serve_photo_file(self, query.get("path"), query.get("size"), upright=False)

    def handle_serve_face_crop(self, query):
        """A face's crop (tagpup.services.photos.face_crop)."""
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_error(400, "Missing 'id' parameter")
            return
        try:
            face_id = int(face_id_list[0])
        except ValueError:
            self.send_error(400, "Invalid 'id' parameter")
            return
        try:
            crop = photo_actions.face_crop(Library(self.db_path), face_id)
        except NotFound as missing:
            self.send_error(404, str(missing))
            return
        except Exception as e:
            logger.error(f"Error serving face crop for ID {face_id}: {e}")
            self.send_error(500, f"Error cropping face: {e}")
            return
        localserver.send_image(self, crop, "image/jpeg")


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
        """Rename a tag, or merge it into another, everywhere it lives
        (tagpup.services.tags.merge): the photo files, photos.tags, the tag tree, and
        the tag's cached CLIP embedding.

        Defaults to a dry run: `apply` must be sent explicitly, and without it nothing
        is written and the plan comes back. Every destructive script in this repo works
        that way, and it has caught real mistakes before they reached photos. TagPup's
        in-memory suggestions live in another process, which this cannot reach.
        """
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        try:
            result = tags_service.merge(
                Library(self.db_path), data.get("from"), data.get("into") or data.get("to"),
                self.get_exiftool_path(), retire=bool(data.get("retire")),
                apply=bool(data.get("apply")))
        except Exception as e:
            logger.error(f"Error merging tag {data.get('from')!r}: {e}")
            self.send_json_error(500, str(e))
            return
        if result.refused:
            self.send_json_error(400, result.refused)
            return
        reply = dict(result.details)
        if not result.ok:
            reply["error"] = result.message()
        self.send_json(reply)

    def handle_get_people(self, query=None):
        """Everyone the library knows, the people keywords name included
        (tagpup.services.people.names). ?include_hidden=1 adds those hidden from
        autocomplete: it answers "does this person exist?", which a hidden person does."""
        include_hidden = (query or {}).get("include_hidden", ["0"])[0] == "1"
        try:
            self.send_json(people_service.names(Library(self.db_path), keywords_too=True,
                                                include_hidden=include_hidden))
        except Exception as e:
            logger.error(f"Error fetching people: {e}")
            self.send_error(500, f"Database error: {e}")

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
        """Name one face (tagpup.services.faces.name_face)."""
        data = self._face_request()
        if data is None:
            return
        face_id, person_name = data.get("face_id"), data.get("person_name")
        if face_id is None or not person_name:
            self.send_error(400, "Missing face_id or person_name")
            return
        try:
            face_id, person_name = int(face_id), str(person_name).strip()
        except (ValueError, TypeError):
            self.send_error(400, "Invalid parameters")
            return
        if self._faces_write(lambda library: faces_service.name_face(library, face_id, person_name)):
            self.send_json({"success": True})

    def _faces_write(self, action):
        """Run a face action (tagpup.services.faces) on this request's library, and answer
        what went wrong: 404 for a face or a library that is not there, 409 for a face
        that cannot be named as things stand, 400 for a request refused, 500 for anything
        else. Returns its Result, or None once answered.

        The faces an action took out of the identify pool come off the cached grids,
        rather than making the next click rebuild them: the action says which, and the
        fingerprints either side of its write (tagpup.store.faces.accounted_write).
        """
        try:
            result = action(Library(self.db_path))
        except NotFound as missing:
            self.send_error(404, str(missing))
            return None
        except Conflict as conflict:
            self.send_json_error(409, str(conflict))
            return None
        except Exception as e:
            logger.error("Error in a face action: %s" % e)
            self.send_error(500, "Internal error: %s" % e)
            return None
        if result.refused:
            self.send_json_error(400, result.refused)
            return None
        fingerprints = result.details.get("fingerprints")
        if fingerprints and result.changed:
            self.identify_cache_forget_faces(None, result.details["face_ids"], *fingerprints)
        return result

    def named_faces(self):
        """Every named face, as named_face_matrix gives them: for automatch."""
        conn = tagpup_db.connect(tagpup_db.readonly_uri(self.db_path), uri=True)
        try:
            return self.named_face_matrix(conn, self.faces_fingerprint(conn))
        finally:
            conn.close()

    def _face_request(self):
        """The request's JSON body, or None once a malformed one has been answered."""
        try:
            return self.read_json_body()
        except Exception as json_err:
            self.send_error(400, f"Malformed JSON: {json_err}")
            return None

    def handle_post_unmatch(self):
        """Take a face's name off (tagpup.services.faces.unname_face)."""
        data = self._face_request()
        if data is None:
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
        if self._faces_write(lambda library: faces_service.unname_face(library, face_id)):
            self.send_json({"success": True})

    def handle_post_unmatch_all(self):
        """Take the names off every face in a photo (tagpup.services.faces.unname_photo)."""
        data = self._face_request()
        if data is None:
            return
        photo_path = data.get("photo_path")
        if not photo_path:
            self.send_error(400, "Missing photo_path")
            return
        if self._faces_write(lambda library: faces_service.unname_photo(library, photo_path)):
            self.send_json({"success": True})

    def handle_post_automatch(self):
        """Automatch a photo's faces (tagpup.services.faces.automatch_photo)."""
        data = self._face_request()
        if data is None:
            return
        photo_path = data.get("photo_path")
        if not photo_path:
            self.send_error(400, "Missing photo_path")
            return
        result = self._faces_write(
            lambda library: faces_service.automatch_photo(library, photo_path, self.named_faces))
        if result:
            self.send_json({"success": True, "matched_count": result.changed})

    def handle_post_folder_automatch(self):
        """Automatch a folder's faces (tagpup.services.faces.automatch_folder)."""
        data = self._face_request()
        if data is None:
            return
        folder_path = data.get("folder_path")
        if not folder_path:
            self.send_error(400, "Missing folder_path")
            return
        result = self._faces_write(
            lambda library: faces_service.automatch_folder(library, folder_path, self.named_faces))
        if result:
            self.send_json({"success": True, "matched_count": result.changed,
                            "remaining_counts": result.details.get("remaining_counts", {})})

    def handle_get_people_with_counts(self):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        conn = None
        try:
            conn = tagpup_db.connect(tagpup_db.readonly_uri(self.db_path), uri=True)
            hidden_tags = store_taxonomy.hidden_tags(conn)
            # Where the tree files everyone, in one read: it was a query per person
            # (docs/findings.md, #50).
            filed = store_taxonomy.filed_people(conn)

            # A person is left out when every node the tree files them under is hidden.
            filtered_rows = []
            for r in store_faces.counts_by_name(conn):
                tag_paths = filed.get(r[0], [])
                if not (tag_paths and all(vocabulary.hidden_by(path, hidden_tags) for path in tag_paths)):
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
        """Take the names off many faces (tagpup.services.faces.unname_faces)."""
        data = self._face_request()
        if data is None:
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
        if self._faces_write(lambda library: faces_service.unname_faces(
                library, face_ids, undo=bool(data.get("undo")))):
            self.send_json({"success": True})

    def handle_post_match_bulk(self):
        """Name many faces as one person (tagpup.services.faces.name_faces)."""
        data = self._face_request()
        if data is None:
            return
        face_ids, person_name = data.get("face_ids"), data.get("person_name")
        if not face_ids or not isinstance(face_ids, list) or not person_name:
            self.send_error(400, "Missing or invalid face_ids or person_name")
            return
        try:
            face_ids, person_name = [int(fid) for fid in face_ids], str(person_name).strip()
        except (ValueError, TypeError):
            self.send_error(400, "Invalid parameters format")
            return
        result = self._faces_write(lambda library: faces_service.name_faces(library, face_ids, person_name))
        if result:
            self.send_json({"success": True, "matched": result.details["matched"],
                            "matched_ids": result.details["matched_ids"],
                            "skipped_excluded": result.details["skipped_excluded"]})

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
        """What is being indexed now, and what waits behind it (tagpup.jobs.indexing).

        The per-folder status can only answer about a folder the page knows of. A page
        that has just loaded knows none, so without this it would show an idle button
        over a busy server.
        """
        self.send_json(self.index_queue().active())

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
        self.send_json(self.index_queue().status(urllib.parse.unquote(path_list[0])))

    def handle_post_folder_index_start(self):
        """Queue one or more folders to be added to this library (tagpup.jobs.indexing).

        TagTuner is where identity work happens, so it brings new photos in itself rather
        than needing a trip through TagPup first. Accepts `folder_path` (one) or
        `folder_paths` (several). Clustering is opt-in: it re-derives every name in the
        library, not only the folders being added.
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

        result = self.index_queue().start(requested, self.folder_indexer(),
                                          cluster=bool(data.get("cluster", False)))
        if result.refused:
            self.send_json_error(400, result.message())
            return
        self.send_json({"success": True, "status": "running", **result.details})

    def handle_post_folder_index_cancel(self):
        """Drop folders that have not started (tagpup.jobs.indexing.IndexQueue.cancel).
        The folder being indexed is left alone."""
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
        result = self.index_queue().cancel(wanted or [], everything=cancel_all)
        self.send_json({"success": True, **result.details})

    def index_queue(self):
        """The folders waiting to be added to this request's library."""
        return indexing_jobs.queue_for(Library(self.db_path))

    def folder_indexer(self):
        """How this server adds a folder to this request's library: through the CLI
        (tagpup.services.indexing.index_folder), refusing assignments while
        cluster-faces runs, since it rewrites the names they would be setting. The queue
        once ran it unguarded."""
        db_path = self.db_path

        def index(folder, cluster, report):
            return indexing.index_folder(Library(db_path), folder, tagpup_config.CODE_ROOT,
                                         cluster=cluster, report=report,
                                         while_clustering=lambda: clustering(db_path))

        return index

    def handle_post_folder_remove(self):
        """Take a folder's photos and faces out of this library
        (tagpup.services.faces.remove_folder). The photo files are never touched."""
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
        folder_path = data.get("folder_path")
        if not folder_path:
            self.send_json_error(400, "Missing folder_path")
            return
        try:
            result = faces_service.remove_folder(Library(self.db_path), folder_path)
        except Exception as e:
            logger.error("Error removing folder %s: %s" % (folder_path, e))
            self.send_json_error(500, str(e))
            return
        self.send_json(dict(result.details, success=True))

    def handle_post_faces_exclude(self):
        """Take faces out of identity work (tagpup.services.faces.exclude)."""
        data = self._face_request()
        if data is None:
            return
        face_ids = self._read_face_ids(data)
        if face_ids is None:
            self.send_error(400, "Missing or invalid face_ids")
            return
        reason = data.get("reason") or "not a person"
        result = self._faces_write(lambda library: faces_service.exclude(library, face_ids, reason))
        if result:
            # The rows changed, not the ids sent: an id that is not in the table was never
            # excluded, and saying it was is how a write reports success on nothing.
            self.send_json({"success": True, "excluded": result.changed})

    def handle_post_faces_restore(self):
        """Bring excluded faces back, unnamed (tagpup.services.faces.restore)."""
        data = self._face_request()
        if data is None:
            return
        face_ids = self._read_face_ids(data)
        if face_ids is None:
            self.send_error(400, "Missing or invalid face_ids")
            return
        result = self._faces_write(lambda library: faces_service.restore(library, face_ids))
        if result:
            self.send_json({"success": True, "restored": result.changed})

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
        """Rename a person everywhere (tagpup.services.tags.rename_person)."""
        try:
            data = self.read_json_body()
        except Exception as json_err:
            self.send_error(400, f"Malformed JSON: {json_err}")
            return
        try:
            result = tags_service.rename_person(Library(self.db_path), data.get("old_name"),
                                                data.get("new_name"), self.get_exiftool_path())
        except NotFound as missing:
            self.send_error(404, str(missing))
            return
        except Exception as e:
            logger.error(f"Error in handle_post_person_rename: {e}")
            self.send_error(500, f"Internal error: {e}")
            return
        if result.refused:
            self.send_json_error(400, result.refused)
            return
        reply = {"success": True, "photos_affected": result.details["photos_affected"],
                 "photos_rewritten": result.details["photos_rewritten"]}
        if not result.ok:
            reply["warning"] = result.message()
        self.send_json(reply)

    def faces_fingerprint(self, conn):
        """Cheap signature of the faces table (tagpup.store.faces.fingerprint)."""
        return store_faces.fingerprint(conn)

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

        `fingerprint_after` is read inside the write, before its commit
        (tagpup.store.faces.accounted_write), so it cannot pick up another thread's
        write and stamp it as though it were accounted for. Without it, this reads the
        fingerprint on `conn`, and must be called with the write lock held. After a
        commit is fine: an entry another write has re-stamped meanwhile no longer
        carries `expected_fingerprint`, and is left to be rebuilt.
        """
        removed = {int(fid) for fid in face_ids}
        if not removed:
            return

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
            self.send_json({"path": localserver.ask_for_folder()})
        except Exception as e:
            self.send_json_error(500, str(e))


#: Listens on IPv4 and IPv6 alike -- see scripts/localserver.py for why that is
#: worth two seconds on every click.
ThreadedHTTPServer = localserver.ThreadedHTTPServer

def start_server(port=8080, db_path="data/photo_index.db", gui_dir="gui"):
    TunerHTTPRequestHandler.db_path = db_path
    TunerHTTPRequestHandler.gui_dir = gui_dir

    # The library's tables, made or brought up to date (tagpup.store.schema).
    try:
        schema.ensure(db_path)
    except Exception as e:
        logger.error(f"Error checking/migrating database schema: {e}")

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

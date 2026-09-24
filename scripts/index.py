# index.py
import os
import json
import socket
import sqlite3
try:
    from . import db as tagpup_db
    from . import paths
except ImportError:  # imported as a top-level module
    import db as tagpup_db
    import paths
import logging
import hashlib
import subprocess
import threading
import time
from typing import List, Dict, Any, Tuple, Optional, Set
import numpy as np
import faiss

logger = logging.getLogger("tagpup_cli.index")

class PathLocker:
    """Stops two indexers working the same photo at once.

    The lock is a file created exclusively, which is atomic even across processes.
    The hazard is what happens when a holder dies without releasing: nothing else
    ever cleaned up another process's lock, so a killed run left its in-flight
    photos permanently unindexable. They were then skipped in silence by every
    later run -- 348 of them had accumulated over three months before anyone
    counted the photos actually in the index against the photos on disk.

    So a lock now records who holds it and since when, and a lock whose holder is
    gone is taken over rather than obeyed.
    """

    #: A lock older than this is assumed abandoned. Locks are released once per
    #: batch of 100 photos, so a live holder should never come close.
    MAX_LOCK_AGE_SECONDS = 6 * 60 * 60

    def __init__(self, lock_dir: str, max_age: float = None):
        # No default: "data/locks" meant the working directory's, and two indexers in
        # different folders then took their locks in different places.
        self.lock_dir = lock_dir
        os.makedirs(self.lock_dir, exist_ok=True)
        self.locked_paths = set()
        self.max_age = self.MAX_LOCK_AGE_SECONDS if max_age is None else max_age
        self.stolen = 0
        self._alive_cache = {}

    def _get_lock_path(self, path: str) -> str:
        # By key, so two spellings of one photo contend for one lock.
        path_hash = hashlib.md5(paths.key(path).encode('utf-8')).hexdigest()
        return os.path.join(self.lock_dir, f"{path_hash}.lock")

    def _write_lock(self, lock_file: str, path: str):
        with open(lock_file, "x", encoding="utf-8") as f:
            json.dump({
                "path": paths.stored(path),
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "acquired": time.time(),
            }, f)

    def _process_alive(self, pid: int) -> bool:
        """Best effort. When in doubt, say alive -- never steal a live lock."""
        if pid in self._alive_cache:
            return self._alive_cache[pid]
        alive = True
        try:
            if os.name == "nt":
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                    capture_output=True, text=True, timeout=10,
                ).stdout
                alive = str(int(pid)) in out
            else:
                os.kill(int(pid), 0)
                alive = True
        except ProcessLookupError:
            alive = False
        except Exception:
            alive = True
        self._alive_cache[pid] = alive
        return alive

    def _is_abandoned(self, lock_file: str) -> bool:
        """Is this lock's holder gone?"""
        try:
            age = time.time() - os.path.getmtime(lock_file)
        except OSError:
            return False
        if age > self.max_age:
            return True
        try:
            with open(lock_file, encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            # Locks written before this format carried only a path, so age is all
            # there is to go on -- and the age check above already had its say.
            return False
        if info.get("host") and info["host"] != socket.gethostname():
            return False  # another machine's lock; only age may retire it
        pid = info.get("pid")
        return bool(pid) and not self._process_alive(pid)

    def acquire(self, path: str) -> bool:
        """Take the lock for a photo. False means somebody live is working on it."""
        lock_file = self._get_lock_path(path)
        try:
            self._write_lock(lock_file, path)
            self.locked_paths.add(path)
            return True
        except FileExistsError:
            pass
        except Exception as e:
            logger.warning(f"Failed to create lock for {path}: {e}")
            return False

        if not self._is_abandoned(lock_file):
            return False

        # The holder is gone. Take it over rather than skipping the photo forever.
        try:
            os.remove(lock_file)
            self._write_lock(lock_file, path)
        except FileExistsError:
            return False  # somebody beat us to it, which is fine
        except Exception as e:
            logger.warning(f"Could not take over the abandoned lock for {path}: {e}")
            return False

        self.locked_paths.add(path)
        self.stolen += 1
        logger.info(f"Took over an abandoned lock for {path}")
        return True

    def release(self, path: str):
        """Release the lock for a specific photo path."""
        lock_file = self._get_lock_path(path)
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception as e:
                logger.warning(f"Failed to remove lock for {path}: {e}")
        self.locked_paths.discard(path)

    def release_all(self):
        """Release all locks held by this process instance."""
        for path in list(self.locked_paths):
            self.release(path)

# Connection settings, the write lock and the busy retry live in db.py, which every
# connection in the codebase goes through. These names are kept so existing callers
# and tests keep working.
DB_BUSY_TIMEOUT_MS = tagpup_db.BUSY_TIMEOUT_MS
configure_connection = tagpup_db.configure
retry_when_busy = tagpup_db.retry_when_busy


def ensure_faces_generation(conn):
    """A counter that moves whenever a face's identity changes, by whoever changes it.

    Identify Faces caches its queue against a fingerprint of the faces table. Counts
    cannot see a person renamed or a face moved from one person to another, so the
    triggers below count those, and every writer -- TagTuner, TagPup, the CLI, a
    script -- bumps it without knowing it exists. A crop being cached is not a change
    of identity and leaves it alone.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS faces_generation (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            generation INTEGER NOT NULL
        )
    """)
    conn.execute("INSERT OR IGNORE INTO faces_generation (id, generation) VALUES (1, 0)")
    bump = "BEGIN UPDATE faces_generation SET generation = generation + 1 WHERE id = 1; END"
    conn.execute("CREATE TRIGGER IF NOT EXISTS faces_generation_insert"
                 " AFTER INSERT ON faces " + bump)
    conn.execute("CREATE TRIGGER IF NOT EXISTS faces_generation_delete"
                 " AFTER DELETE ON faces " + bump)
    conn.execute("CREATE TRIGGER IF NOT EXISTS faces_generation_update"
                 " AFTER UPDATE OF name, name_source, excluded, embedding, photo_path"
                 " ON faces " + bump)
    conn.commit()


def faces_generation(conn):
    """The counter above, or 0 on a database that does not have it yet."""
    try:
        row = conn.execute("SELECT generation FROM faces_generation WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] if row else 0


def ensure_taxonomy_generation(conn):
    """A counter that moves whenever the tag tree changes, by whoever changes it.

    TagPup caches who the tree says each person is, and resolves every keyword it
    writes through that. TagTuner is another process: a person it renamed or merged
    stayed under the old path in TagPup's cache until TagPup restarted, and TagPup
    wrote the old path back into photos. The cache is now kept against this.
    """
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'"
                        " AND name = 'tag_taxonomy'").fetchone():
        return  # nothing to watch yet; PhotoIndex.load creates both
    conn.execute("""
        CREATE TABLE IF NOT EXISTS taxonomy_generation (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            generation INTEGER NOT NULL
        )
    """)
    conn.execute("INSERT OR IGNORE INTO taxonomy_generation (id, generation) VALUES (1, 0)")
    bump = "BEGIN UPDATE taxonomy_generation SET generation = generation + 1 WHERE id = 1; END"
    conn.execute("CREATE TRIGGER IF NOT EXISTS taxonomy_generation_insert"
                 " AFTER INSERT ON tag_taxonomy " + bump)
    conn.execute("CREATE TRIGGER IF NOT EXISTS taxonomy_generation_delete"
                 " AFTER DELETE ON tag_taxonomy " + bump)
    conn.execute("CREATE TRIGGER IF NOT EXISTS taxonomy_generation_update"
                 " AFTER UPDATE OF tag, name, parent_id, has_face ON tag_taxonomy " + bump)
    conn.commit()


class PhotoIndex:
    def __init__(self, db_path: str = "data/photo_index.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.index: Optional[faiss.Index] = None
        self.metadata: List[Dict[str, Any]] = []
        self.indexed_metadata: List[Dict[str, Any]] = []
        self.dim = 512  # Default
        # One load at a time. TagPup loads its startup library in a background thread,
        # and a Suggest clicked meanwhile checks for changes on the same index.
        self._load_lock = threading.RLock()

    def _create_table(self):
        """Create the schema table if it does not exist."""
        if self.conn is None:
            return
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                path TEXT PRIMARY KEY,
                mtime REAL,
                size INTEGER,
                tags TEXT,
                people TEXT,
                captions TEXT,
                raw_metadata TEXT,
                embedding BLOB,
                document_id TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_path TEXT,
                box TEXT,
                embedding BLOB,
                name TEXT,
                crop_image BLOB,
                prob REAL,
                name_source TEXT,
                excluded INTEGER DEFAULT 0,
                excluded_reason TEXT,
                FOREIGN KEY(photo_path) REFERENCES photos(path) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                path TEXT PRIMARY KEY,
                mtime REAL,
                size INTEGER,
                model_name TEXT,
                pretrained TEXT,
                preserve_full_frame INTEGER,
                max_aspect_ratio REAL,
                force_image_size INTEGER,
                embedding BLOB
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_faces_photo_path ON faces(photo_path)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_faces_name ON faces(name)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tag_taxonomy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT UNIQUE,
                parent_id INTEGER,
                name TEXT,
                has_face INTEGER DEFAULT 0,
                hidden_from_autocomplete INTEGER DEFAULT 0,
                FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tag_taxonomy_tag ON tag_taxonomy(tag)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tag_embeddings (
                tag TEXT,
                prompt TEXT,
                model_name TEXT,
                pretrained TEXT,
                embedding BLOB,
                PRIMARY KEY (tag, prompt, model_name, pretrained)
            )
        """)
        self.conn.commit()

    def load(self) -> bool:
        """Connect to SQLite database and build in-memory FAISS index."""
        with self._load_lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> bool:
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
                
            # Set a 30-second timeout to handle concurrent lock waiting gracefully
            # Reloads (after remove_paths, build_or_update) reuse the connection. Each
            # used to open a new one and drop the old one unclosed, and on Windows an
            # unclosed handle keeps the database file locked.
            if self.conn is None:
                self.conn = tagpup_db.connect(self.db_path, timeout=30.0, check_same_thread=False)
            configure_connection(self.conn)
            self.conn.execute("PRAGMA foreign_keys = ON;")
            self._create_table()
            
            # Dynamic migration: add crop_image and prob columns to faces table if they don't exist
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA table_info(faces)")
            columns = [info[1] for info in cursor.fetchall()]
            if "crop_image" not in columns:
                logger.info("Migrating faces table: Adding crop_image column...")
                cursor.execute("ALTER TABLE faces ADD COLUMN crop_image BLOB")
                self.conn.commit()
            if "prob" not in columns:
                logger.info("Migrating faces table: Adding prob column...")
                cursor.execute("ALTER TABLE faces ADD COLUMN prob REAL")
                self.conn.commit()
            if "excluded" not in columns:
                # Faces deliberately kept out of identity work: passers-by, crowd noise,
                # bad crops. An earlier attempt used the magic name 'Non Person', which
                # this same load() still migrates away, because a name cannot survive
                # re-clustering. A column can.
                logger.info("Migrating faces table: Adding excluded columns...")
                cursor.execute("ALTER TABLE faces ADD COLUMN excluded INTEGER DEFAULT 0")
                cursor.execute("ALTER TABLE faces ADD COLUMN excluded_reason TEXT")
                self.conn.commit()
            # A photo's identity, independent of where it sits on disk.
            #
            # Renaming or moving a file leaves its row describing something that no
            # longer exists, and the row is the valuable half: it holds the embedding
            # and the faces, names included. DocumentID is the XMP standard's
            # per-document identifier and most photos already carry one, so this is
            # mostly a matter of recording what is already there.
            cursor.execute("PRAGMA table_info(photos)")
            photo_columns = [info[1] for info in cursor.fetchall()]
            if "document_id" not in photo_columns:
                logger.info("Migrating photos table: Adding document_id column...")
                cursor.execute("ALTER TABLE photos ADD COLUMN document_id TEXT")
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_photos_document_id "
                    "ON photos(document_id)")
                self.conn.commit()

            if "name_source" not in columns:
                # Records who decided a face's name. 'manual' means a person chose it in
                # TagTuner; those decisions survive re-clustering, which otherwise
                # re-derives every name from scratch and discards corrections.
                logger.info("Migrating faces table: Adding name_source column...")
                cursor.execute("ALTER TABLE faces ADD COLUMN name_source TEXT")
                self.conn.commit()
            
            # Identify Faces filters on `excluded` and `name` together, on every load:
            # who is still nameless, how many are excluded, which named faces to
            # compare against. Leading with `excluded` lets a count of the excluded
            # bucket be answered from the index alone, without touching rows that
            # carry a 2 KB embedding and a 6 KB JPEG crop apiece -- 0.37s of scanning
            # on a 225,000-face library, for a number that is usually near zero.
            #
            # Created here rather than in _create_table() because the columns it spans
            # are added by the migrations just above: a database old enough to be
            # missing `excluded` would fail on an index that names it.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_faces_identify ON faces(excluded, name)")
            self.conn.commit()
            ensure_faces_generation(self.conn)
            ensure_taxonomy_generation(self.conn)

            # Paths compare the way the filesystem does (paths.sql_equals): without
            # case on Windows. An equality under a collation can only use an index
            # declared with that collation, so without these every such lookup would
            # scan -- which is what the LOWER(path) and `LIKE ?` comparisons they
            # replace were doing, 225,000 faces at a time.
            if paths.COLLATE != "BINARY":
                for index_name, table, column in (
                    ("idx_photos_path_nocase", "photos", "path"),
                    ("idx_faces_photo_path_nocase", "faces", "photo_path"),
                    ("idx_embedding_cache_path_nocase", "embedding_cache", "path"),
                ):
                    cursor.execute("CREATE INDEX IF NOT EXISTS %s ON %s(%s COLLATE %s)"
                                   % (index_name, table, column, paths.COLLATE))
                self.conn.commit()

            # Migrate tag_taxonomy: is_people -> has_face
            cursor.execute("PRAGMA table_info(tag_taxonomy)")
            tax_columns = [info[1] for info in cursor.fetchall()]
            if tax_columns:
                if "has_face" not in tax_columns:
                    logger.info("Migrating tag_taxonomy table: Adding has_face column...")
                    cursor.execute("ALTER TABLE tag_taxonomy ADD COLUMN has_face INTEGER DEFAULT 0")
                    if "is_people" in tax_columns:
                        cursor.execute("UPDATE tag_taxonomy SET has_face = is_people")
                    elif "is_face" in tax_columns:
                        cursor.execute("UPDATE tag_taxonomy SET has_face = is_face")
                    self.conn.commit()
            
            # Migrate the old 'Non Person' marker onto the excluded column.
            #
            # Storing it as a name never worked -- re-clustering rewrites names, so the
            # marker could not survive -- and the previous migration simply cleared it to
            # NULL, which threw the information away: those faces went straight back into
            # the matching pool as ordinary unidentified faces. They meant exactly what
            # excluded now means, so carry the intent across rather than dropping it.
            cursor.execute("SELECT COUNT(*) FROM faces WHERE name = 'Non Person'")
            if cursor.fetchone()[0] > 0:
                logger.info("Migrating faces table: converting 'Non Person' to excluded...")
                cursor.execute(
                    "UPDATE faces SET name = NULL, excluded = 1,"
                    " excluded_reason = 'migrated from Non Person'"
                    " WHERE name = 'Non Person'"
                )
                self.conn.commit()

            # Taken before the rows: a write landing in between costs one reload
            # later, never a missed one. See reload_if_changed.
            self._signature = self._photos_signature()
            cursor.execute("SELECT path, mtime, size, tags, people, captions, raw_metadata, embedding FROM photos")
            rows = cursor.fetchall()
            
            self.metadata = []
            self.indexed_metadata = []
            embeddings = []
            
            for path, mtime, size, tags_json, people_json, captions_json, raw_meta_json, emb_bytes in rows:
                try:
                    tags = json.loads(tags_json)
                    people = json.loads(people_json)
                    captions = json.loads(captions_json)
                    raw_meta = json.loads(raw_meta_json)
                except Exception:
                    tags, people, captions, raw_meta = [], [], [], {}
                    
                has_emb = (emb_bytes is not None and len(emb_bytes) > 0)
                meta_item = {
                    "path": path,
                    "mtime": mtime,
                    "size": size,
                    "tags": tags,
                    "people": people,
                    "captions": captions,
                    "raw_metadata": raw_meta,
                    "has_embedding": has_emb
                }
                self.metadata.append(meta_item)
                
                if has_emb:
                    emb = np.frombuffer(emb_bytes, dtype=np.float32)
                    embeddings.append(emb)
                    self.indexed_metadata.append(meta_item)
                
            if embeddings:
                self.dim = len(embeddings[0])
                embedding_matrix = np.array(embeddings, dtype=np.float32)
                
                # L2 normalize vectors to guarantee accurate cosine similarity via FlatIP
                norms = np.linalg.norm(embedding_matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                embedding_matrix = embedding_matrix / norms
                
                has_gpu = False
                if hasattr(faiss, "get_num_gpus"):
                    try:
                        has_gpu = (faiss.get_num_gpus() > 0)
                    except Exception:
                        pass
 
                if has_gpu:
                    try:
                        res = faiss.StandardGpuResources()
                        self.index = faiss.index_cpu_to_gpu(res, 0, faiss.IndexFlatIP(self.dim))
                        logger.info("Initialized GPU-accelerated FAISS index.")
                    except Exception as gpu_err:
                        logger.warning(f"Failed to initialize GPU FAISS index: {gpu_err}. Falling back to CPU index.")
                        self.index = faiss.IndexFlatIP(self.dim)
                else:
                    self.index = faiss.IndexFlatIP(self.dim)
 
                self.index.add(embedding_matrix)
                logger.info(f"Loaded {len(self.metadata)} index entries from SQLite.")
            else:
                self.index = None
                
            return True
        except Exception as e:
            logger.error(f"Error loading SQLite database: {e}", exc_info=True)
            self.index = None
            self.metadata = []
            self.indexed_metadata = []
            return False

    def _with_face_names(self, meta):
        """meta's people plus the names already given to this photo's faces."""
        from metadata import face_names
        people = list(meta.get("people", []))
        seen = {p.lower() for p in people}
        for name in face_names(meta["path"], conn=self.conn):
            if name.lower() not in seen:
                seen.add(name.lower())
                people.append(name)
        return people

    def build_or_update(self, embeddings: List[List[float]], metas: List[Dict[str, Any]], dim: int = 512, reload: bool = True):
        """Batch insert/update photos inside the SQLite database (transaction-safe)."""
        if not embeddings or self.conn is None:
            return

        try:
            cursor = self.conn.cursor()
            for meta, emb in zip(metas, embeddings):
                emb_bytes = np.array(emb, dtype=np.float32).tobytes()
                # Record the photo's identity beside its path. Where the file has
                # one it is already in raw_metadata; where it does not, the extractor
                # has minted one into the file by now.
                from identity import read_document_id
                document_id = (meta.get("document_id")
                               or read_document_id(meta.get("raw_metadata", {})))
                # Update a photo already indexed in place, under the spelling its row
                # already has. faces.photo_path references photos.path ON DELETE
                # CASCADE, so anything that deletes the row -- INSERT OR REPLACE is a
                # delete and an insert -- takes every face with it: names given by
                # hand, "nobody" decisions and exclusions. Re-indexing a changed photo
                # did exactly that. The existing spelling is kept because the faces
                # point at it, and a row under a second spelling would be a duplicate.
                # Who the keywords name depends on this library's taxonomy, which
                # the reader only consults when a caller remembers to pass it. The
                # CLI and both folder indexers did not, and the row lost everyone
                # outside the default roots. Resolve here, against this connection.
                from metadata import extract_people
                resolved = extract_people(meta.get("raw_metadata", {}), meta.get("tags", []),
                                          conn=self.conn)
                known = {p.lower() for p in meta.get("people", [])}
                meta = dict(meta, people=list(meta.get("people", []))
                            + [p for p in resolved if p.lower() not in known])
                clause, params = paths.sql_equals("path", meta["path"])
                existing = cursor.execute(
                    "SELECT path FROM photos WHERE " + clause + " LIMIT 1", params).fetchone()
                cursor.execute("""
                    INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding, document_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime = excluded.mtime, size = excluded.size, tags = excluded.tags,
                        people = excluded.people, captions = excluded.captions,
                        raw_metadata = excluded.raw_metadata, embedding = excluded.embedding,
                        document_id = COALESCE(excluded.document_id, photos.document_id)
                """, (
                    existing[0] if existing else paths.stored(meta["path"]),
                    meta.get("mtime", 0.0),
                    meta.get("size", 0),
                    json.dumps(meta.get("tags", [])),
                    # Keyword people and the photo's named faces: a re-index
                    # rebuilt this from keywords alone and dropped everyone
                    # identified only by their face. See metadata.photo_people.
                    json.dumps(self._with_face_names(meta)),
                    json.dumps(meta.get("captions", [])),
                    json.dumps(meta.get("raw_metadata", {})),
                    emb_bytes,
                    document_id
                ))
            self.conn.commit()
            
            if reload:
                # Reload to rebuild the in-memory FAISS index to reflect the updates
                self.load()
        except Exception as e:
            logger.error(f"Error saving batch to SQLite: {e}")
            self.conn.rollback()
            raise e

    def save(self):
        """No-op because SQLite changes are committed incrementally. Kept for signature compatibility."""
        pass

    def search(self, query_vector: List[float], k: int = 15) -> List[Tuple[float, Dict[str, Any]]]:
        """Search the in-memory FAISS index for the k most similar vectors."""
        if self.index is None or self.index.ntotal == 0:
            return []

        query_np = np.array([query_vector], dtype=np.float32)
        norm = np.linalg.norm(query_np)
        if norm > 0:
            query_np = query_np / norm

        k = min(k, self.index.ntotal)
        if k == 0:
            return []

        scores, indices = self.index.search(query_np, k)
        
        results = []
        for sim, idx in zip(scores[0], indices[0]):
            if idx == -1 or idx >= len(self.indexed_metadata):
                continue
            results.append((float(sim), self.indexed_metadata[idx]))
            
        return results

    def remove_paths(self, paths_to_remove: Set[str]):
        """Remove specific paths from the SQLite database and reload."""
        if self.conn is None or not paths_to_remove:
            return

        try:
            cursor = self.conn.cursor()
            # SQLite deletes in chunks or individual queries
            for path in paths_to_remove:
                clause, params = paths.sql_equals("path", path)
                cursor.execute("DELETE FROM photos WHERE " + clause, params)
            self.conn.commit()
            # Rebuild in-memory index
            self.load()
        except Exception as e:
            logger.error(f"Error deleting paths from SQLite: {e}")
            self.conn.rollback()
            raise e

    def clear_clip_embeddings(self):
        """Set all embedding values in photos table to NULL and commit, then clear in-memory FAISS index."""
        if self.conn is None:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("UPDATE photos SET embedding = NULL")
            self.conn.commit()
            # Clear in-memory FAISS index
            self.index = None
            # Reload metadata (has_embedding will be updated to False)
            self.load()
        except Exception as e:
            logger.error(f"Error clearing clip embeddings from SQLite: {e}")
            self.conn.rollback()
            raise e

    def _photos_signature(self):
        """Something that moves whenever a photo row is added, removed or rewritten.

        Every write that changes a row records the file's new mtime, so the sum of
        them moves with it. 41ms on a 68,000-photo library, against 1.3s to load it.
        """
        return tuple(self.conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(rowid), 0), TOTAL(mtime) FROM photos").fetchone())

    def reload_if_changed(self) -> bool:
        """Load again if the photos table has changed since it was read. True if it did.

        For an index held across requests -- Suggest's -- which otherwise went on
        answering from the photos as they were when it was first loaded: photos
        indexed since were never neighbours, and tags saved since never counted.
        """
        with self._load_lock:
            if self.conn is None:
                return self.load()
            if getattr(self, "_signature", None) == self._photos_signature():
                return False
            self.load()
            return True

    def close(self):
        """Close SQLite connection."""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def remove_faces_for_path(self, photo_path: str):
        """Delete all faces associated with a photo path."""
        if self.conn is None:
            return
        try:
            cursor = self.conn.cursor()
            clause, params = paths.sql_equals("photo_path", photo_path)
            cursor.execute("DELETE FROM faces WHERE " + clause, params)
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error removing faces for {photo_path}: {e}")
            self.conn.rollback()

    def save_faces_for_path(self, photo_path: str, faces: List[Dict[str, Any]]):
        """Replace a photo's faces with freshly detected ones.

        This discards any names, manual overrides, exclusions and cached crops on the
        existing rows. Callers that merely want detection results recorded should use
        save_faces_if_absent instead; this one is for explicit re-detection.
        """
        if self.conn is None:
            return
        try:
            cursor = self.conn.cursor()
            # First clean up old face records for this photo
            clause, params = paths.sql_equals("photo_path", photo_path)
            cursor.execute("DELETE FROM faces WHERE " + clause, params)
            
            for face in faces:
                box_json = json.dumps(face["box"])
                emb_bytes = np.array(face["embedding"], dtype=np.float32).tobytes()
                crop_bytes = face.get("crop_image")
                cursor.execute("""
                    INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (paths.stored(photo_path), box_json, emb_bytes, face.get("name"), crop_bytes, face.get("prob")))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error saving faces for {photo_path}: {e}")
            self.conn.rollback()

    def write_on_own_connection(self, operation, label="database write"):
        """Run a write on a connection of its own.

        Preferred over write() for anything that runs from a thread pool or a
        background thread: the shared connection has one transaction state, and
        threads writing through it interleave into each other's.
        """
        return tagpup_db.write_with_connection(self.db_path, operation, label=label)

    def write(self, operation, label="database write"):
        """Run a write with every other writer to this database held back.

        The lock is per database file and lives in db.py, so it also holds back
        writers going through a different connection -- which is most of them: both
        servers open their own per request, and every request is its own thread.
        """
        return tagpup_db.write(self.db_path, operation, label=label)

    def save_faces_if_absent(self, photo_path: str, faces: List[Dict[str, Any]]) -> int:
        """Record detected faces only when this photo has none yet. Returns rows inserted.

        Unlike save_faces_for_path this never deletes: that one clears the photo's rows
        first, which would discard manual names and exclusions. This is for callers that
        detected faces as a side effect of doing something else (the tag suggester) and
        want to keep the work without disturbing anything already recorded.

        Uses its own short-lived connection: callers run inside worker pools, and sharing
        one sqlite connection across threads is how "objects created in a thread" errors
        and lock contention start.
        """
        if not faces:
            return 0
        try:
            return self.write(
                lambda: self._insert_faces_if_absent(photo_path, faces),
                label="recording faces for %s" % os.path.basename(photo_path),
            )
        except Exception as e:
            # Only once the retries are spent. Losing the faces for a photo is not a
            # warning-shaped event: they are gone until it is indexed again.
            logger.error(
                "Detected faces for %s were NOT saved (%s). Re-index this folder to "
                "recover them.", photo_path, e
            )
            return 0

    def _insert_faces_if_absent(self, photo_path: str, faces: List[Dict[str, Any]]) -> int:
        """One attempt at the insert above. Separated so it can simply be retried."""
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            configure_connection(conn)
            cursor = conn.cursor()
            clause, params = paths.sql_equals("photo_path", photo_path)
            cursor.execute("SELECT COUNT(*) FROM faces WHERE " + clause, params)
            if cursor.fetchone()[0] > 0:
                return 0  # already recorded; leave it alone

            inserted = 0
            for face in faces:
                emb = face.get("embedding")
                if emb is None:
                    continue
                cursor.execute(
                    "INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob)"
                    " VALUES (?, ?, ?, NULL, ?, ?)",
                    (
                        paths.stored(photo_path),
                        json.dumps(face.get("box", [])),
                        np.array(emb, dtype=np.float32).tobytes(),
                        face.get("crop_image"),
                        face.get("prob"),
                    ),
                )
                inserted += 1
            conn.commit()
            return inserted
        except Exception:
            # Raised on, not swallowed: this runs inside self.write, whose retry
            # waits out a locked database. Catching here returned 0 before the retry
            # ever saw the error, so a moment's lock lost a photo's faces for good.
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn:
                conn.close()

    def get_manual_face_names(self) -> Dict[int, Optional[str]]:
        """face_id -> name for every face a person decided by hand.

        A manual entry with a name of None is a deliberate "this is nobody I want
        labelled" and is just as binding as a manual assignment.
        """
        if self.conn is None:
            return {}
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id, name FROM faces WHERE name_source = 'manual'")
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"Could not read manual face names: {e}")
            return {}

    def get_excluded_face_ids(self) -> Set[int]:
        """Faces marked as not-a-person, which must not influence identity resolution."""
        if self.conn is None:
            return set()
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM faces WHERE excluded = 1")
            return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"Could not read excluded faces: {e}")
            return set()

    def get_all_faces(self) -> List[Dict[str, Any]]:
        """Retrieve all indexed face coordinates and embeddings from the DB."""
        if self.conn is None:
            return []
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id, photo_path, box, embedding, name, prob FROM faces")
            rows = cursor.fetchall()
            
            faces = []
            for face_id, photo_path, box_json, emb_bytes, name, prob in rows:
                box = json.loads(box_json)
                embedding = np.frombuffer(emb_bytes, dtype=np.float32)
                faces.append({
                    "id": face_id,
                    "photo_path": photo_path,
                    "box": box,
                    "embedding": embedding,
                    "name": name,
                    "prob": prob
                })
            return faces
        except Exception as e:
            logger.error(f"Error retrieving faces: {e}")
            return []

    def get_person_centroids(self) -> Dict[str, np.ndarray]:
        """Each named person's mean face embedding, scaled to unit length.

        Reads only the named, non-excluded faces, which idx_faces_identify answers
        without touching the rest of the table. get_all_faces reads every face and its
        crop image -- 225,000 rows and ten seconds on a cold cache -- and the suggester
        called it twice for every photo to use the few that carry a name.
        """
        conn = tagpup_db.connect(self.db_path, timeout=30.0)
        try:
            rows = conn.execute(
                "SELECT name, embedding FROM faces WHERE excluded = 0 AND name IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        by_name: Dict[str, List[np.ndarray]] = {}
        for name, emb_bytes in rows:
            if name and emb_bytes:
                by_name.setdefault(name, []).append(np.frombuffer(emb_bytes, dtype=np.float32))
        centroids = {}
        for name, embs in by_name.items():
            mean = np.mean(embs, axis=0)
            norm = np.linalg.norm(mean)
            if norm > 0:
                centroids[name] = mean / norm
        return centroids

    def save_face_names(self, face_updates: List[Tuple[Optional[str], int]]):
        """Batch update the resolved names of faces by their record ID."""
        if self.conn is None or not face_updates:
            return
        try:
            cursor = self.conn.cursor()
            cursor.executemany("UPDATE faces SET name = ? WHERE id = ?", face_updates)
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error updating face names: {e}")
            self.conn.rollback()
            raise e

    def reset_face_assignments(self):
        """Clear the names clustering gave to faces, and rebuild photos.people.

        Names given by hand (name_source = 'manual') are left alone: they are the
        person's decisions and the anchors clustering starts from. Clearing their name
        while leaving name_source = 'manual' turned every one of them into a binding
        "this is nobody". Returns the number of faces whose name was cleared.
        """
        if self.conn is None:
            return 0
        try:
            cursor = self.conn.cursor()
            # 1. Clear the automatic names only.
            cleared = cursor.execute(
                "UPDATE faces SET name = NULL"
                " WHERE name IS NOT NULL AND COALESCE(name_source, '') <> 'manual'").rowcount

            # 2. Reset photos.people to original tags extracted from raw_metadata
            cursor.execute("SELECT path, raw_metadata, tags FROM photos")
            rows = cursor.fetchall()
            
            from metadata import extract_people
            
            updates = []
            for path, raw_meta_json, tags_json in rows:
                try:
                    raw_meta = json.loads(raw_meta_json) if raw_meta_json else {}
                    tags = json.loads(tags_json) if tags_json else []
                except Exception:
                    raw_meta = {}
                    tags = []
                orig_people = extract_people(raw_meta, tags, db_path=self.db_path, conn=self.conn)
                # Plus the faces still named, which after the clear are the manual ones.
                people = self._with_face_names({"path": path, "people": orig_people})
                updates.append((json.dumps(people), path))

            if updates:
                cursor.executemany("UPDATE photos SET people = ? WHERE path = ?", updates)

            self.conn.commit()
            return cleared
        except Exception as e:
            logger.error(f"Error resetting face assignments in database: {e}")
            if self.conn:
                self.conn.rollback()
            raise e


    def save_faces_batch(self, batch_faces: Dict[str, List[Dict[str, Any]]], overwrite: bool = False):
        """Save detected faces for a batch of photos in a single transaction.

        By default a photo that already has face rows is left alone. Those rows carry
        assigned names, manual overrides, exclusions and cached crops, and re-detection
        produces none of that -- so replacing them silently discards curation. Re-indexing
        a folder used to do exactly that, which mattered little while only tagged photos
        were indexed and matters a great deal now that every photo is.

        Pass overwrite=True to force re-detection, accepting the loss.
        """
        if self.conn is None or not batch_faces:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            for photo_path, faces in batch_faces.items():
                clause, params = paths.sql_equals("photo_path", photo_path)
                if not overwrite:
                    cursor.execute("SELECT COUNT(*) FROM faces WHERE " + clause, params)
                    if cursor.fetchone()[0] > 0:
                        continue
                cursor.execute("DELETE FROM faces WHERE " + clause, params)
                for face in faces:
                    box_json = json.dumps(face["box"])
                    emb_bytes = np.array(face["embedding"], dtype=np.float32).tobytes()
                    crop_bytes = face.get("crop_image")
                    cursor.execute("""
                        INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (paths.stored(photo_path), box_json, emb_bytes, face.get("name"), crop_bytes, face.get("prob")))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error saving faces batch to SQLite: {e}")
            self.conn.rollback()
            raise e

    def migrate_disk_cache_to_sqlite(self, cache_dir: str):
        """Read existing cache .json files, insert them into embedding_cache table, and delete disk files."""
        if self.conn is None or not os.path.exists(cache_dir):
            return

        try:
            json_files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        except Exception as e:
            logger.warning(f"Failed to scan cache directory '{cache_dir}': {e}")
            return

        if not json_files:
            return

        logger.info(f"Found {len(json_files)} cache files in '{cache_dir}'. Starting database migration...")
        
        batch_size = 1000
        cursor = self.conn.cursor()
        
        for idx in range(0, len(json_files), batch_size):
            batch = json_files[idx:idx + batch_size]
            migrated_files = []
            
            try:
                cursor.execute("BEGIN TRANSACTION")
                for filename in batch:
                    filepath = os.path.join(cache_dir, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            
                        # Extract and validate fields
                        path = data.get("path")
                        mtime = data.get("mtime")
                        size = data.get("size")
                        model_name = data.get("model_name")
                        pretrained = data.get("pretrained")
                        preserve_full_frame = 1 if data.get("preserve_full_frame") else 0
                        max_aspect_ratio = data.get("max_aspect_ratio")
                        force_image_size = data.get("force_image_size")
                        embedding = data.get("embedding")
                        
                        if path and embedding:
                            emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
                            cursor.execute("""
                                INSERT OR REPLACE INTO embedding_cache (
                                    path, mtime, size, model_name, pretrained, 
                                    preserve_full_frame, max_aspect_ratio, force_image_size, embedding
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                path, mtime, size, model_name, pretrained,
                                preserve_full_frame, max_aspect_ratio, force_image_size, emb_bytes
                            ))
                            migrated_files.append(filepath)
                    except Exception as e:
                        # Log error and clean up corrupt file to avoid blocking future migrations
                        logger.warning(f"Corrupt or invalid cache file {filename}: {e}. Removing file.")
                        try:
                            os.remove(filepath)
                        except Exception:
                            pass
                
                self.conn.commit()
                
                # Delete files from disk only after successful DB commit
                for filepath in migrated_files:
                    try:
                        os.remove(filepath)
                    except Exception as e:
                        logger.warning(f"Failed to delete migrated cache file {filepath}: {e}")
                        
                logger.info(f"Successfully migrated and cleaned up {len(migrated_files)} cache files.")
            except Exception as e:
                logger.error(f"Failed to migrate batch of cache files: {e}")
                self.conn.rollback()

    def get_tag_embedding(self, tag: str, prompt: str, model_name: str, pretrained: str) -> Optional[List[float]]:
        """Get precomputed tag embedding if it matches active model settings."""
        if not self.conn:
            return None
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT embedding FROM tag_embeddings WHERE tag = ? AND prompt = ? AND model_name = ? AND pretrained = ?",
                (tag, prompt, model_name, pretrained)
            )
            row = cursor.fetchone()
            if row:
                return np.frombuffer(row[0], dtype=np.float32).tolist()
        except Exception as e:
            logger.debug(f"Failed to load tag embedding for '{tag}': {e}")
        return None

    def save_tag_embedding(self, tag: str, prompt: str, model_name: str, pretrained: str, embedding: List[float]):
        """Save precomputed tag embedding."""
        if not self.conn:
            return
        def store(conn):
            emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
            conn.execute(
                """
                INSERT OR REPLACE INTO tag_embeddings (tag, prompt, model_name, pretrained, embedding)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tag, prompt, model_name, pretrained, emb_bytes)
            )

        try:
            self.write_on_own_connection(store, label="tag embedding for '%s'" % tag)
        except Exception as e:
            # Losing a tag embedding costs the next suggestion run the time to
            # recompute it. Not data loss, so this stays a warning.
            logger.warning(f"Failed to save tag embedding for '{tag}': {e}")




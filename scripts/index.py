# index.py
import os
import json
import socket
import sqlite3
import _root  # noqa: F401
import db as tagpup_db
import paths
import logging
import hashlib
import subprocess
import threading
import time
from typing import List, Dict, Any, Tuple, Optional, Set
import numpy as np

from tagpup.ml.vector_index import VectorIndex
from tagpup.store import embeddings as store_embeddings
from tagpup.store import faces as store_faces
from tagpup.store import generations, schema
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy

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


class PhotoIndex:
    """A library's photos in memory, with the nearest-neighbour index over their CLIP
    embeddings, and the reads and writes indexing and clustering make.

    The SQL is tagpup.store's (photos, faces, embeddings, taxonomy) and the vector index
    tagpup.ml.vector_index's; this class joins them, and keeps the names its callers
    have always used.
    """

    def __init__(self, db_path: str = "data/photo_index.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.index: Optional[VectorIndex] = None
        self.metadata: List[Dict[str, Any]] = []
        self.indexed_metadata: List[Dict[str, Any]] = []
        self.dim = 512  # Default
        # One load at a time. TagPup loads its startup library in a background thread,
        # and a Suggest clicked meanwhile checks for changes on the same index.
        self._load_lock = threading.RLock()

    def load(self) -> bool:
        """Connect to SQLite database and build in-memory FAISS index."""
        with self._load_lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> bool:
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            schema.ensure(self.db_path)
            # Reloads (after remove_paths, build_or_update) reuse the connection. Each
            # used to open a new one and drop the old one unclosed, and on Windows an
            # unclosed handle keeps the database file locked. Foreign keys on: deleting
            # a photo's row takes its faces with it.
            if self.conn is None:
                self.conn = tagpup_db.connect(self.db_path, timeout=30.0, check_same_thread=False,
                                              foreign_keys=True)

            # Taken before the rows: a write landing in between costs one reload
            # later, never a missed one. See reload_if_changed.
            self._signature = self._photos_signature()
            self.metadata = []
            self.indexed_metadata = []
            embeddings = []
            for path, mtime, size, tags_json, people_json, captions_json, raw_meta_json, emb_bytes in (
                    store_photos.index_rows(self.conn)):
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
                    embeddings.append(np.frombuffer(emb_bytes, dtype=np.float32))
                    self.indexed_metadata.append(meta_item)

            if embeddings:
                self.dim = len(embeddings[0])
                self.index = VectorIndex(embeddings, self.indexed_metadata)
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
        people = list(meta.get("people", []))
        seen = {p.lower() for p in people}
        for name in store_faces.face_names(meta["path"], conn=self.conn):
            if name.lower() not in seen:
                seen.add(name.lower())
                people.append(name)
        return people

    def build_or_update(self, embeddings: List[List[float]], metas: List[Dict[str, Any]], dim: int = 512, reload: bool = True):
        """Batch insert/update photos inside the SQLite database (transaction-safe)."""
        if not embeddings or self.conn is None:
            return

        from identity import read_document_id
        from metadata import extract_people
        try:
            for meta, emb in zip(metas, embeddings):
                # Who the keywords name depends on this library's taxonomy, which
                # the reader only consults when a caller remembers to pass it. The
                # CLI and both folder indexers did not, and the row lost everyone
                # outside the default roots. Resolve here, against this connection.
                resolved = extract_people(meta.get("raw_metadata", {}), meta.get("tags", []),
                                          conn=self.conn)
                known = {p.lower() for p in meta.get("people", [])}
                meta = dict(meta, people=list(meta.get("people", []))
                            + [p for p in resolved if p.lower() not in known])
                store_photos.record_indexed(self.conn, meta["path"], {
                    "mtime": meta.get("mtime", 0.0),
                    "size": meta.get("size", 0),
                    "tags": meta.get("tags", []),
                    # Keyword people and the photo's named faces: a re-index rebuilt
                    # this from keywords alone and dropped everyone identified only by
                    # their face. See metadata.photo_people.
                    "people": self._with_face_names(meta),
                    "captions": meta.get("captions", []),
                    "raw_metadata": meta.get("raw_metadata", {}),
                    "embedding": np.array(emb, dtype=np.float32).tobytes(),
                    # The photo's identity beside its path. Where the file has one it is
                    # already in raw_metadata; where it does not, the extractor has
                    # minted one into the file by now.
                    "document_id": (meta.get("document_id")
                                    or read_document_id(meta.get("raw_metadata", {}))),
                })
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
        if self.index is None:
            return []
        return self.index.search(query_vector, k)

    def remove_paths(self, paths_to_remove: Set[str]):
        """Remove specific paths from the SQLite database and reload."""
        if self.conn is None or not paths_to_remove:
            return
        try:
            store_photos.remove(self.conn, paths_to_remove)
            self.conn.commit()
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
            store_photos.clear_embeddings(self.conn)
            self.conn.commit()
            self.index = None
            # Reload metadata (has_embedding will be updated to False)
            self.load()
        except Exception as e:
            logger.error(f"Error clearing clip embeddings from SQLite: {e}")
            self.conn.rollback()
            raise e

    def _photos_signature(self):
        """Something that moves whenever a photo row is added, removed or rewritten: the
        photos generation. It was a sum of mtimes, which a change to a row's people
        leaves alone (docs/findings.md, #52)."""
        return generations.value(self.conn, "photos")

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
            store_faces.remove_for_photo(self.conn, photo_path)
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
            store_faces.remove_for_photo(self.conn, photo_path)
            for face in faces:
                self._insert_face(self.conn, photo_path, face, name=face.get("name"))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error saving faces for {photo_path}: {e}")
            self.conn.rollback()

    @staticmethod
    def _insert_face(conn, photo_path, face, name=None):
        store_faces.insert(conn, photo_path, face.get("box", []),
                           np.array(face["embedding"], dtype=np.float32).tobytes(),
                           name=name, crop=face.get("crop_image"), prob=face.get("prob"))

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

        On a connection of its own: callers run inside worker pools, and sharing one
        sqlite connection across threads is how "objects created in a thread" errors
        and lock contention start. Raised on inside the write, not swallowed: its retry
        waits out a locked database, and returning 0 before the retry saw the error
        lost a photo's faces for good.
        """
        if not faces:
            return 0

        def insert(conn):
            if store_faces.count_for_photo(conn, photo_path) > 0:
                return 0  # already recorded; leave it alone
            inserted = 0
            for face in faces:
                if face.get("embedding") is None:
                    continue
                self._insert_face(conn, photo_path, face)
                inserted += 1
            return inserted

        try:
            return self.write_on_own_connection(
                insert, label="recording faces for %s" % os.path.basename(photo_path))
        except Exception as e:
            # Only once the retries are spent. Losing the faces for a photo is not a
            # warning-shaped event: they are gone until it is indexed again.
            logger.error(
                "Detected faces for %s were NOT saved (%s). Re-index this folder to "
                "recover them.", photo_path, e
            )
            return 0

    def get_manual_face_names(self) -> Dict[int, Optional[str]]:
        """face_id -> name for every face a person decided by hand.

        A manual entry with a name of None is a deliberate "this is nobody I want
        labelled" and is just as binding as a manual assignment.
        """
        if self.conn is None:
            return {}
        try:
            return store_faces.manual_names(self.conn)
        except Exception as e:
            logger.warning(f"Could not read manual face names: {e}")
            return {}

    def get_excluded_face_ids(self) -> Set[int]:
        """Faces marked as not-a-person, which must not influence identity resolution."""
        if self.conn is None:
            return set()
        try:
            return store_faces.excluded_ids(self.conn)
        except Exception as e:
            logger.warning(f"Could not read excluded faces: {e}")
            return set()

    def get_all_faces(self) -> List[Dict[str, Any]]:
        """Retrieve all indexed face coordinates and embeddings from the DB."""
        if self.conn is None:
            return []
        try:
            return [{
                "id": face_id,
                "photo_path": photo_path,
                "box": json.loads(box_json),
                "embedding": np.frombuffer(emb_bytes, dtype=np.float32),
                "name": name,
                "prob": prob,
            } for face_id, photo_path, box_json, emb_bytes, name, prob in store_faces.for_clustering(self.conn)]
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
            rows = store_faces.named_embeddings(conn)
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
            store_faces.set_names(self.conn, {face_id: name for name, face_id in face_updates})
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
        from metadata import extract_people
        try:
            cleared = store_faces.clear_automatic_names(self.conn)
            # Each photo's people again: its keyword people, and the faces still named,
            # which after the clear are the manual ones.
            people_by_path = {}
            for path, raw_meta_json, tags_json in store_photos.keyword_sources(self.conn):
                try:
                    raw_meta = json.loads(raw_meta_json) if raw_meta_json else {}
                    tags = json.loads(tags_json) if tags_json else []
                except Exception:
                    raw_meta, tags = {}, []
                orig_people = extract_people(raw_meta, tags, db_path=self.db_path, conn=self.conn)
                people_by_path[path] = self._with_face_names({"path": path, "people": orig_people})
            store_photos.set_people(self.conn, people_by_path)
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
            tagpup_db.begin(self.conn)
            for photo_path, faces in batch_faces.items():
                if not overwrite and store_faces.count_for_photo(self.conn, photo_path) > 0:
                    continue
                store_faces.remove_for_photo(self.conn, photo_path)
                for face in faces:
                    self._insert_face(self.conn, photo_path, face, name=face.get("name"))
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
        for idx in range(0, len(json_files), batch_size):
            batch = json_files[idx:idx + batch_size]
            migrated_files = []

            try:
                tagpup_db.begin(self.conn)
                for filename in batch:
                    filepath = os.path.join(cache_dir, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        path = data.get("path")
                        embedding = data.get("embedding")
                        if path and embedding:
                            store_embeddings.put(self.conn, path, store_embeddings.Cached(
                                data.get("mtime"), data.get("size"), data.get("model_name"),
                                data.get("pretrained"), bool(data.get("preserve_full_frame")),
                                data.get("max_aspect_ratio"), data.get("force_image_size"),
                                np.array(embedding, dtype=np.float32).tobytes()))
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
            emb_bytes = store_taxonomy.tag_embedding(self.conn, tag, prompt, model_name, pretrained)
            if emb_bytes:
                return np.frombuffer(emb_bytes, dtype=np.float32).tolist()
        except Exception as e:
            logger.debug(f"Failed to load tag embedding for '{tag}': {e}")
        return None

    def save_tag_embedding(self, tag: str, prompt: str, model_name: str, pretrained: str, embedding: List[float]):
        """Save precomputed tag embedding."""
        if not self.conn:
            return

        def store(conn):
            store_taxonomy.keep_tag_embedding(conn, tag, prompt, model_name, pretrained,
                                              np.array(embedding, dtype=np.float32).tobytes())

        try:
            self.write_on_own_connection(store, label="tag embedding for '%s'" % tag)
        except Exception as e:
            # Losing a tag embedding costs the next suggestion run the time to
            # recompute it. Not data loss, so this stays a warning.
            logger.warning(f"Failed to save tag embedding for '{tag}': {e}")

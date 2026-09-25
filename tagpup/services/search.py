"""A library's photos as CLIP sees them: each photo's vector, kept in the library, and
the index that finds the photos nearest a vector.

The vectors are tagpup.store.embeddings'; the model that makes them is tagpup.ml.clip's,
handed in; the nearest-neighbour search is tagpup.ml.vector_index's. The embedder read
and wrote the cache itself, a model and a store in one class, and PhotoIndex lived in
scripts/ with a wrapper for every face read and write (docs/ARCHITECTURE.md, "The
layers, revisited").
"""
import json
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from tagpup.files.identity import read_document_id
from tagpup.ml.vector_index import VectorIndex
from tagpup.store import db, generations, schema
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy

logger = logging.getLogger("tagpup_cli.embedder")
index_log = logging.getLogger("tagpup_cli.index")


def stored_mismatch(photo_index, model_name):
    """(stored, made) when the vectors `photo_index` holds for these settings are not the
    length `model_name` makes; None when they are, or when either is not known."""
    # Here, not at the top: open_clip takes seconds to import, and the index needs none of it.
    from tagpup.ml.clip import output_dim

    stored = photo_index.index.d if photo_index.index is not None else None
    made = output_dim(model_name)
    if stored is None or made is None or stored == made:
        return None
    return (stored, made)


class PhotoEmbeddings:
    """The photos' vectors under one CLIP model: the library's when the file is as it
    was when its vector was made, else made by `clip` and kept.

    `photo_index` is the library's (PhotoIndex): its connection is read, and a vector is
    written on a connection of its own. Without one, nothing is kept. `embed` makes a
    vector of a photo, `clip.embed_image` unless given.
    """

    def __init__(self, clip, photo_index=None, embed=None):
        self.clip = clip
        self.photo_index = photo_index
        self.embed = embed or clip.embed_image
        #: The name its vectors are kept under in a library (tagpup.store.embeddings).
        self.model_key = store_embeddings.model_key(**clip.settings)

    def _library(self):
        return self.photo_index is not None and self.photo_index.conn is not None

    def cached(self, file_path):
        """The vector kept in the library for this file under these model settings, if
        the file is as it was when it was computed. Without a library, nothing is kept:
        the JSON files that stood in for one were imported into it, and retired with it
        on 2026-09-24."""
        if not os.path.exists(file_path):
            return None
        if self._library():
            try:
                stat = os.stat(file_path)
                row = store_embeddings.get(self.photo_index.conn, file_path, self.model_key)
                if row and row.mtime == stat.st_mtime and row.size == stat.st_size:
                    return np.frombuffer(row.vector, dtype=np.float32).tolist()
            except Exception as e:
                logger.warning(f"Failed to read/validate database cache for {file_path}: {e}")
        return None

    def keep(self, file_path, embedding, stamp=None):
        """Keep the vector in the library, stamped with `stamp`: the file's (mtime, size)
        as it was opened to be embedded, else as it is now. Taken after, a photo rotated
        while it was embedded kept the vector from before the turn as current (#86)."""
        try:
            if stamp is None:
                stamp = store_embeddings.stamp_of(file_path)
            if stamp is None or not self._library():
                return

            def store(conn):
                store_embeddings.put(conn, file_path, self.model_key, stamp[0], stamp[1],
                                     np.array(embedding, dtype=np.float32).tobytes())

            # On a connection of its own. Every worker in the suggestion pool writes its
            # embedding here, and they were all going through the index's shared
            # connection -- whose single transaction state they interleaved into,
            # telling the loser the database was locked.
            writer = getattr(self.photo_index, "write_on_own_connection", None)
            if writer is not None:
                writer(store, label="embedding cache for %s" % os.path.basename(file_path))
            else:
                store(self.photo_index.conn)
                self.photo_index.conn.commit()
        except Exception as e:
            logger.warning(f"Failed to write cache for {file_path}: {e}")

    def of(self, file_path, force_recompute=False):
        """A photo's vector: the one kept, unless `force_recompute`; else made and kept."""
        if not force_recompute:
            cached = self.cached(file_path)
            if cached is not None:
                return cached
        # Before the file is opened: see keep.
        stamp = store_embeddings.stamp_of(file_path)
        embedding = self.embed(file_path)
        self.keep(file_path, embedding, stamp)
        return embedding


class PhotoIndex:
    """A library's photos in memory, with the nearest-neighbour index over their CLIP
    embeddings under one model, and what indexing writes.

    The SQL is tagpup.store's (photos, embeddings, taxonomy) and the vector index
    tagpup.ml.vector_index's; this class joins them. Its faces are the faces store's and
    tagpup.services.faces': it held a wrapper for each.
    """

    def __init__(self, db_path: str, model: Optional[str] = None):
        self.db_path = db_path
        #: Whose vectors are searched and written: a tagpup.store.embeddings.model_key,
        #: the model's the runtime runs (tagpup.runtime.Runtime.model_key). Vectors from
        #: other settings cannot be compared with a query embedded under these. None
        #: loads the photos without vectors, and refuses to write one.
        self.model = model
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
                self.conn = db.connect(self.db_path, timeout=30.0, check_same_thread=False,
                                              foreign_keys=True)

            # Taken before the rows: a write landing in between costs one reload
            # later, never a missed one. See reload_if_changed.
            self._signature = self._photos_signature()
            self.metadata = []
            self.indexed_metadata = []
            embeddings = []
            for path, mtime, size, tags_json, people_json, captions_json, raw_meta_json, year, emb_bytes in (
                    store_photos.index_rows(self.conn, self.model)):
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
                    # When it was taken, as the library records it (photos.year).
                    "year": year,
                    "has_embedding": has_emb
                }
                self.metadata.append(meta_item)
                if has_emb:
                    embeddings.append(np.frombuffer(emb_bytes, dtype=np.float32))
                    self.indexed_metadata.append(meta_item)

            if embeddings:
                self.dim = len(embeddings[0])
                self.index = VectorIndex(embeddings, self.indexed_metadata)
                index_log.info(f"Loaded {len(self.metadata)} index entries from SQLite.")
            else:
                self.index = None
            return True
        except Exception as e:
            index_log.error(f"Error loading SQLite database: {e}", exc_info=True)
            self.index = None
            self.metadata = []
            self.indexed_metadata = []
            return False

    def build_or_update(self, embeddings: List[List[float]], metas: List[Dict[str, Any]], dim: int = 512, reload: bool = True):
        """Batch insert/update photos inside the SQLite database (transaction-safe)."""
        if not embeddings or self.conn is None:
            return

        try:
            # Who each photo's keywords name depends on this library's tree, read once;
            # the store rebuilds each photo's people from it and the photo's faces.
            known = store_taxonomy.read_people_vocabulary(self.conn)
            for meta, emb in zip(metas, embeddings):
                store_photos.record_indexed(self.conn, meta["path"], {
                    "mtime": meta.get("mtime", 0.0),
                    "size": meta.get("size", 0),
                    "tags": meta.get("tags", []),
                    "captions": meta.get("captions", []),
                    "raw_metadata": meta.get("raw_metadata", {}),
                    "embedding": np.array(emb, dtype=np.float32).tobytes(),
                    # The photo's identity beside its path. Where the file has one it is
                    # already in raw_metadata; where it does not, the extractor has
                    # minted one into the file by now.
                    "document_id": (meta.get("document_id")
                                    or read_document_id(meta.get("raw_metadata", {}))),
                }, model=self.model, known=known)
            self.conn.commit()

            if reload:
                # Reload to rebuild the in-memory FAISS index to reflect the updates
                self.load()
        except Exception as e:
            index_log.error(f"Error saving batch to SQLite: {e}")
            self.conn.rollback()
            raise e

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
            index_log.error(f"Error deleting paths from SQLite: {e}")
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
            index_log.error(f"Error clearing clip embeddings from SQLite: {e}")
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

    def write_on_own_connection(self, operation, label="database write"):
        """Run a write on a connection of its own.

        Preferred over write() for anything that runs from a thread pool or a
        background thread: the shared connection has one transaction state, and
        threads writing through it interleave into each other's.
        """
        return db.write_with_connection(self.db_path, operation, label=label)

    def write(self, operation, label="database write"):
        """Run a write with every other writer to this database held back.

        The lock is per database file and lives in db.py, so it also holds back
        writers going through a different connection -- which is most of them: both
        servers open their own per request, and every request is its own thread.
        """
        return db.write(self.db_path, operation, label=label)

    def get_tag_embedding(self, tag: str, prompt: str, model_name: str, pretrained: str) -> Optional[List[float]]:
        """Get precomputed tag embedding if it matches active model settings."""
        if not self.conn:
            return None
        try:
            emb_bytes = store_taxonomy.tag_embedding(self.conn, tag, prompt, model_name, pretrained)
            if emb_bytes:
                return np.frombuffer(emb_bytes, dtype=np.float32).tolist()
        except Exception as e:
            index_log.debug(f"Failed to load tag embedding for '{tag}': {e}")
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
            index_log.warning(f"Failed to save tag embedding for '{tag}': {e}")

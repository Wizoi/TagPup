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
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from tagpup.core import paths, suggesting
from tagpup.core.fields import read_document_id
from tagpup.files import images, shares, video_frames
from tagpup.ml.vector_index import VectorIndex
from tagpup.store import db, generations, schema
from tagpup.store import derived as store_derived
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photo_fingerprints
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy

logger = logging.getLogger("tagpup_cli.embedder")
index_log = logging.getLogger("tagpup_cli.index")

#: The pictures a clip's vectors are made of: its vector, frame 0's, and the frames kept beside it
#: (store.embeddings.FRAMES), equally spaced through the clip (tagpup.files.video_frames).
CLIP_PICTURES = 1 + store_embeddings.FRAMES


class NoFrames(Exception):
    """ffmpeg gave no frame of a clip: `problem`, its sentence (it names no file), and `whose` (video_frames.THE_FILE:
    the file as it is, found again until it changes; THE_MOMENT or TOO_SLOW: a later read may succeed)."""

    def __init__(self, problem, whose):
        super().__init__(problem)
        self.problem, self.whose = problem, whose

    @property
    def lasting(self):
        """Is it the file's as it is (video_frames.THE_FILE), found again by every read until the file changes?"""
        return self.whose == video_frames.THE_FILE


class FfmpegGone(Exception):
    """ffmpeg, a prerequisite of the tool (DECISIONS 2026-11-06), is missing or does not run while a run needs it for a
    clip's frames: `str()` is the sentence that says what the owner does (config.FFMPEG_MISSING or FFMPEG_BROKEN). Not a
    NoFrames: not the clip's, not a moment's, so no caller marks a clip, keeps a note or tries it again for it; the run
    stops with this sentence, brought up to the owner as an error (DECISIONS 2026-11-13)."""


class PhotoEmbeddings:
    """The photos' vectors under one CLIP model: the library's when the file is as it
    was when its vector was made, else made by `clip` and kept.

    `photo_index` is the library's (PhotoIndex): its connection is read, and a vector is
    written on a connection of its own. Without one, nothing is kept.
    """

    def __init__(self, clip, photo_index=None, remember=True, model_key=None, ffmpeg=None, ffmpeg_problem=None):
        #: None when only the vectors kept are read (`model_key` given): the CLI's index asks what it keeps of each
        #: photo before it builds a model, and an index where every photo is kept builds none (tagpup_cli.py).
        self.clip = clip
        #: tagpup.config.locate_ffmpeg and ffmpeg_problem themselves, asked at each clip (it may go while the run goes
        #: on): a clip's frames are read with what the first answers once the second says None (frames_of). Handed in
        #: by the runtime (Runtime.embeddings, the one maker of one that makes vectors); a run not given them reads no
        #: clip (the index of a library whose clips all hold their vectors, the CLI's read of the vectors kept).
        self.ffmpeg, self.ffmpeg_problem = ffmpeg, ffmpeg_problem
        self.photo_index = photo_index
        #: False: the library's vectors are read, and none is ever written (a Just look run,
        #: tagpup.jobs.suggestions: a vector is a row of the library).
        self.remember = remember
        #: The name its vectors are kept under in a library (tagpup.store.embeddings).
        self.model_key = model_key if clip is None else store_embeddings.model_key(**clip.settings)

    def _library(self):
        return self.photo_index is not None and self.photo_index.conn is not None

    def cached(self, file_path):
        """The vector kept in the library for this file under these model settings, if
        the file is as it was when it was computed. Without a library, nothing is kept:
        the JSON files that stood in for one were imported into it, and retired with it
        on 2026-09-24."""
        vector, _stale = self.kept(file_path, compare=False)
        return vector

    def kept(self, file_path, compare=True):
        """(vector, Stale) of what the library keeps of this file's vector under these model settings, by
        photo_fingerprints.same_picture: (the vector, None) when its stamp describes the file (nothing decoded);
        (None, Stale) when it does not and, with `compare`, the photo has a fingerprint row a decode of the file is to be
        compared with (Stale.same); else (None, None) -- no vector, no row, no file, no library: it is worked out. A
        read that fails (a locked library, a map missing a root) raises: the photo is not embedded again on a guess, and
        a caller that writes (Suggest, the index) stops at it with the sentence (#2732)."""
        file_stamp = shares.known_stamp(file_path)
        if file_stamp is None or not self._library():
            return None, None
        row = store_embeddings.get(self.photo_index.conn, file_path, self.model_key)
        if row is None:
            return None, None
        vector_stamp = (row.mtime, row.size)
        answer = photo_fingerprints.same_picture(vector_stamp, file_stamp)
        if answer == photo_fingerprints.STAMPED:
            return np.frombuffer(row.vector, dtype=np.float32).tolist(), None
        if not compare:
            return None, None
        fingerprint = photo_fingerprints.of_path(self.photo_index.conn, file_path)
        if fingerprint is None:
            return None, None
        return None, Stale(np.frombuffer(row.vector, dtype=np.float32).tolist(), vector_stamp, file_stamp,
                           fingerprint)

    def fingerprint_of(self, file_path):
        """The (key, hash) of the photo's fingerprint row (photo_fingerprints.of_path), or None: what a vector kept by
        its stamp is recorded again with (tagpup_cli.py's index)."""
        if not self._library():
            return None
        found = photo_fingerprints.of_path(self.photo_index.conn, file_path)
        return (found.key, bytes(found.hash)) if found else None

    def keep(self, file_path, embedding, stamp=None, frames=()):
        """Keep the vector in the library, stamped with `stamp`: the file's (mtime, size)
        as it was opened to be embedded, else as it is now. Taken after, a photo rotated
        while it was embedded kept the vector from before the turn as current (#86). A clip's `frames` (its frames 1 to
        n, `vectors` past the first) are kept with it in the same transaction (store.embeddings.put).

        By path, a photo with no row is given one (store.embeddings.put: Suggest's photo of a folder just added); a
        pass over the rows it read keeps by id (keep_batch). No fingerprint is taken here, so the photo's row is
        forgotten (photo_fingerprints.follows): a row of the picture before a crop must not say a vector of the cropped
        picture is the restored one's. The index records the decode's with the vector (PhotoIndex.build_or_update).

        True when the vector was written; False when it was not -- a looking run, a file gone, no library, a row gone,
        or a failed write (logged) -- so a pass can count what it kept rather than what it tried (#1823)."""
        if not self.remember:
            return False
        try:
            if stamp is None:
                stamp = shares.read_stamp(file_path)
            if stamp is None or not self._library():
                return False

            vector = np.array(embedding, dtype=np.float32).tobytes()
            beside = _frame_bytes(frames)

            def store(conn):
                store_embeddings.put(conn, file_path, self.model_key, stamp[0], stamp[1], vector, None, frames=beside,
                                     kind=images.kind_of(file_path))
                return True

            # On a connection of its own. Every worker in the suggestion pool writes its
            # embedding here, and they were all going through the index's shared
            # connection -- whose single transaction state they interleaved into,
            # telling the loser the database was locked.
            writer = getattr(self.photo_index, "write_on_own_connection", None)
            if writer is not None:
                return bool(writer(store, label="embedding cache for %s" % os.path.basename(file_path)))
            kept = store(self.photo_index.conn)
            self.photo_index.conn.commit()
            return kept
        except Exception as e:
            logger.warning(f"Failed to write cache for {file_path}: {e}")
            return False

    def keep_batch(self, found):
        """Keep each of `found` [(photo id, vector, (mtime, size) of the file as it was opened, a clip's frames 1 to n or
        ())] at that row, by id, in ONE transaction (one connection, one take of the write lock, one commit; #1839): a pass over the rows it read
        (tagpup.services.photo_reembed). A row gone meanwhile is not kept, and none is made for it (put_by_id); the
        fingerprint row of each kept is forgotten, as keep's. How many
        were kept; none in a looking run or without a library. A write that fails raises: what earlier batches kept is
        kept, and the photos of this one are made again by the next run."""
        if not found or not self.remember or not self._library():
            return 0
        rows = [(photo_id, np.asarray(vector, dtype=np.float32).tobytes(), stamp, _frame_bytes(frames))
                for photo_id, vector, stamp, frames in found]

        def store(conn):
            return sum(store_embeddings.put_by_id(conn, photo_id, self.model_key, stamp[0], stamp[1], vector, None,
                                                  frames=frames)
                       for photo_id, vector, stamp, frames in rows)

        return self.photo_index.write_on_own_connection(store, label="the vectors of %d photos" % len(rows))

    def of(self, file_path, force_recompute=False, decoded=None):
        """A photo's vector: the one kept, unless `force_recompute`; else made and kept (made(prepared)). `decoded`, a
        list, is extended with a clip's frames when they are read to make it (prepared): Suggest looks at them for the
        clip's people, and reads them again only when the vector was kept (SuggestionModel.suggest)."""
        if not force_recompute:
            cached = self.cached(file_path)
            if cached is not None:
                return cached
            # A model that waits for its turn on the graphics card (a Suggest run's,
            # tagpup.runtime) is waited for first, and the library asked again: the turn
            # may have been the index's, writing this very vector (docs/findings.md, #750).
            ready = getattr(self.clip, "ready", None)
            if callable(ready):
                ready()
                cached = self.cached(file_path)
                if cached is not None:
                    return cached
        return self.made(file_path, self.prepared(file_path, decoded=decoded))

    def prepared(self, file_path, seen=None, load=True, decoded=None):
        """The processor's half of making a photo's vector (Prepared): the file's stamp, taken before it is opened
        (see keep), and what CLIP is shown of the picture decoded from it (ClipModel.input_of, which hands the
        decoded picture to `seen` when given: what the indexer knows of the file from that read). Nothing is kept and
        nothing runs on the graphics card: the indexer prepares the next photos on threads of their own while the card
        is on this one (tagpup_cli.py's index; docs/AI_PIPELINE.md, phase 1b), with `load` False: a read never loads
        the model (ClipModel.picture_input). Raises images.Unreadable for a picture that does not decode.

        A clip (images.is_clip, by its name, as the index gave its row its kind) is shown its CLIP_PICTURES frames,
        read by ffmpeg (video_frames.frames; `seen` is not told, `decoded`, a list, is extended with them): NoFrames
        when ffmpeg gives none."""
        stamp = shares.read_stamp(file_path)
        if images.is_clip(file_path):
            pictures = self.frames_of(file_path)
            if decoded is not None:
                decoded.extend(pictures)
            return Prepared(stamp, [self.clip.picture_input(picture, load=load) for picture in pictures], frames=True)
        return Prepared(stamp, self.clip.input_of(file_path, seen=seen, load=load))

    def frames_bytes(self):
        """What a clip's frames shown CLIP hold at most while they are read ahead: CLIP_PICTURES pictures, each at most
        video_frames.MAX_SIDE on its longer side (ClipModel.input_bytes). The index's read-ahead reserves it."""
        return CLIP_PICTURES * self.clip.input_bytes(video_frames.MAX_SIDE, video_frames.MAX_SIDE)

    def frames_of(self, file_path):
        """The CLIP_PICTURES frames of the clip at `file_path`, upright RGB pictures, fewer for a clip cut short after
        its index (video_frames.frames), on the processor; NoFrames when ffmpeg gives none; FfmpegGone when ffmpeg is
        missing or does not run (config.ffmpeg_problem, asked at each clip: it may go while the run goes on)."""
        gone = self.ffmpeg_problem()
        if gone:
            raise FfmpegGone(gone)
        found = video_frames.frames(self.ffmpeg(), file_path, CLIP_PICTURES)
        if found.problem:
            raise NoFrames(found.problem, found.whose)
        return [images.from_jpeg(picture) for picture in found.pictures]

    def vectors(self, prepared):
        """[vector, then a clip's frames 1 to n] of what `prepared` made, on the model's device: a photo's one, in a
        pass of its own (ClipModel.embed_input); a clip's frames in one pass together (embed_inputs)."""
        if prepared.frames:
            return self.clip.embed_inputs(prepared.image_input)
        return [self.clip.embed_input(prepared.image_input)]

    def opened(self, file_path):
        """(the file's stamp, taken before it is opened, and its picture decoded upright): the decode `prepared` makes,
        for the indexer to take the picture's fingerprint from before it decides whether CLIP is shown it
        (prepared_from) or its bake gives the vector (tagpup.services.bake_reading). Raises images.Unreadable."""
        stamp = shares.read_stamp(file_path)
        return stamp, images.opened(file_path, upright=True)

    def prepared_from(self, stamp, picture, load=False):
        """What `prepared` makes, of a picture `opened` decoded."""
        return Prepared(stamp, self.clip.picture_input(picture, load=load))

    def made(self, file_path, prepared):
        """The vector of a photo `prepared` (Prepared), made on the model's device and kept, stamped with the file as
        it was opened; a clip's with its frames."""
        found = self.vectors(prepared)
        self.keep(file_path, found[0], prepared.stamp, frames=found[1:])
        return found[0]


def _frame_bytes(frames):
    """A clip's frames 1 to n as kept (float32 bytes each)."""
    return [np.asarray(frame, dtype=np.float32).tobytes() for frame in frames]


class Stale:
    """A photo's vector whose stamp no longer describes its file (a write of the file since: its metadata, or its picture),
    with the photo's fingerprint row: `same` says, from a decode of the file, which (photo_fingerprints.same_picture)."""

    def __init__(self, vector, vector_stamp, file_stamp, row):
        self.vector, self.vector_stamp, self.file_stamp, self.row = vector, vector_stamp, file_stamp, row

    def same(self, fingerprint):
        """Is the picture the file's decode shows, whose fingerprint (images.FINGERPRINT_KEY) is `fingerprint`, the one
        the vector was made of: only the file's metadata changed, so the vector is kept and stamped again?"""
        return photo_fingerprints.same_picture(self.vector_stamp, self.file_stamp, self.row,
                                               (images.FINGERPRINT_KEY, fingerprint)) == photo_fingerprints.SAME


class Prepared:
    """A photo made ready for CLIP on the processor (PhotoEmbeddings.prepared): `stamp`, the file's (mtime, size) before
    it was opened, and `image_input`, what the model is shown -- with `frames`, a clip's, the list of its frames'."""

    def __init__(self, stamp, image_input, frames=False):
        self.stamp, self.image_input, self.frames = stamp, image_input, frames

    def of_the_file(self, mtime, size):
        """Was the file read as a record stamped (`mtime`, `size`) describes it (store.photos.describes)? Not when it
        changed between its metadata's read and this one: its vector is not recorded with that record."""
        return store_photos.describes(mtime, size, self.stamp)


def _record(row, people="people"):
    """A photo's record as the index answers it, from store.photos.records' row: the shape
    every caller reads -- "path", "mtime", "size", "tags", "people", "captions",
    "raw_metadata", "year", "has_embedding" -- and its "id". The people are under `people`
    ("people_ids" for the ids of their nodes: PhotoIndex.records)."""
    photo_id, path, mtime, size, tags_json, people_json, captions_json, raw_json, year, vectored = row
    try:
        tags, listed = json.loads(tags_json), json.loads(people_json)
        captions, raw_meta = json.loads(captions_json), json.loads(raw_json)
    except (TypeError, ValueError) as error:
        # Not an empty photo: a neighbour with no tags would be read as one that has none (#2732).
        raise ValueError("photo %s: its row holds text that is not JSON (%s)" % (photo_id, error)) from error
    return {"id": photo_id, "path": path, "mtime": mtime, "size": size, "tags": tags, people: listed,
            "captions": captions, "raw_metadata": raw_meta, "year": year, "has_embedding": bool(vectored)}


class PhotoIndex:
    """A library's photos as CLIP sees them: the nearest-neighbour index over their vectors
    under one model, and what indexing writes.

    In memory it keeps only what a search needs: each vector, by its photo's id -- a clip's
    frames too, each by the clip's id, so that a clip is matched on its best frame (search). What a
    photo holds -- its path, tags, people, captions, raw_metadata, year -- is read from
    the library by id, for a search's results alone (search), or for every photo when a
    caller asks (records). It held every row's record as well, parsed: on photo_index
    about 230 MB beside the vectors' 270, kept for as long as the library was open
    (owner, 2026-09-26: "if everything is going to be a database call anyway, can the
    in-memory cache be minimized when not in use?").

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
        #: The vectors, each item its photo's id: a clip's frames are items of their own, its id repeated, so
        #: `index.ntotal` counts vectors and `indexed` photos.
        self.index: Optional[VectorIndex] = None
        #: How many photos the library holds, and how many of them hold a vector (each counted once).
        self.count = 0
        self.indexed = 0
        self.dim = 512  # Default
        # One load at a time. TagPup loads its startup library in a background thread,
        # and a Suggest clicked meanwhile checks for changes on the same index.
        self._load_lock = threading.RLock()
        # One read at a time on the shared connection: Suggest searches from four threads.
        self._read_lock = threading.Lock()

    def load(self) -> bool:
        """Connect to SQLite database and build in-memory FAISS index."""
        with self._load_lock:
            return self._load_unlocked()

    def open(self) -> bool:
        """Connect, as load does, but read none of the vectors: for a command that needs only
        the connection and the stamps (the indexer, which read 267 MB of vectors to learn
        their length). `index` stays None."""
        with self._load_lock:
            return self._load_unlocked(vectors=False)

    def stamps(self):
        """(id, path, mtime, size, holds a vector) of every photo, read now (store.photos.stamps):
        what the indexer compares with the files. [] when not connected."""
        if self.conn is None:
            return []
        with self._read_lock:
            return store_photos.stamps(self.conn, with_vectors=self.model)

    #: The reason `owed` gives for a photo never examined for faces (photo_fingerprints.UNEXAMINED), for a program that
    #: reaches the store only through this service.
    UNEXAMINED = photo_fingerprints.UNEXAMINED
    #: The reason it gives for a photo whose current vector's picture was never fingerprinted
    #: (photo_fingerprints.NO_FINGERPRINT): the bake after the index writes its file, and its row follows.
    NO_FINGERPRINT = photo_fingerprints.NO_FINGERPRINT

    def owed(self, on_disk, detector):
        """{paths.key: reasons} of the photos of `on_disk` ({key: (path, mtime, size)}: the files as they are) the index
        owes work (photo_fingerprints.owed, the one answer sync gives too): no vector under this index's model, a vector
        whose stamp does not describe its file, a current vector with no fingerprint row, or never examined for faces by
        `detector` (None: not asked). {} when not connected."""
        if self.conn is None:
            return {}
        with self._read_lock:
            found = photo_fingerprints.owed(self.conn, self.model, detector, on_disk)
        return {paths.key(owed.path): owed.reasons for owed in found.values()}

    def current(self, on_disk, owed):
        """The paths.key of each photo of `on_disk` ({key: (path, mtime, size)}: the files as they are) whose row
        describes its file (store.photos.describes: the one answer sync gives too) and that is not among `owed` (the owed
        method's: work the index owes it). [] when not connected."""
        if self.conn is None:
            return set()
        with self._read_lock:
            rows = store_photos.stamps(self.conn)
        return {key for photo_id, path, mtime, size in rows
                if (key := paths.key(path)) in on_disk and key not in owed
                and store_photos.describes(mtime, size, on_disk[key][1:])}

    def _load_unlocked(self, vectors=True) -> bool:
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            schema.ensure(self.db_path)
            # Reloads (after build_or_update) reuse the connection. Each
            # used to open a new one and drop the old one unclosed, and on Windows an
            # unclosed handle keeps the database file locked. Foreign keys on: deleting
            # a photo's row takes its faces with it.
            if self.conn is None:
                self.conn = db.connect(self.db_path, timeout=30.0, check_same_thread=False,
                                              foreign_keys=True)
            return self._read_rows(vectors)
        except Exception as e:
            index_log.error(f"Error loading SQLite database: {e}", exc_info=True)
            self.index = None
            self.count = self.indexed = 0
            return False

    def _read_rows(self, vectors=True) -> bool:
        """Read the photos' vectors on self.conn into memory, by photo id, and a clip's frames beside its vector,
        each by the clip's id (store.photos.frame_vectors); with `vectors` False, none of them (open)."""
        # Taken before the rows: a write landing in between costs one reload
        # later, never a missed one. See reload_if_changed.
        self._signature = self._photos_signature()
        with self._read_lock:
            self.count, _vectored = store_photos.counts(self.conn, self.model)
            found = self._vectors_by_photo() if vectors and self.model is not None else {}
        # A clip's vector and its frames each under its id; their order is search's to make (suggesting.best_frame).
        ids = [photo_id for photo_id, held in found.items() for _vector in held]
        embeddings = [vector for held in found.values() for vector in held]
        self.indexed = len(found)
        del found
        if embeddings:
            self.dim = len(embeddings[0])
            self.index = VectorIndex(embeddings, ids)
            index_log.info(f"Loaded {len(ids)} vectors of {self.indexed} photos of {self.count} from SQLite.")
        else:
            self.index = None
        return True

    def records(self, photo_ids=None, people_ids=False) -> List[Dict[str, Any]]:
        """What each photo holds (see _record), read from the library now: of `photo_ids`,
        in their order and leaving out any with no row, or of every photo. Not kept. With
        `people_ids`, each photo's people are the ids of their nodes, under "people_ids"
        (no names are built), read in the one statement that reads the rest."""
        if self.conn is None:
            return []
        with self._read_lock:
            rows = store_photos.records(self.conn, self.model, photo_ids, people_ids=people_ids)
        found = [_record(row, "people_ids" if people_ids else "people") for row in rows]
        if photo_ids is None:
            return found
        by_id = {record["id"]: record for record in found}
        return [by_id[photo_id] for photo_id in photo_ids if photo_id in by_id]

    def vectors_by_photo(self):
        """{photo id: [its vector, then a clip's frames 1 to n]} (float32 arrays) of every photo holding a vector under
        this index's model, read now on its connection from the rows search reads (store.photos.vectors and
        frame_vectors): a clip is a photo holding more than one. What the measurement of Suggest's neighbour cut for clips
        compares a clip's frame 0 with its best frame on (scripts/measure_pipeline_holdouts.py, clips; findings #2626).
        Asked of an index that is loaded; the one read of them, which loading the index (_read_rows) flattens."""
        with self._read_lock:
            return self._vectors_by_photo()

    def _vectors_by_photo(self):
        """vectors_by_photo, read by a caller holding self._read_lock."""
        rows = store_photos.vectors(self.conn, self.model)
        frames = store_photos.frame_vectors(self.conn, self.model)
        found = {photo_id: [np.frombuffer(emb_bytes, dtype=np.float32)] for photo_id, emb_bytes in rows if emb_bytes}
        for photo_id, emb_bytes in frames:
            found[photo_id].append(np.frombuffer(emb_bytes, dtype=np.float32))
        return found

    def build_or_update(self, embeddings: List[Optional[List[float]]], metas: List[Dict[str, Any]], dim: int = 512):
        """Record each of `metas` with its vector in `embeddings`, in one transaction: None writes no vector (a clip
        whose frames were not had, or whose vector is kept by its stamp: the one it holds stays, frames and all). Each
        meta's "fingerprint", the (key, hash) of the picture its vector was made of (tagpup.files.images.fingerprint),
        is recorded with the vector, a meta with none forgetting the photo's row; a clip's "frames", its frames 1 to n,
        with its vector (store.embeddings.put)."""
        if not metas or self.conn is None:
            return
        self._record(metas, [None if emb is None else np.array(emb, dtype=np.float32).tobytes() for emb in embeddings])

    def _record(self, metas, blobs):
        """Record each of `metas` with its vector's bytes in `blobs` (None: no vector), in one transaction."""
        # A row is stamped with the read it records: field_values.Fields.whole gives every record its mtime and size.
        try:
            # Who each photo's keywords name depends on this library's tree, read once;
            # the store rebuilds each photo's people from it and the photo's faces.
            known = store_taxonomy.read_people_vocabulary(self.conn)
            batch = store_derived.Batch(self.conn)   # the tree and the folders, once for the batch
            # The nodes of the keywords the batch's files hold and the tree lacks, made together (one scan of the library for
            # the batch, not one for each node); the people they make are in the vocabulary read after.
            if store_derived.make_nodes(self.conn, [meta.get("tags", []) for meta in metas], batch):
                known = store_taxonomy.read_people_vocabulary(self.conn)
            batch.prepared = True
            for meta, blob in zip(metas, blobs):
                store_photos.record_indexed(self.conn, meta["path"], {
                    "kind": meta.get("kind"),
                    "mtime": meta["mtime"],
                    "size": meta["size"],
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                    "tags": meta.get("tags", []),
                    "captions": meta.get("captions", []),
                    "raw_metadata": meta.get("raw_metadata", {}),
                    "embedding": blob,
                    "frames": _frame_bytes(meta.get("frames", ())),
                    "fingerprint": meta.get("fingerprint"),
                    # A read that failed for good keeps what the row holds (store.photos.record_indexed).
                    "read_error": meta.get("read_error"),
                    # The photo's identity beside its path. Where the file has one it is
                    # already in raw_metadata; where it does not, the extractor has
                    # minted one into the file by now.
                    "document_id": (meta.get("document_id")
                                    or read_document_id(meta.get("raw_metadata", {}))),
                }, model=self.model, known=known, batch=batch)
            self.conn.commit()
        except Exception as e:
            index_log.error(f"Error saving batch to SQLite: {e}")
            self.conn.rollback()
            raise e

    def search(self, query_vector: List[float], k: int = 15) -> List[Tuple[float, Dict[str, Any]]]:
        """The `k` photos nearest `query_vector`: [(similarity, record)], nearest first, ties by photo id, each
        record read from the library for this answer (records). A clip is matched on its best frame, and is one of
        the `k` once (suggesting.best_frame). A photo removed since the vectors were read is left out."""
        if self.index is None:
            return []
        nearest = suggesting.best_frame(self.index.search(query_vector, k * CLIP_PICTURES), k)
        found = {record["id"]: record for record in self.records([photo_id for _sim, photo_id in nearest])}
        return [(sim, found[photo_id]) for sim, photo_id in nearest if photo_id in found]

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

        A vector or a clip's frames written meanwhile without its photo's row moving (Suggest keeping a vector it
        made) is not searched until the photos generation next moves, the frames as the vectors.
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

"""A new photo model's vectors made for a library's photos, beside its own model's (docs/AI_PIPELINE.md, phase 5): what
the readiness gate of a photo-model switch counts (tagpup.services.settings.photo_switch_on) before it lets the library
switch. The CLI's `embed-photos`; the switch itself is `set-photo-model` (switch), the settings' journaled change.

The vectors are kept under the new model's key (tagpup.store.embeddings.model_key: the library's settings with the
wanted model and its own image size), beside the library's key, which nothing here changes: every reader reads the
library's key alone, so a pass part-way is never read by anything until the switch. A photo holds the new vector when
any vector is kept under the key (store.photos.stamps with_vectors, the gate's count); a stopped pass is taken up where
it stopped, and a second run finds nothing left. Each vector is kept at the row it was read from, by id, a batch
a transaction (PhotoEmbeddings.keep_batch): a photo removed meanwhile is skipped, never given a row back.

A clip (a row of kind 'video') is shown its frames, as the index shows it them (search.PhotoEmbeddings.prepared,
vectors; ffmpeg from the runtime): frame 0's vector is its vector, frames 1 to 4 are kept beside it in the same write.

A file that does not decode, or cannot be read, is counted by why, logged, and tried again by the next run (no list of
skipped photos: DECISIONS 2026-10-17); a file not found is counted as gone. A clip ffmpeg cannot read as it is
(search.NoFrames.lasting) is listed apart, as the index lists it, and not counted as unread: it is the file's, not a
drive away. Gone and unread together past UNREAD_LIMIT
stop the pass (Unreachable, STOPPED): Windows answers an unmounted drive and a share that stopped answering as files
not found, and a library's files really gone are a handful (#1838). Faces are never looked at, and nothing is journaled: the table is derived (DECISIONS
2026-10-16). The pass itself (embed_pass) is the AI pipeline harness's too (scripts/measure_pipeline_holdouts.py).
"""
import collections
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from tagpup.files import images, shares
from tagpup.ml import photo_models
from tagpup.services import search
from tagpup.services import settings as library_settings
from tagpup.services.settings import LibrarySettings
from tagpup.store import db
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy

logger = logging.getLogger("tagpup_cli.embedder")

#: The share of a pass's photos (at least 20) whose files may be not found or not read before it stops: past it, a
#: drive or share is not connected, and the rest would only be counted as gone.
UNREAD_LIMIT = 0.01

#: What a pass stopped by UNREAD_LIMIT says: the photos not found or not read so far, of the pass's.
STOPPED = "%d of %d photo files were not found or could not be read: is the drive or share connected?"

#: Vectors kept a transaction (PhotoEmbeddings.keep_batch): what a write that fails loses at most.
BATCH = 256

#: Seconds a photo, read ahead and embedded by SigLIP 2 SO400M on the owner's card (0.034 to 0.041 measured, phase 5
#: step 2): what the dry run's estimate is made of.
SECONDS_A_PHOTO = 0.04

#: Seconds a clip: ffmpeg's reads of its five frames, on the read-ahead's threads (an estimate, not measured: the dry
#: run's guess for the clips among the rows).
SECONDS_A_CLIP = 2.0

#: The group of settings a switch of photo model acknowledges (validation.SETTING_GROUPS): the consequences the gear's
#: dialog asks the owner to tick, named by the command line that switches.
ACKNOWLEDGED = ("clip",)


class Unreachable(Exception):
    """More of a pass's photo files were not found or could not be read than UNREAD_LIMIT allows (STOPPED). `counts`
    are the pass's until it stopped: what was kept is kept."""

    def __init__(self, message, counts):
        super().__init__(message)
        self.counts = counts


def model_of(text):
    """(model name, weights) of NAME:WEIGHTS, one TagPup knows (tagpup.ml.photo_models.MODELS), or ValueError with the
    sentence to show."""
    name, _colon, weights = (text or "").partition(":")
    pair = (name.strip(), weights.strip())
    if pair not in photo_models.MODELS:
        raise ValueError("TagPup knows no photo model %s; give one of %s." % (
            text, ", ".join("%s:%s" % known for known in sorted(photo_models.MODELS))))
    return pair


def switched_to(model):
    """The settings a switch to the photo model `model` (name, weights) changes: the model, at its own image size."""
    return {"model.name": model[0], "model.pretrained": model[1], "model.force_image_size": ""}


def wanted(own, model):
    """The library's settings `own` (LibrarySettings) switched to the photo model `model` (switched_to): what the new
    vectors are made under, and the settings a switch would hold."""
    return LibrarySettings(dict(own.values, **switched_to(model)))


def _key_of(runtime, library, model):
    own = runtime.settings(library)
    # The library's own model at another image size has no use: a switch is to another model, and index makes the
    # vectors of the library's own.
    if (own.embedder["model_name"], own.embedder["pretrained"]) == model:
        raise ValueError("%s:%s is the library's photo model already: its vectors are made by index." % model)
    settings = wanted(own, model)
    return settings, store_embeddings.model_key(**settings.embedder)


def switch(library, model, apply=False):
    """Switch `library` to the photo model `model` (name, weights) at its own image size, as one journaled change of its
    settings (tagpup.services.settings.change, acknowledging ACKNOWLEDGED): held to the gate of a switch
    (settings.photo_switch_on), which refuses while more than its share of the photos hold no vector under the new key,
    details["photo_vectors"] the counts either way; the undo is held to the same gate (settings.undo_check). A dry run
    unless `apply`. details["tag_words"] is what Suggest holds made under the new model and the library's own, read
    before the change (tag_words). The CLI's `set-photo-model`."""
    words = tag_words(library, model)
    result = library_settings.change(library, switched_to(model), acknowledged=ACKNOWLEDGED, apply=apply)
    result.details["tag_words"] = words
    return result


def tag_words(library, model):
    """{"rows", "words"} of the words Suggest holds made under `model` (tag_embeddings), and "own": the words it holds
    under the library's model now, read-only. Nothing makes them ahead: Suggest makes each word it asks about under the
    library's model at its first run on it, and keeps it."""
    own = library_settings.read(library).embedder
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows, words = store_taxonomy.tag_embedding_counts(conn, *model)
        _rows_own, own_words = store_taxonomy.tag_embedding_counts(conn, own["model_name"], own["pretrained"])
    finally:
        conn.close()
    return {"rows": rows, "words": words, "own": own_words}


def _rows(library, key):
    """[(photo id, path)] of the photos owed a vector (store.photos.owes_a_vector: photos and clips) holding none
    under `key`, by id, and how many hold one, read now."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = [row for row in store_photos.stamps(conn, with_vectors=key, with_kind=True) if store_photos.owes_a_vector(row[4])]
    finally:
        conn.close()
    todo = sorted((photo_id, path) for photo_id, path, _mtime, _size, _kind, held in rows if not held)
    return todo, len(rows) - len(todo)


def plan(library, runtime, model):
    """What a run would do, read-only: {"key", "photos", "held" (already hold a vector under the key), "to_embed",
    "gone" (of those, the file is not there), "seconds" (the estimate)}. ValueError for a model not to be made."""
    _settings, key = _key_of(runtime, library, model)
    todo, held = _rows(library, key)
    there = [path for _photo_id, path in todo if shares.stamp_of(path) is not None]   # a share away counts there
    clips = sum(1 for path in there if images.is_clip(path))   # the pass's own test (PhotoEmbeddings.prepared)
    return {"key": key, "photos": len(todo) + held, "held": held, "to_embed": len(todo), "gone": len(todo) - len(there),
            "seconds": (len(there) - clips) * SECONDS_A_PHOTO + clips * SECONDS_A_CLIP}


def run(library, runtime, model, progress=None, should_stop=None, report=None):
    """Make the vectors under `model` of every photo of `library` that holds none, in a turn of the graphics card
    (waited for in order behind any other process, `report` told who holds it; `should_stop` cancels the wait,
    tagpup.core.gpu_turns.Cancelled). The list is read once the turn is taken: a run that waited behind another finds
    what it made. {"key", "held" (before), and embed_pass's counts}; `progress` is told the counts after each batch kept.
    ValueError for a model not to be made; Unreachable past UNREAD_LIMIT."""
    settings, key = _key_of(runtime, library, model)
    clip = runtime.clip(None, settings)   # None: the library still uses its own model
    index = search.PhotoIndex(library.path, key)
    index.open()
    try:
        embeddings = search.PhotoEmbeddings(clip, index, ffmpeg=runtime.ffmpeg())
        if embeddings.model_key != key:
            raise RuntimeError("the model's vectors would be kept under %s, not %s" % (embeddings.model_key, key))

        def keep(found):
            return embeddings.keep_batch([(photo_id, vector, stamp, frames) for photo_id, stamp, vector, frames in found])

        with runtime.gpu_turn("making the vectors of %s:%s (%s)" % (model[0], model[1], library.name), (clip,),
                              cancelled=should_stop, report=report):
            todo, held = _rows(library, key)
            if todo:
                clip.load()
            counts = embed_pass(embeddings, [(path, photo_id) for photo_id, path in todo], keep,
                                progress=progress, should_stop=should_stop, batch=BATCH)
    finally:
        index.close()
    return dict(counts, key=key, held=held)


def embed_pass(embeddings, todo, keep, progress=None, should_stop=None, workers=6, batch=256,
               unread_limit=UNREAD_LIMIT):
    """Each of `todo` [(file, what keep is told of it)] embedded by `embeddings` (search.PhotoEmbeddings): a photo
    decoded, padded and resized, a clip's frames read by ffmpeg (PhotoEmbeddings.prepared, never loading the model) on
    `workers` threads ahead of the forward on this one, as the indexer reads ahead, and handed to
    `keep([(what, (mtime, size), vector, a clip's frames 1 to n or [])])` -> how many it kept, `batch` at a time, the
    last ones too when the pass is stopped (`should_stop`, asked before each photo) or fails: what was kept is not
    embedded again by the next run. A file not found ("gone"), and a photo that cannot be read (by why, logged), are
    counted apart; more of the two together than `unread_limit` of the pass (at least 20) raise Unreachable. A clip
    ffmpeg cannot read as it is (search.NoFrames.lasting) is neither: its (file, ffmpeg's sentence) joins
    "clips_unread". {"to_embed", "embedded", "kept", "not_kept", "gone", "unread" {why: count}, "clips_unread",
    "stopped", "seconds"}, handed to `progress` after each batch too."""
    counts = {"to_embed": len(todo), "embedded": 0, "kept": 0, "not_kept": 0, "gone": 0, "unread": {},
              "clips_unread": [], "stopped": False, "seconds": 0.0}
    limit = max(20, int(unread_limit * len(todo)))
    started = time.perf_counter()
    waiting = []

    def read(item):
        stat = os.stat(item[0])   # unbounded: the read of the photo's picture, next, has no deadline (by design)
        return (stat.st_mtime, stat.st_size), embeddings.prepared(item[0], load=False)

    def missed(problem=None):
        not_read = counts["gone"] + sum(counts["unread"].values())
        if not_read > limit:
            raise Unreachable(STOPPED % (not_read, len(todo)), counts) from problem

    def not_read(problem):
        why = type(problem).__name__
        logger.warning("A photo was not embedded (%s); the next run tries it again.", why)
        counts["unread"][why] = counts["unread"].get(why, 0) + 1
        missed(problem)

    def hand_over():
        kept = keep(waiting) if waiting else 0
        counts["kept"] += kept
        counts["not_kept"] += len(waiting) - kept
        waiting.clear()
        counts["seconds"] = time.perf_counter() - started

    items = iter(todo)
    pending = collections.deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def fill():
            while len(pending) < 2 * workers:
                item = next(items, None)
                if item is None:
                    return
                pending.append((item, pool.submit(read, item)))
        try:
            fill()
            while pending:
                if should_stop is not None and should_stop():
                    counts["stopped"] = True
                    break
                item, future = pending.popleft()
                fill()
                try:
                    stamp, prepared = future.result()
                except FileNotFoundError as problem:   # a file gone, or a drive or share not connected
                    counts["gone"] += 1
                    missed(problem)
                    continue
                except search.NoFrames as problem:
                    if not problem.lasting:   # the moment's: a share away, ffmpeg too slow
                        not_read(problem)
                        continue
                    logger.info("No vector: ffmpeg gave no frame of %s: %s", item[0], problem.problem)
                    counts["clips_unread"].append((item[0], problem.problem))
                    continue
                except Exception as problem:   # images.Unreadable, a locked file, a share that does not answer
                    not_read(problem)
                    continue
                found = embeddings.vectors(prepared)
                future = prepared = None   # a clip's frames go with them, not held to the next read
                waiting.append((item[1], stamp, found[0], found[1:]))
                counts["embedded"] += 1
                if len(waiting) >= batch:
                    hand_over()
                    if progress is not None:
                        progress(dict(counts))
        finally:
            for _item, future in pending:
                future.cancel()
            hand_over()
    return counts

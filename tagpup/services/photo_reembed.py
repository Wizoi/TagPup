"""A new photo model's vectors made for a library's photos, beside its own model's (docs/AI_PIPELINE.md, phase 5): what
the readiness gate of a photo-model switch counts (tagpup.services.settings.photo_switch_on) before it lets the library
switch. The CLI's `embed-photos`.

The vectors are kept under the new model's key (tagpup.store.embeddings.model_key: the library's settings with the
wanted model and its own image size), beside the library's key, which nothing here changes: every reader reads the
library's key alone, so a pass part-way is never read by anything until the switch. A photo holds the new vector when
any vector is kept under the key (store.photos.stamps with_vectors, the gate's count); a stopped pass is taken up where
it stopped, and a second run finds nothing left. Each vector is kept at the row it was read from, by id
(PhotoEmbeddings.keep with photo_id): a photo removed meanwhile is skipped, never given a row back.

A file that does not decode, or cannot be read, is counted by why, logged, and tried again by the next run (no list of
skipped photos: DECISIONS 2026-10-17); a file gone is counted. More of them than UNREAD_LIMIT stops the pass: a share
that stopped answering. Faces are never looked at, and nothing is journaled: the table is derived (DECISIONS
2026-10-16). The pass itself (embed_pass) is the AI pipeline harness's too (scripts/measure_pipeline_holdouts.py).
"""
import collections
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from tagpup.ml import photo_models
from tagpup.services import search
from tagpup.services.settings import LibrarySettings
from tagpup.store import db
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photos as store_photos

logger = logging.getLogger("tagpup_cli.embedder")

#: The share of a pass's photos that may fail to be read (gone files apart) before it stops: past it, a share stopped
#: answering, and the rest would only be counted as failed.
UNREAD_LIMIT = 0.01

#: Seconds a photo, read ahead and embedded by SigLIP 2 SO400M on the owner's card (0.034 to 0.041 measured, phase 5
#: step 2): what the dry run's estimate is made of.
SECONDS_A_PHOTO = 0.04


class Unreachable(Exception):
    """More of a pass's photos could not be read than UNREAD_LIMIT allows. `counts` are the pass's until it stopped:
    what was kept is kept."""

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


def wanted(own, model):
    """The library's settings `own` (LibrarySettings) with the photo model `model` (name, weights) at its own image
    size: what the new vectors are made under, and the settings a switch would hold."""
    return LibrarySettings(dict(own.values, **{"model.name": model[0], "model.pretrained": model[1],
                                               "model.force_image_size": ""}))


def _key_of(runtime, library, model):
    settings = wanted(runtime.settings(library), model)
    key = store_embeddings.model_key(**settings.embedder)
    if key == runtime.model_key(library):
        raise ValueError("%s:%s is the library's photo model already: its vectors are made by index." % model)
    return settings, key


def _rows(library, key):
    """[(photo id, path)] of the photos holding no vector under `key`, by id, and how many hold one, read now."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = store_photos.stamps(conn, with_vectors=key)
    finally:
        conn.close()
    todo = sorted((photo_id, path) for photo_id, path, _mtime, _size, held in rows if not held)
    return todo, len(rows) - len(todo)


def plan(library, runtime, model):
    """What a run would do, read-only: {"key", "photos", "held" (already hold a vector under the key), "to_embed",
    "gone" (of those, the file is not there), "seconds" (the estimate)}. ValueError for a model not to be made."""
    _settings, key = _key_of(runtime, library, model)
    todo, held = _rows(library, key)
    gone = sum(1 for _photo_id, path in todo if not os.path.exists(path))
    return {"key": key, "photos": len(todo) + held, "held": held, "to_embed": len(todo), "gone": gone,
            "seconds": (len(todo) - gone) * SECONDS_A_PHOTO}


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
        embeddings = search.PhotoEmbeddings(clip, index)
        if embeddings.model_key != key:
            raise RuntimeError("the model's vectors would be kept under %s, not %s" % (embeddings.model_key, key))

        def keep(found):
            return sum(embeddings.keep(path, vector, stamp, photo_id=photo_id) for (photo_id, path), stamp, vector
                       in found)

        with runtime.gpu_turn("making the vectors of %s:%s (%s)" % (model[0], model[1], library.name), (clip,),
                              cancelled=should_stop, report=report):
            todo, held = _rows(library, key)
            if todo:
                clip.load()
            counts = embed_pass(clip, [(path, (photo_id, path)) for photo_id, path in todo], keep,
                                progress=progress, should_stop=should_stop)
    finally:
        index.close()
    return dict(counts, key=key, held=held)


def embed_pass(clip, todo, keep, progress=None, should_stop=None, workers=6, batch=256, unread_limit=UNREAD_LIMIT):
    """Each of `todo` [(file, what keep is told of it)] embedded by `clip`: decoded, padded and resized
    (ClipModel.input_of, never loading the model) on `workers` threads ahead of the forward on this one, as the indexer
    reads ahead, and handed to `keep([(what, (mtime, size), vector)])` -> how many it kept, `batch` at a time, the last
    ones too when the pass is stopped (`should_stop`, asked before each photo) or fails: what was kept is not embedded
    again by the next run. A file gone, and a photo that cannot be read, are counted (the second by why) and logged;
    more than `unread_limit` of the second raise Unreachable. {"to_embed", "embedded", "kept", "not_kept", "gone",
    "unread" {why: count}, "stopped", "seconds"}, handed to `progress` after each batch too."""
    counts = {"to_embed": len(todo), "embedded": 0, "kept": 0, "not_kept": 0, "gone": 0, "unread": {},
              "stopped": False, "seconds": 0.0}
    limit = max(20, int(unread_limit * len(todo)))
    started = time.perf_counter()
    waiting = []

    def read(item):
        stat = os.stat(item[0])
        return item, (stat.st_mtime, stat.st_size), clip.input_of(item[0], load=False)

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
                pending.append(pool.submit(read, item))
        try:
            fill()
            while pending:
                if should_stop is not None and should_stop():
                    counts["stopped"] = True
                    break
                future = pending.popleft()
                fill()
                try:
                    item, stamp, picture = future.result()
                except FileNotFoundError:
                    counts["gone"] += 1
                    continue
                except Exception as problem:   # images.Unreadable, a locked file, a share that does not answer
                    why = type(problem).__name__
                    logger.warning("A photo was not embedded (%s); the next run tries it again.", why)
                    counts["unread"][why] = counts["unread"].get(why, 0) + 1
                    if sum(counts["unread"].values()) > limit:
                        raise Unreachable("%d of %d photos could not be read (%s): the pass stops." % (
                            sum(counts["unread"].values()), len(todo), counts["unread"]), counts) from problem
                    continue
                waiting.append((item[1], stamp, clip.embed_input(picture)))
                counts["embedded"] += 1
                if len(waiting) >= batch:
                    hand_over()
                    if progress is not None:
                        progress(dict(counts))
        finally:
            for future in pending:
                future.cancel()
            hand_over()
    return counts

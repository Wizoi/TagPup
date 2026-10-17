"""Make the vectors of turned photos describe the photo as it is shown (AI pipeline phase 1f; finding #1529).

CLIP is shown a photo upright (images.opened(..., upright=True)), but the vectors made before that rule hold the
sideways picture of a photo whose file declares an EXIF Orientation of 2 to 8, and they were never redone: a vector is
kept while its file's stamp matches. A row does not hold the Orientation (raw_metadata does not read it: 0 of 59,519
rows of photo_index, counted 2026-10-16) and a vector has no age, so the turned photos are found by the header of the
file, and a vector is told stale by making it again and comparing. No doctor check can find one for that reason.
Deleted at phase 5's re-embed.

`repair` is a dry run unless `apply`: it counts. Applied, each turned photo that holds a vector for its file as it is
is embedded again, on a turn of the graphics card taken through the runtime, by PhotoEmbeddings, the one writer of
vectors (it stamps a vector with the file as it was opened, so a photo turned or edited between the look and the embed
is embedded as it now is and stamped so, and one changed during the embed is left stale for the next look to redo).
A re-run makes the vectors again and finds each already matching (cosine UPRIGHT or more); a killed run is resumed by
what the table holds. Face vectors and boxes are not touched: they are as stored on purpose (DECISIONS 2026-10-16,
"turned").

By design: the write is not a journaled change. `embeddings` is derived (ARCHITECTURE.md): the indexer and Suggest
write it unjournaled, and a vector made again is the same file's vector made right, not an edit of the owner's data.
By design: a TIFF is never "turned" here (Pillow applies its Orientation as it loads it, so its boxes turn with it),
though CLIP's upright load transposes it too: 0 of the 17 TIFFs of photo_index declare an Orientation other than 1, and
no other library holds one (counted 2026-10-16), so no branch handles one.
"""
import logging

import numpy as np

from tagpup.files import images
from tagpup.files import shares
from tagpup.services import search
from tagpup.store import db
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photos as store_photos

logger = logging.getLogger(__name__)

#: A vector made again from the upright photo is the stored one when it is this close (phase 0: 0.999996 or more for
#: every photo that was not turned; the turned ones read 0.77 to 0.96).
UPRIGHT = 0.999
BATCH = 1000
#: `progress` is called after this many photos embedded again.
EMBED_PROGRESS = 25


def _turned(library, model, progress):
    """(counts, [(photo id, native path)]) of the photos whose file declares an Orientation of 2 to 8 and that hold a
    vector under `model` for the file as it is now. Read only. A share is asked once (as thumbnails.warm does): a
    photo on one that did not answer is counted as skipped, with the share named in "away", never as unreadable."""
    counts = {"photos": 0, "missing": 0, "failed": 0, "away": [], "skipped_away": 0, "no_header": 0, "turned": 0,
              "no_vector": 0, "stale_stamp": 0, "to_recompute": 0}
    found = []
    away = set()
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        after = 0
        while True:
            batch = store_photos.thumb_rows(conn, None, after, BATCH)
            if not batch:
                break
            after = batch[-1][0]
            for photo_id, _row_path, path in batch:
                counts["photos"] += 1
                share = shares.share_of(path) if shares.on_a_network_drive(path) else None
                if share is not None and share in away:
                    counts["skipped_away"] += 1   # asked once: not asked again for each of its photos
                    continue
                stamp = shares.stamp_of(path)
                if stamp is shares.UNANSWERED:
                    away.add(share)
                    counts["away"].append(share)
                    counts["skipped_away"] += 1
                    continue
                if stamp is shares.CANNOT_READ:
                    counts["failed"] += 1
                    continue
                if stamp is None:
                    counts["missing"] += 1
                    continue
                try:
                    _width, _height, oriented, orientation = images.shown_shape(path)
                except Exception as why:   # a header that does not read: not turned, as far as anything can say
                    logger.info("Photo %d: its header does not read: %s", photo_id, why)
                    counts["no_header"] += 1
                    continue
                if orientation == 1 or oriented:
                    continue
                counts["turned"] += 1
                held = store_embeddings.get_by_id(conn, photo_id, model)
                if held is None:
                    counts["no_vector"] += 1   # the next look at it makes one, upright
                elif not store_photos.describes(held.mtime, held.size, stamp):
                    counts["stale_stamp"] += 1   # the next look makes it again, upright
                else:
                    counts["to_recompute"] += 1
                    found.append((photo_id, path))
            if progress:
                progress(dict(counts))
    finally:
        conn.close()
    return counts, found


def _cosine(a, b):
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def repair(library, runtime, apply=False, progress=None, report=None):
    """Counts of the turned photos of `library` and, with `apply`, of what was done to their vectors:
    {"photos", "missing", "failed", "away" (the shares that did not answer), "skipped_away", "no_header", "turned",
    "no_vector", "stale_stamp", "to_recompute"} always, and with `apply` {"replaced": made again, different, and read
    back as written, "already_upright": made again and the same, "not_kept": made again and not read back (the file
    changed meanwhile, or the write failed: run it again), "damaged": did not decode, "unreadable": any
    other failure (CUDA out of memory, a file gone) with "reasons" counting each by its name}. `runtime` gives the
    model and the turn on the card (tagpup.runtime.Runtime); `report` is told who holds the card while it waits."""
    model = runtime.model_key(library)
    counts, found = _turned(library, model, progress)
    if not apply:
        return counts
    counts.update(replaced=0, already_upright=0, not_kept=0, damaged=0, unreadable=0, reasons={})
    if not found:
        return counts
    index = search.PhotoIndex(library.path, model)
    index.open()
    try:
        embeddings = runtime.embeddings(library, index)
        with runtime.gpu_turn("making the vectors of turned photos again (%s)" % library.name, (embeddings.clip,),
                              report=report):
            embeddings.clip.load()
            for number, (photo_id, path) in enumerate(found, 1):
                held = embeddings.cached(path)
                try:
                    new = embeddings.of(path, force_recompute=True)
                except images.Unreadable:
                    counts["damaged"] += 1
                    continue
                except Exception as why:   # CUDA out of memory, a decompression bomb, a file gone: counted, the run goes on
                    reason = type(why).__name__
                    logger.warning("Photo %d: its vector could not be made again: %s", photo_id, reason)
                    counts["unreadable"] += 1
                    counts["reasons"][reason] = counts["reasons"].get(reason, 0) + 1
                    continue
                kept = embeddings.cached(path)
                if kept is None or not np.allclose(kept, new, atol=1e-6):
                    counts["not_kept"] += 1
                elif held is not None and _cosine(held, new) >= UPRIGHT:
                    counts["already_upright"] += 1
                else:
                    counts["replaced"] += 1
                if progress and number % EMBED_PROGRESS == 0:
                    progress(dict(counts))
    finally:
        index.close()
    return counts

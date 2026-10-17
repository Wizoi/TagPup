"""Make the vectors of turned photos describe the photo as it is shown (AI pipeline phase 1f; finding #1529).

CLIP is shown a photo upright (images.opened(..., upright=True)), but the vectors made before that rule hold the
sideways picture of a photo whose file declares an EXIF Orientation of 2 to 8, and they were never redone: a vector is
kept while its file's stamp matches. A row does not hold the Orientation (raw_metadata does not read it) and a vector
has no age, so the turned photos are found by the header of the file (photos.box_shape, the one reading that says
"turned"), and a vector is told stale by making it again and comparing.

`repair` is a dry run unless `apply`: it counts. Applied, each turned photo that holds a vector for its file as it is
is embedded again, on a turn of the graphics card taken through the runtime, and the vector is replaced only when the
new one differs (cosine below UPRIGHT) and only while the row still holds the stamp it was found with. So a re-run
changes nothing more (it costs the embeds, and writes none), a killed run is resumed by what the table now holds, and a
photo whose file changed meanwhile is that photo's conflict, never a vector of another picture. Face vectors and boxes
are not touched: they are as stored on purpose (DECISIONS 2026-10-16, "turned").
"""
import logging

import numpy as np

from tagpup.files import images
from tagpup.services import photos as photo_service
from tagpup.store import db
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photos as store_photos

logger = logging.getLogger(__name__)

#: A vector made again from the upright photo is the stored one when it is this close (phase 0: 0.999996 or more for
#: every photo that was not turned; the turned ones read 0.77 to 0.96).
UPRIGHT = 0.999
BATCH = 1000


def _turned(library, model, progress):
    """(counts, [(photo id, native path, stamp)]) of the photos whose file declares an Orientation of 2 to 8 and that
    hold a vector under `model` for the file as it is now. Read only."""
    counts = {"photos": 0, "unreached": 0, "turned": 0, "no_vector": 0, "stale_stamp": 0, "to_recompute": 0}
    found = []
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
                stamp = store_embeddings.stamp_of(path)
                shape = photo_service.box_shape(path) if stamp is not None else None
                if shape is None or not shape["size"]:
                    counts["unreached"] += 1   # no file, a share away, or a header that does not read
                    continue
                if not shape["turned"]:
                    continue
                counts["turned"] += 1
                held = store_embeddings.get_by_id(conn, photo_id, model)
                if held is None:
                    counts["no_vector"] += 1   # the next look at it makes one, upright
                elif not store_photos.describes(held.mtime, held.size, stamp):
                    counts["stale_stamp"] += 1   # the next look makes it again, upright
                else:
                    counts["to_recompute"] += 1
                    found.append((photo_id, path, stamp))
            if progress:
                progress(dict(counts))
    finally:
        conn.close()
    return counts, found


def _cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def repair(library, runtime, apply=False, progress=None):
    """Counts of the turned photos of `library` and, with `apply`, of what was done to their vectors:
    {"photos", "unreached", "turned", "no_vector", "stale_stamp", "to_recompute"} always, and with `apply`
    {"replaced": vectors written, counted by the rows changed, "already_upright", "conflict": the file or its vector
    changed since it was found, "damaged": did not decode, "failed": could not be read, "verified": replaced vectors
    read back as written}. `runtime` gives the model and the turn on the card (tagpup.runtime.Runtime)."""
    model = runtime.model_key(library)
    counts, found = _turned(library, model, progress)
    if not apply:
        return counts
    counts.update(replaced=0, already_upright=0, conflict=0, damaged=0, failed=0, verified=0)
    if not found:
        return counts
    clip = runtime.clip(library)
    written = {}
    with runtime.gpu_turn("making the vectors of turned photos again (%s)" % library.name, (clip,)):
        clip.load()
        for photo_id, path, stamp in found:
            try:
                if store_embeddings.stamp_of(path) != stamp:
                    counts["conflict"] += 1
                    continue
                new = np.asarray(clip.embed_image(path), dtype=np.float32)
                if store_embeddings.stamp_of(path) != stamp:
                    counts["conflict"] += 1   # turned again while it was embedded: this is the picture before
                    continue
            except images.Unreadable:
                counts["damaged"] += 1
                continue
            except OSError as why:
                logger.warning("Photo %d could not be read to make its vector again: %s", photo_id, why)
                counts["failed"] += 1
                continue
            conn = db.connect(db.readonly_uri(library.path), uri=True)
            try:
                held = store_embeddings.get_by_id(conn, photo_id, model)
            finally:
                conn.close()
            if held is None or not store_photos.describes(held.mtime, held.size, stamp):
                counts["conflict"] += 1
                continue
            if _cosine(np.frombuffer(held.vector, dtype=np.float32), new) >= UPRIGHT:
                counts["already_upright"] += 1
                continue
            vector = new.tobytes()
            changed = db.write_with_connection(
                library.path, lambda conn: store_embeddings.replace_current(conn, photo_id, model, held, vector),
                label="a turned photo's vector")
            if changed:
                counts["replaced"] += 1
                written[photo_id] = vector
            else:
                counts["conflict"] += 1
            if progress:
                progress(dict(counts))
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        for photo_id, vector in written.items():
            now = store_embeddings.get_by_id(conn, photo_id, model)
            counts["verified"] += 1 if now is not None and now.vector == vector else 0
    finally:
        conn.close()
    return counts

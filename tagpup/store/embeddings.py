"""The embeddings table: each photo's CLIP vector, one per model, with the stamp of the
file it was computed from.

It replaces two copies (docs/findings.md, #62): `photos.embedding`, which search read
and nothing re-checked, and `embedding_cache`, keyed by path and by the file's mtime
and size, which Suggest read. A keyword write changes the mtime, so Suggest embedded the
photo again; a rotation left the search vector describing the picture before the turn;
and a change of model settings replaced one copy and not the other.

Here a vector belongs to a photo's row, by id, and goes with it: a trigger deletes it
with the photo, whichever connection deletes it (#65), and a rename moves nothing. Its
stamp is the file's mtime and size when it was computed. A write of the app's own that
changes only metadata carries the stamp forward (`restamp`), from the stamp the file had
just before the write -- never the row's, which lags a file changed elsewhere (#84); one
that changes how the photo looks takes the vector away (`forget`). A file changed any
other way no longer matches its stamp, and is embedded again.

A video clip has more than one picture: its vector is its frame 0, and its frames 1 to FRAMES are rows of
`embedding_frames` beside it (migration 38), written with it by `put` and `put_by_id` and taken with it, or with the
photo, by the two triggers. A photo has none.
"""
import collections

from tagpup.store import roots as store_roots

#: A photo's vector for one model, and the stamp of the file it was computed from.
#: `vector` is float32 bytes.
Stored = collections.namedtuple("Stored", "mtime size vector")

#: The most frames a vector carries beside it: a clip's frames 1 to 4, its frame 0 being the vector itself.
FRAMES = 4


def model_key(model_name, pretrained, preserve_full_frame, max_aspect_ratio, force_image_size):
    """The one name of a set of model settings. Two vectors compare only under the same
    key: each of the five changes what a photo embeds to."""
    return "%s|%s|%s|%r|%s" % (model_name, pretrained, "full-frame" if preserve_full_frame else "cropped",
                               float(max_aspect_ratio), force_image_size or "native")


def _photo(conn, photo_path):
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    return "photo_id IN (SELECT id FROM photos WHERE %s)" % where, params


def get(conn, photo_path, model):
    """The Stored vector of one photo under `model`, or None."""
    where, params = _photo(conn, photo_path)
    row = conn.execute("SELECT mtime, size, vector FROM embeddings WHERE " + where + " AND model = ?",
                       params + (model,)).fetchone()
    return Stored(*row) if row else None


def put(conn, photo_path, model, mtime, size, vector, fingerprint, frames=()):
    """Keep `vector` as the photo's under `model`, computed from the file at `mtime` and
    `size`, replacing any before it. A photo with no row gets one (photos.ensure_row).
    `fingerprint`, required, is the (key, hash) of the picture the vector was made of, recorded as the photo's row, or
    None, which forgets the row (photo_fingerprints.follows). `frames`, a clip's, are its frames 1 to n (at most FRAMES,
    float32 bytes each), replacing all it had under `model`; a photo has none. The caller commits."""
    from tagpup.store import photos   # photos imports this module
    frames = _checked(frames)
    photo_id = photos.ensure_row(conn, photo_path)
    _drop_frames(conn, photo_id, model)
    conn.execute("INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, size, vector)"
                 " VALUES (?, ?, ?, ?, ?)", (photo_id, model, mtime, size, vector))
    _put_frames(conn, photo_id, model, frames)
    _follows(conn, photo_id, fingerprint)


def put_by_id(conn, photo_id, model, mtime, size, vector, fingerprint, frames=()):
    """Keep `vector` as the photo `photo_id`'s under `model`, as put does, with its `fingerprint` and `frames` (as put),
    while the photo has a row: True when it was written, False when the row is gone (removed by a sync or a delete
    meanwhile), and no row is made for it -- a pass over the rows it read (tagpup.services.photo_reembed) never brings
    back one taken away. The caller commits."""
    frames = _checked(frames)
    _drop_frames(conn, photo_id, model)
    written = conn.execute("INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, size, vector)"
                           " SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM photos WHERE id = ?)",
                           (photo_id, model, mtime, size, vector, photo_id)).rowcount > 0
    if written:
        _put_frames(conn, photo_id, model, frames)
        _follows(conn, photo_id, fingerprint)
    return written


def _checked(frames):
    frames = tuple(frames)
    if len(frames) > FRAMES:
        raise ValueError("a vector carries at most %d frames beside it, not %d" % (FRAMES, len(frames)))
    return frames


def _drop_frames(conn, photo_id, model):
    # A replaced vector fires no delete trigger (INSERT OR REPLACE, recursive_triggers off): its frames go here.
    conn.execute("DELETE FROM embedding_frames WHERE photo_id = ? AND model = ?", (photo_id, model))


def _put_frames(conn, photo_id, model, frames):
    conn.executemany("INSERT INTO embedding_frames (photo_id, model, frame, vector) VALUES (?, ?, ?, ?)",
                     [(photo_id, model, number, frame) for number, frame in enumerate(frames, 1)])


def frames_of(conn, photo_id, model):
    """[vector] of a photo's frames 1 to n under `model`, in order, by photo id: a clip's; [] for a photo."""
    return [vector for (vector,) in conn.execute(
        "SELECT vector FROM embedding_frames WHERE photo_id = ? AND model = ? ORDER BY frame", (photo_id, model))]


def _follows(conn, photo_id, fingerprint):
    from tagpup.store import photo_fingerprints   # it imports photos, which imports this module
    photo_fingerprints.follows(conn, photo_id, fingerprint)


def restamp(conn, photo_id, before, after):
    """A write of the app's own changed only a photo's metadata, and the file's stamp
    from `before` -- read from the file just before the write -- to `after`, each
    (mtime, size): its vectors computed from the file as it was are still its vectors.
    Only those stamped `before`; one already stale stays so. Returns rows changed. The
    caller commits."""
    if before is None or after is None or before == after or None in before:
        return 0
    return conn.execute("UPDATE embeddings SET mtime = ?, size = ? WHERE photo_id = ? AND mtime = ? AND size = ?",
                        tuple(after) + (photo_id,) + tuple(before)).rowcount


def forget(conn, photo_path):
    """Take away a photo's vectors: it now looks different (a rotation). Returns rows
    deleted. The caller commits."""
    where, params = _photo(conn, photo_path)
    return conn.execute("DELETE FROM embeddings WHERE " + where, params).rowcount


def models_of(conn, photo_id):
    """The models a photo has a vector under, by photo id."""
    return {model for (model,) in conn.execute("SELECT model FROM embeddings WHERE photo_id = ?", (photo_id,))}


def lacking(conn, photo_id, held):
    """[(model, mtime, size, vector)] of a photo's vectors under a model not in `held`, by photo id: the vectors
    another photo of the same file has none of. Only those are read."""
    held = sorted(held)
    where = " AND model NOT IN (%s)" % ",".join("?" * len(held)) if held else ""
    return conn.execute("SELECT model, mtime, size, vector FROM embeddings WHERE photo_id = ?" + where,
                        (photo_id,) + tuple(held)).fetchall()


def count(conn, model):
    """How many photos have a vector under `model`."""
    return conn.execute("SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)).fetchone()[0]


def key_totals(conn):
    """{key: (vectors, bytes)} of every key the library holds vectors under: one scan of the table, the vectors' lengths
    read and not the vectors (2.6 s cold on photo_index, 2026-10-24). What a dry run of retiring a photo model's vectors
    reads (tagpup.services.retire_photo_vectors)."""
    return {model: (count, int(size)) for model, count, size in conn.execute(
        "SELECT model, COUNT(*), TOTAL(LENGTH(vector)) FROM embeddings GROUP BY model")}


def model_of(key):
    """(name, pretrained) of the photo model a key (model_key) names: the first two of its five parts."""
    name, pretrained = key.split("|")[:2]
    return name, pretrained


def next_under(conn, key, after, limit):
    """[(rowid, bytes)] of the next `limit` vectors under `key` above rowid `after`, by rowid: a search of the integer
    primary key, the vectors' lengths and not the vectors."""
    return conn.execute("SELECT rowid, LENGTH(vector) FROM embeddings WHERE model = ? AND rowid > ? ORDER BY rowid"
                        " LIMIT ?", (key, after, limit)).fetchall()


def delete_between(conn, key, after, last):
    """Delete the vectors under `key` whose rowid is above `after` and at most `last` (next_under's, read in the same
    transaction). Returns the rows deleted. The caller commits."""
    return conn.execute("DELETE FROM embeddings WHERE model = ? AND rowid > ? AND rowid <= ?",
                        (key, after, last)).rowcount

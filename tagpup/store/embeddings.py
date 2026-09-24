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
"""
import collections
import os

from tagpup.core import paths

#: A photo's vector for one model, and the stamp of the file it was computed from.
#: `vector` is float32 bytes.
Stored = collections.namedtuple("Stored", "mtime size vector")


def model_key(model_name, pretrained, preserve_full_frame, max_aspect_ratio, force_image_size):
    """The one name of a set of model settings. Two vectors compare only under the same
    key: each of the five changes what a photo embeds to."""
    return "%s|%s|%s|%r|%s" % (model_name, pretrained, "full-frame" if preserve_full_frame else "cropped",
                               float(max_aspect_ratio), force_image_size or "native")


def _photo(photo_path):
    where, params = paths.sql_equals("path", photo_path)
    return "photo_id IN (SELECT id FROM photos WHERE %s)" % where, params


def get(conn, photo_path, model):
    """The Stored vector of one photo under `model`, or None."""
    where, params = _photo(photo_path)
    row = conn.execute("SELECT mtime, size, vector FROM embeddings WHERE " + where + " AND model = ?",
                       params + (model,)).fetchone()
    return Stored(*row) if row else None


def put(conn, photo_path, model, mtime, size, vector):
    """Keep `vector` as the photo's under `model`, computed from the file at `mtime` and
    `size`, replacing any before it. A photo with no row gets one (photos.ensure_row).
    The caller commits."""
    from tagpup.store import photos   # photos imports this module
    photo_id = photos.ensure_row(conn, photo_path)
    conn.execute("INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, size, vector)"
                 " VALUES (?, ?, ?, ?, ?)", (photo_id, model, mtime, size, vector))


def stamp_of(photo_path):
    """(mtime, size) of the file now, or None when it cannot be read: what a writer takes
    just before it writes, to carry the photo's vectors over its write."""
    try:
        stat = os.stat(photo_path)
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


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
    where, params = _photo(photo_path)
    return conn.execute("DELETE FROM embeddings WHERE " + where, params).rowcount


def clear(conn):
    """Forget every photo's vectors. Returns rows deleted. The caller commits."""
    return conn.execute("DELETE FROM embeddings").rowcount


def count(conn, model):
    """How many photos have a vector under `model`."""
    return conn.execute("SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)).fetchone()[0]

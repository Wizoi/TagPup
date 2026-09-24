"""embedding_cache: each photo's CLIP embedding, with the file stamp and model settings
it was computed under, so it is computed once while none of them changes.

Keyed by path until phase 4 gives photos ids. The rows move and go with their photo
in tagpup.store.photos (move_rows, forget_photo).
"""
import collections

from tagpup.core import paths

#: A cached embedding and what it was computed under. `embedding` is float32 bytes.
Cached = collections.namedtuple(
    "Cached", "mtime size model_name pretrained preserve_full_frame max_aspect_ratio"
              " force_image_size embedding")


def cached(conn, photo_path):
    """The cached embedding of one photo, or None. By equality, which the index serves."""
    clause, params = paths.sql_equals("path", photo_path)
    row = conn.execute(
        "SELECT mtime, size, model_name, pretrained, preserve_full_frame, max_aspect_ratio,"
        " force_image_size, embedding FROM embedding_cache WHERE " + clause, params).fetchone()
    return Cached(*row) if row else None


def put(conn, photo_path, entry):
    """Keep `entry` (a Cached) as the embedding of one photo, replacing any before it.
    The caller commits."""
    conn.execute(
        "INSERT OR REPLACE INTO embedding_cache (path, mtime, size, model_name, pretrained,"
        " preserve_full_frame, max_aspect_ratio, force_image_size, embedding)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (paths.stored(photo_path), entry.mtime, entry.size, entry.model_name, entry.pretrained,
         1 if entry.preserve_full_frame else 0, entry.max_aspect_ratio, entry.force_image_size,
         entry.embedding))

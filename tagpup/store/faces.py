"""The faces table.

The names a photo's faces were given, turning their boxes when a photo is turned, a
face's crop, a write to the table that the Identify Faces grids can account for, and
what indexing and clustering read and record. The servers' queries move here in
phase 3 (ARCHITECTURE.md).
"""
import contextlib
import json
import logging
import os
import types

from tagpup.core import paths
from tagpup.store import db, generations

logger = logging.getLogger(__name__)


def names_in_photo(conn, photo_path):
    """The names given to faces in one photo, on `conn`. By equality, which the index on
    photo_path serves."""
    where, params = paths.sql_equals("photo_path", photo_path)
    return {name for (name,) in conn.execute(
        "SELECT name FROM faces WHERE " + where + " AND name IS NOT NULL", params)}


def generation(conn):
    """The faces table's generation (tagpup.store.generations), or 0 on a library that
    does not count it yet. It moves when a name changes, which none of the table's
    counts need to."""
    return generations.value(conn, "faces")


def fingerprint(conn):
    """A cheap signature of the faces table, which moves whenever a face is added, named,
    renamed, given to someone else or excluded. TagTuner caches its Identify Faces grids
    against it."""
    # SUM(excluded) matters: excluding an unnamed face changes what the queue should show
    # without changing the row count, the name count, or the maximum id, so leaving it
    # out serves a stale queue after every exclusion.
    row = conn.execute(
        "SELECT COUNT(*), COUNT(name), COALESCE(MAX(id), 0), COALESCE(SUM(excluded), 0) FROM faces"
    ).fetchone()
    return (tuple(row) if row else (0, 0, 0, 0)) + (generation(conn),)


@contextlib.contextmanager
def accounted_write(db_path, label="faces write"):
    """A write to the faces table whose effect alone the fingerprint's change describes.

    Holds the library's write lock and one IMMEDIATE transaction. Yields `write`, with
    `conn` and `before`, the fingerprint as the write began; `after` is read just before
    the commit. Nobody else can write between the two, so a cache that drops the faces
    this write took out of the pool may re-stamp itself with `after`: the difference is
    this write, and no batch the indexer or TagPup committed meanwhile. Reading them
    before the lock and after the commit stamped such a batch as accounted for, and its
    faces never reached the grid. Rolled back if the block raises.
    """
    with db.lock_for(db_path):
        conn = db.connect(db_path, timeout=30.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            write = types.SimpleNamespace(conn=conn, before=fingerprint(conn), after=None)
            try:
                yield write
            except BaseException:
                conn.rollback()
                raise
            write.after = fingerprint(conn)
            conn.commit()
            logger.debug("%s committed", label)
        finally:
            conn.close()


def face_names(photo_path, db_path=None, conn=None):
    """The names given to a photo's faces, excluded faces left out, in detection order.

    From `conn`, else from the library at `db_path`, read-only. Nothing when neither
    can be read: a photo's people are its keywords' then, rather than an error.
    """
    own = None
    try:
        if conn is None:
            if not db_path or not os.path.exists(db_path):
                return []
            own = conn = db.connect(db.readonly_uri(db_path), uri=True)
        clause, params = paths.sql_equals("photo_path", photo_path)
        rows = conn.execute(
            "SELECT name FROM faces WHERE " + clause
            + " AND name IS NOT NULL AND COALESCE(excluded, 0) = 0 ORDER BY id", params).fetchall()
        return [name for (name,) in rows if name]
    except Exception as e:
        logger.warning("Could not read face names for %s: %s", photo_path, e)
        return []
    finally:
        if own is not None:
            own.close()


def turned_box(box, direction, width, height):
    """A face box after a quarter turn of a `width` x `height` image.

    Left is counter-clockwise, as Image.rotate(90, expand=True) turns it.
    """
    x1, y1, x2, y2 = box[:4]
    if direction == "left":
        return [y1, width - x2, y2, width - x1]
    return [height - y2, x1, height - y1, x2]


def turn_boxes(db_path, photo_path, direction, width, height):
    """Turn a photo's stored face boxes with it. Returns how many rows changed.

    Only for a photo Pillow shows already oriented (a TIFF: Pillow applies its
    Orientation on load), where every box -- and every crop cut from it -- is in the
    turned picture's coordinates once the Orientation changes. The cached crop is
    dropped so the next request cuts it again from the right place.
    """
    where, where_params = paths.sql_equals("photo_path", photo_path)

    def turn(conn):
        cursor = conn.cursor()
        rows = cursor.execute("SELECT id, box FROM faces WHERE " + where, where_params).fetchall()
        changed = 0
        for face_id, box_json in rows:
            try:
                box = json.loads(box_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(box, list) or len(box) < 4:
                continue
            cursor.execute("UPDATE faces SET box = ?, crop_image = NULL WHERE id = ?",
                           (json.dumps(turned_box(box, direction, width, height)), face_id))
            changed += cursor.rowcount
        return changed

    return db.write_with_connection(
        db_path, turn, label="face boxes for rotated %s" % os.path.basename(photo_path))


def crop_of(db_path, face_id):
    """(photo path, box, cached crop) of a face, or None if there is no such face -- or
    no such library, which connecting would create."""
    if not os.path.exists(db_path):
        return None
    conn = db.connect(db_path, timeout=30.0)
    try:
        row = conn.execute("SELECT photo_path, box, crop_image FROM faces WHERE id = ?",
                           (face_id,)).fetchone()
    finally:
        conn.close()
    return tuple(row) if row else None


def cache_crop(db_path, face_id, jpeg):
    """Keep a face's crop in its row, so it is cut from the photo once. Returns rows changed.

    Through the write lock: both servers used to write it back on their own connection."""
    def store(conn):
        return conn.execute("UPDATE faces SET crop_image = ? WHERE id = ?",
                            (jpeg, face_id)).rowcount

    return db.write_with_connection(db_path, store, label="crop of face %s" % face_id)


# ---- What PhotoIndex and clustering read and write ---------------------------------------

def count_for_photo(conn, photo_path):
    """How many face rows a photo has."""
    where, params = paths.sql_equals("photo_path", photo_path)
    return conn.execute("SELECT COUNT(*) FROM faces WHERE " + where, params).fetchone()[0]


def remove_for_photo(conn, photo_path):
    """Delete a photo's face rows, names and decisions with them. Returns rows deleted.
    The caller commits."""
    where, params = paths.sql_equals("photo_path", photo_path)
    return conn.execute("DELETE FROM faces WHERE " + where, params).rowcount


def insert(conn, photo_path, box, embedding, name=None, crop=None, prob=None):
    """Record one detected face: `box` as a list, `embedding` as float32 bytes. The caller
    commits."""
    conn.execute(
        "INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob) VALUES (?, ?, ?, ?, ?, ?)",
        (paths.stored(photo_path), json.dumps(box), embedding, name, crop, prob))


def manual_names(conn):
    """face id -> name for every face a person decided by hand. A name of None is a
    deliberate "this is nobody", as binding as a name."""
    return dict(conn.execute("SELECT id, name FROM faces WHERE name_source = 'manual'").fetchall())


def excluded_ids(conn):
    """The faces kept out of identity work."""
    return {face_id for (face_id,) in conn.execute("SELECT id FROM faces WHERE excluded = 1")}


def for_clustering(conn):
    """(id, photo_path, box JSON, embedding bytes, name, prob) of every face. The crop is
    left behind: 6 KB a face, and clustering never looks at it."""
    return conn.execute("SELECT id, photo_path, box, embedding, name, prob FROM faces").fetchall()


def named_embeddings(conn):
    """(name, embedding bytes) of every named face that is not excluded, which
    idx_faces_identify answers without touching the rest of the table."""
    return conn.execute(
        "SELECT name, embedding FROM faces WHERE excluded = 0 AND name IS NOT NULL").fetchall()


def in_photo(conn, photo_path):
    """(box JSON, embedding bytes, prob, excluded, name, name_source) of each face in one
    photo, for suggesting who is in it."""
    where, params = paths.sql_equals("photo_path", photo_path)
    return conn.execute(
        "SELECT box, embedding, prob, excluded, name, name_source FROM faces WHERE " + where,
        params).fetchall()


def set_names(conn, names_by_id):
    """Give each face in {id: name} its name, leaving who decided it alone. The caller
    commits."""
    conn.executemany("UPDATE faces SET name = ? WHERE id = ?",
                     [(name, face_id) for face_id, name in names_by_id.items()])


def clear_automatic_names(conn):
    """Clear the names clustering gave, leaving the ones given by hand. Returns how many
    were cleared. The caller commits."""
    return conn.execute(
        "UPDATE faces SET name = NULL"
        " WHERE name IS NOT NULL AND COALESCE(name_source, '') <> 'manual'").rowcount

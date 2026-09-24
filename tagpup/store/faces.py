"""The faces table.

For now, the names a photo's faces were given, and turning their boxes when a photo
is turned. The rest of the table's queries, in scripts/index.py and the servers, move
here with the store step of phase 2 (ARCHITECTURE.md).
"""
import json
import logging
import os

from tagpup.core import paths
from tagpup.store import db

logger = logging.getLogger(__name__)


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

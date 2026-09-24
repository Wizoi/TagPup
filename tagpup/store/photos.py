"""The photos table.

For now, recording a file's new mtime and size after a write, and forgetting a
deleted photo. The rest of the table's queries, in scripts/index.py and the servers,
move here with the store step of phase 2 (ARCHITECTURE.md).
"""
import logging
import os

from tagpup.core import paths
from tagpup.store import db

logger = logging.getLogger(__name__)


def record_file_stat(db_path, photo_path):
    """Record a photo's current mtime and size in its index row. Returns rows changed.

    For a write that changes the file but not what the index describes -- a rotation
    changes only the Orientation tag. Left stale, the folder scan would distrust the
    row and re-read the photo with ExifTool on every scan.
    """
    stat = os.stat(photo_path)
    where, where_params = paths.sql_equals("path", photo_path)

    def store(conn):
        cursor = conn.execute("UPDATE photos SET mtime = ?, size = ? WHERE " + where,
                              (stat.st_mtime, stat.st_size) + where_params)
        return cursor.rowcount

    return db.write_with_connection(
        db_path, store, label="file stat for %s" % os.path.basename(photo_path))


def forget_photo(db_path, photo_path):
    """Remove a deleted photo's row, its faces and its cached embedding.

    Returns how many rows of each were removed. The faces are deleted by name rather
    than left to the foreign key's cascade: db.connect() does not turn foreign keys
    on, and a face left behind points at a photo that no longer exists.
    """
    def forget(conn):
        cursor = conn.cursor()
        removed = {}
        for table, column in (("faces", "photo_path"), ("photos", "path"),
                              ("embedding_cache", "path")):
            where, params = paths.sql_equals(column, photo_path)
            cursor.execute("DELETE FROM %s WHERE %s" % (table, where), params)
            removed[table] = cursor.rowcount
        return removed

    removed = db.write_with_connection(
        db_path, forget, label="index rows for deleted %s" % os.path.basename(photo_path))
    if not removed.get("photos"):
        logger.info("Deleted %s, which the index had no row for.", photo_path)
    return removed

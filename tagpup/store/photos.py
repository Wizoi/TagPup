"""The photos table.

For now, recording a file's new mtime and size after a write. The rest of the table's
queries, in scripts/index.py and the servers, move here with the store step of phase 2
(ARCHITECTURE.md).
"""
import os

from tagpup.core import paths
from tagpup.store import db


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

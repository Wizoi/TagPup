"""The photos table.

For now, recording what a write did to a file, or what was read back from it, and
forgetting a deleted photo. The rest of the table's queries, in scripts/index.py and
the servers, move here with the store step of phase 2 (ARCHITECTURE.md).
"""
import json
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


def record_reads(db_path, records, label="photos read back"):
    """Record what was just read from each photo's file: its raw metadata, mtime and
    size. Returns how many rows changed.

    A record with no metadata -- a file that could not be read -- is left as it was.
    """
    def store(conn):
        changed = 0
        for entry in records:
            if not entry.get("raw_metadata"):
                continue
            where, where_params = paths.sql_equals("path", entry["path"])
            changed += conn.execute(
                "UPDATE photos SET raw_metadata = ?, mtime = ?, size = ? WHERE " + where,
                (json.dumps(entry["raw_metadata"]), entry.get("mtime", 0.0),
                 entry.get("size", 0)) + where_params).rowcount
        return changed

    return db.write_with_connection(db_path, store, label=label)

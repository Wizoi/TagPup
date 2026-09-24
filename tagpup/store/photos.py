"""The photos table.

For now, recording what a write did to a file, or what was read back from it, moving
renamed photos' rows, and forgetting a deleted photo. The rest of the table's queries,
in scripts/index.py and the servers, move here with the store step of phase 2
(ARCHITECTURE.md).
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


def move_rows(db_path, renames):
    """Move index rows from each old path to its new one, and its faces with them.

    `renames` maps old path to new path, in any spelling. Returns (moved, skipped):
    how many photo rows actually changed, and the (old, new) pairs left where they
    were because the new path already had rows. Re-pointing rows at a path that
    already has them is how 233 duplicate faces were made, so a destination is
    checked first and an occupied one is reported rather than merged into.

    A destination is free when nothing is there, when it is the same file (a rename
    that only changes case), or when whatever is there is itself moving away in this
    same call -- a rename that shuffles numbered files among themselves, or the
    occupant of a name that was moved aside to make room. The rows are moved in one
    transaction, by rowid, through a placeholder, so a shuffle never collides with
    itself on the way.
    """
    def rows_at(cursor, table, column, id_column, path):
        where, params = paths.sql_equals(column, path)
        return [r[0] for r in cursor.execute(
            "SELECT %s FROM %s WHERE %s" % (id_column, table, where), params)]

    def move(conn):
        cursor = conn.cursor()
        plan = dict(renames)
        skipped = []
        # Settle what moves first: skipping one rename can make another's
        # destination occupied, so repeat until nothing changes.
        changed = True
        while changed:
            changed = False
            leaving = {paths.key(old) for old in plan}
            arriving = set()
            for old_path, new_path in list(plan.items()):
                new_key = paths.key(new_path)
                clash = new_key in arriving
                if not clash and not paths.same(old_path, new_path) and new_key not in leaving:
                    clash = bool(rows_at(cursor, "photos", "path", "rowid", new_path)
                                 or rows_at(cursor, "faces", "photo_path", "id", new_path))
                if clash:
                    skipped.append((old_path, new_path))
                    del plan[old_path]
                    changed = True
                    break
                arriving.add(new_key)

        has_cache = bool(cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'embedding_cache'").fetchone())
        staged = []
        for n, (old_path, new_path) in enumerate(plan.items()):
            photo_ids = rows_at(cursor, "photos", "path", "rowid", old_path)
            face_ids = rows_at(cursor, "faces", "photo_path", "id", old_path)
            # The cached CLIP embedding too: a rename keeps the file's mtime and size,
            # which is what the cache is checked against, so it is still good. Left
            # behind, the renamed photo was embedded again from scratch.
            cache_ids = (rows_at(cursor, "embedding_cache", "path", "rowid", old_path)
                         if has_cache else [])
            # "<" cannot appear in a Windows file name, and this never outlives
            # the transaction.
            placeholder = "<moving %d>" % n
            cursor.executemany("UPDATE photos SET path = ? WHERE rowid = ?",
                               [(placeholder, rowid) for rowid in photo_ids])
            if cache_ids:
                cursor.executemany("UPDATE embedding_cache SET path = ? WHERE rowid = ?",
                                   [(placeholder, rowid) for rowid in cache_ids])
            staged.append((paths.stored(new_path), photo_ids, face_ids, cache_ids))

        moved = 0
        for new_stored, photo_ids, face_ids, cache_ids in staged:
            for rowid in photo_ids:
                cursor.execute("UPDATE photos SET path = ? WHERE rowid = ?", (new_stored, rowid))
                moved += cursor.rowcount
            cursor.executemany("UPDATE faces SET photo_path = ? WHERE id = ?",
                               [(new_stored, face_id) for face_id in face_ids])
            if cache_ids:
                # Whatever was cached under the new name described another file; it
                # is only derived data, keyed by path, and would block the move.
                where, params = paths.sql_equals("path", new_stored)
                cursor.execute("DELETE FROM embedding_cache WHERE " + where, params)
                cursor.executemany("UPDATE embedding_cache SET path = ? WHERE rowid = ?",
                                   [(new_stored, rowid) for rowid in cache_ids])
        return moved, skipped

    moved, skipped = db.write_with_connection(
        db_path, move, label="index rows for %d renamed photo(s)" % len(renames))
    for old_path, new_path in skipped:
        logger.warning("Renamed %s to %s, but the index already has rows for the new "
                       "name; left both as they were.", old_path, new_path)
    return moved, skipped

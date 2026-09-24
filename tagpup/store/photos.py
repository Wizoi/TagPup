"""The photos table.

Recording what a write did to a file -- its tags, or only its new mtime and size -- or
what was read back from it, moving renamed photos' rows, forgetting a deleted photo,
and what PhotoIndex reads and records when it indexes. The servers' queries move here
in phase 3 (ARCHITECTURE.md).
"""
import json
import logging
import os

from tagpup.core import fields, paths, vocabulary
from tagpup.store import db, faces, taxonomy

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


def record_tags(db_path, photo_path, tags, flat=None, hierarchical=None):
    """Tell the index what a photo's keywords now are.

    Saving one photo has always done this; the bulk writers did not, so tagging fifty
    photos left fifty index rows describing what they used to hold. Nothing in the app
    showed the difference -- the folder cache was updated, so the screen was right --
    which is how it went unnoticed until a repair script, planning from the index,
    reported nothing to do on a folder that had just been tagged wholesale.

    `flat` and `hierarchical` are what was actually written to the file, as
    write_keyword_fields returns them, so raw_metadata keeps agreeing with the photo.
    Left out, they are what write_keyword_fields would have written for `tags`.

    The file's new mtime and size are recorded too. Writing keywords changes both, and
    the folder scan only trusts a row whose mtime and size match the file; without
    them every photo tagged in bulk was re-read with ExifTool on every scan after.
    """
    if flat is None and hierarchical is None:
        flat, hierarchical = fields.expand_tag_fields(tags)
    where, where_params = paths.sql_equals("path", photo_path)
    try:
        stat = os.stat(photo_path)
    except OSError:
        stat = None

    def store(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT rowid, raw_metadata FROM photos WHERE " + where, where_params)
        row = cursor.fetchone()
        if not row:
            return False   # never indexed; adding it here would be an index, not an edit
        rowid, raw_json = row

        try:
            raw_meta = json.loads(raw_json) if raw_json else {}
        except Exception:
            raw_meta = {}
        fields.record_keyword_fields(raw_meta, flat or [], hierarchical or [])

        people = vocabulary.people_in_photo(
            raw_meta, tags, faces.face_names(photo_path, conn=conn),
            taxonomy.people_vocabulary(conn=conn))
        if stat is None:
            cursor.execute(
                "UPDATE photos SET tags = ?, people = ?, raw_metadata = ? WHERE rowid = ?",
                (json.dumps(tags), json.dumps(people), json.dumps(raw_meta), rowid),
            )
        else:
            cursor.execute(
                "UPDATE photos SET tags = ?, people = ?, raw_metadata = ?, mtime = ?, size = ?"
                " WHERE rowid = ?",
                (json.dumps(tags), json.dumps(people), json.dumps(raw_meta),
                 stat.st_mtime, stat.st_size, rowid),
            )
        return cursor.rowcount > 0

    try:
        return db.write_with_connection(
            db_path, store, label="index row for %s" % os.path.basename(photo_path)
        )
    except Exception as e:
        # The file is already written and correct; a stale index row is recoverable.
        logger.warning("Could not update the index for %s: %s", photo_path, e)
        return False


def read_tags(db_path, photo_paths):
    """(path, tags, raw_metadata) for each photo in `photo_paths` the index has a row for,
    in the order given, the path in its stored spelling.

    Read before a write, on a connection closed again at once, so nothing holds a
    transaction while the rows are rewritten one at a time through the write lock. A
    row whose columns do not parse is left out, as nothing can be written from it.
    """
    found = []
    conn = db.connect(db_path, timeout=30.0)
    try:
        for path in photo_paths:
            path = paths.stored(path)
            where, where_params = paths.sql_equals("path", path)
            row = conn.execute("SELECT tags, raw_metadata FROM photos WHERE " + where,
                               where_params).fetchone()
            if not row:
                continue
            try:
                tags = json.loads(row[0]) if row[0] else []
                raw_meta = json.loads(row[1]) if row[1] else {}
            except Exception:
                continue
            found.append((path, tags, raw_meta))
    finally:
        conn.close()
    return found


def carrying(db_path, tag):
    """{path as stored: its tags} of each photo whose tags hold `tag` or a tag under it:
    the photos renaming or deleting it rewrites (vocabulary.retag).

    Each action changing a tag everywhere scanned for these with a copy of its own, and
    merging found only the photos carrying the tag itself (docs/findings.md, #38).
    """
    found = {}
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        for path, tags_json in conn.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL"):
            try:
                tags = json.loads(tags_json)
            except (TypeError, ValueError):
                continue
            if vocabulary.retag(tags, tag)[1]:
                found[path] = tags
    finally:
        conn.close()
    return found


def update_people(conn, photo_path, gained=(), lost=()):
    """Keep a photo's list of people in step after some of its faces were named or
    unnamed: each name in `gained` is added, and each in `lost` goes unless another face
    in the photo still carries it. On `conn`, whose transaction the caller holds.
    Returns whether the list changed.

    Seven face actions each kept the list with a copy of their own. Naming faces in bulk
    appended to the list it then compared with, so it wrote only when an old name went
    too (docs/findings.md, #42).
    """
    where, params = paths.sql_equals("path", photo_path)
    row = conn.execute("SELECT path, people FROM photos WHERE " + where, params).fetchone()
    if not row:
        return False
    try:
        people = json.loads(row[1]) if row[1] else []
    except (TypeError, ValueError):
        people = []
    updated = list(people)
    for name in gained:
        if name and name not in updated:
            updated.append(name)
    faces_where, faces_params = paths.sql_equals("photo_path", photo_path)
    for name in lost:
        if not name or name in gained or name not in updated:
            continue
        still = conn.execute("SELECT COUNT(*) FROM faces WHERE " + faces_where
                             + " AND name = ? AND excluded = 0", faces_params + (name,)).fetchone()[0]
        if not still:
            updated = [person for person in updated if person != name]
    if updated == people:
        return False
    where, params = paths.sql_equals("path", row[0])
    conn.execute("UPDATE photos SET people = ? WHERE " + where, (json.dumps(updated),) + params)
    return True


def tag_usage(db_path):
    """Photos per tag, a photo counting toward each level above its tags as well: a
    photo tagged "Activity/Hiking" counts for "Activity". What the tree view shows.

    Once per photo: a photo carrying two tags under a node counted twice toward it and
    toward everything above it (docs/findings.md, #41).
    """
    counts = {}
    if not os.path.exists(db_path):
        return counts
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL"):
                try:
                    levels = {level for tag in json.loads(tags_json) for level in vocabulary.lineage(tag)}
                except Exception:
                    continue
                for level in levels:
                    counts[level] = counts.get(level, 0) + 1
        finally:
            conn.close()
    except Exception:
        pass
    return counts


def record_saved(db_path, photo_path, tags, people, captions, raw_meta):
    """Record what saving one photo left in its file, and the file's mtime and size.
    Returns rows changed.

    A photo the index has never seen is not added here: that would be a row with no
    embedding and no faces, which is an index entry in name only.
    """
    stat = os.stat(photo_path)
    where, where_params = paths.sql_equals("path", photo_path)

    def update_row(conn):
        return conn.execute(
            "UPDATE photos SET mtime = ?, size = ?, tags = ?, people = ?, captions = ?,"
            " raw_metadata = ? WHERE " + where,
            (stat.st_mtime, stat.st_size, json.dumps(tags), json.dumps(people),
             json.dumps(captions), json.dumps(raw_meta)) + where_params).rowcount

    return db.write_with_connection(
        db_path, update_row, label="index row for %s" % os.path.basename(photo_path))


# ---- What PhotoIndex reads and writes ---------------------------------------------------

#: A photo row as the index holds it. `embedding` is float32 bytes, or None.
INDEX_COLUMNS = ("path", "mtime", "size", "tags", "people", "captions", "raw_metadata", "embedding")


def index_rows(conn):
    """Every photo row, INDEX_COLUMNS each: the whole library, as PhotoIndex loads it."""
    return conn.execute("SELECT " + ", ".join(INDEX_COLUMNS) + " FROM photos").fetchall()


def stored_spelling(conn, photo_path):
    """The path a photo's row is stored under, or None if it has no row."""
    clause, params = paths.sql_equals("path", photo_path)
    row = conn.execute("SELECT path FROM photos WHERE " + clause + " LIMIT 1", params).fetchone()
    return row[0] if row else None


def record_indexed(conn, photo_path, row):
    """Record what indexing read of a photo: `row` has mtime, size, tags, people,
    captions, raw_metadata (as values), embedding (bytes) and document_id. The caller
    commits.

    A photo indexed before is updated in place, under the spelling its row already has.
    faces.photo_path references photos.path ON DELETE CASCADE, so anything that deletes
    the row -- INSERT OR REPLACE is a delete and an insert -- takes every face with it:
    names given by hand, "nobody" decisions and exclusions. Re-indexing a changed photo
    did exactly that. A document_id already recorded is kept when the file has none.
    """
    conn.execute(
        "INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding, document_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(path) DO UPDATE SET"
        " mtime = excluded.mtime, size = excluded.size, tags = excluded.tags,"
        " people = excluded.people, captions = excluded.captions,"
        " raw_metadata = excluded.raw_metadata, embedding = excluded.embedding,"
        " document_id = COALESCE(excluded.document_id, photos.document_id)",
        (stored_spelling(conn, photo_path) or paths.stored(photo_path),
         row.get("mtime", 0.0), row.get("size", 0), json.dumps(row.get("tags", [])),
         json.dumps(row.get("people", [])), json.dumps(row.get("captions", [])),
         json.dumps(row.get("raw_metadata", {})), row.get("embedding"), row.get("document_id")))


def remove(conn, photo_paths):
    """Delete the rows of `photo_paths`; their faces go with them on a connection that
    enforces foreign keys (db.connect(..., foreign_keys=True)). Returns rows deleted.
    The caller commits."""
    removed = 0
    for photo_path in photo_paths:
        clause, params = paths.sql_equals("path", photo_path)
        removed += conn.execute("DELETE FROM photos WHERE " + clause, params).rowcount
    return removed


def clear_embeddings(conn):
    """Forget every photo's CLIP embedding. Returns rows changed. The caller commits."""
    return conn.execute("UPDATE photos SET embedding = NULL WHERE embedding IS NOT NULL").rowcount


def keyword_sources(conn):
    """(path, raw_metadata JSON, tags JSON) of every photo: what its keyword people are
    derived from."""
    return conn.execute("SELECT path, raw_metadata, tags FROM photos").fetchall()


def set_people(conn, people_by_path):
    """Replace the people of each photo in {stored path: [names]}. The caller commits."""
    conn.executemany("UPDATE photos SET people = ? WHERE path = ?",
                     [(json.dumps(people), path) for path, people in people_by_path.items()])


def remove_under(conn, folder):
    """Take the photos under a folder, at any depth, out of the library with their faces.
    The files are not touched. Returns {photos_removed, faces_removed, manual_lost,
    excluded_lost}: what the deletes removed, and the face work that went with them. The
    caller commits.

    Faces are matched on their own photo_path, so a face goes with its folder even where
    its photo row is missing or spelled apart; and deleted explicitly, since the cascade
    runs only where a connection turned foreign keys on.
    """
    faces_where, faces_params = paths.sql_under("photo_path", folder)
    photos_where, photos_params = paths.sql_under("path", folder)
    manual = conn.execute("SELECT COUNT(*) FROM faces WHERE " + faces_where
                          + " AND name_source = 'manual'", faces_params).fetchone()[0]
    excluded = conn.execute("SELECT COUNT(*) FROM faces WHERE " + faces_where
                            + " AND excluded = 1", faces_params).fetchone()[0]
    faces_removed = conn.execute("DELETE FROM faces WHERE " + faces_where, faces_params).rowcount
    photos_removed = conn.execute("DELETE FROM photos WHERE " + photos_where, photos_params).rowcount
    return dict(photos_removed=photos_removed, faces_removed=faces_removed,
                manual_lost=manual, excluded_lost=excluded)


# ---- What TagTuner's screens read -----------------------------------------------------

def details(conn, photo_path):
    """(people JSON, tags JSON, captions JSON, mtime, raw_metadata JSON) of one photo, or
    None."""
    where, params = paths.sql_equals("path", photo_path)
    return conn.execute("SELECT people, tags, captions, mtime, raw_metadata FROM photos WHERE " + where,
                        params).fetchone()


def tag_lists(conn):
    """Each photo's tags, as a list; a row whose tags cannot be read is left out."""
    lists = []
    for (tags_json,) in conn.execute("SELECT tags FROM photos"):
        try:
            lists.append(json.loads(tags_json or "[]"))
        except (TypeError, ValueError):
            continue
    return lists


def with_tag(conn, tag):
    """(path, tags, mtime) of each photo carrying exactly `tag`."""
    found = []
    for path, tags_json, mtime in conn.execute("SELECT path, tags, mtime FROM photos"):
        try:
            tags = json.loads(tags_json or "[]")
        except (TypeError, ValueError):
            continue
        if tag in tags:
            found.append((path, tags, mtime))
    return found


def folder_counts(conn):
    """{paths.key of a folder: photos the library holds directly in it}."""
    counts = {}
    for (photo_path,) in conn.execute("SELECT path FROM photos"):
        folder = paths.key(os.path.dirname(photo_path))
        counts[folder] = counts.get(folder, 0) + 1
    return counts


def rows_under(conn, folder):
    """(path, mtime, size, tags JSON, people JSON, captions JSON, raw_metadata JSON) of
    each photo under a folder, at any depth."""
    where, params = paths.sql_under("path", folder)
    return conn.execute("SELECT path, mtime, size, tags, people, captions, raw_metadata FROM photos"
                        " WHERE " + where, params).fetchall()


def set_captions(conn, photo_path, captions):
    """Record a photo's captions. Returns rows changed. The caller commits."""
    where, params = paths.sql_equals("path", photo_path)
    return conn.execute("UPDATE photos SET captions = ? WHERE " + where,
                        (json.dumps(captions),) + params).rowcount

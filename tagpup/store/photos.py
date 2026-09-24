"""The photos table.

Recording what a write did to a file -- its tags, or only its new mtime and size -- or
what was read back from it, moving renamed photos' rows, forgetting a deleted photo,
and what PhotoIndex reads and records when it indexes. The servers' queries move here
in phase 3 (ARCHITECTURE.md).
"""
import json
import logging
import os

from tagpup.core import dates, fields, paths, vocabulary
from tagpup.store import db, embeddings, faces, people
from tagpup.store.people import PEOPLE_JSON

logger = logging.getLogger(__name__)


def date_photos(conn, photo_ids=None):
    """Record when each photo in `photo_ids` -- every photo, without -- was taken, from its
    raw metadata and its path (tagpup.core.dates): `taken`, its Date Taken as recorded,
    and `year`, the year of it, else one in its name. Every write of a photo's metadata
    or path calls this; readers read the columns. The caller commits."""
    query = "SELECT id, path, raw_metadata FROM photos"
    params = ()
    if photo_ids is not None:
        photo_ids = list(photo_ids)
        if not photo_ids:
            return
        query += " WHERE id IN (%s)" % ",".join("?" * len(photo_ids))
        params = tuple(photo_ids)
    dated = []
    for photo_id, path, raw_json in conn.execute(query, params).fetchall():
        try:
            raw = json.loads(raw_json) if raw_json else {}
        except (TypeError, ValueError):
            raw = {}
        dated.append((dates.date_taken(raw), dates.photo_year(raw, path), photo_id))
    conn.executemany("UPDATE photos SET taken = ?, year = ? WHERE id = ?", dated)


def _dated_paths(conn, photo_paths):
    ids = []
    for photo_path in photo_paths:
        where, params = paths.sql_equals("path", photo_path)
        ids += [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params)]
    date_photos(conn, ids)


def _stamp(conn, photo_path, mtime, size, looks_different=False, before=None):
    """Record the stamp a write of the app's own left on a photo's file, and bring its
    vectors with it (tagpup.store.embeddings): carried forward from `before`, the
    file's stamp just before the write (embeddings.stamp_of), when only metadata
    changed; taken away when the photo `looks_different` -- a rotation, whose
    Orientation the embedder applies. Without `before` they stay as they are, and a
    vector the write left behind is computed again. Returns the photo's id, or None
    without a row. The caller commits."""
    where, params = paths.sql_equals("path", photo_path)
    row = conn.execute("SELECT id, mtime, size FROM photos WHERE " + where + " LIMIT 1", params).fetchone()
    if row is None:
        return None
    photo_id = row[0]
    conn.execute("UPDATE photos SET mtime = ?, size = ? WHERE id = ?", (mtime, size, photo_id))
    if looks_different:
        conn.execute("DELETE FROM embeddings WHERE photo_id = ?", (photo_id,))
    else:
        embeddings.restamp(conn, photo_id, before, (mtime, size))
    return photo_id


def record_file_stat(db_path, photo_path, looks_different=False, before=None):
    """Record a photo's current mtime and size in its index row. Returns rows changed.

    For a write that changes the file but not what the index describes: a caption, or
    a rotation, which changes only the Orientation tag -- and so how the photo looks to
    the embedder, `looks_different`, which takes its vectors away. `before` is the
    file's stamp just before the write (_stamp). Left stale, the folder scan would
    distrust the row and re-read the photo with ExifTool on every scan.
    """
    stat = os.stat(photo_path)

    def store(conn):
        return 0 if _stamp(conn, photo_path, stat.st_mtime, stat.st_size, looks_different, before) is None else 1

    return db.write_with_connection(
        db_path, store, label="file stat for %s" % os.path.basename(photo_path))


def forget_photo(db_path, photo_path):
    """Remove a deleted photo's row and its faces; its vectors go with the row.

    Returns how many rows of each were removed. The faces are deleted explicitly
    rather than left to the foreign key's cascade: db.connect() does not turn foreign
    keys on, and a face left behind points at a photo that no longer exists.
    """
    def forget(conn):
        removed = {"faces": faces.remove_for_photo(conn, photo_path)}
        where, params = paths.sql_equals("path", photo_path)
        removed["photos"] = conn.execute("DELETE FROM photos WHERE " + where, params).rowcount
        return removed

    removed = db.write_with_connection(
        db_path, forget, label="index rows for deleted %s" % os.path.basename(photo_path))
    if not removed.get("photos"):
        logger.info("Deleted %s, which the index had no row for.", photo_path)
    return removed


def record_reads(db_path, records, label="photos read back", before=None):
    """Record what was just read from each photo's file: its raw metadata, mtime and
    size. Returns how many rows changed.

    A record with no metadata -- a file that could not be read -- is left as it was.
    `before` maps a path to its file's stamp just before a metadata write of the app's
    own, whose vectors are carried forward (_stamp); a file read back after changing
    elsewhere might look different, and keeps the stamp that tells the embedder so.
    """
    def store(conn):
        changed = 0
        for entry in records:
            if not entry.get("raw_metadata"):
                continue
            if before and entry["path"] in before:
                _stamp(conn, entry["path"], entry.get("mtime", 0.0), entry.get("size", 0),
                       before=before[entry["path"]])
            where, where_params = paths.sql_equals("path", entry["path"])
            written = conn.execute(
                "UPDATE photos SET raw_metadata = ?, mtime = ?, size = ? WHERE " + where,
                (json.dumps(entry["raw_metadata"]), entry.get("mtime", 0.0),
                 entry.get("size", 0)) + where_params).rowcount
            if written:
                # A file's person fields are one source of its people (#89), and a time
                # shift changes when it was taken.
                people.rebuild_photos(conn, [entry["path"]])
                _dated_paths(conn, [entry["path"]])
            changed += written
        return changed

    return db.write_with_connection(db_path, store, label=label)


def move_rows(db_path, renames):
    """Move index rows from each old path to its new one. Its faces point at the row by
    id, so they go with it.

    `renames` maps old path to new path, in any spelling. Returns (moved, skipped):
    how many photo rows actually changed, and the (old, new) pairs left where they
    were because the new path already had rows. Re-pointing rows at a path that
    already has them is how 233 duplicate faces were made, so a destination is
    checked first and an occupied one is reported rather than merged into.

    A destination is free when nothing is there, when it is the same file (a rename
    that only changes case), or when whatever is there is itself moving away in this
    same call -- a rename that shuffles numbered files among themselves, or the
    occupant of a name that was moved aside to make room. The rows are moved in one
    transaction, by id, through a placeholder, so a shuffle never collides with itself
    on the way.
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
                    clash = bool(rows_at(cursor, "photos", "path", "id", new_path))
                if clash:
                    skipped.append((old_path, new_path))
                    del plan[old_path]
                    changed = True
                    break
                arriving.add(new_key)

        staged = []
        for n, (old_path, new_path) in enumerate(plan.items()):
            # Its faces and vectors point at the row by id, and go with it.
            photo_ids = rows_at(cursor, "photos", "path", "id", old_path)
            # "<" cannot appear in a Windows file name, and this never outlives
            # the transaction.
            placeholder = "<moving %d>" % n
            cursor.executemany("UPDATE photos SET path = ? WHERE id = ?",
                               [(placeholder, photo_id) for photo_id in photo_ids])
            staged.append((paths.stored(new_path), photo_ids))

        moved = 0
        for new_stored, photo_ids in staged:
            for photo_id in photo_ids:
                cursor.execute("UPDATE photos SET path = ? WHERE id = ?", (new_stored, photo_id))
                moved += cursor.rowcount
            date_photos(conn, photo_ids)   # a year may be in the new name
        return moved, skipped

    moved, skipped = db.write_with_connection(
        db_path, move, label="index rows for %d renamed photo(s)" % len(renames))
    for old_path, new_path in skipped:
        logger.warning("Renamed %s to %s, but the index already has rows for the new "
                       "name; left both as they were.", old_path, new_path)
    return moved, skipped


def record_tags(db_path, photo_path, tags, flat=None, hierarchical=None, before=None):
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
    `before` is the file's stamp just before the write, over which its vectors are
    carried (_stamp); without it, as when nothing was written, they are left alone.
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
        cursor.execute("SELECT id, raw_metadata FROM photos WHERE " + where, where_params)
        row = cursor.fetchone()
        if not row:
            return False   # never indexed; adding it here would be an index, not an edit
        photo_id, raw_json = row

        try:
            raw_meta = json.loads(raw_json) if raw_json else {}
        except Exception:
            raw_meta = {}
        fields.record_keyword_fields(raw_meta, flat or [], hierarchical or [])

        if stat is None:
            cursor.execute("UPDATE photos SET tags = ?, raw_metadata = ? WHERE id = ?",
                           (json.dumps(tags), json.dumps(raw_meta), photo_id))
        else:
            cursor.execute("UPDATE photos SET tags = ?, raw_metadata = ?, mtime = ?, size = ? WHERE id = ?",
                           (json.dumps(tags), json.dumps(raw_meta), stat.st_mtime, stat.st_size, photo_id))
            embeddings.restamp(conn, photo_id, before, (stat.st_mtime, stat.st_size))
        changed = cursor.rowcount > 0
        people.rebuild(conn, [photo_id])
        date_photos(conn, [photo_id])
        return changed

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


def record_saved(db_path, photo_path, tags, captions, raw_meta, before=None):
    """Record what saving one photo left in its file, and the file's mtime and size; its
    people are rebuilt from what the file now says. `before` is the file's stamp just
    before the save wrote it (_stamp). Returns rows changed.

    A photo the index has never seen is not added here: that would be a row with no
    embedding and no faces, which is an index entry in name only.
    """
    stat = os.stat(photo_path)
    where, where_params = paths.sql_equals("path", photo_path)

    def update_row(conn):
        photo_id = _stamp(conn, photo_path, stat.st_mtime, stat.st_size, before=before)
        changed = conn.execute(
            "UPDATE photos SET tags = ?, captions = ?, raw_metadata = ? WHERE " + where,
            (json.dumps(tags), json.dumps(captions), json.dumps(raw_meta)) + where_params).rowcount
        if photo_id is not None:
            people.rebuild(conn, [photo_id])
            date_photos(conn, [photo_id])
        return changed

    return db.write_with_connection(
        db_path, update_row, label="index row for %s" % os.path.basename(photo_path))


# ---- What PhotoIndex reads and writes ---------------------------------------------------

#: A photo row as the index holds it, and its vector under one model: float32 bytes,
#: or None.
INDEX_COLUMNS = ("path", "mtime", "size", "tags", "people", "captions", "raw_metadata", "year", "vector")


def index_rows(conn, model):
    """Every photo row, INDEX_COLUMNS each, with its vector under `model`: the whole
    library, as PhotoIndex loads it."""
    return conn.execute(
        "SELECT p.path, p.mtime, p.size, p.tags, " + PEOPLE_JSON + ", p.captions, p.raw_metadata, p.year, e.vector"
        " FROM photos p LEFT JOIN embeddings e ON e.photo_id = p.id AND e.model = ?", (model,)).fetchall()


def ensure_row(conn, photo_path):
    """The id of a photo's row, making the row if it has none.

    Every photo a face or an embedding is recorded for has a row (docs/findings.md,
    #48): Suggest detects faces in photos never indexed, and they were recorded against
    a path no row had. A row made here holds the path and nothing read from the file:
    its mtime and size stay empty, so the folder scan and the refresh read the file
    rather than trust it. The caller commits.
    """
    clause, params = paths.sql_equals("path", photo_path)
    row = conn.execute("SELECT id FROM photos WHERE " + clause + " LIMIT 1", params).fetchone()
    if row:
        return row[0]
    photo_id = conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata)"
                            " VALUES (?, '[]', '[]', '{}')", (paths.stored(photo_path),)).lastrowid
    date_photos(conn, [photo_id])
    return photo_id


def stored_spelling(conn, photo_path):
    """The path a photo's row is stored under, or None if it has no row."""
    clause, params = paths.sql_equals("path", photo_path)
    row = conn.execute("SELECT path FROM photos WHERE " + clause + " LIMIT 1", params).fetchone()
    return row[0] if row else None


def record_indexed(conn, photo_path, row, model=None, known=None):
    """Record what indexing read of a photo: `row` has mtime, size, tags, captions,
    raw_metadata (as values), embedding (bytes) and document_id; the embedding is kept
    under `model`, stamped with the row's mtime and size, and the photo's people are
    rebuilt. The caller commits.

    A photo indexed before is updated in place, under the spelling its row already has.
    faces.photo_id references photos.id ON DELETE CASCADE, so anything that deletes
    the row -- INSERT OR REPLACE is a delete and an insert -- takes every face with it:
    names given by hand, "nobody" decisions and exclusions. Re-indexing a changed photo
    did exactly that. A document_id already recorded is kept when the file has none.
    """
    stored = stored_spelling(conn, photo_path) or paths.stored(photo_path)
    conn.execute(
        "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata, document_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(path) DO UPDATE SET"
        " mtime = excluded.mtime, size = excluded.size, tags = excluded.tags,"
        " captions = excluded.captions, raw_metadata = excluded.raw_metadata,"
        " document_id = COALESCE(excluded.document_id, photos.document_id)",
        (stored, row.get("mtime", 0.0), row.get("size", 0), json.dumps(row.get("tags", [])),
         json.dumps(row.get("captions", [])), json.dumps(row.get("raw_metadata", {})),
         row.get("document_id")))
    people.rebuild_photos(conn, [stored], known)
    _dated_paths(conn, [stored])
    if row.get("embedding") is not None:
        if model is None:
            # Dropped without a word, it left a library without the vector (#85).
            raise ValueError("An embedding is kept under the model that made it; none was named.")
        embeddings.put(conn, stored, model, row.get("mtime", 0.0), row.get("size", 0), row["embedding"])


def remove(conn, photo_paths):
    """Delete the rows of `photo_paths` and their faces, whether or not the connection
    enforces foreign keys. Returns photo rows deleted. The caller commits."""
    removed = 0
    for photo_path in photo_paths:
        faces.remove_for_photo(conn, photo_path)
        clause, params = paths.sql_equals("path", photo_path)
        removed += conn.execute("DELETE FROM photos WHERE " + clause, params).rowcount
    return removed


def clear_embeddings(conn):
    """Forget every photo's CLIP vectors. Returns rows deleted. The caller commits."""
    return embeddings.clear(conn)


def remove_under(conn, folder):
    """Take the photos under a folder, at any depth, out of the library with their faces.
    The files are not touched. Returns {photos_removed, faces_removed, manual_lost,
    excluded_lost}: what the deletes removed, and the face work that went with them. The
    caller commits.

    Faces are deleted explicitly, since the cascade runs only where a connection turned
    foreign keys on.
    """
    photos_where, photos_params = paths.sql_under("path", folder)
    faces_where = "photo_id IN (SELECT id FROM photos WHERE %s)" % photos_where
    faces_params = photos_params
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
    """(people JSON, tags JSON, captions JSON, mtime, year) of one photo, or None."""
    where, params = paths.sql_equals("path", photo_path)
    return conn.execute("SELECT " + PEOPLE_JSON + ", p.tags, p.captions, p.mtime, p.year"
                        " FROM photos p WHERE " + where, params).fetchone()


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
    return conn.execute("SELECT p.path, p.mtime, p.size, p.tags, " + PEOPLE_JSON + ", p.captions,"
                        " p.raw_metadata FROM photos p WHERE " + where, params).fetchall()


def set_captions(conn, photo_path, captions):
    """Record a photo's captions. Returns rows changed. The caller commits."""
    where, params = paths.sql_equals("path", photo_path)
    return conn.execute("UPDATE photos SET captions = ? WHERE " + where,
                        (json.dumps(captions),) + params).rowcount


# ---- What refresh_rows_from_files reads and writes --------------------------------------

def rows_to_check(conn, folder=None):
    """(path, mtime, size, tags JSON, captions JSON, raw_metadata JSON, people JSON) of
    every photo, or of those under `folder`: what is compared with the files."""
    query = "SELECT p.path, p.mtime, p.size, p.tags, p.captions, p.raw_metadata, " + PEOPLE_JSON + " FROM photos p"
    params = ()
    if folder:
        where, params = paths.sql_under("path", folder)
        query += " WHERE " + where
    return conn.execute(query, params).fetchall()


def row_as_recorded(conn, stored_path):
    """(tags, people, captions, raw_metadata, mtime, size, document_id) of the row stored
    under exactly `stored_path`, or None."""
    return conn.execute("SELECT p.tags, " + PEOPLE_JSON + ", p.captions, p.raw_metadata, p.mtime, p.size,"
                        " p.document_id FROM photos p WHERE p.path = ?", (stored_path,)).fetchone()


def record_refreshed(conn, stored_path, record, seen=None):
    """Record what was read from a photo's file: tags, captions, raw_metadata, mtime,
    size, and its document_id where the row has none, and rebuild its people from them.
    With `seen` (mtime, size), only while the row still has them: a row the app saved
    since describes something newer. Returns rows changed. The caller commits."""
    guard, guard_params = "", ()
    if seen is not None:
        guard, guard_params = " AND mtime IS ? AND size IS ?", tuple(seen)
    changed = conn.execute(
        "UPDATE photos SET tags = ?, captions = ?, raw_metadata = ?, mtime = ?, size = ?,"
        " document_id = COALESCE(document_id, ?) WHERE path = ?" + guard,
        (json.dumps(record["tags"]), json.dumps(record["captions"]), json.dumps(record["raw_metadata"]),
         record["mtime"], record["size"], record.get("document_id"), stored_path) + guard_params).rowcount
    if changed:
        refreshed = [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE path = ?", (stored_path,))]
        people.rebuild(conn, refreshed)
        date_photos(conn, refreshed)
    return changed


# ---- What relink_renamed_photos reads ---------------------------------------------------

def all_paths(conn):
    """Every photo's path, as stored."""
    return [path for (path,) in conn.execute("SELECT path FROM photos")]


def identities(conn):
    """{path as stored: document_id} for every photo whose identity is recorded."""
    return {path: str(doc_id).strip() for path, doc_id in conn.execute(
        "SELECT path, document_id FROM photos WHERE document_id IS NOT NULL") if path and doc_id}


def tags_by_path(conn):
    """(path as stored, tags) of each photo; a row whose tags cannot be read is left out."""
    found = []
    for path, tags_json in conn.execute("SELECT path, tags FROM photos"):
        try:
            found.append((path, json.loads(tags_json or "[]")))
        except (TypeError, ValueError):
            continue
    return found


# ---- What backfill_document_ids reads and writes ----------------------------------------

def without_identity(conn):
    """The paths, as stored, of photos with no document_id recorded. Raises on a library
    from before the column."""
    return [path for (path,) in conn.execute(
        "SELECT path FROM photos WHERE document_id IS NULL OR document_id = ''") if path]


def record_identity(conn, photo_path, document_id, stat=None):
    """Record a photo's document_id; with `stat`, the mtime and size the file has now it
    was written to. Returns rows changed. The caller commits."""
    where, params = paths.sql_equals("path", photo_path)
    if stat is not None:
        # Nobody kept the file's stamp from before the identity was written, so its
        # vectors are not carried over it, and are computed again.
        _stamp(conn, photo_path, stat.st_mtime, stat.st_size)
    return conn.execute("UPDATE photos SET document_id = ? WHERE " + where,
                        (document_id,) + params).rowcount

"""The photos table.

Recording what a write did to a file -- its tags, or only its new mtime and size -- or
what was read back from it, moving renamed photos' rows, forgetting a deleted photo,
and what PhotoIndex reads and records when it indexes. The servers' queries move here
in phase 3 (docs/ARCHITECTURE.md).

Paths cross this module's boundary native, and the library holds them as its roots say
(tagpup.store.roots): every path read from a row is made native here, every path written is
converted, and a comparison is made by the converted argument. A library with no roots is
read and written exactly as it always was.
"""
import collections
import json
import logging
import os

from tagpup.core import dates, fields, paths, vocabulary
from tagpup.core.result import NotHeld
from tagpup.store import added_folders, damaged_files, db, derived, embeddings, face_tags, faces, folders, people
from tagpup.store import roots as store_roots
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
    roots = store_roots.roots_for(conn)
    for photo_id, path, raw_json in conn.execute(query, params).fetchall():
        try:
            raw = json.loads(raw_json) if raw_json else {}
        except (TypeError, ValueError):
            raw = {}
        dated.append((dates.date_taken(raw), dates.photo_year(raw, paths.from_row(path, roots)), photo_id))
    conn.executemany("UPDATE photos SET taken = ?, year = ? WHERE id = ?", dated)


def _ids_of(conn, photo_paths):
    ids = []
    for photo_path in photo_paths:
        where, params = store_roots.sql_equals(conn, "path", photo_path)
        ids += [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params)]
    return ids


def _dated_paths(conn, photo_paths):
    date_photos(conn, _ids_of(conn, photo_paths))


#: How far a row's mtime may be from its file's and the row still describe the file:
#: what the folder scan allows, and the refresh.
MTIME_TOLERANCE = 0.1


def describes(row_mtime, row_size, stamp):
    """Does a row stamped (`row_mtime`, `row_size`) describe the file whose stamp is
    `stamp`, (mtime, size)? What the folder scan trusts a row by; None for any of them
    is no."""
    if row_mtime is None or row_size is None or not stamp or None in stamp:
        return False
    return row_size == stamp[1] and abs(row_mtime - stamp[0]) < MTIME_TOLERANCE


def _describes_before(row_mtime, row_size, before):
    """May a row be stamped with what a write of the app's own left on part of its file
    -- its keywords, a caption, its Orientation -- as describing the file? Only where
    the row described the file just before the write: `before`, the file's stamp then
    (embeddings.stamp_of). Then the row holds everything else the file holds.

    Suggest makes a row for a photo the index never read (ensure_row): the path alone,
    mtime and size empty. A tag write stamped it, and the row -- the keyword fields and
    nothing else, no Date Taken -- was trusted from then on (docs/findings.md, #247). A
    row read before its file changed elsewhere, a date set in another program, was
    stamped the same way, and claimed to match fields it never read (#249). Left
    unstamped, either is read at the next scan.
    """
    return describes(row_mtime, row_size, before)


def _stamp(conn, photo_path, mtime, size, looks_different=False, before=None, whole=False):
    """Record the stamp a write of the app's own left on a photo's file, and bring its
    vectors with it (tagpup.store.embeddings): carried forward from `before`, the
    file's stamp just before the write (embeddings.stamp_of), when only metadata
    changed; taken away when the photo `looks_different` -- a rotation, whose
    Orientation the embedder applies. Without `before` they stay as they are, and a
    vector the write left behind is computed again.

    The row is stamped only where it described the file just before the write
    (_describes_before), or where the caller records the `whole` file as read back after
    the write; otherwise its stamp stays as it is, and the next scan reads the file.
    Returns (the photo's id, whether the row was stamped), or (None, False) without a
    row. The caller commits."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    row = conn.execute("SELECT id, mtime, size FROM photos WHERE " + where + " LIMIT 1", params).fetchone()
    if row is None:
        return None, False
    photo_id = row[0]
    stamped = whole or _describes_before(row[1], row[2], before)
    if stamped:
        conn.execute("UPDATE photos SET mtime = ?, size = ? WHERE id = ?", (mtime, size, photo_id))
    if looks_different:
        conn.execute("DELETE FROM embeddings WHERE photo_id = ?", (photo_id,))
    else:
        embeddings.restamp(conn, photo_id, before, (mtime, size))
    return photo_id, stamped


def record_file_stat(db_path, photo_path, looks_different=False, before=None):
    """Record a photo's current mtime and size in its index row. Returns rows stamped.

    For a write that changes the file but not what the index describes: a caption, or
    a rotation, which changes only the Orientation tag -- and so how the photo looks to
    the embedder, `looks_different`, which takes its vectors away. `before` is the
    file's stamp just before the write (_stamp). Left stale, the folder scan would
    distrust the row and re-read the photo with ExifTool on every scan. A row that did
    not describe the file before the write, or without `before`, is left unstamped
    (_describes_before).
    """
    stat = os.stat(photo_path)

    def store(conn):
        return 1 if _stamp(conn, photo_path, stat.st_mtime, stat.st_size, looks_different, before)[1] else 0

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
        where, params = store_roots.sql_equals(conn, "path", photo_path)
        removed["photos"] = conn.execute("DELETE FROM photos WHERE " + where, params).rowcount
        derived.prune(conn)   # the photo's rows went with it; the folders it emptied do not
        return removed

    removed = db.write_with_connection(
        db_path, forget, label="index rows for deleted %s" % os.path.basename(photo_path))
    if not removed.get("photos"):
        logger.info("Deleted %s, which the index had no row for.", photo_path)
    return removed


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
    moved, skipped = db.write_with_connection(
        db_path, lambda conn: move_rows_in(conn, renames), label="index rows for %d renamed photo(s)" % len(renames))
    for old_path, new_path in skipped:
        logger.warning("Renamed %s to %s, but the index already has rows for the new "
                       "name; left both as they were.", old_path, new_path)
    return moved, skipped


def _ids_at(cursor, path):
    where, params = store_roots.sql_equals(cursor.connection, "path", path)
    return [r[0] for r in cursor.execute("SELECT id FROM photos WHERE " + where, params)]


def move_rows_in(conn, renames):
    """move_rows on the caller's connection, in the caller's transaction: the file journal
    marks a rename done in the transaction that moves its row. Returns (moved, skipped)."""
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
                clash = bool(_ids_at(cursor, new_path))
            if clash:
                skipped.append((old_path, new_path))
                del plan[old_path]
                changed = True
                break
            arriving.add(new_key)

    staged = []
    for n, (old_path, new_path) in enumerate(plan.items()):
        # Its faces and vectors point at the row by id, and go with it.
        photo_ids = _ids_at(cursor, old_path)
        # "<" cannot appear in a Windows file name, and this never outlives
        # the transaction.
        placeholder = "<moving %d>" % n
        cursor.executemany("UPDATE photos SET path = ? WHERE id = ?",
                           [(placeholder, photo_id) for photo_id in photo_ids])
        staged.append((store_roots.to_row(conn, new_path), photo_ids))

    moved = 0
    every = []
    for new_stored, photo_ids in staged:
        for photo_id in photo_ids:
            cursor.execute("UPDATE photos SET path = ? WHERE id = ?", (new_stored, photo_id))
            moved += cursor.rowcount
        date_photos(conn, photo_ids)   # a year may be in the new name
        every += photo_ids
    # The photos' folders, and the ones they left, in one call: one read of the tag tree for all of them.
    derived.refresh_photos(conn, every)
    return moved, skipped


def rows_of(conn, photo_paths):
    """{paths.key(path): (id, path as stored, document_id)} of each photo in
    `photo_paths` the library has a row for. One indexed lookup a photo."""
    found = {}
    for photo_path in photo_paths:
        where, params = store_roots.sql_equals(conn, "path", photo_path)
        row = conn.execute("SELECT id, path, document_id FROM photos WHERE " + where + " LIMIT 1",
                           params).fetchone()
        if row:
            found[paths.key(photo_path)] = store_roots.native_one(conn, row, 1)
    return found


def follow_fields(conn, photo_path, written, stat=None, before=None, batch=None):
    """Make a photo's row say what the file journal just left in its file: `written` is
    {field: value} of the fields written, forward, again after a crash, or back in an
    undo (tagpup.services.file_changes). The caller commits, in the transaction that marks
    the file done.

    As record_tags does for a keyword write, and for every field: raw_metadata takes each
    field the scan reads (fields.scan_reads; under its bare name too, where the scan
    stored one), a cleared field taken out; the tags are derived again when a keyword
    field was written, the captions when a caption field was, document_id follows the
    identity. With `stat`, the file's new mtime and size, its vectors carried over
    `before`, its stamp just before the write (_stamp). Without it -- a file read and not
    written -- the stamp stays, so the scan still reads what the row does not say. The
    photo's people and dates are rebuilt. Returns the photo's id, or None without a row.

    Only the fields written are recorded, so a row that did not describe the file just
    before the write -- one Suggest made, or one the file has moved on from -- is not
    stamped (_describes_before), as record_tags does not stamp one: it would claim to
    match a file whose other fields, its Date Taken first, it never held. Its vectors
    still follow the file's new stamp. A loop of them hands every call one derived.Batch (the tag
    tree read once for the loop, not once a photo)."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    row = conn.execute("SELECT id, raw_metadata, mtime, size FROM photos WHERE " + where + " LIMIT 1",
                       params).fetchone()
    if row is None:
        return None
    photo_id, raw_json, row_mtime, row_size = row
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except (TypeError, ValueError):
        raw = {}
    keywords = captions = False
    identity = None
    for field, value in written.items():
        key = fields.read_key(field)
        texts = fields.field_values(value)
        if fields.scan_reads(key):
            bare = key.split(":", 1)[1]
            for name in (key, bare):
                if name != key and name not in raw:
                    continue
                if not texts:
                    raw.pop(name, None)
                elif key in fields.LIST_FIELDS or len(texts) > 1:
                    raw[name] = list(texts)
                else:
                    raw[name] = texts[0]
        keywords = keywords or key in vocabulary.KEYWORD_FIELDS + vocabulary.HIERARCHY_FIELDS
        captions = captions or key in vocabulary.CAPTION_FIELDS
        if key.endswith(":DocumentID"):
            identity = (texts[0] if texts else None,)
    columns, values = ["raw_metadata"], [json.dumps(raw)]
    if keywords:
        columns.append("tags")
        values.append(json.dumps(vocabulary.extract_tags(raw)))
    if captions:
        columns.append("captions")
        values.append(json.dumps(vocabulary.extract_captions(raw)))
    if identity is not None:
        columns.append("document_id")
        values.append(identity[0])
    if stat is not None and _describes_before(row_mtime, row_size, before):
        columns += ["mtime", "size"]
        values += [stat.st_mtime, stat.st_size]
    conn.execute("UPDATE photos SET %s WHERE id = ?" % ", ".join("%s = ?" % c for c in columns),
                 values + [photo_id])
    if stat is not None:
        embeddings.restamp(conn, photo_id, before, (stat.st_mtime, stat.st_size))
    people.rebuild(conn, [photo_id])
    face_tags.name_photos(conn, [photo_id])
    date_photos(conn, [photo_id])
    derived.refresh_photos(conn, [photo_id], batch)
    return photo_id


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

    Only the keyword fields are recorded, so a row that did not describe the file just
    before the write -- one Suggest made, or one the file has moved on from, or without
    `before` -- is not stamped (_describes_before): it would claim to match a file whose
    other fields, its Date Taken first, it never held.
    """
    if flat is None and hierarchical is None:
        flat, hierarchical = fields.expand_tag_fields(tags)
    try:
        stat = os.stat(photo_path)
    except OSError:
        stat = None

    def store(conn):
        cursor = conn.cursor()
        where, where_params = store_roots.sql_equals(conn, "path", photo_path)
        cursor.execute("SELECT id, raw_metadata, mtime, size FROM photos WHERE " + where, where_params)
        row = cursor.fetchone()
        if not row:
            return False   # never indexed; adding it here would be an index, not an edit
        photo_id, raw_json, row_mtime, row_size = row

        try:
            raw_meta = json.loads(raw_json) if raw_json else {}
        except Exception:
            raw_meta = {}
        fields.record_keyword_fields(raw_meta, flat or [], hierarchical or [])

        if stat is None or not _describes_before(row_mtime, row_size, before):
            cursor.execute("UPDATE photos SET tags = ?, raw_metadata = ? WHERE id = ?",
                           (json.dumps(tags), json.dumps(raw_meta), photo_id))
        else:
            cursor.execute("UPDATE photos SET tags = ?, raw_metadata = ?, mtime = ?, size = ? WHERE id = ?",
                           (json.dumps(tags), json.dumps(raw_meta), stat.st_mtime, stat.st_size, photo_id))
        if stat is not None:
            embeddings.restamp(conn, photo_id, before, (stat.st_mtime, stat.st_size))
        changed = cursor.rowcount > 0
        people.rebuild(conn, [photo_id])
        face_tags.name_photos(conn, [photo_id])
        date_photos(conn, [photo_id])
        derived.refresh_photos(conn, [photo_id])
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
        roots = store_roots.roots_for(conn)
        for path in photo_paths:
            path = paths.stored(path)
            where, where_params = store_roots.sql_equals(conn, "path", path)
            row = conn.execute("SELECT tags, raw_metadata FROM photos WHERE " + where,
                               where_params).fetchone()
            if not row:
                continue
            try:
                tags = json.loads(row[0]) if row[0] else []
                raw_meta = json.loads(store_roots.raw_to_native(row[1], roots)) if row[1] else {}
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
        roots = store_roots.roots_for(conn)
        for path, tags_json in conn.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL"):
            try:
                tags = json.loads(tags_json)
            except (TypeError, ValueError):
                continue
            if vocabulary.retag(tags, tag)[1]:
                found[paths.from_row(path, roots)] = tags
    finally:
        conn.close()
    return found


def tag_usage(db_path):
    """Photos per tag, a photo counting toward each level above its tags as well: a
    photo tagged Activity/Hiking counts for Activity. What the tree view shows.

    Once per photo: a photo carrying two tags under a node counted twice toward it and
    toward everything above it (docs/findings.md, #41).

    From `photo_tags` (migration 19) when the library has it: one pass of an index rolled up the tree's
    parents (store.library_view.keyword_counts), 40 ms on photo_index's scale where reading and parsing every
    photo's tags JSON was 350 ms, which held the Python process at page start and made every other first request
    wait behind it (docs/findings.md, #534). A library without the derived tables is counted from the JSON as it
    always was. The same counts, node for node (tests/test_tag_usage_from_photo_tags.py).
    """
    counts = {}
    if not os.path.exists(db_path):
        return counts
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            if derived.present(conn):
                from tagpup.store import library_view   # library_view imports this module's neighbours
                return {node["tag"]: node["count"] for node in library_view.keyword_counts(conn) if node["count"]}
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

    def update_row(conn):
        # The save read the file back whole (raw_meta): the row describes it now.
        photo_id = _stamp(conn, photo_path, stat.st_mtime, stat.st_size, before=before, whole=True)[0]
        where, where_params = store_roots.sql_equals(conn, "path", photo_path)
        changed = conn.execute(
            "UPDATE photos SET tags = ?, captions = ?, raw_metadata = ? WHERE " + where,
            (json.dumps(tags), json.dumps(captions),
             store_roots.raw_to_row(json.dumps(raw_meta), store_roots.roots_for(conn))) + where_params).rowcount
        if photo_id is not None:
            people.rebuild(conn, [photo_id])
            face_tags.name_photos(conn, [photo_id])
            date_photos(conn, [photo_id])
            derived.refresh_photos(conn, [photo_id])
        return changed

    return db.write_with_connection(
        db_path, update_row, label="index row for %s" % os.path.basename(photo_path))


# ---- What PhotoIndex reads and writes ---------------------------------------------------

#: A photo row as the index holds it, and its vector under one model: float32 bytes,
#: or None.
INDEX_COLUMNS = ("path", "mtime", "size", "tags", "people", "captions", "raw_metadata", "year", "vector")


def index_rows(conn, model):
    """Every photo row, INDEX_COLUMNS each, with its vector under `model`: the whole
    library, as the indexer compares it with the files."""
    return store_roots.natives(conn, conn.execute(
        "SELECT p.path, p.mtime, p.size, p.tags, " + PEOPLE_JSON + ", p.captions, p.raw_metadata, p.year, e.vector"
        " FROM photos p LEFT JOIN embeddings e ON e.photo_id = p.id AND e.model = ?", (model,)).fetchall(),
        0, raw=(6,))


def vectors(conn, model):
    """(photo id, vector bytes) of every photo holding a vector under `model`, by id: all
    PhotoIndex keeps in memory. Nothing else of the row is read."""
    return conn.execute("SELECT e.photo_id, e.vector FROM embeddings e JOIN photos p ON p.id = e.photo_id"
                        " WHERE e.model = ? ORDER BY e.photo_id", (model,)).fetchall()


def counts(conn, model):
    """(photos, photos holding a vector under `model`)."""
    return conn.execute("SELECT (SELECT COUNT(*) FROM photos),"
                        " (SELECT COUNT(*) FROM embeddings e JOIN photos p ON p.id = e.photo_id"
                        " WHERE e.model = ?)", (model,)).fetchone()


#: A photo's record as PhotoIndex answers it (records): its id first, then
#: RECORD_COLUMNS, and whether it holds a vector under the model asked about.
RECORD_COLUMNS = ("id", "path", "mtime", "size", "tags", "people", "captions", "raw_metadata", "year", "vectored")

#: Ids asked about at once: SQLite's limit on bound values is far above, and a list this
#: long is one statement's worth.
CHUNK = 500


def records(conn, model, photo_ids=None):
    """RECORD_COLUMNS of the photos `photo_ids` (in no particular order; an id with no row
    is left out), or of every photo: what a search answers with, read for its results
    alone. The vector is not read, only whether there is one."""
    select = ("SELECT p.id, p.path, p.mtime, p.size, p.tags, " + PEOPLE_JSON + ", p.captions, p.raw_metadata,"
              " p.year, EXISTS (SELECT 1 FROM embeddings e WHERE e.photo_id = p.id AND e.model = ?) FROM photos p")
    if photo_ids is None:
        return store_roots.natives(conn, conn.execute(select + " ORDER BY p.id", (model,)).fetchall(),
                                   1, raw=(7,))
    photo_ids = list(photo_ids)
    found = []
    for start in range(0, len(photo_ids), CHUNK):
        chunk = photo_ids[start:start + CHUNK]
        found += conn.execute(select + " WHERE p.id IN (%s)" % ",".join("?" * len(chunk)),
                              (model,) + tuple(chunk)).fetchall()
    return store_roots.natives(conn, found, 1, raw=(7,))


def _row_id(conn, photo_path):
    clause, params = store_roots.sql_equals(conn, "path", photo_path)
    row = conn.execute("SELECT id FROM photos WHERE " + clause + " LIMIT 1", params).fetchone()
    return row[0] if row else None


def _unread_row(conn, photo_path):
    return conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata)"
                        " VALUES (?, '[]', '[]', '{}')", (store_roots.to_row(conn, photo_path),)).lastrowid


def ensure_row(conn, photo_path, admit=False):
    """The id of a photo's row, making the row if it has none -- only in a folder the
    library holds or was asked to add (tagpup.store.folders.holds), unless `admit` (a migration that
    kept what an older version made). Raises NotHeld, and makes nothing, for a photo in
    any other folder.

    Every photo a face or an embedding is recorded for has a row (docs/findings.md,
    #48): Suggest detects faces in photos never indexed, and they were recorded against
    a path no row had. But a row makes its folder the library's: sync keeps the folder in
    step, the watcher watches it, and its new files are indexed and grow the tag tree
    from their tags. Suggest in a folder of another library's made 25 rows in kr-track
    that way, without asking (2026-09-28). A row made here holds the path and nothing
    read from the file: its mtime and size stay empty, so the folder scan and the refresh
    read the file rather than trust it. The caller commits.
    """
    photo_id = _row_id(conn, photo_path)
    if photo_id is not None:
        return photo_id
    if not admit:
        folder = os.path.dirname(paths.stored(photo_path))
        if not folders.holds(conn, folder):
            raise NotHeld(folder)
    photo_id = _unread_row(conn, photo_path)
    date_photos(conn, [photo_id])
    derived.refresh_photos(conn, [photo_id])
    return photo_id


def stored_spelling(conn, photo_path):
    """The path a photo's row is stored under -- native, as its row spells it -- or None if
    it has no row."""
    clause, params = store_roots.sql_equals(conn, "path", photo_path)
    row = conn.execute("SELECT path FROM photos WHERE " + clause + " LIMIT 1", params).fetchone()
    return store_roots.from_row(conn, row[0]) if row else None


def record_indexed(conn, photo_path, row, model=None, known=None, batch=None):
    """Record what indexing read of a photo: `row` has mtime, size, tags, captions,
    raw_metadata (as values), embedding (bytes) and document_id; the embedding is kept
    under `model`, stamped with the row's mtime and size, and the photo's people are
    rebuilt. The caller commits.

    A photo indexed before is updated in place, under the spelling its row already has.
    faces.photo_id references photos.id ON DELETE CASCADE, so anything that deletes
    the row -- INSERT OR REPLACE is a delete and an insert -- takes every face with it:
    names given by hand, "nobody" decisions and exclusions. Re-indexing a changed photo
    did exactly that. A document_id already recorded is kept when the file has none.

    Begins a write transaction first, if none is open (store_roots.begin_write): the index
    writes a batch on a connection it commits, and every path of the batch is converted by the
    roots the library has as the write lock is taken. `batch` is the run's derived.Batch: the
    tag tree and the folders found, shared by the photos of one transaction so that each costs
    no read of either.
    """
    store_roots.begin_write(conn)
    roots = store_roots.roots_for(conn)
    held = stored_spelling(conn, photo_path)
    stored = held or paths.stored(photo_path)
    row_path = paths.to_row(stored, roots)
    conn.execute(
        "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata, document_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(path) DO UPDATE SET"
        " mtime = excluded.mtime, size = excluded.size, tags = excluded.tags,"
        " captions = excluded.captions, raw_metadata = excluded.raw_metadata,"
        " document_id = COALESCE(excluded.document_id, photos.document_id)",
        (row_path, row.get("mtime", 0.0), row.get("size", 0), json.dumps(row.get("tags", [])),
         json.dumps(row.get("captions", [])),
         store_roots.raw_to_row(json.dumps(row.get("raw_metadata", {})), roots), row.get("document_id")))
    people.rebuild_photos(conn, [stored], known)
    _dated_paths(conn, [stored])
    photo_id = _row_id(conn, stored)
    if held:
        # A photo read again: its faces are there, and its keywords may have changed. A new
        # photo has none yet; the faces recorded for it name themselves (services.faces).
        face_tags.name_photos(conn, [photo_id], vocabulary=known, batch=batch)
    derived.record(conn, photo_id, row_path, row.get("tags", []), row.get("raw_metadata", {}), batch)
    if row.get("embedding") is not None:
        if model is None:
            # Dropped without a word, it left a library without the vector (#85).
            raise ValueError("An embedding is kept under the model that made it; none was named.")
        embeddings.put(conn, stored, model, row.get("mtime", 0.0), row.get("size", 0), row["embedding"])


def remove(conn, photo_paths):
    """Delete the rows of `photo_paths` and their faces, whether or not the connection
    enforces foreign keys. Returns photo rows deleted. The caller commits."""
    store_roots.begin_write(conn)
    removed = 0
    for photo_path in photo_paths:
        faces.remove_for_photo(conn, photo_path)
        clause, params = store_roots.sql_equals(conn, "path", photo_path)
        removed += conn.execute("DELETE FROM photos WHERE " + clause, params).rowcount
    derived.prune(conn)
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
    photos_where, photos_params = store_roots.sql_under(conn, "path", folder)
    faces_where = "photo_id IN (SELECT id FROM photos WHERE %s)" % photos_where
    faces_params = photos_params
    manual = conn.execute("SELECT COUNT(*) FROM faces WHERE " + faces_where
                          + " AND name_source = 'manual'", faces_params).fetchone()[0]
    excluded = conn.execute("SELECT COUNT(*) FROM faces WHERE " + faces_where
                            + " AND excluded = 1", faces_params).fetchone()[0]
    faces_removed = conn.execute("DELETE FROM faces WHERE " + faces_where, faces_params).rowcount
    photos_removed = conn.execute("DELETE FROM photos WHERE " + photos_where, photos_params).rowcount
    derived.prune(conn)
    # Out of the library: what was asked to be added there goes too, or a Suggest would
    # make it the library's again unasked.
    added_folders.forget_under(conn, folder)
    damaged_files.forget_under(conn, folder)
    return dict(photos_removed=photos_removed, faces_removed=faces_removed,
                manual_lost=manual, excluded_lost=excluded)


# ---- What the thumbnail cache reads -----------------------------------------------------

#: A photo as the thumbnail cache asks for it: its id, its path as the row holds it (what the cache's
#: entry is keyed by, so that a library moved to another place keeps its entries), its native path and its
#: damaged_files record, if any.
ThumbRow = collections.namedtuple("ThumbRow", "id row_path path damaged")


def thumb_row(conn, photo_id):
    """The ThumbRow of the photo with this id, or None: one read by primary key, never a BLOB. Raises
    paths.RootsError for a photo under a root this machine does not place."""
    row = conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        return None
    native = store_roots.from_row(conn, row[0])
    return ThumbRow(photo_id, row[0], native, damaged_files.one(conn, native))


def thumb_rows(conn, folder=None, after=0, limit=1000):
    """[(id, path as the row holds it, native path)] of the photos with an id above `after`, in id order, at
    most `limit`: those under `folder` at any depth, or every one. What warming the cache walks, a batch
    at a time."""
    query, params = "SELECT id, path FROM photos WHERE id > ?", [after]
    if folder:
        where, folder_params = store_roots.sql_under(conn, "path", folder)
        query += " AND " + where
        params += list(folder_params)
    rows = conn.execute(query + " ORDER BY id LIMIT ?", params + [limit]).fetchall()
    return [(photo_id, row_path, store_roots.from_row(conn, row_path)) for photo_id, row_path in rows]


def row_paths(conn):
    """{photo id: path as the row holds it} of every photo: what sweeping the cache compares its entries
    with. 68,000 short strings; one scan of the table, once per sweep."""
    return dict(conn.execute("SELECT id, path FROM photos"))


def ids_under(conn, folder):
    """The ids of the photos under `folder`, at any depth: one range of the path index."""
    where, params = store_roots.sql_under(conn, "path", folder)
    return [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params)]


def ids_at(conn, photo_paths):
    """The ids of the photos at `photo_paths`, however spelled: one indexed lookup a path."""
    return _ids_of(conn, photo_paths)


# ---- What TagTuner's screens read -----------------------------------------------------

def details(conn, photo_path):
    """(people JSON, tags JSON, captions JSON, mtime, year) of one photo, or None."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
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
    roots = store_roots.roots_for(conn)
    for path, tags_json, mtime in conn.execute("SELECT path, tags, mtime FROM photos"):
        try:
            tags = json.loads(tags_json or "[]")
        except (TypeError, ValueError):
            continue
        if tag in tags:
            found.append((paths.from_row(path, roots), tags, mtime))
    return found


def count_under(conn, folder):
    """How many photos under `folder`, at any depth, the library holds."""
    where, params = store_roots.sql_under(conn, "path", folder)
    return conn.execute("SELECT COUNT(*) FROM photos WHERE " + where, params).fetchone()[0]


def rows_under(conn, folder):
    """(path, mtime, size, tags JSON, people JSON, captions JSON, raw_metadata JSON) of
    each photo under a folder, at any depth."""
    where, params = store_roots.sql_under(conn, "path", folder)
    return store_roots.natives(conn, conn.execute(
        "SELECT p.path, p.mtime, p.size, p.tags, " + PEOPLE_JSON + ", p.captions,"
        " p.raw_metadata FROM photos p WHERE " + where, params).fetchall(), 0, raw=(6,))


# ---- What refresh_rows_from_files reads and writes --------------------------------------

def rows_to_check(conn, folder=None):
    """(path, mtime, size, tags JSON, captions JSON, raw_metadata JSON, people JSON, id)
    of every photo, or of those under `folder`: what is compared with the files."""
    query = ("SELECT p.path, p.mtime, p.size, p.tags, p.captions, p.raw_metadata, " + PEOPLE_JSON
             + ", p.id FROM photos p")
    params = ()
    if folder:
        where, params = store_roots.sql_under(conn, "path", folder)
        query += " WHERE " + where
    return store_roots.natives(conn, conn.execute(query, params).fetchall(), 0, raw=(5,))


def stamps(conn, folder=None):
    """(id, path as stored, mtime, size) of every photo, or of those under `folder`: what
    sync compares with the disk (tagpup.services.sync). Nothing else of the row is read."""
    query, params = "SELECT id, path, mtime, size FROM photos", ()
    if folder:
        where, params = store_roots.sql_under(conn, "path", folder)
        query += " WHERE " + where
    return store_roots.natives(conn, conn.execute(query, params).fetchall(), 1)


def row_as_recorded(conn, stored_path):
    """(tags, people, captions, raw_metadata, mtime, size, document_id) of the row stored
    under exactly `stored_path`, or None."""
    return store_roots.native_one(conn, conn.execute(
        "SELECT p.tags, " + PEOPLE_JSON + ", p.captions, p.raw_metadata, p.mtime, p.size,"
        " p.document_id FROM photos p WHERE p.path = ?", (store_roots.to_row(conn, stored_path),)).fetchone(),
        raw=(3,))


# ---- What relink_renamed_photos reads ---------------------------------------------------

def all_paths(conn):
    """Every photo's path, as stored."""
    roots = store_roots.roots_for(conn)
    return [paths.from_row(path, roots) for (path,) in conn.execute("SELECT path FROM photos")]


def identities(conn):
    """{path as stored: document_id} for every photo whose identity is recorded."""
    roots = store_roots.roots_for(conn)
    return {paths.from_row(path, roots): str(doc_id).strip() for path, doc_id in conn.execute(
        "SELECT path, document_id FROM photos WHERE document_id IS NOT NULL") if path and doc_id}


def tags_by_photo(conn):
    """(id, path as stored, tags) of each photo; a row whose tags cannot be read is left
    out."""
    found = []
    roots = store_roots.roots_for(conn)
    for photo_id, path, tags_json in conn.execute("SELECT id, path, tags FROM photos"):
        try:
            found.append((photo_id, paths.from_row(path, roots), json.loads(tags_json or "[]")))
        except (TypeError, ValueError):
            continue
    return found


# ---- What backfill_document_ids reads and writes ----------------------------------------

def without_identity(conn):
    """The paths, as stored, of photos with no document_id recorded. Raises on a library
    from before the column."""
    roots = store_roots.roots_for(conn)
    return [paths.from_row(path, roots) for (path,) in conn.execute(
        "SELECT path FROM photos WHERE document_id IS NULL OR document_id = ''") if path]


def record_identity(conn, photo_path, document_id, stat=None, before=None):
    """Record a photo's document_id; with `stat`, the mtime and size the file has now it
    was written to, where the row described the file at `before`, its stamp just before
    the write (_describes_before), over which its vectors are carried. Returns rows
    changed. The caller commits."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    if stat is not None:
        _stamp(conn, photo_path, stat.st_mtime, stat.st_size, before=before)
    return conn.execute("UPDATE photos SET document_id = ? WHERE " + where,
                        (document_id,) + params).rowcount

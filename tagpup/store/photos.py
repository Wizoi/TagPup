"""The photos table.

Recording what a write did to a file or what was read back from it, moving renamed photos' rows, forgetting a deleted photo,
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
from tagpup.store import added_folders, db, derived, embeddings, face_tags, faces, folders, people
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


def same_mtime(row_mtime, file_mtime):
    """Are a row's modified time and its file's the same (within MTIME_TOLERANCE)? The one rule: the scan, refresh
    and the MCP's comparison of a row with its file go by it. None for either is no."""
    return row_mtime is not None and file_mtime is not None and abs(row_mtime - file_mtime) < MTIME_TOLERANCE


def describes(row_mtime, row_size, stamp):
    """Does a row stamped (`row_mtime`, `row_size`) describe the file whose stamp is
    `stamp`, (mtime, size)? What the folder scan trusts a row by; None for any of them
    is no."""
    if row_mtime is None or row_size is None or not stamp or None in stamp:
        return False
    return row_size == stamp[1] and same_mtime(row_mtime, stamp[0])


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


def _ids_at(cursor, path):
    where, params = store_roots.sql_equals(cursor.connection, "path", path)
    return [r[0] for r in cursor.execute("SELECT id FROM photos WHERE " + where, params)]


def move_rows_in(conn, renames):
    """Move index rows from each old path to its new one, in the caller's transaction: the file
    journal marks a rename done in the transaction that moves its row. Its faces and vectors point
    at the row by id, so they go with it.

    `renames` maps old path to new path, in any spelling. Returns (moved, skipped): how many photo
    rows actually changed, and the (old, new) pairs left where they were because the new path
    already had rows. Re-pointing rows at a path that already has them is how 233 duplicate faces
    were made, so a destination is checked first and an occupied one is reported rather than merged
    into. A destination is free when nothing is there, when it is the same file (a rename that
    only changes case), or when whatever is there is itself moving away in this same call -- a
    rename that shuffles numbered files among themselves, or the occupant of a name moved aside to
    make room. The rows are moved by id, through a placeholder, so a shuffle never collides with
    itself on the way."""
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


def follow_fields(conn, photo_path, written, stat=None, before=None, batch=None, whole=None):
    """Make a photo's row say what the file journal just left in its file: `written` is
    {field: value} of the fields written, forward, again after a crash, or back in an
    undo (tagpup.services.file_changes). The caller commits, in the transaction that marks
    the file done.

    For every field: raw_metadata takes each field the scan reads (fields.scan_reads; under its bare name too, where the scan
    stored one), a cleared field taken out; the tags are derived again when a keyword
    field was written, the captions when a caption field was, document_id follows the
    identity. With `stat`, the file's new mtime and size, its vectors carried over
    `before`, its stamp just before the write (_stamp). Without it -- a file read and not
    written -- the stamp stays, so the scan still reads what the row does not say. The
    photo's people and dates are rebuilt. Returns the photo's id, or None without a row.

    Only the fields written are recorded, so a row that did not describe the file just
    before the write -- one Suggest made, or one the file has moved on from -- is not
    stamped (_describes_before): it would claim to match a file whose other fields, its
    Date Taken first, it never held. Its vectors
    still follow the file's new stamp. A loop of them hands every call one derived.Batch (the tag
    tree read once for the loop, not once a photo).

    `whole` is the file read back whole after the write (every field the scan reads, in the
    indexer's shape: tagpup.files.metadata.raw_metadata), as a save reads it: it is the row's
    raw_metadata, its tags and captions are derived from it, and the row is stamped with `stat`
    whatever it described before, since everything it says was just read."""
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
    keywords = captions = whole is not None
    identity = None
    if whole is not None:
        raw = dict(whole)
    for field, value in written.items():
        key = fields.read_key(field)
        texts = fields.field_values(value)
        if whole is None and fields.scan_reads(key):
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
    raw_text = json.dumps(raw)
    columns, values = ["raw_metadata"], [raw_text if whole is None else store_roots.raw_to_row(raw_text, store_roots.roots_for(conn))]
    if keywords:
        columns.append("tags")
        values.append(json.dumps(vocabulary.extract_tags(raw)))
    if captions:
        columns.append("captions")
        values.append(json.dumps(vocabulary.extract_captions(raw)))
    if identity is not None:
        columns.append("document_id")
        values.append(identity[0])
    if stat is not None and (whole is not None or _describes_before(row_mtime, row_size, before)):
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

    From `photo_tags`: one pass of an index rolled up the tree's parents (store.library_view.keyword_counts), 40 ms on
    photo_index's scale where reading and parsing every photo's tags JSON was 350 ms, which held the Python process at
    page start and made every other first request wait behind it (docs/findings.md, #534).
    """
    counts = {}
    if not os.path.exists(db_path):
        return counts
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            from tagpup.store import library_view   # library_view imports this module's neighbours
            return {node["tag"]: node["count"] for node in library_view.keyword_counts(conn) if node["count"]}
        finally:
            conn.close()
    except Exception:
        pass
    return counts


# ---- What PhotoIndex reads and writes ---------------------------------------------------

def vectors(conn, model):
    """(photo id, vector bytes) of every photo holding a vector under `model`, by id: all
    PhotoIndex keeps in memory. Nothing else of the row is read."""
    return conn.execute("SELECT e.photo_id, e.vector FROM embeddings e JOIN photos p ON p.id = e.photo_id"
                        " WHERE e.model = ? ORDER BY e.photo_id", (model,)).fetchall()


def vector_dim(conn, model):
    """The length of the vectors held under `model`, read from one of them; None when there is none.
    The vectors' bytes are four to a number (float32)."""
    row = conn.execute("SELECT length(vector) / 4 FROM embeddings WHERE model = ? LIMIT 1", (model,)).fetchone()
    return row[0] if row else None


def stamps_with_vectors(conn, model):
    """(path, mtime, size, holds a vector under `model`) of every photo, native paths: all the
    indexer needs to know whether a file is as it was. None of the JSON of the row is read."""
    return store_roots.natives(conn, conn.execute(
        "SELECT p.path, p.mtime, p.size, EXISTS (SELECT 1 FROM embeddings e WHERE e.photo_id = p.id AND e.model = ?)"
        " FROM photos p", (model,)).fetchall(), 0)


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
    if not (batch is not None and batch.prepared) and derived.make_nodes(conn, [row.get("tags", [])], batch):
        known = None   # the vocabulary the caller read holds none of the people just made
    people.rebuild_photos(conn, [stored], known)
    _dated_paths(conn, [stored])
    photo_id = _row_id(conn, stored)
    if held:
        # A photo read again: its faces are there, and its keywords may have changed. A new
        # photo has none yet; the faces recorded for it name themselves (services.faces).
        face_tags.name_photos(conn, [photo_id], vocabulary=known, batch=batch)
    derived.record(conn, photo_id, row_path, row.get("tags", []), batch)
    if row.get("embedding") is not None:
        if model is None:
            # Dropped without a word, it left a library without the vector (#85).
            raise ValueError("An embedding is kept under the model that made it; none was named.")
        embeddings.put(conn, stored, model, row.get("mtime", 0.0), row.get("size", 0), row["embedding"])


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
    return dict(photos_removed=photos_removed, faces_removed=faces_removed,
                manual_lost=manual, excluded_lost=excluded)


# ---- What the thumbnail cache reads -----------------------------------------------------

#: A photo as the thumbnail cache asks for it: its id, its path as the row holds it (what the cache's
#: entry is keyed by, so that a library moved to another place keeps its entries), and its native path.
ThumbRow = collections.namedtuple("ThumbRow", "id row_path path")


def thumb_row(conn, photo_id):
    """The ThumbRow of the photo with this id, or None: one read by primary key, never a BLOB. Raises
    paths.RootsError for a photo under a root this machine does not place."""
    row = conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        return None
    native = store_roots.from_row(conn, row[0])
    return ThumbRow(photo_id, row[0], native)


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


# ---- What pairing a missing row with a file reads ---------------------------------------------------

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

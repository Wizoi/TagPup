"""A library's tables, and the migrations that bring a library to them.

The only place a table, a column, an index or a trigger is made. There were four:
PhotoIndex.load, TagTuner's start-up (without the write lock), the desktop runner
(whose faces table had none of the columns every screen now filters on), and the tag
tree, which made its own table when it saved. Each carried its own idea of the schema.

A migration runs once per library, in order, and `schema_version` records it. The
first makes the tables as they stood in 2026-09, when every library the owner has was
already that shape; the rest are ordinary steps forward. A library older than that is
refused, not converted: the code that converted older libraries -- missing columns,
'Non Person' names, is_people -- ran on every open for months, had nothing left to
convert, and retired on 2026-09-24.

`ensure` is called wherever a library is opened, including each request that names
one. After the first time in a process it costs a stat.
"""
import collections
import logging
import os
import sqlite3
import threading
import time

from tagpup.core import paths
from tagpup.store import db

logger = logging.getLogger(__name__)

#: One step. `changes_data` says whether it rewrites what people decided or what files
#: hold, which is worth a backup first; making a table, an index or a trigger is not.
Migration = collections.namedtuple("Migration", "version name apply changes_data")


def _columns(conn, table):
    return [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]


#: The columns a library of 2026-09 has, which the older ones lacked. A library without
#: them is older than TagPup opens.
REQUIRED_COLUMNS = {
    "photos": ("document_id",),
    # crop_image is not among them: migration 3 moves it to face_crops.
    "faces": ("prob", "name_source", "excluded", "excluded_reason"),
    "tag_taxonomy": ("has_face", "hidden_from_autocomplete"),
}


class TooOld(Exception):
    """A library older than the tables of 2026-09, which TagPup no longer converts."""


def _tables(conn):
    """The tables of 2026-09, and their indexes. A library that already has tables must
    have their columns: it is refused, not converted, when it does not."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            path TEXT PRIMARY KEY,
            mtime REAL,
            size INTEGER,
            tags TEXT,
            people TEXT,
            captions TEXT,
            raw_metadata TEXT,
            embedding BLOB,
            document_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_path TEXT,
            box TEXT,
            embedding BLOB,
            name TEXT,
            crop_image BLOB,
            prob REAL,
            name_source TEXT,
            excluded INTEGER DEFAULT 0,
            excluded_reason TEXT,
            FOREIGN KEY(photo_path) REFERENCES photos(path) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embedding_cache (
            path TEXT PRIMARY KEY,
            mtime REAL,
            size INTEGER,
            model_name TEXT,
            pretrained TEXT,
            preserve_full_frame INTEGER,
            max_aspect_ratio REAL,
            force_image_size INTEGER,
            embedding BLOB
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_taxonomy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT UNIQUE,
            parent_id INTEGER,
            name TEXT,
            has_face INTEGER DEFAULT 0,
            hidden_from_autocomplete INTEGER DEFAULT 0,
            FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_embeddings (
            tag TEXT,
            prompt TEXT,
            model_name TEXT,
            pretrained TEXT,
            embedding BLOB,
            PRIMARY KEY (tag, prompt, model_name, pretrained)
        )
    """)

    missing = ["%s.%s" % (table, column) for table, columns in REQUIRED_COLUMNS.items()
               for column in columns if column not in _columns(conn, table)]
    if missing:
        raise TooOld("This library is older than TagPup opens: it has no %s. A version of "
                     "TagPup from before 2026-09-24 converts it." % ", ".join(missing))

    conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_photo_path ON faces(photo_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_name ON faces(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_taxonomy_tag ON tag_taxonomy(tag)")
    # Only a library that gained the column by migration had this one: a new library
    # made document_id in its CREATE TABLE and never got the index.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_document_id ON photos(document_id)")
    # Identify Faces filters on `excluded` and `name` together, on every load. Leading
    # with `excluded` answers a count of the excluded bucket from the index alone,
    # without touching rows that carry a 2 KB embedding and a 6 KB crop apiece.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_identify ON faces(excluded, name)")
    # Paths compare the way the filesystem does (paths.sql_equals): without case on
    # Windows. An equality under a collation can only use an index declared with it.
    if paths.COLLATE != "BINARY":
        for index_name, table, column in (
            ("idx_photos_path_nocase", "photos", "path"),
            ("idx_faces_photo_path_nocase", "faces", "photo_path"),
            ("idx_embedding_cache_path_nocase", "embedding_cache", "path"),
        ):
            conn.execute("CREATE INDEX IF NOT EXISTS %s ON %s(%s COLLATE %s)"
                         % (index_name, table, column, paths.COLLATE))


#: What moves each generation (tagpup.store.generations). Faces: whatever changes who a
#: face is -- a crop being cached is not that. The tree: any node's path, name, place
#: or face flag. Photos: any change to a row.
GENERATION_TRIGGERS = (
    ("faces", "faces", "UPDATE OF name, name_source, excluded, embedding, photo_path"),
    ("taxonomy", "tag_taxonomy", "UPDATE OF tag, name, parent_id, has_face"),
    ("photos", "photos", "UPDATE"),
)


def _generations(conn):
    """One `generations` table for photos, faces and the tag tree.

    Replaces faces_generation and taxonomy_generation, one table and three triggers
    each, and adds photos, which nothing counted: Suggest's index watched a sum of
    mtimes, and missed a change to a row's people, which does not touch mtime.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS generations ("
                 " name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    existing = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for name, table, update in GENERATION_TRIGGERS:
        start = 0
        old = "%s_generation" % name
        if old in existing:
            row = conn.execute("SELECT generation FROM %s WHERE id = 1" % old).fetchone()
            start = row[0] if row else 0
            for event in ("insert", "delete", "update"):
                conn.execute("DROP TRIGGER IF EXISTS %s_%s" % (old, event))
            conn.execute("DROP TABLE %s" % old)
        conn.execute("INSERT OR IGNORE INTO generations (name, value) VALUES (?, ?)", (name, start))
        bump = ("BEGIN UPDATE generations SET value = value + 1 WHERE name = '%s'; END" % name)
        for event, when in (("insert", "INSERT"), ("delete", "DELETE"), ("update", update)):
            conn.execute("CREATE TRIGGER IF NOT EXISTS generation_%s_%s AFTER %s ON %s %s"
                         % (name, event, when, table, bump))


def _face_crops(conn):
    """Each face's crop in `face_crops`, out of `faces`.

    6 KB of JPEG a face, 1.1 GB of photo_index, carried by every read of the faces table
    that forgot to leave it out, and by every rebuild of it. A trigger takes a face's
    crop with the face, whichever connection deletes it: only one connection in the app
    turns foreign keys on, so ON DELETE CASCADE alone would leave crops behind. The
    column goes; the space it held is free within the file until it is vacuumed.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS face_crops ("
                 " face_id INTEGER PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,"
                 " jpeg BLOB NOT NULL)")
    conn.execute("CREATE TRIGGER IF NOT EXISTS face_crops_go_with_their_face AFTER DELETE ON faces"
                 " BEGIN DELETE FROM face_crops WHERE face_id = OLD.id; END")
    # A library whose record of migrations was lost has run this before, and has no
    # column left to move.
    if "crop_image" in _columns(conn, "faces"):
        conn.execute("INSERT OR IGNORE INTO face_crops (face_id, jpeg)"
                     " SELECT id, crop_image FROM faces WHERE crop_image IS NOT NULL")
        conn.execute("ALTER TABLE faces DROP COLUMN crop_image")


#: What moves the faces generation once faces point at photos by id (migration 4).
FACES_GENERATION_UPDATE = "UPDATE OF name, name_source, excluded, embedding, photo_id"


def _photo_ids(conn):
    """Photos get an integer id, and faces point at a photo by it (docs/ARCHITECTURE.md,
    principle 3). A photo's path was its identity: every rename re-pointed its faces by
    hand, and a face spelled apart from its photo's row joined to nothing -- the joins
    compared paths with case, where every lookup did not.

    Each photo keeps its rowid as its id. A face whose photo has no row gets one first,
    the path and nothing read from the file (photos.ensure_row), so no face is lost;
    a face finds its photo by paths.key, so a face that spelled its photo apart from the
    row -- in case, or in its separators, which no collation equates -- still finds it.
    Both tables are rebuilt, with their indexes and triggers; a face keeps its id, so
    its crop follows, and neither table gives out an id it gave before.

    Dropping a table on a connection with foreign keys on deletes its rows first, and
    every face and crop with them: schema._ensure connects without, and this checks.
    """
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("Migration 4 rebuilds photos and faces, which with foreign keys on"
                           " would delete every face first; nothing was changed")
    collate = paths.COLLATE
    # A row for every photo a face is on, one per file however the faces spelled it. A
    # face goes to the row it spells exactly, else to the file's row, else to a row
    # made for it in the stored form, as photos.ensure_row makes one.
    exact, ids = {}, {}
    for photo_id, path in conn.execute("SELECT rowid, path FROM photos"):
        exact[path] = photo_id
        ids.setdefault(paths.key(path), photo_id)
    conn.execute("CREATE TEMP TABLE face_photo (photo_path TEXT PRIMARY KEY, photo_id INTEGER NOT NULL)")
    for (face_path,) in conn.execute("SELECT DISTINCT photo_path FROM faces WHERE photo_path IS NOT NULL").fetchall():
        key = paths.key(face_path)
        if face_path not in exact and key not in ids:
            ids[key] = conn.execute("INSERT INTO photos (path, tags, people, captions, raw_metadata)"
                                    " VALUES (?, '[]', '[]', '[]', '{}')", (paths.stored(face_path),)).lastrowid
        conn.execute("INSERT INTO face_photo VALUES (?, ?)", (face_path, exact.get(face_path, ids.get(key))))
    face_counter = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'faces'").fetchone()

    conn.execute("""
        CREATE TABLE photos_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            mtime REAL,
            size INTEGER,
            tags TEXT,
            people TEXT,
            captions TEXT,
            raw_metadata TEXT,
            embedding BLOB,
            document_id TEXT
        )
    """)
    conn.execute("INSERT INTO photos_new (id, path, mtime, size, tags, people, captions, raw_metadata,"
                 " embedding, document_id) SELECT rowid, path, mtime, size, tags, people, captions,"
                 " raw_metadata, embedding, document_id FROM photos")
    conn.execute("DROP TABLE photos")
    conn.execute("ALTER TABLE photos_new RENAME TO photos")
    conn.execute("CREATE INDEX idx_photos_document_id ON photos(document_id)")
    if collate != "BINARY":
        conn.execute("CREATE INDEX idx_photos_path_nocase ON photos(path COLLATE %s)" % collate)

    conn.execute("""
        CREATE TABLE faces_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            box TEXT,
            embedding BLOB,
            name TEXT,
            prob REAL,
            name_source TEXT,
            excluded INTEGER DEFAULT 0,
            excluded_reason TEXT
        )
    """)
    # A face with no path at all has no photo to point at, and is reported rather than
    # dropped: the NOT NULL would otherwise stop the migration with no word of why.
    before, pathless = conn.execute("SELECT COUNT(*), COUNT(*) - COUNT(photo_path) FROM faces").fetchone()
    if pathless:
        raise RuntimeError("%d of %d faces name no photo; nothing was changed" % (pathless, before))
    conn.execute("INSERT INTO faces_new (id, photo_id, box, embedding, name, prob, name_source, excluded,"
                 " excluded_reason) SELECT f.id, m.photo_id, f.box, f.embedding, f.name, f.prob,"
                 " f.name_source, f.excluded, f.excluded_reason"
                 " FROM faces f JOIN temp.face_photo m ON m.photo_path = f.photo_path")
    after = conn.execute("SELECT COUNT(*) FROM faces_new").fetchone()[0]
    if after != before:
        raise RuntimeError("%d of %d faces found no photo; nothing was changed" % (before - after, before))
    conn.execute("DROP TABLE temp.face_photo")
    conn.execute("DROP TABLE faces")
    conn.execute("ALTER TABLE faces_new RENAME TO faces")
    # The faces that exist set the new counter; the old one also counted those deleted.
    if face_counter:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'faces'", (face_counter[0],))
    conn.execute("CREATE INDEX idx_faces_photo_id ON faces(photo_id)")
    conn.execute("CREATE INDEX idx_faces_name ON faces(name)")
    conn.execute("CREATE INDEX idx_faces_identify ON faces(excluded, name)")

    # Dropping the tables took their triggers.
    for name, table, update in (("photos", "photos", "UPDATE"),
                                ("faces", "faces", FACES_GENERATION_UPDATE)):
        bump = "BEGIN UPDATE generations SET value = value + 1 WHERE name = '%s'; END" % name
        for event, when in (("insert", "INSERT"), ("delete", "DELETE"), ("update", update)):
            conn.execute("CREATE TRIGGER generation_%s_%s AFTER %s ON %s %s" % (name, event, when, table, bump))
    conn.execute("CREATE TRIGGER face_crops_go_with_their_face AFTER DELETE ON faces"
                 " BEGIN DELETE FROM face_crops WHERE face_id = OLD.id; END")


def _embeddings(conn):
    """Each photo's CLIP vectors in `embeddings`, one per model, stamped with the file
    they were computed from (tagpup.store.embeddings; docs/findings.md, #62, #65).

    `photos.embedding` and `embedding_cache` held the same vectors -- identical, byte for
    byte, in every photo both had -- but only the cache said which model made them. So
    the cache says which model; the stamp is the photo row's, which the app's own
    metadata writes kept in step with the file, where the cache's fell behind at every
    keyword write. A vector in `photos.embedding` with no cache row to name its model
    is left out: nothing says what it can be compared with, and the next Suggest or
    index computes it again. Cached vectors for photos the library no longer has go:
    derived data about files that are not in it. Both old stores go.
    """
    from tagpup.store import embeddings   # the store imports this module
    conn.execute("CREATE TABLE embeddings ("
                 " photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,"
                 " model TEXT NOT NULL, mtime REAL, size INTEGER, vector BLOB NOT NULL,"
                 " PRIMARY KEY (photo_id, model))")
    conn.execute("CREATE TRIGGER embeddings_go_with_their_photo AFTER DELETE ON photos"
                 " BEGIN DELETE FROM embeddings WHERE photo_id = OLD.id; END")
    rows = {}
    for photo_id, path, mtime, size, vector in conn.execute(
            "SELECT id, path, mtime, size, embedding FROM photos").fetchall():
        rows[paths.key(path)] = (photo_id, mtime, size, vector)
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    kept = set()
    if "embedding_cache" in tables:
        for (path, mtime, size, model_name, pretrained, full_frame, aspect, image_size,
             vector) in conn.execute(
                "SELECT path, mtime, size, model_name, pretrained, preserve_full_frame,"
                " max_aspect_ratio, force_image_size, embedding FROM embedding_cache").fetchall():
            photo = rows.get(paths.key(path))
            if photo is None or not vector or model_name is None:
                continue
            photo_id, row_mtime, row_size, row_vector = photo
            model = embeddings.model_key(model_name, pretrained, bool(full_frame), aspect, image_size)
            # The row's stamp where the row holds this very vector; the cache's own else.
            stamp = (row_mtime, row_size) if row_vector == vector else (mtime, size)
            conn.execute("INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, size, vector)"
                         " VALUES (?, ?, ?, ?, ?)", (photo_id,) + (model,) + stamp + (vector,))
            kept.add(photo_id)
        conn.execute("DROP TABLE embedding_cache")
    unnamed = sum(1 for photo_id, _m, _s, vector in rows.values() if vector and photo_id not in kept)
    if unnamed:
        logger.info("%d photo vector(s) had no model to name them; they are computed again", unnamed)
    conn.execute("ALTER TABLE photos DROP COLUMN embedding")


def _photo_people(conn):
    """Each photo's people in `photo_people`, written only by tagpup.store.people.rebuild
    (docs/findings.md, #63), in place of the `photos.people` list.

    Every photo is rebuilt by the rule from its keywords, its faces and the tree. On
    both libraries of 2026-09-24 that gave every photo the people its list held, bar
    one listed in another order. A photo's people moving moves the photos generation,
    as a change to the list did.
    """
    from tagpup.store import people   # the store imports this module
    conn.execute("CREATE TABLE photo_people ("
                 " photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,"
                 " position INTEGER NOT NULL, name TEXT NOT NULL,"
                 " source TEXT NOT NULL CHECK (source IN ('keyword', 'face')),"
                 " PRIMARY KEY (photo_id, position))")
    conn.execute("CREATE INDEX idx_photo_people_name ON photo_people(name)")
    conn.execute("CREATE TRIGGER photo_people_go_with_their_photo AFTER DELETE ON photos"
                 " BEGIN DELETE FROM photo_people WHERE photo_id = OLD.id; END")
    people.rebuild(conn)
    for event in ("insert", "delete"):
        conn.execute("CREATE TRIGGER generation_photos_people_%s AFTER %s ON photo_people"
                     " BEGIN UPDATE generations SET value = value + 1 WHERE name = 'photos'; END"
                     % (event, event.upper()))
    conn.execute("ALTER TABLE photos DROP COLUMN people")


def _suggestions_file(conn):
    """The JSON file beside the library on `conn` that held its saved suggestions until
    migration 7, named as tagpup.jobs.suggestions named it; None for a library in memory."""
    main = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not main:
        return None
    name = os.path.splitext(os.path.basename(main))[0]
    base = "gui_suggestions_cache.json" if name == "photo_index" else "gui_suggestions_cache_%s.json" % name
    return os.path.join(os.path.dirname(main), base)


def _suggestions(conn):
    """What Suggest offered each photo, in `suggestions`, by the photo's id
    (tagpup.store.suggestions; docs/findings.md, #64), in place of a JSON file beside the
    library that nothing kept in step with the photos.

    The file's entries are taken in for the photos the library has a row for, and for
    files still on disk, which get a row as a photo Suggest saw does (photos.ensure_row).
    An entry for a file that is gone is left out: 307 of photo_index's 310 on 2026-09-24.
    The file is left where it is; nothing reads it after this.
    """
    import json
    conn.execute("CREATE TABLE suggestions ("
                 " photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,"
                 " tags TEXT, people TEXT, title TEXT, raw TEXT, before_consensus INTEGER NOT NULL DEFAULT 0,"
                 " error TEXT, model TEXT, created TEXT)")
    conn.execute("CREATE TRIGGER suggestions_go_with_their_photo AFTER DELETE ON photos"
                 " BEGIN DELETE FROM suggestions WHERE photo_id = OLD.id; END")
    path = _suggestions_file(conn)
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("Saved suggestions in %s could not be read, and were not taken in: %s", path, e)
        return
    from tagpup.store import suggestions   # the store imports this module
    ids = {paths.key(p): i for i, p in conn.execute("SELECT id, path FROM photos")}
    succeeded = set()
    taken = left = 0
    for status in saved.values() if isinstance(saved, dict) else ():
        entries = status.get("suggestions") if isinstance(status, dict) else None
        for photo, found in (entries or {}).items():
            if not isinstance(found, dict):
                continue
            # A photo listed under two folders: a success is not replaced by an error.
            if paths.key(photo) in succeeded and "error" in found:
                continue
            if "error" not in found:
                succeeded.add(paths.key(photo))
            photo_id = ids.get(paths.key(photo))
            if photo_id is not None:
                suggestions.put_for(conn, photo_id, found)
            elif os.path.exists(photo):
                suggestions.put(conn, photo, found)
            else:
                left += 1
                continue
            taken += 1
    logger.info("Took in %d saved suggestion(s) from %s; left out %d for files that are gone.",
                taken, os.path.basename(path), left)


def _dates(conn):
    """When each photo was taken, in `photos.taken` (its Date Taken, as recorded) and
    `photos.year` (the year of it, else one in its name): tagpup.core.dates, derived from
    the photo's metadata and path by tagpup.store.photos.date_photos at every write of
    either. Every reader parsed the raw metadata for it, in seven places in TagTuner
    alone, and the suggester read it back out of the JSON (docs/findings.md, #67).
    """
    conn.execute("ALTER TABLE photos ADD COLUMN taken TEXT")
    conn.execute("ALTER TABLE photos ADD COLUMN year INTEGER")
    from tagpup.store import photos   # the store imports this module
    photos.date_photos(conn)


def _journal(conn):
    """The journal: `changes` and `change_rows` (tagpup.store.journal; docs/ARCHITECTURE.md,
    phase 7.5). Every bulk edit is recorded as what it found and what it left, one row per
    changed column, so it can be undone where the rows are still what it left, and
    rehearsed before it is applied. A bulk operation copied the whole library first
    instead (db.backup: 1.4 GB for photo_index, five kept) and could be undone only by
    restoring that copy over everything done since.

    `old` and `new` are declared with no type: a column without one keeps each value as
    it was given -- an integer, a real, text or a BLOB -- where any declared type would
    convert some of them. `action` and the row's `id` are the plan's additions: the
    first tells an inserted row from an updated one whose old values were NULL, the
    second is the order the rows were written in, which an undo reverses. Only adds
    tables, so it needs no backup and works from migration 7 or 8 alike.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS changes ("
                 " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                 " operation TEXT NOT NULL,"
                 " status TEXT NOT NULL CHECK (status IN"
                 " ('planned', 'applied', 'derived_pending', 'undone', 'failed', 'pruned')),"
                 " schema_version INTEGER NOT NULL,"
                 " created TEXT NOT NULL, applied TEXT, undone TEXT, summary TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS change_rows ("
                 " id INTEGER PRIMARY KEY,"
                 " change_id INTEGER NOT NULL REFERENCES changes(id),"
                 " action TEXT NOT NULL CHECK (action IN ('insert', 'update', 'delete')),"
                 " table_name TEXT NOT NULL, row_key TEXT NOT NULL, column_name TEXT NOT NULL,"
                 " old, new)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_change_rows_change ON change_rows(change_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_change_rows_row ON change_rows(table_name, row_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_status ON changes(status)")


def _settings(conn):
    """The library's settings: `settings(key, value)`, one row per setting, the key its
    declaration's in tagpup.core.validation.SETTINGS ("faces.min_face_size") and the value
    as text (tagpup.store.settings; docs/ARCHITECTURE.md, phase 7.6). They lived in
    config.ini, one file for the machine, so a library opened on another machine, or with
    the file edited, was read with settings it was not made with, and nothing said so.

    Written only through the journal (tagpup.services.settings), so every change is in
    the library's history and can be undone. The table starts empty: the service stamps
    it -- from the defaults, or once from config.ini for a library in use. Only adds a
    table, so it needs no backup.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)")


MIGRATIONS = (
    Migration(1, "the tables as of 2026-09", _tables, changes_data=False),
    Migration(2, "one generations table", _generations, changes_data=False),
    Migration(3, "face crops in their own table", _face_crops, changes_data=True),
    Migration(4, "photos by id", _photo_ids, changes_data=True),
    Migration(5, "one embeddings table", _embeddings, changes_data=True),
    Migration(6, "each photo's people in photo_people", _photo_people, changes_data=True),
    Migration(7, "suggestions by photo", _suggestions, changes_data=True),
    Migration(8, "when each photo was taken", _dates, changes_data=False),
    Migration(9, "a journal of changes", _journal, changes_data=False),
    Migration(10, "the library's settings", _settings, changes_data=False),
)

LATEST = MIGRATIONS[-1].version


def version(conn):
    """The last migration applied to the library on `conn`; 0 for none."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] or 0


def pending(db_path):
    """The migrations the library at `db_path` has not had, in order. Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        current = version(conn)
    finally:
        conn.close()
    return [m for m in MIGRATIONS if m.version > current]


#: Libraries this process has found current, by what the file is and when it last
#: changed. A file deleted and made again at the same path is a different file; one a
#: backup was copied over keeps its id and creation time on Windows, but not its
#: modified time (docs/findings.md, #56). Either is looked at again, as is a library
#: whose file a checkpoint has written to since: one query, now and then.
_current = {}
_current_guard = threading.Lock()


def _identity(db_path):
    try:
        stat = os.stat(db_path)
    except OSError:
        return None
    return (stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size)


def ensure(db_path):
    """Bring the library at `db_path` to the current schema, creating it if need be.

    Returns the names of the migrations applied: none, usually. Each runs in a
    transaction of its own, under the library's write lock, so two programs opening
    the same library at once apply it once. One that rewrites data is preceded by a
    backup, unless the library has no tables yet. A library this process has already
    found current costs a stat and no connection.

    The first time a process opens a library, a change of the journal that a crash left
    `derived_pending` is finished (tagpup.store.journal.settle).
    """
    key = db._key(db_path)
    identity = _identity(db_path)
    with _current_guard:
        if identity is not None and _current.get(key) == identity:
            return []
    applied = _ensure(db_path)
    from tagpup.store import journal   # the journal imports this module
    journal.settle_once(db_path)
    with _current_guard:
        _current[key] = _identity(db_path)
    return applied


#: The counters migration 2 replaced. A version of the app from before it, still running
#: while a new one opens the library, makes them again on its next load, and nothing
#: would take them away: the library says it is current (docs/findings.md, #55).
LEGACY_COUNTERS = ("faces_generation", "taxonomy_generation")


def legacy_counters(conn):
    """The tables and triggers of LEGACY_COUNTERS the library on `conn` still has."""
    return sorted(name for kind, name in conn.execute("SELECT type, name FROM sqlite_master")
                  if (kind == "table" and name in LEGACY_COUNTERS)
                  or (kind == "trigger" and name.startswith(tuple(c + "_" for c in LEGACY_COUNTERS))))


def _drop_legacy_counters(db_path, conn):
    if not legacy_counters(conn):
        return
    with db.lock_for(db_path):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for kind, name in conn.execute("SELECT type, name FROM sqlite_master").fetchall():
                if kind == "trigger" and name.startswith(tuple(c + "_" for c in LEGACY_COUNTERS)):
                    conn.execute("DROP TRIGGER IF EXISTS %s" % name)
            for name in LEGACY_COUNTERS:
                conn.execute("DROP TABLE IF EXISTS %s" % name)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    logger.info("%s: took away the counters an older version made again", db_path)


def _ensure(db_path):
    conn = db.connect(db_path, timeout=30.0)
    try:
        if version(conn) >= LATEST:
            _drop_legacy_counters(db_path, conn)
            return []
        applied = []
        with db.lock_for(db_path):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version ("
                         " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
            conn.commit()
            # One copy, from before the first migration that rewrites data: it holds the
            # library as it was for every one after it too.
            backed_up = False
            for migration in MIGRATIONS:
                if version(conn) >= migration.version:
                    continue
                if migration.changes_data and not backed_up and _has_rows(conn):
                    logger.info("Backed up to %s", db.backup(db_path, "migration-%d" % migration.version))
                    backed_up = True
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # Again inside the transaction: another process may have got here first.
                    if version(conn) >= migration.version:
                        conn.rollback()
                        continue
                    migration.apply(conn)
                    conn.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                                 (migration.version, migration.name, time.strftime("%Y-%m-%d %H:%M:%S")))
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                logger.info("%s: migration %d, %s", db_path, migration.version, migration.name)
                applied.append(migration.name)
        return applied
    finally:
        conn.close()


def _has_rows(conn):
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return "photos" in tables and conn.execute("SELECT 1 FROM photos LIMIT 1").fetchone() is not None

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

Each migration says what it is (ADDITIVE, DATA or DESTRUCTIVE) and names the checks it
must pass; it runs in one transaction, and a failed check rolls it back (CheckFailed).
What it takes first follows from its kind: nothing for one that adds, its rows recorded
in the journal for one that changes data, one full backup for one that destroys. Every
run is recorded in `changes` as "migration N: name" (docs/ARCHITECTURE.md, phase 7.5).
A table is rebuilt only through `rebuild_table`, SQLite's twelve steps.

`ensure` is called wherever a library is opened, including each request that names
one. After the first time in a process it costs a stat.
"""
import collections
import logging
import os
import re
import sqlite3
import threading
import time

from tagpup.core import paths
from tagpup.store import db

logger = logging.getLogger(__name__)

#: What a migration is, which decides what it takes before it runs and what it leaves
#: in the journal (docs/ARCHITECTURE.md, phase 7.5):
#:
#: - ADDITIVE adds tables, columns, indexes or triggers, and may fill a column it adds
#:   from the row's own values. Nothing that was there changes: the runner watches every
#:   row while it runs and refuses one that did. No backup.
#: - DATA rewrites or moves values in rows of the tables the journal keys (journal.KEYS).
#:   Every row it changes is recorded as a change of the journal, which an undo puts back
#:   while no newer change has touched those rows and no later migration has run. An undo
#:   puts the values back and leaves the version where it is, so the values it replaces
#:   must be ones the code at its version still reads; if not, it is DESTRUCTIVE.
#: - DESTRUCTIVE drops a column or a table, rebuilds a table -- whose rows cannot be
#:   recorded one by one -- or loses information. One full backup first, taken under the
#:   write lock; that copy is the only way back.
ADDITIVE, DATA, DESTRUCTIVE = "additive", "data-changing", "destructive"
KINDS = (ADDITIVE, DATA, DESTRUCTIVE)

#: One step: its number and name, the function that makes it, its kind and why it is
#: that kind (one line), the tables it writes or reshapes (`touches`, which the checks
#: look at), and the checks that must pass before it commits. Every field is required:
#: a migration that does not say what it is cannot be declared.
Migration = collections.namedtuple("Migration", "version name apply kind why touches checks")


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


def _change_files(conn):
    """Photo files in the journal: `change_files`, one row for each file a bulk edit
    writes, and `changes.owner`, the process carrying a change out (tagpup.store.
    file_journal; docs/ARCHITECTURE.md, phase 7.5). A batch of file writes cannot be one
    transaction, so each file carries its own state -- planned, writing, done, conflict,
    undone -- with the fields it held before and is to hold after, and a file a crash
    left `writing` is settled by what it holds (tagpup.services.file_changes). Only adds,
    so it needs no backup.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS change_files ("
                 " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                 " change_id INTEGER NOT NULL REFERENCES changes(id),"
                 " photo_id INTEGER,"
                 " path TEXT NOT NULL,"
                 " new_path TEXT,"
                 " fields_before TEXT NOT NULL,"
                 " fields_after TEXT NOT NULL,"
                 " state TEXT NOT NULL CHECK (state IN ('planned', 'writing', 'done', 'conflict', 'undone')),"
                 " note TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_change_files_change ON change_files(change_id)")
    if "owner" not in _columns(conn, "changes"):
        conn.execute("ALTER TABLE changes ADD COLUMN owner TEXT")


def _change_file_stamps(conn):
    """The stamp of each photo file just before its write: `change_files.stamp`, JSON
    [mtime, size], recorded as the file is marked writing (tagpup.store.file_journal).
    A crash between a file's write and its row lost it, and settling could not carry the
    photo's CLIP vectors over the write (docs/findings.md, #265). Only adds a column, so
    it needs no backup.
    """
    if "stamp" not in _columns(conn, "change_files"):
        conn.execute("ALTER TABLE change_files ADD COLUMN stamp TEXT")


# ---- What a migration holds true before it commits ----------------------------------------

#: The runner's own tables: it writes them as it records each migration.
RUNNER_TABLES = ("schema_version", "changes", "change_rows")

#: Tables any migration may change and nothing records: the counters its triggers move
#: (tagpup.store.generations) and each photo's people, derived and rebuilt
#: (journal.DERIVED, which a test holds to this).
UNWATCHED = ("generations", "photo_people")


class CheckFailed(RuntimeError):
    """A check a migration names, or one its kind implies, found something wrong before it
    committed. Everything it did is rolled back. `check` is the check's name, `problems`
    what it found: tables, counts and keys, never values."""

    def __init__(self, check, problems, migration=None):
        self.check, self.problems, self.migration = check, list(problems), migration
        super().__init__(self._text())

    def _text(self):
        shown = "; ".join(self.problems[:5])
        if len(self.problems) > 5:
            shown += "; and %d more" % (len(self.problems) - 5)
        where = ("Migration %d (%s) was not applied" % (self.migration.version, self.migration.name)
                 if self.migration else "Nothing was changed")
        return "%s: its check %r failed: %s" % (where, self.check, shown)

    def of(self, migration):
        """This failure, naming the migration it stopped."""
        self.migration = migration
        self.args = (self._text(),)
        return self


def _tables_now(conn):
    return [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if not name.startswith("sqlite_")]


def _quoted(name):
    return '"%s"' % name.replace('"', '""')


def _literal(text):
    return "'%s'" % text.replace("'", "''")


def _count(conn, table):
    return conn.execute("SELECT COUNT(*) FROM %s" % _quoted(table)).fetchone()[0]


def _require(check, problems):
    if problems:
        raise CheckFailed(check, problems)


class Check:
    """One thing a migration holds true. `before` reads, in the migration's transaction,
    what `after` compares with; `after` returns what is wrong, [] when nothing is."""
    name = ""

    def before(self, conn, migration):
        return None

    def after(self, conn, migration, state):
        return []


class RowsKept(Check):
    """The tables named -- without names, every table the library had but those in `but`
    and those the migration drops -- have as many rows after as before.

    Without names, an additive migration's are only the tables it touches: the runner's
    watch refuses one that changed a row anywhere else, and counting every table, twice,
    took 26 s on the owner's library under the write lock, most of it face_crops
    (docs/findings.md, #272)."""

    def __init__(self, *tables, but=()):
        self.tables, self.but = tables, tuple(but)
        self.name = "rows kept" + (" in " + ", ".join(tables) if tables else "")

    def before(self, conn, migration):
        present = set(_tables_now(conn))
        tables = self.tables
        if not tables:
            among = migration.touches if migration.kind == ADDITIVE else present
            tables = sorted(t for t in among if t not in RUNNER_TABLES and t not in self.but)
        return {table: _count(conn, table) for table in tables if table in present}

    def after(self, conn, migration, state):
        present = set(_tables_now(conn))
        problems = []
        for table, was in state.items():
            if table not in present:
                if self.tables:
                    problems.append("%s is gone" % table)
                continue
            now = _count(conn, table)
            if now != was:
                problems.append("%s had %d row(s) and has %d" % (table, was, now))
        return problems


class RowsNotFewer(RowsKept):
    """The tables named have no fewer rows after than before: a migration that may add
    rows to them (a photo row for a file only a face named) and must lose none."""

    def __init__(self, *tables):
        super().__init__(*tables)
        self.name = "no rows lost in " + ", ".join(tables)

    def after(self, conn, migration, state):
        present = set(_tables_now(conn))
        problems = []
        for table, was in state.items():
            now = _count(conn, table) if table in present else 0
            if now < was:
                problems.append("%s had %d row(s) and has %d" % (table, was, now))
        return problems


def _violations(conn, tables):
    """Counter of (table, parent) -> rows of `tables`, and of every table naming one of
    them, whose foreign key names a row that is not there (PRAGMA foreign_key_check)."""
    present = set(_tables_now(conn))
    named = {t for t in tables if t in present}
    checked = set(named)
    for table in present:
        if any(row[2] in named for row in conn.execute("PRAGMA foreign_key_list(%s)" % _quoted(table))):
            checked.add(table)
    found = collections.Counter()
    for table in sorted(checked):
        for row in conn.execute("PRAGMA foreign_key_check(%s)" % _quoted(table)):
            found[(row[0], row[2])] += 1
    return found


def _worse(before, after):
    return ["%d row(s) of %s name a row of %s that is not there" % (after[key] - before.get(key, 0), key[0], key[1])
            for key in sorted(after) if after[key] > before.get(key, 0)]


class ForeignKeys(Check):
    """PRAGMA foreign_key_check on the tables the migration touches and every table naming
    one of them: no row names a row that is not there, but those that already did. The
    runner writes with foreign keys off, so SQLite would not say."""
    name = "foreign keys"

    def before(self, conn, migration):
        return _violations(conn, migration.touches)

    def after(self, conn, migration, state):
        return _worse(state, _violations(conn, migration.touches))


class Integrity(Check):
    """PRAGMA quick_check on each table the migration touches: its pages, its rows and
    their constraints."""
    name = "integrity"

    def after(self, conn, migration, state):
        present = set(_tables_now(conn))
        problems = []
        for table in migration.touches:
            if table in present:
                found = [row[0] for row in conn.execute("PRAGMA quick_check(%s)" % _quoted(table))]
                if found != ["ok"]:
                    problems += ["%s: %s" % (table, text) for text in found[:5]]
        return problems


class CropsMoved(Check):
    """Migration 3: every face that held a crop has one in face_crops after."""
    name = "every crop moved"

    def before(self, conn, migration):
        if "crop_image" not in _columns(conn, "faces"):
            return None
        return conn.execute("SELECT COUNT(*) FROM faces WHERE crop_image IS NOT NULL").fetchone()[0]

    def after(self, conn, migration, state):
        if state is None:
            return []
        moved = conn.execute("SELECT COUNT(*) FROM faces f JOIN face_crops c ON c.face_id = f.id").fetchone()[0]
        return [] if moved >= state else ["%d face(s) held a crop and %d have one" % (state, moved)]


#: Every migration names these two, and a count of rows; a test holds it to them.
STANDARD = (ForeignKeys(), Integrity())


def rebuild_table(conn, table, definition, copy=None, drop=(), then=()):
    """Rebuild `table` as `definition` declares it -- what goes between the parentheses of
    its CREATE TABLE -- by the twelve steps SQLite's documentation gives for a change
    ALTER TABLE cannot make (sqlite.org/lang_altertable.html, "otheralter"). The one way a
    migration rebuilds a table; `tests/test_migrations.py` holds every other to it.

    The runner has done steps 1 and 2 -- foreign keys off, which only takes effect outside
    a transaction, then the transaction -- and this refuses a connection that has not:
    dropping the table with foreign keys on would first delete every row naming it.
    `copy` maps each column of the new table to the expression that fills it from the old
    (default: every column both have, by name). The indexes and triggers of the table,
    and the triggers and views elsewhere that name it, are made again as they were but
    those named in `drop`; `then` are statements run after, such as new indexes. Its
    AUTOINCREMENT counter is kept, which dropping the table would reset to the highest id
    left (#80). Step 10, foreign_key_check, runs here: a row naming one that is not there,
    and did not before, refuses the rebuild (CheckFailed), as does a row not copied. The
    caller commits (step 11). Returns the rows copied."""
    # 1, 2. Foreign keys off, outside the transaction; then the transaction.
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("Rebuilding %s with foreign keys on would delete every row naming it first;"
                           " nothing was changed" % table)
    if not conn.in_transaction:
        raise RuntimeError("Rebuilding %s outside a transaction could leave it half made; nothing was changed"
                           % table)
    # 3. What goes with the table: its indexes and triggers, and every trigger or view
    # elsewhere naming it, which would stop the rename at step 7 while the table is gone.
    naming = re.compile("(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(table), re.IGNORECASE)
    own, elsewhere = [], []
    for kind, name, owner, sql in conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
            " WHERE type IN ('index', 'trigger', 'view') AND sql IS NOT NULL").fetchall():
        if owner == table and kind != "view":
            own.append((kind, name, sql))
        elif kind != "index" and naming.search(sql):
            elsewhere.append((kind, name, sql))
    violations = _violations(conn, [table])
    counters = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone()
    counter = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone() if counters else None
    rows = _count(conn, table)
    # 4. The new table.
    new = "tagpup_rebuilt_%s" % table
    conn.execute("CREATE TABLE %s (%s)" % (_quoted(new), definition))
    # 5. Its rows.
    old_columns = _columns(conn, table)
    copy = dict(copy) if copy else {c: _quoted(c) for c in _columns(conn, new) if c in old_columns}
    conn.execute("INSERT INTO %s (%s) SELECT %s FROM %s" % (
        _quoted(new), ", ".join(_quoted(c) for c in copy), ", ".join(copy.values()), _quoted(table)))
    copied = _count(conn, new)
    if copied != rows:
        raise CheckFailed("rows kept in %s" % table, ["%d of %d row(s) copied" % (copied, rows)])
    # 6. The old table, and what names it elsewhere.
    for kind, name, _sql in elsewhere:
        conn.execute("DROP %s %s" % (kind.upper(), _quoted(name)))
    conn.execute("DROP TABLE %s" % _quoted(table))
    # 7. The new one in its place.
    conn.execute("ALTER TABLE %s RENAME TO %s" % (_quoted(new), _quoted(table)))
    # 8, 9. Its indexes and triggers, and the triggers and views elsewhere, again.
    for _kind, name, sql in own + elsewhere:
        if name not in drop:
            conn.execute(sql)
    for statement in then:
        conn.execute(statement)
    if counter:
        if conn.execute("SELECT 1 FROM sqlite_sequence WHERE name = ?", (table,)).fetchone():
            conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (counter[0], table))
        else:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, counter[0]))
    # 10. foreign_key_check, before the caller commits.
    _require("foreign keys", _worse(violations, _violations(conn, [table])))
    return copied


def _same(a, b):
    """Equal as SQLite stores them: 1 is not 1.0, nor b'1' '1' (journal._same)."""
    return type(a) is type(b) and a == b


class _Watch:
    """Every row of every table the library has, as a migration changes it: TEMP triggers
    on each, which the connection alone sees and which go with the transaction if it is
    rolled back. What SQLite's session extension records, which is reachable from Python
    only through APSW (tagpup.store.journal)."""

    TABLE = "tagpup_migration_watch"

    def __init__(self, conn):
        self.conn = conn
        self.columns = {t: _columns(conn, t) for t in _tables_now(conn)
                        if t not in RUNNER_TABLES and t not in UNWATCHED}
        conn.execute("CREATE TEMP TABLE %s (seq INTEGER PRIMARY KEY, action TEXT NOT NULL, tbl TEXT NOT NULL,"
                     " rid INTEGER, col TEXT, old, new)" % self.TABLE)
        self.triggers = []
        for number, table in enumerate(sorted(self.columns)):
            self._install(number, table, self.columns[table])

    def _install(self, number, table, columns):
        into = "INSERT INTO %s (action, tbl, rid, col, old, new)" % self.TABLE
        name = _literal(table)
        update = ["%s SELECT 'rekey', %s, OLD.rowid, NULL, NULL, NULL WHERE OLD.rowid IS NOT NEW.rowid;"
                  % (into, name)]
        delete = []
        for column in columns:
            q = _quoted(column)
            update.append("%s SELECT 'update', %s, OLD.rowid, %s, OLD.%s, NEW.%s"
                          " WHERE OLD.%s IS NOT NEW.%s OR typeof(OLD.%s) != typeof(NEW.%s);"
                          % (into, name, _literal(column), q, q, q, q, q, q))
            delete.append("%s VALUES ('delete', %s, OLD.rowid, %s, OLD.%s, NULL);" % (into, name, _literal(column), q))
        insert = ["%s VALUES ('insert', %s, NEW.rowid, NULL, NULL, NULL);" % (into, name)]
        for event, body in (("INSERT", insert), ("UPDATE", update), ("DELETE", delete)):
            trigger = "tagpup_watch_%d_%s" % (number, event.lower())
            self.conn.execute("CREATE TEMP TRIGGER %s AFTER %s ON main.%s BEGIN %s END"
                              % (trigger, event, _quoted(table), " ".join(body)))
            self.triggers.append(trigger)

    def finish(self):
        """([(table, rowid, before, after)] of every row not what it was, in the order it
        was first written, and the tables a row of which moved to another rowid). `before`
        is None for a row the migration made, `after` for one it deleted; an update's
        `before` holds the columns it changed, a deleted row's every column. The triggers
        and their table go."""
        rows, order, moved = {}, [], set()
        for action, table, rid, column, old in self.conn.execute(
                "SELECT action, tbl, rid, col, old FROM temp.%s ORDER BY seq" % self.TABLE):
            if action == "rekey":
                moved.add(table)
                continue
            entry = rows.get((table, rid))
            if entry is None:
                entry = rows[(table, rid)] = (action, {})
                order.append((table, rid))
            if action != "insert":
                entry[1].setdefault(column, old)
        for trigger in self.triggers:
            self.conn.execute("DROP TRIGGER IF EXISTS temp.%s" % trigger)
        self.conn.execute("DROP TABLE temp.%s" % self.TABLE)

        present = set(_tables_now(self.conn))
        by_table = collections.defaultdict(list)
        for table, rid in order:
            by_table[table].append(rid)
        now = {}
        for table, rids in by_table.items():
            if table not in present:
                continue
            columns = _columns(self.conn, table)
            for start in range(0, len(rids), 500):
                chunk = rids[start:start + 500]
                for row in self.conn.execute("SELECT rowid, %s FROM %s WHERE rowid IN (%s)" % (
                        ", ".join(_quoted(c) for c in columns), _quoted(table), ",".join("?" * len(chunk))), chunk):
                    now[(table, row[0])] = dict(zip(columns, row[1:]))
        seen = []
        for table, rid in order:
            first, before = rows[(table, rid)]
            after = now.get((table, rid))
            if first == "insert":
                if after is not None:
                    seen.append((table, rid, None, after))
            elif after is None:
                seen.append((table, rid, before, None))
            else:
                changed = {c: v for c, v in before.items() if c in after and not _same(v, after[c])}
                if changed:
                    seen.append((table, rid, changed, after))
        return seen, sorted(moved)


def _shape(conn):
    return {table: _columns(conn, table) for table in _tables_now(conn)}


def _dropped(conn, shape):
    """What of `shape` -- tables and their columns -- the library no longer has."""
    now = _shape(conn)
    problems = []
    for table, columns in sorted(shape.items()):
        if table not in now:
            problems.append("%s is gone" % table)
        else:
            problems += ["%s.%s is gone" % (table, c) for c in columns if c not in now[table]]
    return problems


def _tally(seen, moved):
    """'<table>: n inserted, n updated, n deleted' of each table rows of which changed."""
    counts = collections.defaultdict(collections.Counter)
    for table, _rid, before, after in seen:
        counts[table]["inserted" if before is None else "deleted" if after is None else "updated"] += 1
    problems = ["%s: %s" % (table, ", ".join("%d %s" % (n, what) for what, n in sorted(c.items())))
                for table, c in sorted(counts.items())]
    return problems + ["%s: a row moved to another rowid" % table for table in moved]


def _journal_rows(conn, seen, moved, made):
    """(the journal's RowChanges for `seen`, and what of it the journal cannot record).
    Every row of a table the migration made (`made`) is an insert."""
    from tagpup.store import journal   # the journal imports this module
    changes, unrecorded = [], collections.Counter()
    for table in moved:
        unrecorded["%s (a row moved to another rowid)" % table] += 1
    for table, _rid, before, after in seen:
        keys = journal.KEYS.get(table)
        if keys is None:
            unrecorded[table] += 1
        elif before is None:
            changes.append(journal.RowChange("insert", table, tuple(after[k] for k in keys), None, dict(after)))
        elif after is None:
            changes.append(journal.RowChange("delete", table, tuple(before[k] for k in keys), dict(before), None))
        elif set(keys) & set(before):
            unrecorded["%s (a row's key changed)" % table] += 1
        else:
            changes.append(journal.RowChange("update", table, tuple(after[k] for k in keys), dict(before),
                                             {c: after[c] for c in before}))
    for table in sorted(made):
        if table in RUNNER_TABLES or table in UNWATCHED:
            continue
        columns = _columns(conn, table)
        rows = conn.execute("SELECT %s FROM %s" % (", ".join(_quoted(c) for c in columns), _quoted(table))).fetchall()
        keys = journal.KEYS.get(table)
        if keys is None:
            if rows:
                unrecorded[table] += len(rows)
            continue
        for row in rows:
            row = dict(zip(columns, row))
            changes.append(journal.RowChange("insert", table, tuple(row[k] for k in keys), None, row))
    return changes, ["%d row(s) of %s, which the journal does not record: declare it destructive" % (n, table)
                     for table, n in sorted(unrecorded.items())]


MIGRATIONS = (
    Migration(1, "the tables as of 2026-09", _tables, ADDITIVE,
              "makes the tables and indexes a library lacks, IF NOT EXISTS; an older library is refused, not converted",
              ("photos", "faces", "embedding_cache", "tag_taxonomy", "tag_embeddings"),
              (RowsKept(),) + STANDARD),
    Migration(2, "one generations table", _generations, DESTRUCTIVE,
              "drops faces_generation and taxonomy_generation and their triggers, once their counts are carried over",
              ("generations",),
              (RowsKept(),) + STANDARD),
    Migration(3, "face crops in their own table", _face_crops, DESTRUCTIVE,
              "drops faces.crop_image once each crop is copied to face_crops: a dropped column is not recorded row by row",
              ("faces", "face_crops"),
              (RowsKept(but=("face_crops",)), CropsMoved()) + STANDARD),
    Migration(4, "photos by id", _photo_ids, DESTRUCTIVE,
              "rebuilds photos and faces, faces naming their photo by id, which cannot be recorded row by row",
              ("photos", "faces"),
              (RowsKept(but=("photos",)), RowsNotFewer("photos")) + STANDARD),
    Migration(5, "one embeddings table", _embeddings, DESTRUCTIVE,
              "drops embedding_cache and photos.embedding, leaving out vectors no model names and those of photos gone",
              ("embeddings", "photos"),
              (RowsKept(),) + STANDARD),
    Migration(6, "each photo's people in photo_people", _photo_people, DESTRUCTIVE,
              "drops photos.people for rows the rule rebuilds: a name the list held that nothing else gives is lost",
              ("photo_people", "photos"),
              (RowsKept(),) + STANDARD),
    Migration(7, "suggestions by photo", _suggestions, DATA,
              "takes a file's saved suggestions in as rows, with a photo row for a file on disk that had none",
              ("photos", "suggestions"),
              (RowsKept(but=("photos", "suggestions")), RowsNotFewer("photos")) + STANDARD),
    Migration(8, "when each photo was taken", _dates, ADDITIVE,
              "adds photos.taken and photos.year, filled from each row's own metadata and path; nothing else changes",
              ("photos",),
              (RowsKept(),) + STANDARD),
    Migration(9, "a journal of changes", _journal, ADDITIVE,
              "adds the journal's tables, changes and change_rows, empty",
              ("changes", "change_rows"),
              (RowsKept(),) + STANDARD),
    Migration(10, "the library's settings", _settings, ADDITIVE,
              "adds the settings table, empty",
              ("settings",),
              (RowsKept(),) + STANDARD),
    Migration(11, "photo files in the journal", _change_files, ADDITIVE,
              "adds the change_files table, empty, and the column changes.owner, NULL in every row",
              ("change_files", "changes"),
              (RowsKept(),) + STANDARD),
    Migration(12, "the stamp of each file before its write", _change_file_stamps, ADDITIVE,
              "adds the column change_files.stamp, NULL in every row",
              ("change_files",),
              (RowsKept(),) + STANDARD),
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
    the same library at once apply it once, and a check it names that fails rolls it
    back (CheckFailed). What each takes first follows from its kind (ADDITIVE, DATA,
    DESTRUCTIVE); each is recorded in the journal. A library this process has already
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
        with db.lock_for(db_path):
            conn.execute("BEGIN IMMEDIATE")
            try:
                # A library with no tables yet is being made, not migrated: nothing to back
                # up, watch or record.
                made = not [t for t in _tables_now(conn) if t != "schema_version"]
                conn.execute("CREATE TABLE IF NOT EXISTS schema_version ("
                             " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            run = _Run(db_path, conn, made)
            return [migration.name for migration in MIGRATIONS if run.migrate(migration)]
    finally:
        conn.close()


class _Run:
    """One pass of the runner over a library, under its write lock: the backup it has
    taken, if any -- one, from before the first migration that needs it, holds the
    library for every one after it too -- and the migrations that ran before the journal
    existed to record them, recorded once it does (migration 9)."""

    def __init__(self, db_path, conn, made):
        self.db_path, self.conn, self.made = db_path, conn, made
        self.backup = None
        self.backed_up = False
        self.unrecorded = []

    def migrate(self, migration):
        """Apply `migration` if the library has not had it. True when it was applied."""
        conn = self.conn
        if version(conn) >= migration.version:
            return False
        started = time.time()
        # The first of SQLite's twelve steps: foreign keys off, which only takes effect
        # outside a transaction. Dropping a table with them on deletes every row naming it.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Again inside the transaction: another process may have got here first.
            if version(conn) >= migration.version:
                conn.rollback()
                return False
            from tagpup.store import journal   # the journal imports this module
            journaled = migration.kind == DATA and not self.made and journal.has_journal(conn)
            summary = {"kind": migration.kind}
            if migration.kind == DESTRUCTIVE or (migration.kind == DATA and not journaled):
                # A change of data the journal cannot hold yet (a library from before
                # migration 9) is backed up like one that destroys.
                summary["backup"] = self._backup(migration)
            checks, rows = self._apply(migration, journaled)
            summary["checks"] = checks
            conn.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                         (migration.version, migration.name, time.strftime("%Y-%m-%d %H:%M:%S")))
            self._record(journal, migration, summary, rows)
            conn.commit()
        except CheckFailed as e:
            conn.rollback()
            raise e.of(migration) from None
        except BaseException:
            conn.rollback()
            raise
        logger.info("%s: migration %d, %s (%s), %.1fs", self.db_path, migration.version, migration.name,
                    migration.kind, time.time() - started)
        return True

    def _backup(self, migration):
        """The file name of the one backup of this run, taken now if it has not been: with
        this connection holding the write lock, so the copy is the library as the
        migration finds it. None for a library with nothing in it to lose."""
        if not self.backed_up and _has_rows(self.conn):
            self.backup = db.backup(self.db_path, "migration-%d" % migration.version)
            logger.info("Backed up to %s", self.backup)
        self.backed_up = True
        return os.path.basename(self.backup) if self.backup else None

    def _apply(self, migration, journaled):
        """Run `migration` and its checks, in the transaction. Returns (the names of the
        checks it passed, the journal's rows of what it changed if it is journaled)."""
        conn = self.conn
        states = [check.before(conn, migration) for check in migration.checks]
        shape = _shape(conn) if migration.kind != DESTRUCTIVE else None
        watching = journaled or (migration.kind == ADDITIVE and not self.made)
        watch = _Watch(conn) if watching else None
        try:
            migration.apply(conn)
        except sqlite3.OperationalError as e:
            # A watched column dropped or renamed: the watch's triggers name it.
            if watch and "tagpup_watch_" in str(e):
                raise CheckFailed("nothing dropped", ["it altered a column the runner watches: %s" % e]) from None
            raise
        seen, moved = watch.finish() if watch else ([], [])
        passed, rows = [], []
        if shape is not None:
            _require("nothing dropped", _dropped(conn, shape))
            passed.append("nothing dropped")
        if migration.kind == ADDITIVE and watch:
            _require("nothing that was there changed", _tally(seen, moved))
            passed.append("nothing that was there changed")
        if journaled:
            made = set(_tables_now(conn)) - set(shape)
            rows, unrecorded = _journal_rows(conn, seen, moved, made)
            _require("every change recorded", unrecorded)
            passed.append("every change recorded")
        for check, state in zip(migration.checks, states):
            _require(check.name, check.after(conn, migration, state))
            passed.append(check.name)
        return passed, rows

    def _record(self, journal, migration, summary, rows):
        """Record the migration as a change of the journal, and any that ran before the
        journal existed. A change with rows belongs to the version it made, and an undo
        puts them back while the library is at it; one without is recorded at the version
        before, so the journal refuses to undo it: a schema is not undone."""
        if self.made:
            return
        self.unrecorded.append((migration, summary, rows))
        if not journal.has_journal(self.conn):
            return
        for done, done_summary, done_rows in self.unrecorded:
            journal.record(self.conn, "migration %d: %s" % (done.version, done.name), done_rows, done_summary,
                           schema_version=done.version if done_rows else done.version - 1)
        self.unrecorded = []


def _has_rows(conn):
    """Has the library anything a backup would keep: a row in any table but the record of
    its migrations and the counters? Not only photos: a tree or a journal is worth one."""
    for table in _tables_now(conn):
        if table in ("schema_version", "generations"):
            continue
        if conn.execute("SELECT 1 FROM %s LIMIT 1" % _quoted(table)).fetchone():
            return True
    return False

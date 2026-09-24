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
    "faces": ("crop_image", "prob", "name_source", "excluded", "excluded_reason"),
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


MIGRATIONS = (
    Migration(1, "the tables as of 2026-09", _tables, changes_data=False),
    Migration(2, "one generations table", _generations, changes_data=False),
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
    """
    key = db._key(db_path)
    identity = _identity(db_path)
    with _current_guard:
        if identity is not None and _current.get(key) == identity:
            return []
    applied = _ensure(db_path)
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
            for migration in MIGRATIONS:
                if version(conn) >= migration.version:
                    continue
                if migration.changes_data and _has_rows(conn):
                    logger.info("Backed up to %s", db.backup(db_path, "migration-%d" % migration.version))
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

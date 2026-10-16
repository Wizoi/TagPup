"""A library's tables, and the migrations that bring a library to them.

The only place a table, a column, an index or a trigger is made. There were four:
PhotoIndex.load, TagTuner's start-up (without the write lock), the desktop runner
(whose faces table had none of the columns every screen now filters on), and the tag
tree, which made its own table when it saved. Each carried its own idea of the schema.

A migration runs once per library, in order, and `schema_version` records it. The
first, version 32 (`BASELINE`), makes a NEW library directly in the schema as it stood after the
thirty-second step of the old chain; a later one (33 on) is an ordinary step forward. A library below the
baseline is refused (`TooOld`), not converted: the steps 1 to 32 were deleted on 2026-10-15
(owner decision: no plan to bring up older versions), and the snapshots and backups below 32 can no longer
be restored. Before that, the conversions of libraries older than 2026-09 had retired on 2026-09-24.

Each migration says what it is (ADDITIVE or DESTRUCTIVE) and names the checks it must
pass; it runs in one transaction, and a failed check rolls it back (CheckFailed). What it
takes first follows from its kind: nothing for one that adds, one full backup for one that
destroys. Every run is recorded in `changes` as "migration N: name" (docs/ARCHITECTURE.md,
phase 7.5), and is not undone: a schema is not.

`ensure` is called wherever a library is opened, including each request that names
one. After the first time in a process it costs a stat.
"""
import collections
import contextlib
import contextvars
import logging
import os
import sqlite3
import threading
import time

from tagpup.core import paths
from tagpup.store import db, derived, search_index

logger = logging.getLogger(__name__)

#: What a migration is, which decides what it takes before it runs (docs/ARCHITECTURE.md, phase 7.5):
#:
#: - ADDITIVE adds tables, columns, indexes or triggers, and may fill a column it adds
#:   from the row's own values. Nothing that was there changes: the runner watches every
#:   row while it runs and refuses one that did. No backup.
#: - DESTRUCTIVE drops a column or a table, rewrites values that were there, or loses
#:   information. One full backup first, taken under the write lock; that copy is the only
#:   way back.
ADDITIVE, DESTRUCTIVE = "additive", "destructive"
KINDS = (ADDITIVE, DESTRUCTIVE)

#: One step: its number and name, the function that makes it, its kind and why it is
#: that kind (one line), the tables it writes or reshapes (`touches`, which the checks
#: look at), and the checks that must pass before it commits. Every field is required:
#: a migration that does not say what it is cannot be declared.
Migration = collections.namedtuple("Migration", "version name apply kind why touches checks")


def table_columns(conn, table):
    """The columns of `table` on `conn`, in their order: the one reading of them the schema's watch and the journal share."""
    return [row[1] for row in conn.execute('PRAGMA table_info("%s")' % table)]


#: The first version of the schema this one makes: a new library is made at it (`_baseline`), and a library below it is
#: refused (owner, 2026-10-15: no plan to bring up older versions; a snapshot or backup below it cannot be restored).
BASELINE = 32


class NewerLibrary(Exception):
    """A library whose schema is newer than this version of TagPup knows (`LATEST`): an older app
    that opened it would misread what the newer one wrote and write it back wrong (identity stage 2:
    names without ids, which the newer doctor then overwrites). Refused wherever a library is opened;
    `str(e)` is the sentence to show the owner, never a traceback. `found` is the library's version,
    `known` this app's."""

    def __init__(self, name, found, known):
        self.name, self.found, self.known = name, found, known
        super().__init__(newer_sentence(name, found, known))


class TooOld(NewerLibrary):
    """A library whose schema is older than `BASELINE`: this version has no steps to bring it forward. Refused wherever a
    library is opened, as a newer one is (it is a NewerLibrary, so every place that answers one answers this: a 409 page, a
    sentence and exit 1); `str(e)` is the sentence to show the owner. `found` is the library's version, `known` the baseline."""

    def __init__(self, name, found):
        self.name, self.found, self.known = name, found, BASELINE
        Exception.__init__(self, too_old_sentence(name, found))


#: Where a library's backups are, for the owner who has to go back to an older checkout.
BACKUPS_NOTE = ("Your backups are in data/backups (the library's snapshots in data/backups/<library>/daily, weekly "
                "and monthly, the copies taken before a bulk change beside them as <library>.before-...db). To go "
                "back to an older TagPup, prefer to list and restore a snapshot with this version "
                "(`tagpup_cli.py --db <library> snapshots list`, `snapshots restore <name> --apply`). If you copy a "
                "backup over the library file by hand instead: stop every TagPup and delete <library>.db-wal and "
                "<library>.db-shm beside the library first, or the newer version's write-ahead log is replayed over the "
                "older copy and the library is a mixture of both.")


def too_old_sentence(what, found):
    """The one sentence for a library (`what` is its file name) below the baseline."""
    return ("%s is older than TagPup can bring forward (its schema is %d; the oldest this version opens is %d): restore a "
            "newer snapshot (`tagpup_cli.py --db <library> snapshots list`, then `snapshots restore <name> --apply`)."
            % (what, found, BASELINE))


def newer_sentence(what, found, known, recover=True):
    """The one sentence for something made by a newer TagPup than this one: a library (`what` is its file
    name) or a snapshot of one (snapshots.restore uses it). It says which app to start, and, for a library
    (`recover`), where the backups are and how to go back."""
    said = ("%s was made by a newer version of TagPup (its schema is %d; this version knows up to %d), so this "
            "version will not open it: it would misread it and write it wrong. Start the newest TagPup you have "
            "installed (its launchers, TagPup.cmd and TagTuner.cmd, install and start the current version)."
            % (what, found, known))
    return said + " " + BACKUPS_NOTE if recover else said


# ---- What may open a library newer than this version -----------------------------------------
#
# ONE owner of which operations may: `reading_newer()`. Inside it `ensure` lets a newer library through
# without migrating, settling or writing anything, so the recovery tools of an older checkout still work:
# listing and restoring snapshots, reading the journal, the doctor's report, the MCP's read tools. An entry point
# enters it only for an operation of RECOVERY (the CLI's commands by name; the MCP's read tools; the doctor's
# report); everything that writes through the app is outside it, and `ensure` refuses.

#: The CLI commands that may open a newer library: the journal's read and the snapshots (list and restore).
RECOVERY_COMMANDS = ("history", "snapshots")

_reading_newer = contextvars.ContextVar("tagpup_reading_newer", default=False)


@contextlib.contextmanager
def reading_newer():
    """Within it, `ensure` lets a library newer than this version through, changing nothing."""
    token = _reading_newer.set(True)
    try:
        yield
    finally:
        _reading_newer.reset(token)


def newer_note(db_path):
    """One line saying the library at `db_path` is newer (or older, below BASELINE) than this version opens and is being
    read as it is, or None."""
    sentence = newer_problem(db_path)
    if sentence is None:
        return None
    found = version_of(db_path)
    if found < BASELINE:
        return ("Note: %s is older than TagPup can bring forward (schema %d; the oldest this version opens is %d): it is only "
                "read, nothing is written." % (os.path.basename(db_path), found, BASELINE))
    return ("Note: %s is from a newer TagPup (schema %d; this version knows %d): it is only read, nothing is written."
            % (os.path.basename(db_path), found, LATEST))


def version_of(db_path):
    """The schema version of the library file at `db_path`, read only; 0 for none."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return version(conn)
    finally:
        conn.close()


def newer_problem(db_path):
    """The sentence (NewerLibrary's) when the library at `db_path` is newer than this version knows, or older than
    BASELINE (TooOld's), else None, also for a file that is not there or not a library yet. Reads only: for an entry point that
    looks at a library without opening it through `ensure` (the MCP's tools, the CLI's group, the doctor).
    A file that cannot be read just now (locked, not a database) is not decided here: it says None, and the
    open that follows says why; `ensure` refuses a newer library whatever this answered."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            found = version(conn)
            made = found >= BASELINE or _has_tables(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if found > LATEST:
        return newer_sentence(os.path.basename(db_path), found, LATEST)
    if found < BASELINE and made:
        return too_old_sentence(os.path.basename(db_path), found)
    return None


def _baseline(conn):
    """A NEW library, made directly in the schema a library had after migration 32 (owner, 2026-10-15: migrations 1 to 32 are
    collapsed; the next is 33). Every table, index and trigger of that schema, in the one transaction the runner gives it: a
    failure leaves a library with no table, which the next open makes again. Not a migration of an older library: one below
    BASELINE is refused (`_migrate`). Checked once against the library the old chain made and the three live libraries
    (2026-10-15: every table, column, index, foreign key and trigger identical; docs/DATABASE.md, "The baseline")."""
    for statement in (
        "CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, mtime REAL, size INTEGER,"
        " tags TEXT, captions TEXT, raw_metadata TEXT, document_id TEXT, taken TEXT, year INTEGER)",
        "CREATE TABLE faces (id INTEGER PRIMARY KEY AUTOINCREMENT, photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,"
        " box TEXT, embedding BLOB, name TEXT, prob REAL, name_source TEXT, excluded INTEGER DEFAULT 0, excluded_reason TEXT,"
        " tag_id INTEGER)",
        "CREATE TABLE face_crops (face_id INTEGER PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE, jpeg BLOB NOT NULL)",
        "CREATE TABLE embeddings (photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE, model TEXT NOT NULL,"
        " mtime REAL, size INTEGER, vector BLOB NOT NULL, PRIMARY KEY (photo_id, model))",
        "CREATE TABLE photo_people (photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,"
        " position INTEGER NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('keyword', 'face')),"
        " tag_id INTEGER, PRIMARY KEY (photo_id, position))",
        "CREATE TABLE suggestions (photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE, tags TEXT,"
        " people TEXT, title TEXT, raw TEXT, before_consensus INTEGER NOT NULL DEFAULT 0, error TEXT, model TEXT, created TEXT)",
        "CREATE TABLE tag_taxonomy (id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT UNIQUE, parent_id INTEGER, name TEXT,"
        " has_face INTEGER DEFAULT 0, hidden_from_autocomplete INTEGER DEFAULT 0,"
        " FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE)",
        "CREATE TABLE tag_embeddings (tag TEXT, prompt TEXT, model_name TEXT, pretrained TEXT, embedding BLOB,"
        " PRIMARY KEY (tag, prompt, model_name, pretrained))",
        "CREATE TABLE generations (name TEXT PRIMARY KEY, value INTEGER NOT NULL)",
        "CREATE TABLE changes (id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, status TEXT NOT NULL CHECK"
        " (status IN ('planned', 'applied', 'derived_pending', 'undone', 'failed', 'pruned')),"
        " schema_version INTEGER NOT NULL, created TEXT NOT NULL, applied TEXT, undone TEXT, summary TEXT, owner TEXT)",
        "CREATE TABLE change_rows (id INTEGER PRIMARY KEY, change_id INTEGER NOT NULL REFERENCES changes(id),"
        " action TEXT NOT NULL CHECK (action IN ('insert', 'update', 'delete')),"
        " table_name TEXT NOT NULL, row_key TEXT NOT NULL, column_name TEXT NOT NULL, old, new)",
        "CREATE TABLE change_files (id INTEGER PRIMARY KEY AUTOINCREMENT, change_id INTEGER NOT NULL REFERENCES changes(id),"
        " photo_id INTEGER, path TEXT NOT NULL, new_path TEXT, fields_before TEXT NOT NULL, fields_after TEXT NOT NULL,"
        " state TEXT NOT NULL CHECK (state IN ('planned', 'writing', 'done', 'conflict', 'undone')), note TEXT, stamp TEXT)",
        "CREATE TABLE settings (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)",
        "CREATE TABLE job_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, library TEXT COLLATE NOCASE,"
        " started TEXT NOT NULL, finished TEXT, outcome TEXT NOT NULL CHECK"
        " (outcome IN ('running', 'done', 'failed', 'abandoned')), changed TEXT, owner TEXT, note TEXT)",
        # The folders asked to be added, the photos whose faces are to be detected (a mark whose photo is gone is read as none)
        # and those whose faces were detected, and the library's roots (tagpup.store.added_folders, faces_detected, roots).
        "CREATE TABLE added_folders (path TEXT PRIMARY KEY COLLATE %s, subfolders INTEGER NOT NULL, added TEXT NOT NULL)"
        % paths.COLLATE,
        "CREATE TABLE faces_pending (photo_id INTEGER PRIMARY KEY, since TEXT NOT NULL)",
        "CREATE TABLE faces_detected (photo_id INTEGER PRIMARY KEY, detector TEXT NOT NULL, mtime REAL, size INTEGER,"
        " found INTEGER NOT NULL, at TEXT NOT NULL)",
        "CREATE TABLE roots (name TEXT PRIMARY KEY NOT NULL, address TEXT NOT NULL, added TEXT NOT NULL)",
        # What the library views stand on, derived from the photos (tagpup.store.derived).
        "CREATE TABLE photo_tags (photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,"
        " tag_id INTEGER NOT NULL REFERENCES tag_taxonomy(id) ON DELETE CASCADE, PRIMARY KEY (photo_id, tag_id)) WITHOUT ROWID",
        "CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,"
        " path TEXT NOT NULL UNIQUE COLLATE %s, name TEXT NOT NULL)" % paths.COLLATE,
        "CREATE TABLE photo_folder (photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,"
        " folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE)",
        "CREATE INDEX idx_photos_document_id ON photos(document_id)",
        "CREATE INDEX idx_photos_taken ON photos(taken, id)",
        "CREATE INDEX idx_photos_year ON photos(year, taken, id)",
        "CREATE INDEX idx_faces_photo_id ON faces(photo_id)",
        "CREATE INDEX idx_faces_name ON faces(name)",
        # Identify Faces filters on `excluded` and `name` together, on every load. Leading with `excluded` answers a count of
        # the excluded bucket from the index alone, without touching rows that carry a 2 KB embedding.
        "CREATE INDEX idx_faces_identify ON faces(excluded, name)",
        # A person read by id: `WHERE tag_id = ?` needs these, and the pairs of name and id come out of idx_faces_person.
        "CREATE INDEX idx_faces_person ON faces(name, tag_id)",
        "CREATE INDEX idx_faces_tag ON faces(tag_id)",
        "CREATE INDEX idx_photo_people_name ON photo_people(name)",
        "CREATE INDEX idx_photo_people_tag ON photo_people(tag_id, photo_id)",
        "CREATE INDEX idx_tag_taxonomy_tag ON tag_taxonomy(tag)",
        "CREATE INDEX idx_change_rows_change ON change_rows(change_id)",
        "CREATE INDEX idx_change_rows_row ON change_rows(table_name, row_key)",
        "CREATE INDEX idx_changes_status ON changes(status)",
        "CREATE INDEX idx_change_files_change ON change_files(change_id)",
        "CREATE INDEX idx_job_runs_job ON job_runs(job, library, id)",
        # A tag's photos, by one seek, and covering: the photo ids come out of the index.
        "CREATE INDEX idx_photo_tags_tag ON photo_tags(tag_id, photo_id)",
        "CREATE INDEX idx_folders_parent ON folders(parent_id)",
        "CREATE INDEX idx_photo_folder_folder ON photo_folder(folder_id)",
        # A crop, an embedding, a person's line, a suggestion and the derived rows go with their photo, face or node, on any
        # connection: only one connection in the app turns foreign keys on, so ON DELETE CASCADE alone would leave them behind.
        "CREATE TRIGGER face_crops_go_with_their_face AFTER DELETE ON faces"
        " BEGIN DELETE FROM face_crops WHERE face_id = OLD.id; END",
        "CREATE TRIGGER embeddings_go_with_their_photo AFTER DELETE ON photos"
        " BEGIN DELETE FROM embeddings WHERE photo_id = OLD.id; END",
        "CREATE TRIGGER photo_people_go_with_their_photo AFTER DELETE ON photos"
        " BEGIN DELETE FROM photo_people WHERE photo_id = OLD.id; END",
        "CREATE TRIGGER suggestions_go_with_their_photo AFTER DELETE ON photos"
        " BEGIN DELETE FROM suggestions WHERE photo_id = OLD.id; END",
        "CREATE TRIGGER derived_go_with_their_photo AFTER DELETE ON photos BEGIN"
        " DELETE FROM photo_tags WHERE photo_id = OLD.id; DELETE FROM photo_folder WHERE photo_id = OLD.id; END",
        "CREATE TRIGGER photo_tags_go_with_their_node AFTER DELETE ON tag_taxonomy"
        " BEGIN DELETE FROM photo_tags WHERE tag_id = OLD.id; END",
        # The backstop for a person that faces name: the DELETE of a node of the tag tree a face names aborts, whichever
        # connection deletes it (an old checkout, a script, an undo). The tree edits that delete a person the right way
        # unname or repoint their faces first, so the trigger sees nothing.
        "CREATE TRIGGER person_tag_not_deleted_while_named BEFORE DELETE ON tag_taxonomy"
        " WHEN EXISTS (SELECT 1 FROM faces WHERE tag_id = OLD.id)"
        " BEGIN SELECT RAISE(ABORT, 'person_tag_not_deleted_while_named'); END",
    ):
        conn.execute(statement)
    # The word index a search matches words in (two contentless FTS5 tables and the trigger that takes a deleted photo's rows),
    # and the two indexes a library view orders by file name and by caption with: expressions of built-in functions, so nothing
    # writes them but SQLite (tagpup.store.search_index, library_view).
    from tagpup.store import library_view   # the store imports this module
    for statement in search_index.CREATE:
        conn.execute(statement)
    conn.execute("CREATE INDEX %s ON photos(%s COLLATE NOCASE, id)" % (library_view.NAME_INDEX, library_view.name_sql("path")))
    conn.execute("CREATE INDEX %s ON photos(%s COLLATE NOCASE, id)" % (library_view.CAPTION_INDEX,
                                                                     library_view.caption_sql("captions")))
    # What moves each generation (tagpup.store.generations). Faces: whatever changes who a face is -- a crop being cached is not
    # that. The tree: any node's path, name, place or face flag. Photos: any change to a row, and to a photo's people.
    for name, table, update in (("faces", "faces", "UPDATE OF name, name_source, excluded, embedding, photo_id, tag_id"),
                                ("taxonomy", "tag_taxonomy", "UPDATE OF tag, name, parent_id, has_face"),
                                ("photos", "photos", "UPDATE")):
        conn.execute("INSERT INTO generations (name, value) VALUES (?, 0)", (name,))
        bump = "BEGIN UPDATE generations SET value = value + 1 WHERE name = '%s'; END" % name
        for event, when in (("insert", "INSERT"), ("delete", "DELETE"), ("update", update)):
            conn.execute("CREATE TRIGGER generation_%s_%s AFTER %s ON %s %s" % (name, event, when, table, bump))
    for event in ("insert", "delete"):
        conn.execute("CREATE TRIGGER generation_photos_people_%s AFTER %s ON photo_people"
                     " BEGIN UPDATE generations SET value = value + 1 WHERE name = 'photos'; END" % (event, event.upper()))
    # Paths compare the way the filesystem does (paths.sql_equals): without case on Windows. An equality under a collation
    # can only use an index declared with it.
    if paths.COLLATE != "BINARY":
        conn.execute("CREATE INDEX idx_photos_path_nocase ON photos(path COLLATE %s)" % paths.COLLATE)


# ---- What a migration holds true before it commits ----------------------------------------

#: The runner's own tables: it writes them as it records each migration.
RUNNER_TABLES = ("schema_version", "changes", "change_rows")

#: Tables any migration may change and nothing records: the counters its triggers move
#: (tagpup.store.generations); every derived table (derived.ALL: each photo's people, the folders, the word index); and the
#: shadow tables FTS5 keeps for the word index (search_index.SHADOWS), which SQLite writes and on a virtual one of which no
#: trigger can be made.
UNWATCHED = ("generations",) + derived.ALL + search_index.SHADOWS


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


STANDARD = (ForeignKeys(), Integrity())


def same_stored(a, b):
    """Equal as SQLite stores them: 1 is not 1.0, nor b'1' '1'. The journal's comparison of a row with what a change read is
    this one."""
    return type(a) is type(b) and a == b


class _Watch:
    """Every row of every table the library has, as a migration changes it: TEMP triggers
    on each, which the connection alone sees and which go with the transaction if it is
    rolled back. What SQLite's session extension records, which is reachable from Python
    only through APSW (tagpup.store.journal)."""

    TABLE = "tagpup_migration_watch"

    def __init__(self, conn):
        self.conn = conn
        self.columns = {t: table_columns(conn, t) for t in _tables_now(conn)
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
            columns = table_columns(self.conn, table)
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
                changed = {c: v for c, v in before.items() if c in after and not same_stored(v, after[c])}
                if changed:
                    seen.append((table, rid, changed, after))
        return seen, sorted(moved)


def _shape(conn):
    return {table: table_columns(conn, table) for table in _tables_now(conn)}


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


MIGRATIONS = (
    Migration(BASELINE, "the baseline: a new library, as migration 32 left one", _baseline, ADDITIVE,
              "makes every table, index and trigger of a new library in one transaction; a library below it is refused, not "
              "converted (steps 1 to 32 were deleted on 2026-10-15)",
              (),
              (RowsKept(),) + STANDARD),
)

LATEST = MIGRATIONS[-1].version


def _has_tables(conn):
    """Has the library on `conn` any table but the record of its migrations: is it made, or an empty file?"""
    return bool([t for t in _tables_now(conn) if t != "schema_version"])


def version(conn):
    """The last migration applied to the library on `conn`; 0 for none."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] or 0


def pending_on(conn):
    """The migrations the library on `conn` has not had, in order. Reads only."""
    current = version(conn)
    return [m for m in MIGRATIONS if m.version > current]


def pending(db_path):
    """pending_on, of the library file at `db_path` opened read-only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return pending_on(conn)
    finally:
        conn.close()


#: Libraries this process has found current, by what the file is and when it last
#: changed. A file deleted and made again at the same path is a different file; one a
#: backup was copied over keeps its id and creation time on Windows, but not its
#: modified time (docs/findings.md, #56). Either is looked at again, as is a library
#: whose file a checkpoint has written to since: one query, now and then.
#:
#: The stat of the main file does NOT see a newer app's commit: in WAL mode a commit goes to the
#: -wal file, and the main file stays as it was while any connection holds a read mark. So a
#: library found current is also asked its version again once RECHECK_SECONDS have passed since it
#: was last asked (a read-only query): a long-lived server, MCP or watcher is refused within that
#: bound after a newer app migrates the library (docs/findings.md, the newer-library guard).
_current = {}
_current_guard = threading.Lock()
RECHECK_SECONDS = 2.0
_clock = time.monotonic


def forget(db_path=None):
    """Forget that this process found the library at `db_path` current -- every library, without one -- so the next
    `ensure` reads the file: for a test that changes a library, or the code's migrations, behind the process's back."""
    with _current_guard:
        if db_path is None:
            _current.clear()
        else:
            _current.pop(db.key(db_path), None)


def _still_current(path):
    """False when the library at `path`, found current, is now newer than this version knows (or cannot
    be read just now: `ensure` then looks at it in full and says why)."""
    try:
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            return version(conn) <= LATEST
        finally:
            conn.close()
    except sqlite3.Error:
        return False


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
    back (CheckFailed). What each takes first follows from its kind (ADDITIVE,
    DESTRUCTIVE); each is recorded in the journal. A library this process has already
    found current costs a stat and no connection, except that its version is read again
    (read only) every RECHECK_SECONDS, so a newer app's migration refuses a process that
    has run on it for hours: NewerLibrary within that bound.

    The first time a process opens a library, a change of the journal that a crash left
    `derived_pending` is finished (tagpup.store.journal.settle).
    """
    key = db.key(db_path)
    identity = _identity(db_path)
    with _current_guard:
        seen = _current.get(key)
    if identity is not None and seen is not None and seen[0] == identity:
        if _clock() - seen[1] < RECHECK_SECONDS:
            return []
        if _still_current(db_path):
            with _current_guard:
                _current[key] = (identity, _clock())
            return []
        with _current_guard:
            _current.pop(key, None)
    try:
        applied = _ensure(db_path)
    except NewerLibrary:
        if _reading_newer.get():
            return []   # reading_newer(): as it is, nothing migrated, settled or remembered
        raise
    from tagpup.store import journal   # the journal imports this module
    journal.settle_once(db_path)
    with _current_guard:
        _current[key] = (_identity(db_path), _clock())
    return applied


#: The libraries this process is migrating just now, by the database's key, and how many threads are. A request for
#: one must not wait on its write lock holding a server thread (tagpup.web.libraries answers it 503 at once).
_migrating = {}


def migrating(db_path):
    """Is a thread of this process migrating (or making) the library at `db_path` just now? A cheap read, no connection."""
    with _current_guard:
        return _migrating.get(db.key(db_path), 0) > 0


def _ensure(db_path):
    conn = db.connect(db_path, timeout=30.0)
    try:
        found = version(conn)
        if found > LATEST:
            raise NewerLibrary(os.path.basename(db_path), found, LATEST)
        if found == LATEST:
            return []
        key = db.key(db_path)
        with _current_guard:
            _migrating[key] = _migrating.get(key, 0) + 1
        try:
            return _migrate(db_path, conn)
        finally:
            with _current_guard:
                _migrating[key] -= 1
    finally:
        conn.close()


def _migrate(db_path, conn):
    with db.lock_for(db_path):
        conn.execute("BEGIN IMMEDIATE")
        try:
            # A library with no tables yet is being made, not migrated: nothing to back
            # up, watch or record. One below the baseline has nothing here to bring it forward: refused before anything is
            # written to it (not even the table of migrations), under the lock, so a library another process has just made
            # is read as made.
            made = not _has_tables(conn)
            found = version(conn)
            if not made and found < BASELINE:
                raise TooOld(os.path.basename(db_path), found)
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version ("
                         " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        run = _Run(db_path, conn, made)
        return [migration.name for migration in MIGRATIONS if run.migrate(migration)]


class _Run:
    """One pass of the runner over a library, under its write lock: the backup it has
    taken, if any -- one, from before the first migration that needs it, holds the
    library for every one after it too."""

    def __init__(self, db_path, conn, made):
        self.db_path, self.conn, self.made = db_path, conn, made
        self.backup = None
        self.backed_up = False
        self.note = None   # what the migration just run says it did, if it says (its return value)

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
            summary = {"kind": migration.kind}
            if migration.kind == DESTRUCTIVE:
                summary["backup"] = self._backup(migration)
            summary["checks"] = self._apply(migration)
            if self.note:
                summary["took"] = self.note
            conn.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                         (migration.version, migration.name, time.strftime("%Y-%m-%d %H:%M:%S")))
            self._record(journal, migration, summary)
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
        migration finds it. None for a library with nothing in it to lose, and for one being made: the baseline writes the
        tables, and the first migration after it (a destructive one) must not copy what the baseline has just made."""
        if not self.backed_up and not self.made and _has_rows(self.conn):
            self.backup = db.backup(self.db_path, "migration-%d" % migration.version)
            logger.info("Backed up to %s", self.backup)
        self.backed_up = True
        return os.path.basename(self.backup) if self.backup else None

    def _apply(self, migration):
        """Run `migration` and its checks, in the transaction. Returns the names of the
        checks it passed."""
        conn = self.conn
        states = [check.before(conn, migration) for check in migration.checks]
        shape = _shape(conn) if migration.kind != DESTRUCTIVE else None
        watch = _Watch(conn) if migration.kind == ADDITIVE and not self.made else None
        try:
            done = migration.apply(conn)
            self.note = done if isinstance(done, dict) else None   # a migration may say what it took (a dict of counts)
        except sqlite3.OperationalError as e:
            # A watched column dropped or renamed: the watch's triggers name it.
            if watch and "tagpup_watch_" in str(e):
                raise CheckFailed("nothing dropped", ["it altered a column the runner watches: %s" % e]) from None
            raise
        seen, moved = watch.finish() if watch else ([], [])
        passed = []
        if shape is not None:
            _require("nothing dropped", _dropped(conn, shape))
            passed.append("nothing dropped")
        if watch:
            _require("nothing that was there changed", _tally(seen, moved))
            passed.append("nothing that was there changed")
        for check, state in zip(migration.checks, states):
            _require(check.name, check.after(conn, migration, state))
            passed.append(check.name)
        return passed

    def _record(self, journal, migration, summary):
        """Record the migration as a change of the journal, with no rows, at the version before it, so the journal
        refuses to undo it: a schema is not undone."""
        if self.made:
            return
        journal.record(self.conn, "migration %d: %s" % (migration.version, migration.name), [], summary,
                       schema_version=migration.version - 1)


def _has_rows(conn):
    """Has the library anything a backup would keep: a row in any table but the record of
    its migrations and the counters? Not only photos: a tree or a journal is worth one."""
    for table in _tables_now(conn):
        if table in ("schema_version", "generations"):
            continue
        if conn.execute("SELECT 1 FROM %s LIMIT 1" % _quoted(table)).fetchone():
            return True
    return False

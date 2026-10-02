"""The library's roots, and the one place a connection's paths are converted by them.

A library may hold a photo's path as a root's name and the path under it (`@pictures/2024/
a.jpg`, tagpup.core.paths) instead of this machine's spelling of it, so that the library does
not say where a machine keeps its photos (docs/ARCHITECTURE.md, "Roots and machines"). The
library holds its roots -- a name and the share's own address -- in the `roots` table
(migration 18: empty, which opening a library only ever makes); each machine holds where it
keeps each (machine_roots.json, tagpup.config, reached through tagpup.core.machine).

**Native in memory, root-relative in the database.** Everything above the store handles
this machine's native paths. A store function reads a path column through `from_row` and
writes it through `to_row`, both idempotent where they must be: a row already converted is
written as it is, so a journal row replayed after a conversion, or a path converted twice,
is the same row. A library with no roots has the identity for Roots, and every function
here then returns what it was given, byte for byte.

**One Roots for one operation.** `roots_for(conn)` is the Roots a connection converts by, kept
on the connection (tagpup.store.db.Connection.roots_state), so an operation that holds a
connection converts every path of it by one map and never asks the table or the map a row. It
is read again when the library's roots table changed under it (PRAGMA data_version, which
another connection's commit moves: one number a call outside a transaction, nothing inside
one), and the machine's map is looked at again once a second at most -- tagpup.config's cache
makes that a stat, and the same Roots back when the file is as it was -- so a map edited
while a long-lived connection sits idle is found. Inside a transaction the Roots is the one
the transaction began with. `pinned(db_path)` holds one Roots for every connection of the
library in this process for a whole run: a map edited meanwhile changes nothing until the run
ends, and a change of the library's roots by another process stops it (RootsChanged), where
converting under the old Roots would write a native row into a converted library. It is built
and tested, and no run in `services` calls it yet: an index run, a sync pass and a file change
hold the Roots of their one connection, which is what keeps each operation consistent, and
wrapping a run that opens a connection per batch is stage 3's (docs/ARCHITECTURE.md, "Roots
and machines"). A write on a connection of its own that spans the change is
refused at its commit and run again (db.write_with_connection; `unchanged`), and one that
writes on a connection its caller commits begins its transaction first (`begin_write`).

SQL does the comparing, as before: `sql_equals`, `sql_under` and `sql_in` here convert their
argument and answer the same ranges on the same indexes (tagpup.core.paths). A function of a
column is never in a WHERE: rows are converted after they are read.
"""
import contextlib
import json
import re
import sqlite3
import string
import threading
import time

from tagpup.core import machine, paths, validation
from tagpup.store import db

TABLE = "roots"

#: How long a connection trusts the map it holds before asking again (a stat).
RECHECK_SECONDS = 1.0

#: The settings that hold folders, one a line: converted line by line.
FOLDER_SETTINGS = ("library.roots", "library.ignored")

TIME = "%Y-%m-%d %H:%M:%S"


class RootsChanged(RuntimeError):
    """The library's roots were changed, by another process, while an operation that holds
    one Roots for its whole length was running. The operation stops: whatever it converts
    from here would be converted by roots the library no longer has."""


# ---- The table -------------------------------------------------------------------------

def has_table(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None


def _rows(conn):
    """((name, address), ...) of the library's roots, by name; () for a library with none or
    one behind migration 18."""
    try:
        return tuple((name, address or "") for name, address in
                     conn.execute("SELECT name, address FROM roots ORDER BY name"))
    except sqlite3.OperationalError as problem:
        if "no such table" in str(problem):
            return ()
        raise


def every(conn):
    """[(name, address, added)] of the library's roots, by name."""
    if not has_table(conn):
        return []
    return [tuple(row) for row in conn.execute("SELECT name, address, added FROM roots ORDER BY name")]


def logical(conn):
    """{name: the share's address} of the library's roots."""
    return dict(_rows(conn))


def listing(db_path):
    """[{"name", "address", "added"}] of the roots of the library at `db_path`, read-only:
    nothing is migrated, and a library behind migration 18 has none."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return [{"name": name, "address": address, "added": added} for name, address, added in every(conn)]
    finally:
        conn.close()


def insert(conn, name, address):
    """Add a root. The caller commits, in the transaction that converts the rows it names."""
    conn.execute("INSERT INTO roots (name, address, added) VALUES (?, ?, ?)",
                 (paths.root_name(name), address or "", time.strftime(TIME)))
    _forget(conn)


def delete(conn, name):
    """Take a root away. The caller commits, in the transaction that converts its rows back."""
    removed = conn.execute("DELETE FROM roots WHERE name = ?", (paths.root_name(name),)).rowcount
    _forget(conn)
    return removed


# ---- The Roots a connection converts by ---------------------------------------------------

class State:
    """What a connection holds of the library's roots."""
    __slots__ = ("version", "rows", "roots", "checked", "file")

    def __init__(self, version, rows, roots, checked, file):
        self.version, self.rows, self.roots, self.checked, self.file = version, rows, roots, checked, file


class _Pin:
    __slots__ = ("rows", "roots", "count")

    def __init__(self, rows, roots):
        self.rows, self.roots, self.count = rows, roots, 0


_pins = {}
_pins_guard = threading.Lock()


def _forget(conn):
    """Let the connection read the library's roots again at its next use."""
    if isinstance(conn, db.Connection):
        conn.roots_state = None


def _file_of(conn):
    """The file the connection is to, as a key; None for a database in memory."""
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return paths.key(file) if file else None
    return None


def roots_for(source):
    """The paths.Roots the connection `source` converts its paths by: IDENTITY for a library
    with no roots. A path or a Library opens a look at the library for it; give a connection
    where there is one, so the operation holding it converts everything by one Roots.

    Raises paths.RootsError, naming machine_roots.json, when the library has roots and this
    machine's map cannot be read or does not place them: never an empty answer."""
    if isinstance(source, sqlite3.Connection):
        return _held(source)
    if hasattr(source, "execute"):
        # Something that stands in for a connection (a test's recorder): it has no library.
        return machine.IDENTITY
    path = getattr(source, "path", source)
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return _held(conn)
    finally:
        conn.close()


def _held(conn):
    if not isinstance(conn, db.Connection):
        # A connection opened elsewhere (a test's, a script's) has nowhere to keep it: read
        # the library's roots for each use. db.connect's connections are what the app uses.
        return machine.roots_of(dict(_rows(conn)))
    state = conn.roots_state
    if state is not None and conn.in_transaction:
        return state.roots
    version = conn.execute("PRAGMA data_version").fetchone()[0]
    now = time.monotonic()
    file = state.file if state is not None else _file_of(conn)
    if state is None or state.version != version:
        rows = _rows(conn)
    else:
        rows = state.rows
    with _pins_guard:
        pin = _pins.get(file) if file else None
    if pin is not None:
        if rows != pin.rows:
            raise RootsChanged("the library's roots were changed while this run held them; it stops, since "
                               "what it would convert from here would be converted by roots the library no "
                               "longer has. Start it again.")
        roots = pin.roots
    elif state is not None and rows == state.rows and now - state.checked < RECHECK_SECONDS:
        roots = state.roots
    else:
        roots = machine.roots_of(dict(rows))
    conn.roots_state = State(version, rows, roots, now, file)
    return roots


def begin_write(conn):
    """Begin a write transaction on a connection the caller commits, if none is open, so the
    roots the paths of the writes are converted by are the library's at the moment the write
    lock is taken: another process adopting the library meanwhile waits for the commit. The
    store's writes on a connection that lives longer than one operation (the index's) begin
    here. A no-op inside a transaction."""
    if conn.in_transaction:
        return
    conn.execute("BEGIN IMMEDIATE")
    state = getattr(conn, "roots_state", None)
    if state is not None and _rows(conn) != state.rows:
        conn.roots_state = None


def unchanged(conn):
    """Refuse, as a busy database -- which db.write_with_connection runs again, with the roots
    as they are now -- a write whose paths were converted by roots the library no longer has.
    Called before its commit, where the write lock is held."""
    state = getattr(conn, "roots_state", None)
    if state is not None and _rows(conn) != state.rows:
        conn.roots_state = None
        raise sqlite3.OperationalError("database is locked: the library's roots changed while this write was"
                                       " being prepared")


@contextlib.contextmanager
def pinned(db_path):
    """Hold the library's Roots, and the machine's map as it is now, for every connection of
    it in this process until the block ends: a run converts every path by one map however
    often the map is edited meanwhile. Yields the Roots. Nested pins share one. A change of
    the library's own roots by another process stops the run (RootsChanged)."""
    file = paths.key(db_path)
    with _pins_guard:
        pin = _pins.get(file)
    if pin is None:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            rows = _rows(conn)
        finally:
            conn.close()
        made = _Pin(rows, machine.roots_of(dict(rows)))
        with _pins_guard:
            pin = _pins.setdefault(file, made)
    with _pins_guard:
        pin.count += 1
    try:
        yield pin.roots
    finally:
        with _pins_guard:
            pin.count -= 1
            if pin.count <= 0 and _pins.get(file) is pin:
                del _pins[file]


# ---- Converting at the boundary -----------------------------------------------------------

def to_row(conn, path):
    """`path`, native, as the library holds it (paths.to_row)."""
    return paths.to_row(path, roots_for(conn))


def from_row(conn, value):
    """What the library holds, as this machine's native path (paths.from_row)."""
    return paths.from_row(value, roots_for(conn))


def sql_equals(conn, column, path):
    """paths.sql_equals, the argument converted by the connection's Roots."""
    return paths.sql_equals(column, path, roots_for(conn))


def sql_under(conn, column, folder):
    """paths.sql_under, the argument converted by the connection's Roots."""
    return paths.sql_under(column, folder, roots_for(conn))


def sql_in(conn, column, folder):
    """paths.sql_in, the argument converted by the connection's Roots."""
    return paths.sql_in(column, folder, roots_for(conn))


def natives(conn, rows, *columns, raw=()):
    """`rows`, as read, with the path at each index in `columns` made native, and the
    raw_metadata at each index in `raw` read as this machine would have it: the rows
    themselves, untouched, for a library with no roots; else a list of tuples."""
    roots = roots_for(conn)
    if roots.identity:
        return rows
    converted = []
    for row in rows:
        row = list(row)
        for column in columns:
            row[column] = paths.from_row(row[column], roots)
        for column in raw:
            row[column] = raw_to_native(row[column], roots)
        converted.append(tuple(row))
    return converted


_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def path_order(path):
    """The sort key of a native path that puts paths in the order the path indexes do: the
    order a library that holds its paths as the machine spells them lists them in. A row
    form sorts by its root's name first, so a list in a converted library is sorted here, after
    the paths are native, to be the list the owner has always seen."""
    return path.translate(_FOLD) if paths.COLLATE == "NOCASE" else path


def ordered(conn, rows, column=0):
    """`rows` -- already native -- by the path at index `column`, as an ORDER BY path gave
    them when the library held native paths. Untouched for a library with no roots, which the
    query ordered itself."""
    if roots_for(conn).identity:
        return rows
    return sorted(rows, key=lambda row: path_order(row[column]))


def native_one(conn, row, *columns, raw=()):
    """natives for a row that may be None."""
    if row is None:
        return None
    return natives(conn, [row], *columns, raw=raw)[0]


# ---- A path inside a column of JSON ---------------------------------------------------------

_SOURCE_FILE = re.compile(r'("SourceFile"\s*:\s*)("(?:[^"\\]|\\.)*")')


def _of_root(value, only):
    """Is `value` a row of root `only` -- `@only` or `@only/...`, the name in any case? Always
    true when no root is asked for. A test of the row's own form, never of the text around it."""
    if not isinstance(value, str) or not value.startswith(paths.ROOT_MARK):
        return False
    return only is None or value[1:].partition(paths.ROW_SEP)[0].lower() == only


def source_to_row(value, roots):
    """ExifTool's SourceFile of a photo, `D:/Training/Pictures/2024/a.jpg` -- the path the way
    ExifTool spells it, forward slashes -- as `@pictures/2024/a.jpg`, when the file is under a
    root and converting back gives exactly this string; any other spelling is left as it is,
    which loses nothing: the field says where the file was read, and a row that is compared
    with a fresh read compares the same string it was written with."""
    if not isinstance(value, str) or not value or value.startswith(paths.ROOT_MARK):
        return value
    try:
        row = paths.to_row(value, roots)
        if row == value or not row.startswith(paths.ROOT_MARK):
            return value
        return row if paths.exiftool_spelling(paths.from_row(row, roots)) == value else value
    except paths.RootsError:
        return value


def source_from_row(value, roots, only=None):
    """source_to_row, the other way: a row (of root `only`, if one is named) as ExifTool's
    spelling of the path."""
    if _of_root(value, only):
        return paths.exiftool_spelling(paths.from_row(value, roots))
    return value


def _source(text, roots, convert):
    if roots.identity or not text or '"SourceFile"' not in text:
        return text

    def swap(found):
        try:
            value = json.loads(found.group(2))
        except ValueError:
            return found.group(0)
        changed = convert(value, roots)
        return found.group(0) if changed == value else found.group(1) + json.dumps(changed)

    return _SOURCE_FILE.sub(swap, text)


def raw_to_row(text, roots):
    """raw_metadata as the library holds it: its SourceFile converted (source_to_row). The
    rest of the text is as it was."""
    return _source(text, roots, source_to_row)


def raw_to_native(text, roots, only=None):
    """raw_metadata as this machine reads it: its SourceFile as ExifTool would give it here --
    with `only`, just a SourceFile that is a row of that root, and nothing else of the text."""
    return _source(text, roots, lambda value, each: source_from_row(value, each, only))


def _exact(value, roots):
    """A native stored path as `@name/...` when converting back gives exactly this string."""
    if not isinstance(value, str) or not value or value.startswith(paths.ROOT_MARK):
        return value
    try:
        row = paths.to_row(value, roots)
        return row if row.startswith(paths.ROOT_MARK) and paths.from_row(row, roots) == value else value
    except paths.RootsError:
        return value


def _suggested(text, roots, convert):
    """suggestions.raw, the suggester's own output, with the paths in it -- the photo's own
    and each nearest neighbour's -- converted. Parsed and written back only when a path
    changed."""
    if roots.identity or not text:
        return text
    try:
        raw = json.loads(text)
    except ValueError:
        return text
    if not isinstance(raw, dict):
        return text
    changed = False
    if isinstance(raw.get("path"), str) and convert(raw["path"], roots) != raw["path"]:
        raw["path"] = convert(raw["path"], roots)
        changed = True
    for neighbour in raw.get("nearest_neighbors") or []:
        if isinstance(neighbour, dict) and isinstance(neighbour.get("path"), str):
            converted = convert(neighbour["path"], roots)
            if converted != neighbour["path"]:
                neighbour["path"] = converted
                changed = True
    return json.dumps(raw) if changed else text


def suggested_to_row(text, roots):
    return _suggested(text, roots, _exact)


def suggested_to_native(text, roots, only=None):
    """suggestions.raw with its paths native: with `only`, just those that are rows of that
    root."""
    return _suggested(text, roots, lambda value, each: paths.from_row(value, each) if _of_root(value, only) else value)


# ---- Folder lists: the settings library.roots and library.ignored ------------------------------

def folders_to_row(text, roots):
    """A folder setting's text, one folder a line, as the library holds it: each line that is
    a folder under a root as the root's row (`@pictures/2024`), every other line -- and the
    blanks -- as it was typed."""
    if roots.identity or not text:
        return text
    lines = []
    for line in str(text).split(validation.FOLDER_SEPARATOR):
        folder = validation.trim(line)
        if folder and not folder.startswith(paths.ROOT_MARK) and roots.locate(folder) is not None:
            line = paths.to_row(folder, roots)
        lines.append(line)
    return validation.FOLDER_SEPARATOR.join(lines)


def folders_to_native(text, roots):
    """folders_to_row, the other way: each line that is a root's row as this machine's path."""
    if roots.identity or not text:
        return text
    lines = []
    for line in str(text).split(validation.FOLDER_SEPARATOR):
        folder = validation.trim(line)
        if folder.startswith(paths.ROOT_MARK):
            line = paths.from_row(folder, roots)
        lines.append(line)
    return validation.FOLDER_SEPARATOR.join(lines)


# ---- A column of the journal's tables, as the library holds it -----------------------------------

def row_value(roots, table, column, value, key=None):
    """`value` of `table`.`column` as the library holds it, which is what the journal writes
    and compares by: a photo's path as its row, its raw_metadata's SourceFile, a suggestion's
    paths, the folders of the two folder settings (`key` the setting's name). Any other value,
    and every value of a library with no roots, as it is. Idempotent."""
    if roots.identity or value is None:
        return value
    if table == "photos":
        if column == "path":
            return value if not value or value.startswith(paths.ROOT_MARK) else paths.to_row(value, roots)
        if column == "raw_metadata":
            return raw_to_row(value, roots)
    elif table == "suggestions" and column == "raw":
        return suggested_to_row(value, roots)
    elif table == "settings" and column == "value" and key in FOLDER_SETTINGS:
        return folders_to_row(value, roots)
    return value


def native_value(roots, table, column, value, key=None):
    """row_value, the other way: what this machine reads."""
    if roots.identity or value is None:
        return value
    if table == "photos":
        if column == "path":
            return paths.from_row(value, roots) if value and value.startswith(paths.ROOT_MARK) else value
        if column == "raw_metadata":
            return raw_to_native(value, roots)
    elif table == "suggestions" and column == "raw":
        return suggested_to_native(value, roots)
    elif table == "settings" and column == "value" and key in FOLDER_SETTINGS:
        return folders_to_native(value, roots)
    return value


def row_values(roots, table, values, key=None):
    """{column: value} as the library holds them (row_value). `key` is the row's key, for
    the settings; found in `values` when it is there."""
    if roots.identity:
        return values
    key = key if key is not None else values.get("key")
    return {column: row_value(roots, table, column, value, key) for column, value in values.items()}

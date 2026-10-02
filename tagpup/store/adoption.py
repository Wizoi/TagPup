"""Adopting a library by a root: the one operation that converts a library's paths.

Opening a library never converts it (migration 18 only makes the empty `roots` table), and
the owner's libraries stay as they are until they say so. `adopt` is what they say: it adds a
root to the library and converts every path the library holds under it -- `photos.path` and the
SourceFile inside `photos.raw_metadata`, `suggestions.raw`'s paths, `change_files.path` and
`new_path`, `added_folders.path`, `damaged_files.path`, and the folders of the settings
`library.roots` and `library.ignored` -- in ONE transaction, under the library's write lock,
and records ONE change of the journal, `roots adopt`, which History lists and `undo` reverses
(`unadopt`: the same conversion the other way, by the machine's map).

What it refuses, with nothing written and each reason named (`Refused`): a root of that name
already (it is adopted once), a location that is not there, a location no row of the library
lies under (a wrong location would put every row outside the root), a library in which another
process holds the write lock, an unfinished change of photo files (it is settled first), a row
that does not convert back to the same file (`paths.key`), and two rows that would become one.
A row under no root keeps its native path and is reported, by folder, never guessed at.

The journal keeps what it was: the old and new values of `change_rows` are not rewritten. A
change recorded before the adoption holds native paths, and the journal converts them as it
reads them (tagpup.store.journal), so its undo writes the row form for a rooted photo.

Undone, the conversion is refused while a change made after the adoption touched a path (a
file change, a row's path, the folder settings): its recorded values are the row form, which a
library holding no roots cannot read. Undo those first.

Every step is `_reached`, which the tests stop the process at: a crash anywhere before the commit
leaves the library exactly as it was, since it is one transaction.
"""
import json
import logging
import os
import sqlite3

from tagpup.core import machine, paths, validation
from tagpup.store import db, journal, schema
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: The journal's name for the change is OPERATION and the root's name: "roots adopt: pictures".
OPERATION = "roots adopt"

#: How long the adoption waits for another process's write lock before it refuses.
LOCK_WAIT_MS = 3000

#: How many rows of the photos table are read and written at once.
CHUNK = 2000

#: How long a backup made for another operation still covers this one, in seconds. A backup
#: from minutes ago holds the library as it is (docs/DEVELOPMENT.md: no redundant backups).
RECENT_BACKUP_SECONDS = 15 * 60

#: The steps of an adoption, in order; `_reached` is told of each.
STEPS = ("photos converted", "other tables converted", "settings converted", "root recorded",
         "verified", "change recorded")

#: Where each path column is, by table: (key column, the columns that are a path, the columns of
#: JSON holding paths and how to convert them).
TABLES = (
    ("photos", "id", ("path",), ("raw_metadata",)),
    ("suggestions", "photo_id", (), ("raw",)),
    ("change_files", "id", ("path", "new_path"), ()),
    ("added_folders", "path", ("path",), ()),
    ("damaged_files", "path", ("path",), ()),
)


class Refused(Exception):
    """An adoption, or its undo, refused: nothing was written. `reasons` names each."""

    def __init__(self, reasons):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


def _reached(step):
    """A step is done (STEPS). Nothing happens here; the tests stop the process at each."""


# ---- Converting one value -------------------------------------------------------------------

class _Counts:
    """What one table's pass found, by kind: counts, never values."""

    def __init__(self):
        self.rows = 0
        self.convert = 0
        self.rooted = 0
        self.outside = 0
        self.respelled = 0
        self.irreversible = 0
        self.json = 0

    def as_dict(self):
        return {"rows": self.rows, "convert": self.convert, "already": self.rooted, "outside": self.outside,
                "respelled": self.respelled, "irreversible": self.irreversible, "json": self.json}


def _forward(value, roots, name, counts):
    """`value`, a path, as the library will hold it once root `name` is adopted: (the new
    value, whether it changed). A path that is the root's own, or under it, as its row; any
    other -- already a row, under no root, under another -- as it is. Counts what it found:
    a row that does not convert back to the same file is `irreversible` and is left as it is."""
    if not value or value.startswith(paths.ROOT_MARK):
        counts.rooted += 1 if value else 0
        return value, False
    found = roots.locate(value)
    if found is None or found[0] != name:
        counts.outside += 1
        return value, False
    try:
        row = paths.to_row(value, roots)
        back = paths.from_row(row, roots)
    except paths.RootsError:
        counts.irreversible += 1
        return value, False
    if paths.key(back) != paths.key(value):
        counts.irreversible += 1
        return value, False
    if back != value:
        counts.respelled += 1
    counts.convert += 1
    return row, True


def _backward(value, roots, name, counts):
    """_forward the other way: a row of root `name` as the native path, any other value as it
    is."""
    if not value or not value.startswith(paths.ROOT_MARK):
        return value, False
    named = value[1:].partition(paths.ROW_SEP)[0].lower()
    if named != name:
        counts.rooted += 1
        return value, False
    try:
        native = paths.from_row(value, roots)
    except paths.RootsError:
        counts.irreversible += 1
        return value, False
    counts.convert += 1
    return native, True


def _json_forward(text, roots, column, table):
    if table == "photos" and column == "raw_metadata":
        return store_roots.raw_to_row(text, roots)
    return store_roots.suggested_to_row(text, roots)


def _json_backward(text, roots, column, table):
    if table == "photos" and column == "raw_metadata":
        return store_roots.raw_to_native(text, roots)
    return store_roots.suggested_to_native(text, roots)


# ---- Passes over the tables ----------------------------------------------------------------

def _roots_with(conn, name, address, locations):
    """The Roots the library will have once `name` is adopted, at `locations` (the machine's
    map for it, else the place the owner named). The machine's places for the library's other
    roots are kept, so a path under one of them is still that root's."""
    logical = dict(store_roots._rows(conn))
    existing = machine.roots_of(logical) if logical else machine.IDENTITY
    logical[name] = address
    places = {each: list(found) for each, found in existing.locations.items()}
    places[name] = list(locations)
    return paths.Roots.of(logical, places, existing.map_file)


def _photos_pass(conn, roots, name, apply, direction):
    """Convert (or just count) the photos' paths and raw_metadata, a chunk at a time by id.
    Returns (_Counts, the converted paths as they will be, by folded spelling, for the
    collision check, the rows under no root as (folder) for the report)."""
    counts = _Counts()
    folded, outside, collisions = {}, [], 0
    last = -1
    step = _forward if direction == "adopt" else _backward
    while True:
        chunk = conn.execute("SELECT id, path, raw_metadata FROM photos WHERE id > ? ORDER BY id LIMIT ?",
                             (last, CHUNK)).fetchall()
        if not chunk:
            break
        last = chunk[-1][0]
        paths_only, both = [], []
        for photo_id, path, raw in chunk:
            counts.rows += 1
            new_path, changed = step(path, roots, name, counts)
            if direction == "adopt" and not changed and not path.startswith(paths.ROOT_MARK):
                outside.append(os.path.dirname(path))
            converted = None
            if raw:
                converted = (_json_forward if direction == "adopt" else _json_backward)(raw, roots, "raw_metadata",
                                                                                    "photos")
                if converted != raw:
                    counts.json += 1
                else:
                    converted = None
            key = store_roots.path_order(new_path)
            if key in folded:
                collisions += 1
            folded[key] = folded.get(key, 0) + 1
            if converted is not None:
                both.append((new_path, converted, photo_id))
            elif changed:
                paths_only.append((new_path, photo_id))
        if apply:
            if paths_only:
                conn.executemany("UPDATE photos SET path = ? WHERE id = ?", paths_only)
            if both:
                conn.executemany("UPDATE photos SET path = ?, raw_metadata = ? WHERE id = ?", both)
    return counts, collisions, outside


def _simple_pass(conn, roots, name, apply, direction, table, key, columns, json_columns):
    """Convert (or count) one of the smaller tables: every row, whole."""
    counts = _Counts()
    step = _forward if direction == "adopt" else _backward
    convert = _json_forward if direction == "adopt" else _json_backward
    names = list(columns) + list(json_columns)
    if not names:
        return counts, 0
    rows = conn.execute("SELECT %s, %s FROM %s" % (key, ", ".join(names), table)).fetchall()
    seen, collisions = {}, 0
    for row in rows:
        counts.rows += 1
        found = dict(zip(names, row[1:]))
        new = {}
        for column in columns:
            value = found[column]
            moved, changed = step(value, roots, name, counts) if value else (value, False)
            if changed:
                new[column] = moved
        for column in json_columns:
            text = found[column]
            converted = convert(text, roots, column, table) if text else text
            if converted != text:
                new[column] = converted
                counts.json += 1
        if table in ("added_folders", "damaged_files"):
            folded = store_roots.path_order(new.get("path", found["path"]))
            if folded in seen:
                collisions += 1
            seen[folded] = True
        if apply and new:
            conn.execute("UPDATE %s SET %s WHERE %s = ?" % (
                table, ", ".join("%s = ?" % column for column in new), key), list(new.values()) + [row[0]])
    return counts, collisions


def _settings_pass(conn, roots, name, apply, direction):
    """The two folder settings, converted a line at a time. Returns the keys it rewrote."""
    rewritten = []
    for key in store_roots.FOLDER_SETTINGS:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if not row or not row[0]:
            continue
        lines, changed = [], False
        for line in str(row[0]).split(validation.FOLDER_SEPARATOR):
            folder = validation.trim(line)
            if direction == "adopt":
                found = roots.locate(folder) if folder and not folder.startswith(paths.ROOT_MARK) else None
                if found is not None and found[0] == name:
                    try:
                        converted = paths.to_row(folder, roots)
                        if paths.key(paths.from_row(converted, roots)) == paths.key(folder):
                            line, changed = converted, True
                    except paths.RootsError:
                        pass
            elif folder.startswith(paths.ROOT_MARK) and folder[1:].partition(paths.ROW_SEP)[0].lower() == name:
                line, changed = paths.from_row(folder, roots), True
            lines.append(line)
        if changed:
            rewritten.append(key)
            if apply:
                conn.execute("UPDATE settings SET value = ? WHERE key = ?",
                             (validation.FOLDER_SEPARATOR.join(lines), key))
    return rewritten


def _tables_pass(conn, roots, name, apply, direction):
    """Every table, converted (or counted): (the report by table, collisions, outside folders,
    the settings rewritten)."""
    report, collisions = {}, 0
    counts, clash, outside = _photos_pass(conn, roots, name, apply, direction)
    report["photos"] = counts.as_dict()
    collisions += clash
    if apply:
        _reached("photos converted")
    for table, key, columns, json_columns in TABLES[1:]:
        if not _has(conn, table):
            continue
        counts, clash = _simple_pass(conn, roots, name, apply, direction, table, key, columns, json_columns)
        report[table] = counts.as_dict()
        collisions += clash
    if apply:
        _reached("other tables converted")
    settings = _settings_pass(conn, roots, name, apply, direction) if _has(conn, "settings") else []
    if apply:
        _reached("settings converted")
    return report, collisions, outside, settings


def _has(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _counted(conn):
    """{table: rows} of every table a conversion touches, for the before-and-after check."""
    return {table: conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            for table, _key, _columns, _json in TABLES if _has(conn, table)}


# ---- What stands in the way ------------------------------------------------------------------

def _unfinished(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'changes'").fetchone():
        return []
    return [change_id for (change_id,) in conn.execute("SELECT id FROM changes WHERE status = 'planned' ORDER BY id")]


def early_refusals(conn, name, locations):
    """What can be refused before a row is read: a root of that name already, a location that is
    not there, a change of photo files not finished. [] when none: the backup is taken then."""
    reasons = []
    held = {each for each, _address in store_roots._rows(conn)}
    if name in held:
        reasons.append("the library already has a root named %r: a root is adopted once" % name)
    if not any(os.path.isdir(place) for place in locations[:1]):
        reasons.append("the location %r is not a folder on this machine" % (locations[0] if locations else ""))
    unfinished = _unfinished(conn)
    if unfinished:
        reasons.append("%d change(s) of photo files are not finished (change %s): let the app settle them, "
                       "or undo them, first" % (len(unfinished), unfinished[0]))
    return reasons


def refusals(conn, name, locations, roots, counts, collisions):
    """Why the root cannot be adopted, [] when it can: each a sentence naming a count, never a
    path."""
    reasons = early_refusals(conn, name, locations)
    photos = counts.get("photos", {})
    if photos.get("rows") and not photos.get("convert") and not photos.get("irreversible"):
        reasons.append("no row of the library lies under the location: all %d photo row(s) are outside it "
                       "(%d already rooted); a wrong location would put every row outside the root"
                       % (photos["rows"] - photos.get("already", 0), photos.get("already", 0)))
    irreversible = sum(each.get("irreversible", 0) for each in counts.values())
    if irreversible:
        reasons.append("%d row(s) do not convert back to the same file under this root" % irreversible)
    if collisions:
        reasons.append("%d row(s) would become the same row as another once converted (two rows of one "
                       "file): merge them first" % collisions)
    return reasons


def _report(conn, roots, name, address, locations, apply, direction="adopt"):
    report, collisions, outside, settings = _tables_pass(conn, roots, name, apply, direction)
    return {"root": name, "address": address, "locations": list(locations), "tables": report,
            "settings": settings, "collisions": collisions,
            "outside": paths.outside_roots(outside, roots), "outside_rows": len(outside)}


def _clean(location):
    return paths.stored(location) if location else ""


# ---- The dry run -----------------------------------------------------------------------------

def rehearse(db_path, name, address, locations):
    """What adopting root `name` at `locations` -- the first is where this machine puts its
    paths -- would do to the library at `db_path`, and why it would be refused: a dict
    {root, address, locations, tables: {table: counts}, settings, outside: [{group, count}],
    outside_rows, collisions, refused: [reasons]}. Reads only, on a connection that writes
    nothing; a library behind this version's schema is not migrated, and has no roots (its
    tables are counted as they are)."""
    name = paths.root_name(name)
    locations = [_clean(each) for each in locations]
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        roots = _roots_with(conn, name, address or "", locations)
        report = _report(conn, roots, name, address, locations, apply=False)
        counts = report["tables"]
        report["refused"] = refusals(conn, name, locations, roots, counts, report["collisions"])
        return report
    finally:
        conn.close()


# ---- The adoption ----------------------------------------------------------------------------

def _recent_backup(db_path):
    """The newest `before-*` copy of the library made within RECENT_BACKUP_SECONDS, or None."""
    from tagpup.core.library import Library
    import time
    folder = Library(db_path).backups
    stem = os.path.splitext(os.path.basename(db_path))[0] + ".before-"
    newest = None
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for each in names:
        if each.startswith(stem) and each.endswith(".db"):
            made = os.path.getmtime(os.path.join(folder, each))
            if time.time() - made <= RECENT_BACKUP_SECONDS and (newest is None or made > newest[0]):
                newest = (made, os.path.join(folder, each))
    return newest[1] if newest else None


def backup(db_path):
    """The copy that covers the adoption: one made within the last RECENT_BACKUP_SECONDS if there
    is one, else a new one (tagpup.store.db.backup). Returns (its path, whether it was just made)."""
    found = _recent_backup(db_path)
    if found:
        return found, False
    return db.backup(db_path, "roots-adopt"), True


def adopt(db_path, name, address, locations):
    """Adopt root `name` (the share's own `address`, kept as given) at `locations` -- where
    this machine keeps it, the first being where a path under it is put -- for the library at
    `db_path`: convert every path under it and record the change, in one transaction under the
    write lock. Returns what it did, as rehearse reports what it would. Refused (Refused), with
    nothing written, for every reason refusals() gives, and for another process holding the
    write lock, after the library's backup is taken (`backup`: a copy made within the quarter
    hour before covers it) -- under the write lock too, so the copy is the library as the
    adoption finds it and two adoptions at once do not both copy it. The caller has written the
    machine's map."""
    name = paths.root_name(name)
    locations = [_clean(each) for each in locations]
    schema.ensure(db_path)
    with db.lock_for(db_path):
        conn = db.connect(db_path)
        try:
            conn.execute("PRAGMA busy_timeout=%d" % LOCK_WAIT_MS)
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as problem:
                raise Refused("another process holds the library's write lock (%s); nothing was written"
                              % problem) from None
            try:
                store_roots._forget(conn)
                early = early_refusals(conn, name, locations)
                if early:
                    raise Refused(early)
                try:
                    kept, made = backup(db_path)
                except Exception as problem:
                    raise Refused("the library could not be backed up first (%s)" % problem) from None
                roots = _roots_with(conn, name, address or "", locations)
                before = _counted(conn)
                # Converted at once, in this transaction: what is refused below is rolled back
                # with it, and two rows that would become one are SQLite's to refuse (UNIQUE).
                try:
                    report = _report(conn, roots, name, address, locations, apply=True)
                except sqlite3.IntegrityError as problem:
                    raise Refused("two rows would become one row once converted (two rows of one file): "
                                  "merge them first (%s)" % problem) from None
                reasons = refusals(conn, name, locations, roots, report["tables"], report["collisions"])
                if reasons:
                    raise Refused(reasons)
                store_roots.insert(conn, name, address or "")
                _reached("root recorded")
                actual = _held_roots(conn)
                problems = verify(conn, actual)
                if tuple(actual.locations.get(name, ())) != tuple(roots.locations.get(name, ())):
                    problems.append("the machine's map places the root at %r, not where its rows were converted by"
                                    % (list(actual.locations.get(name, ())),))
                after = _counted(conn)
                if after != before:
                    problems.append("the tables' row counts changed: %r then %r" % (before, after))
                if problems:
                    raise Refused(["verification failed, so nothing was kept: " + "; ".join(problems[:5])])
                _reached("verified")
                summary = {"root": name, "converted": {table: counts["convert"] for table, counts
                                                       in report["tables"].items()},
                           "json": {table: counts["json"] for table, counts in report["tables"].items()
                                    if counts["json"]},
                           "settings": report["settings"], "outside_rows": report["outside_rows"],
                           "respelled": sum(counts["respelled"] for counts in report["tables"].values())}
                report["backup"] = {"file": kept, "made": made}
                report["change"] = journal.record(conn, "%s: %s" % (OPERATION, name), [], summary)
                _reached("change recorded")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            store_roots._forget(conn)
            logger.info("%s: adopted root %s, %s", db_path, name, json.dumps(report["tables"].get("photos", {})))
            return report
        finally:
            conn.close()


def _held_roots(conn):
    """The Roots the library on `conn` has now, as its roots table and the machine's map say:
    asked of the map itself, not of a run that holds the library's Roots (store.roots.pinned) --
    this is the change those runs stop for."""
    store_roots._forget(conn)
    return machine.roots_of(dict(store_roots._rows(conn)))


# ---- The check -------------------------------------------------------------------------------

def verify(conn, roots):
    """What is wrong with the library's roots, [] when nothing: every rooted row converts back
    (its root is the library's, and this machine places it), and no table's row is a rooted row
    that is not what to_row writes. Reads only; counts, never paths. The doctor's check
    (tagpup.store.checks) and the adoption's own, before it commits."""
    problems = []
    unknown = unplaced = malformed = 0
    why_unplaced = ""
    for table, key, columns, _json in TABLES:
        if not _has(conn, table) or not columns:
            continue
        for column in columns:
            for (value,) in conn.execute("SELECT %s FROM %s WHERE %s IS NOT NULL" % (column, table, column)):
                if not value.startswith(paths.ROOT_MARK):
                    continue
                try:
                    paths.from_row(value, roots)
                except paths.UnknownRoot:
                    unknown += 1
                except paths.UnmappedRoot as problem:
                    unplaced += 1
                    why_unplaced = why_unplaced or str(problem)
                except paths.RootsError:
                    malformed += 1
    if unknown:
        problems.append("%d row(s) name a root the library does not have" % unknown)
    if unplaced:
        problems.append("%d row(s) name a root this machine does not place: %s" % (unplaced, why_unplaced))
    if malformed:
        problems.append("%d row(s) are not a path under a root" % malformed)
    return problems


# ---- The undo --------------------------------------------------------------------------------

#: The rows a change recorded that hold a path: what makes an undo of the adoption wait, since
#: they are the row form, which a library without the root cannot read.
_PATH_ROWS = ("(r.table_name = 'photos' AND r.column_name IN ('path', 'raw_metadata'))"
              " OR (r.table_name = 'suggestions' AND r.column_name = 'raw')"
              " OR (r.table_name = 'settings' AND r.row_key IN ('[\"library.roots\"]', '[\"library.ignored\"]'))")


def later_path_changes(conn, change_id):
    """(id, operation) of each change after `change_id`, applied and not undone, that wrote a
    path: a photo file, a photo's path or raw_metadata, a suggestion, a folder setting."""
    files = ("OR EXISTS (SELECT 1 FROM change_files f WHERE f.change_id = c.id)"
             if _has(conn, "change_files") else "")
    return conn.execute(
        "SELECT c.id, c.operation FROM changes c WHERE c.id > ? AND c.undone IS NULL"
        " AND c.status IN ('applied', 'derived_pending', 'planned') AND substr(c.operation, 1, ?) <> ? AND ("
        "EXISTS (SELECT 1 FROM change_rows r WHERE r.change_id = c.id AND (" + _PATH_ROWS + ")) " + files + ")"
        " ORDER BY c.id", (change_id, len(OPERATION), OPERATION)).fetchall()


def undo_refusals(conn, change_id):
    """Why the adoption `change_id` cannot be undone now, [] when it can."""
    reasons = ["change %d (%s), made after it, wrote a path: undo it first" % (other, operation)
               for other, operation in later_path_changes(conn, change_id)]
    row = conn.execute("SELECT summary FROM changes WHERE id = ?", (change_id,)).fetchone()
    summary = json.loads(row[0] or "{}") if row else {}
    name = summary.get("root")
    if not name:
        reasons.append("change %d does not say which root it adopted" % change_id)
    elif name not in {each for each, _address in store_roots._rows(conn)}:
        reasons.append("the library has no root %r any more" % name)
    return reasons


def undo_in(conn, change_id):
    """Undo the adoption `change_id` on `conn`, in the caller's transaction: every path under
    the root back to the native path this machine's map gives it, the root taken away.
    Refused (journal.Refusal) when a later change wrote a path, or this machine does not place
    the root. Returns the rows converted, by table. Verified before it returns: the row counts
    are what they were, and no row still names the root."""
    reasons = undo_refusals(conn, change_id)
    if reasons:
        raise journal.Refusal(reasons)
    summary = json.loads(conn.execute("SELECT summary FROM changes WHERE id = ?", (change_id,)).fetchone()[0] or "{}")
    name = summary["root"]
    store_roots._forget(conn)
    roots = store_roots.roots_for(conn)
    if name not in roots.locations:
        raise journal.Refusal([roots.what_to_add(name)])
    before = _counted(conn)
    report, _collisions, _outside, _settings = _tables_pass(conn, roots, name, True, "unadopt")
    left = _rooted_left(conn, name)
    if left:
        raise journal.Refusal(["%d row(s) still name the root %r after converting them back" % (left, name)])
    store_roots.delete(conn, name)
    after = _counted(conn)
    if after != before:
        raise journal.Refusal(["the tables' row counts changed: %r then %r" % (before, after)])
    return {table: counts["convert"] for table, counts in report.items()}


def _rooted_left(conn, name):
    """How many rows of the converted tables still name root `name`."""
    left = 0
    mark = paths.ROOT_MARK + name
    for table, _key, columns, _json in TABLES:
        if not _has(conn, table):
            continue
        for column in columns:
            for (value,) in conn.execute("SELECT %s FROM %s WHERE %s IS NOT NULL" % (column, table, column)):
                if value.lower() == mark or value.lower().startswith(mark + paths.ROW_SEP):
                    left += 1
    return left

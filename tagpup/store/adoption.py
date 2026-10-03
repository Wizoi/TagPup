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
already (it is adopted once), a root whose place nests with another root's (one root holds
each folder), a location that is not there, a location no row of the library lies under (a
wrong location would put every row outside the root), a library in which another process holds
the write lock, an unfinished change of photo files (it is settled first), a row that is not an
absolute native path or does not convert back, a row spelled by the share's address
(converting it would retarget it from the master to this machine's copy: it is named as its
own count and folder list), and two rows that would become one when at least one of them
converts. A row under no root keeps its native path and is reported, by folder, never guessed
at; two rows of one file that are both outside the root are reported as already duplicates
(`checks.one_file_two_rows`) and do not block.

Nested roots are the model's (tagpup.core.paths resolves them, the deeper winning) and not the
adoption's: the rows of the outer root that lie under an inner one would have to be moved, and an
undo could not put back what a later rename or index had changed. So the adoption refuses them,
in either direction, and `verify` still fails on any row held under one root that lies under
another's place. Every location the map lists for a root is equivalent: a row under any of them
converts, taking the first one's spelling (`respelled`).

The journal keeps what it was at the adoption: the old and new values of `change_rows` are not
rewritten. A change recorded before it holds native paths, and the journal converts them as it
reads them (tagpup.store.journal), so its undo writes the row form for a rooted photo.

Undone, the adoption converts the tables back and also the row-form values later changes
recorded (`change_rows`, `change_files`), in the same transaction, so the journal can still
undo those changes in a library that holds no root. Only the values that hold a path by their
structure -- photos.path, the two folder settings, the SourceFile of raw_metadata, the path
fields of suggestions.raw -- and only when the value is a row of the undone root (`@name` or
`@name/...`) are converted; no text is searched. It is refused only for a later change whose
recorded path cannot be converted back, which it names.

The backup is a full copy taken while the transaction holds the write lock, so another process's
write waits for it: a marker beside the library says why (`tagpup.store.db.busy_note`), and the
dry run says how long to expect.

Every step is `_reached`, which the tests stop the process at: a crash anywhere before the commit
leaves the library exactly as it was, since it is one transaction.
"""
import json
import logging
import math
import os
import sqlite3
import time

from tagpup.core import machine, paths, validation
from tagpup.core.library import Library
from tagpup.store import db, derived, journal, schema
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: The journal's name for the change is OPERATION and the root's name: "roots adopt: pictures".
OPERATION = "roots adopt"

#: How long the adoption waits for another process's write lock before it refuses.
LOCK_WAIT_MS = 3000

#: How many rows of the photos table are read and written at once.
CHUNK = 2000

#: The speed a backup copy is assumed to run at before one has been timed on this machine.
DEFAULT_BACKUP_RATE = 100 * 1024 * 1024

#: Where the last backup's measured speed is kept, in the library's backups folder, one file for
#: each library: backup_rate.<library>.json.
RATE_FILE = "backup_rate"

#: A speed below this is not believed (a stalled copy, a bad file): the default is used.
MIN_BACKUP_RATE = 1024 * 1024

#: The steps of an adoption, in order; `_reached` is told of each.
STEPS = ("photos converted", "other tables converted", "settings converted", "root recorded",
         "folders rebuilt", "verified", "change recorded")

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
        self.rows = self.convert = self.rooted = self.outside = self.respelled = 0
        self.irreversible = self.json = self.share_spelled = 0
        self.collisions = self.duplicates = 0
        self.outside_dirs = []
        self.share_dirs = []

    def as_dict(self):
        return {"rows": self.rows, "convert": self.convert, "already": self.rooted, "outside": self.outside,
                "respelled": self.respelled, "irreversible": self.irreversible, "json": self.json,
                "share_spelled": self.share_spelled, "duplicates": self.duplicates}


def _round_trip(row, roots):
    """The native path `row` reads as, when it is what to_row writes for that path (so the
    conversion is reversible), else None."""
    try:
        back = paths.from_row(row, roots)
        return back if paths.to_row(back, roots) == row else None
    except paths.RootsError:
        return None


def _under_a_place(value, roots, name):
    """Is `value` at one of the places the map lists for root `name` -- not only spelled by its
    share address?"""
    return any(paths.same(value, place) or paths.is_under(value, place) for place in roots.locations.get(name, ()))


def _forward(value, roots, name, counts):
    """`value`, a path, as the library will hold it once root `name` is adopted: (the new
    value, whether it changed). A path under one of the root's places as its row; any other --
    already a row, under no root -- as it is. Counts what it found: a value that is not an
    absolute path here, or does not convert back, is `irreversible`; one spelled by the share's
    address is `share_spelled`; both are left as they are, and refuse the adoption."""
    if not value:
        return value, False
    if value.startswith(paths.ROOT_MARK):
        counts.rooted += 1
        return value, False
    if not paths.is_native_absolute(value):
        counts.irreversible += 1
        return value, False
    found = roots.locate(value)
    if found is None or found[0] != name:
        counts.outside += 1
        counts.outside_dirs.append(os.path.dirname(value))
        return value, False
    if not _under_a_place(value, roots, name):
        counts.share_spelled += 1
        counts.share_dirs.append(os.path.dirname(value))
        return value, False
    try:
        row = paths.to_row(value, roots)
    except paths.RootsError:
        counts.irreversible += 1
        return value, False
    back = _round_trip(row, roots)
    if back is None:
        counts.irreversible += 1
        return value, False
    if back != value:
        counts.respelled += 1
    counts.convert += 1
    return row, True


def _backward(value, roots, name, counts):
    """_forward the other way: a row of root `name` as the native path, any other value as it
    is."""
    if not store_roots._of_root(value, name):
        if value and value.startswith(paths.ROOT_MARK):
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


def _json_backward(text, roots, column, table, name):
    """JSON holding paths, put back: the SourceFile or the suggestion paths that are rows of root
    `name`, and nothing else of the text."""
    if table == "photos" and column == "raw_metadata":
        return store_roots.raw_to_native(text, roots, only=name)
    return store_roots.suggested_to_native(text, roots, only=name)


def _line_forward(folder, roots, name):
    """One folder of a folder setting, converted (or None when it is not this root's)."""
    if folder.startswith(paths.ROOT_MARK):
        return None
    found = roots.locate(folder)
    if found is None or found[0] != name or not _under_a_place(folder, roots, name):
        return None
    converted = paths.to_row(folder, roots)
    return converted if _round_trip(converted, roots) is not None else None


def _line_backward(folder, roots, name):
    if not store_roots._of_root(folder, name):
        return None
    return paths.from_row(folder, roots)


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


def _claim(seen, key, changed):
    """Note a row's final spelling: `seen` is {folded spelling: [rows, rows that changed]}."""
    held = seen.setdefault(key, [0, 0])
    held[0] += 1
    held[1] += 1 if changed else 0


def _clashes(seen, counts):
    """Rows that would become one: where at least one of them changed. Rows that already were
    one -- two rows of one file, neither converting -- are duplicates, counted apart."""
    for rows, changed in seen.values():
        if rows > 1:
            if changed:
                counts.collisions += rows - 1
            else:
                counts.duplicates += rows - 1


def _photos_pass(conn, roots, name, apply, direction, counts):
    """Convert (or just count) the photos' paths and raw_metadata, a chunk at a time by id."""
    seen = {}
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
            converted = None
            if raw:
                converted = (_json_forward(raw, roots, "raw_metadata", "photos") if direction == "adopt"
                             else _json_backward(raw, roots, "raw_metadata", "photos", name))
                if converted != raw:
                    counts.json += 1
                else:
                    converted = None
            _claim(seen, store_roots.path_order(new_path), changed)
            if converted is not None:
                both.append((new_path, converted, photo_id))
            elif changed:
                paths_only.append((new_path, photo_id))
        if apply:
            if paths_only:
                conn.executemany("UPDATE photos SET path = ? WHERE id = ?", paths_only)
            if both:
                conn.executemany("UPDATE photos SET path = ?, raw_metadata = ? WHERE id = ?", both)
    _clashes(seen, counts)
    return counts


def _simple_pass(conn, roots, name, apply, direction, table, key, columns, json_columns, counts):
    """Convert (or count) one of the smaller tables: every row, whole."""
    step = _forward if direction == "adopt" else _backward
    names = list(columns) + list(json_columns)
    if not names:
        return counts
    rows = conn.execute("SELECT %s, %s FROM %s" % (key, ", ".join(names), table)).fetchall()
    seen = {}
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
            if not text:
                continue
            converted = (_json_forward(text, roots, column, table) if direction == "adopt"
                         else _json_backward(text, roots, column, table, name))
            if converted != text:
                new[column] = converted
                counts.json += 1
        if table in ("added_folders", "damaged_files"):
            _claim(seen, store_roots.path_order(new.get("path", found["path"])), "path" in new)
        if apply and new:
            conn.execute("UPDATE %s SET %s WHERE %s = ?" % (
                table, ", ".join("%s = ?" % column for column in new), key), list(new.values()) + [row[0]])
    _clashes(seen, counts)
    return counts


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
            if folder:
                try:
                    converted = (_line_forward(folder, roots, name) if direction == "adopt"
                                 else _line_backward(folder, roots, name))
                except paths.RootsError:
                    converted = None
                if converted is not None:
                    line, changed = converted, True
            lines.append(line)
        if changed:
            rewritten.append(key)
            if apply:
                conn.execute("UPDATE settings SET value = ? WHERE key = ?",
                             (validation.FOLDER_SEPARATOR.join(lines), key))
    return rewritten


def _tables_pass(conn, roots, name, apply, direction):
    """Every table, converted (or counted): {"tables": {table: counts}, "settings": [keys],
    "collisions", "duplicates", "outside_dirs", "share_dirs"}."""
    tables, share = {}, []
    counts = _photos_pass(conn, roots, name, apply, direction, _Counts())
    tables["photos"] = counts.as_dict()
    outside = counts.outside_dirs
    collisions, duplicates = counts.collisions, counts.duplicates
    share += counts.share_dirs
    if apply:
        _reached("photos converted")
    for table, key, columns, json_columns in TABLES[1:]:
        if not _has(conn, table):
            continue
        counts = _simple_pass(conn, roots, name, apply, direction, table, key, columns, json_columns, _Counts())
        tables[table] = counts.as_dict()
        collisions += counts.collisions
        duplicates += counts.duplicates
        share += counts.share_dirs
    if apply:
        _reached("other tables converted")
    settings = _settings_pass(conn, roots, name, apply, direction) if _has(conn, "settings") else []
    if apply:
        _reached("settings converted")
    return {"tables": tables, "settings": settings, "collisions": collisions, "duplicates": duplicates,
            "outside_dirs": outside, "share_dirs": share}


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


def _places_of(address, locations):
    """The places a root is known by, as comparable absolute paths: its locations and, when it
    has one that is a path, its address."""
    found = [place for place in locations if place]
    if address and paths.is_native_absolute(address):
        found.append(address)
    return found


def nesting(conn, name, address, locations):
    """The names of the library's roots whose place nests with root `name`'s -- under it, over it,
    or the same, by location or by address. One root holds each folder."""
    held = dict(store_roots._rows(conn))
    if not held:
        return []
    try:
        placed = machine.roots_of(held).locations
    except paths.RootsError:
        placed = {}
    mine = _places_of(address, locations)
    nested = []
    for other, other_address in sorted(held.items()):
        theirs = _places_of(other_address, placed.get(other, ()))
        if any(paths.same(a, b) or paths.is_under(a, b) or paths.is_under(b, a) for a in mine for b in theirs):
            nested.append(other)
    return nested


def early_refusals(conn, name, locations, address=""):
    """What can be refused before a row is read: a root of that name already, a root whose
    place nests with another's, a location that is not there, a change of photo files not
    finished. [] when none: the backup is taken then."""
    reasons = []
    held = {each for each, _address in store_roots._rows(conn)}
    if name in held:
        reasons.append("the library already has a root named %r: a root is adopted once" % name)
    for other in nesting(conn, name, address, locations):
        if other != name:
            reasons.append("the new root %r and the library's root %r hold overlapping folders (the place of one is "
                           "under, over or the same as the other's): one root must hold each folder, since a row has "
                           "one root and an undo could not return rows moved from one to the other. Adopt a root "
                           "beside it, not inside or around it" % (name, other))
    if not any(os.path.isdir(place) for place in locations[:1]):
        reasons.append("the location %r is not a folder on this machine" % (locations[0] if locations else ""))
    unfinished = _unfinished(conn)
    if unfinished:
        reasons.append("%d change(s) of photo files are not finished (change %s): let the app settle them, "
                       "or undo them, first" % (len(unfinished), unfinished[0]))
    return reasons


def refusals(conn, name, locations, roots, report):
    """Why the root cannot be adopted, [] when it can: each a sentence naming a count, never a
    path."""
    reasons = early_refusals(conn, name, locations, roots.logical.get(name, ""))
    counts = report["tables"]
    photos = counts.get("photos", {})
    if (photos.get("rows") and not photos.get("convert") and not photos.get("irreversible")
            and not photos.get("share_spelled")):
        reasons.append("no row of the library lies under the location: all %d photo row(s) are outside it "
                       "(%d already rooted); a wrong location would put every row outside the root"
                       % (photos["rows"] - photos.get("already", 0), photos.get("already", 0)))
    irreversible = sum(each.get("irreversible", 0) for each in counts.values())
    if irreversible:
        reasons.append("%d row(s) are not an absolute path on this machine or do not convert back to the "
                       "same file under this root" % irreversible)
    shared = sum(each.get("share_spelled", 0) for each in counts.values())
    if shared:
        reasons.append("%d row(s) are spelled by the share's address (%s), not by a place this machine keeps the "
                       "root at: converting them would retarget them from the master, the share, to this "
                       "machine's copy at %s. Fix those rows' spelling first -- index them again from the copy, "
                       "or remove and add their folders -- or adopt a root whose location is the share itself"
                       % (shared, roots.logical.get(name) or "no address", locations[0] if locations else ""))
    if report["collisions"]:
        reasons.append("%d row(s) would become the same row as another once converted (two rows of one "
                       "file): merge them first (checks.one_file_two_rows)" % report["collisions"])
    return reasons


def _report(conn, roots, name, address, locations, apply):
    done = _tables_pass(conn, roots, name, apply, "adopt")
    return {"root": name, "address": address, "locations": list(locations), "tables": done["tables"],
            "settings": done["settings"], "collisions": done["collisions"], "duplicates": done["duplicates"],
            "outside": paths.outside_roots(done["outside_dirs"], roots), "outside_rows": len(done["outside_dirs"]),
            "share_spelled": {"rows": len(done["share_dirs"]),
                              "folders": paths.outside_roots(done["share_dirs"], paths.Roots())}}


def _clean(location):
    return paths.stored(location) if location else ""


# ---- The dry run -----------------------------------------------------------------------------

def rehearse(db_path, name, address, locations):
    """What adopting root `name` at `locations` -- the first is where this machine puts its
    paths, all are equivalent -- would do to the library at `db_path`, and why it would be
    refused: a dict {root, address, locations, tables: {table: counts}, settings, outside:
    [{group, count}], outside_rows, share_spelled: {rows, folders}, collisions, duplicates,
    backup: {bytes, seconds}, refused: [reasons]}. Reads only, on a connection that writes
    nothing; a library behind this version's schema is not migrated, and has no roots (its
    tables are counted as they are)."""
    name = paths.root_name(name)
    locations = [_clean(each) for each in locations]
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        roots = _roots_with(conn, name, address or "", locations)
        report = _report(conn, roots, name, address, locations, apply=False)
        report["refused"] = refusals(conn, name, locations, roots, report)
        report["backup"] = backup_estimate(db_path)
        return report
    finally:
        conn.close()


# ---- The adoption ----------------------------------------------------------------------------

def _rate_file(db_path):
    library = Library(db_path)
    return os.path.join(library.backups, "%s.%s.json" % (RATE_FILE, library.name))


def _library_bytes(db_path):
    return sum(os.path.getsize(db_path + suffix) for suffix in ("", "-wal") if os.path.exists(db_path + suffix))


def backup_estimate(db_path):
    """{"bytes", "seconds"}: how big the library is and how long a backup copy will take, by the
    speed the last one was measured at (kept beside the backups) or DEFAULT_BACKUP_RATE. The
    copy holds the write lock for all of it, so the apps are better stopped."""
    rate = DEFAULT_BACKUP_RATE
    try:
        with open(_rate_file(db_path), encoding="utf-8") as handle:
            kept = float(json.load(handle)["bytes_per_second"])
        if math.isfinite(kept) and kept >= MIN_BACKUP_RATE:
            rate = kept
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        pass
    size = _library_bytes(db_path)
    return {"bytes": size, "seconds": max(1, int(round(size / rate)))}


def backup(db_path):
    """A copy of the library as it stands, made now (tagpup.store.db.backup). Always a new one: a
    copy from minutes ago can predate rows the adoption rewrites. The speed it ran at is kept,
    for the next estimate."""
    started = time.monotonic()
    size = _library_bytes(db_path)
    kept = db.backup(db_path, "roots-adopt")
    elapsed = max(time.monotonic() - started, 0.001)
    rate = size / elapsed
    if math.isfinite(rate) and rate >= MIN_BACKUP_RATE:
        target = _rate_file(db_path)
        temporary = "%s.%d.tmp" % (target, os.getpid())
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"bytes_per_second": rate}, handle)
            os.replace(temporary, target)
        except OSError:
            try:
                os.remove(temporary)
            except OSError:
                pass
    return kept


def adopt(db_path, name, address, locations):
    """Adopt root `name` (the share's own `address`, kept as given) at `locations` -- where
    this machine keeps it, the first being where a path under it is put, every one a place a row
    may be under -- for the library at `db_path`: convert every path under it and record the
    change, in one transaction under the write lock. Returns what it did, as rehearse reports
    what it would. Refused (Refused), with nothing written, for every reason refusals() gives, and
    for another process holding the write lock, after a new backup of the library is taken
    (`backup`) -- under the write lock too, so the copy is the library as the adoption finds it
    and two adoptions at once do not both copy it; another process's write meanwhile is told why
    (`db.busy_note`). The caller has written the machine's map."""
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
                early = early_refusals(conn, name, locations, address or "")
                if early:
                    raise Refused(early)
                estimate = backup_estimate(db_path)
                db.mark_busy(db_path, "the library is being adopted by a root (roots adopt) and a backup copy "
                             "holds the write lock for the length of the copy, about %d s; try again after it"
                             % estimate["seconds"])
                try:
                    kept = backup(db_path)
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
                reasons = refusals(conn, name, locations, roots, report)
                if reasons:
                    raise Refused(reasons)
                store_roots.insert(conn, name, address or "")
                _reached("root recorded")
                # The folder tree holds the folders in the form the rows are in now (the paths of
                # the derived tables are path columns too): made again, in this transaction.
                report["derived"] = dict(zip(("folders", "photos_in_one"), derived.rebuild_folders(conn)))
                _reached("folders rebuilt")
                actual = _held_roots(conn)
                problems = verify(conn, actual)
                if any(derived.stale_folders(conn)):
                    problems.append("the folder tree is not what the converted paths give")
                if tuple(actual.locations.get(name, ())) != tuple(roots.locations.get(name, ())):
                    problems.append("the machine's map places the root at %r, not where its rows were converted by"
                                    % (list(actual.locations.get(name, ())),))
                after = _counted(conn)
                if after != before:
                    problems.append("the tables' row counts changed: %r then %r" % (before, after))
                if problems:
                    raise Refused(["verification failed, so nothing was kept: " + "; ".join(problems[:5])])
                _reached("verified")
                # Counts and names of settings, never a path or a file name: a summary is read
                # without reveal.
                summary = {"root": name, "converted": {table: counts["convert"] for table, counts
                                                       in report["tables"].items()},
                           "json": {table: counts["json"] for table, counts in report["tables"].items()
                                    if counts["json"]},
                           "settings": report["settings"], "outside_rows": report["outside_rows"],
                           "respelled": sum(counts["respelled"] for counts in report["tables"].values())}
                report["backup"] = {"file": kept}
                report["change"] = journal.record(conn, "%s: %s" % (OPERATION, name), [], summary)
                _reached("change recorded")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                db.clear_busy(db_path)
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
    (its root is the library's, and this machine places it), and none is held under one root
    though the file it names lies under another's place (a lookup under that root would miss it).
    Reads only; counts, never paths. The doctor's check (tagpup.store.checks) and the adoption's
    own, before it commits."""
    problems = []
    unknown = unplaced = malformed = shadowed = native = 0
    why_unplaced = ""
    for table, key, columns, _json in TABLES:
        if not _has(conn, table) or not columns:
            continue
        for column in columns:
            for (value,) in conn.execute("SELECT %s FROM %s WHERE %s IS NOT NULL" % (column, table, column)):
                if not value.startswith(paths.ROOT_MARK):
                    # A row that kept its native path where Roots.locate says it is a root's -- by one of
                    # its places or by its share's address (adopted before the address was given, or a
                    # write that raced the adoption): every lookup of that path is by the row form, and
                    # misses it. The same classifier as the adoption's own (_forward).
                    if paths.is_native_absolute(value) and roots.locate(value) is not None:
                        native += 1
                    continue
                try:
                    native_path = paths.from_row(value, roots)
                except paths.UnknownRoot:
                    unknown += 1
                    continue
                except paths.UnmappedRoot as problem:
                    unplaced += 1
                    why_unplaced = why_unplaced or str(problem)
                    continue
                except paths.RootsError:
                    malformed += 1
                    continue
                found = roots.locate(native_path)
                if found is not None and found[0] != value[1:].partition(paths.ROW_SEP)[0].lower():
                    shadowed += 1
    if unknown:
        problems.append("%d row(s) name a root the library does not have" % unknown)
    if unplaced:
        problems.append("%d row(s) name a root this machine does not place: %s" % (unplaced, why_unplaced))
    if malformed:
        problems.append("%d row(s) are not a path under a root" % malformed)
    if native:
        problems.append("%d row(s) kept their native path where this library's roots resolve it to a root (its place "
                        "or its share's address): a lookup by the row form misses them" % native)
    if shadowed:
        problems.append("%d row(s) are held under one root but lie under another root's place, so a lookup under "
                        "that root misses them" % shadowed)
    return problems


# ---- The undo --------------------------------------------------------------------------------

#: The values of a change's recorded rows that hold a path by their structure, and nothing else:
#: what an undo of the adoption converts back (alias `r` is change_rows).
_PATH_ROWS = ("(r.table_name = 'photos' AND r.column_name IN ('path', 'raw_metadata'))"
              " OR (r.table_name = 'suggestions' AND r.column_name = 'raw')"
              " OR (r.table_name = 'settings' AND r.column_name = 'value'"
              " AND r.row_key IN ('[\"library.roots\"]', '[\"library.ignored\"]'))")


def undo_refusals(conn, change_id):
    """Why the adoption `change_id` cannot be undone now, [] when it can: it names no root, or
    the library has none of that name any more. Later changes do not stop it: their recorded
    values are converted with the tables."""
    reasons = []
    row = conn.execute("SELECT summary FROM changes WHERE id = ?", (change_id,)).fetchone()
    summary = json.loads(row[0] or "{}") if row else {}
    name = summary.get("root")
    if not name:
        reasons.append("change %d does not say which root it adopted" % change_id)
    elif name not in {each for each, _address in store_roots._rows(conn)}:
        reasons.append("the library has no root %r any more" % name)
    return reasons


def back_value(table, column, value, roots, name):
    """A value a change recorded, with the paths in it that are rows of root `name` made native --
    by what the value is: photos.path itself, the folder lines of the folder settings, the
    SourceFile of raw_metadata, the path fields of suggestions.raw -- and nothing else of it: not a
    text searched for an "@". RootsError when a row of the root in it cannot be converted."""
    if not isinstance(value, str):
        return value
    if table == "photos" and column == "path":
        return paths.from_row(value, roots) if store_roots._of_root(value, name) else value
    if table == "settings":
        lines = []
        for line in value.split(validation.FOLDER_SEPARATOR):
            found = _line_backward(validation.trim(line), roots, name) if line.strip() else None
            lines.append(line if found is None else found)
        return validation.FOLDER_SEPARATOR.join(lines)
    return _json_backward(value, roots, column, table, name)


def _journal_pass(conn, roots, name):
    """Convert back the paths later changes recorded in `change_rows`: (values converted, the ids
    of the changes holding one that cannot be)."""
    bad, converted = set(), 0
    rows = conn.execute("SELECT r.id, r.change_id, r.table_name, r.column_name, r.old, r.new FROM change_rows r"
                        " WHERE " + _PATH_ROWS).fetchall()
    for row_id, change_id, table, column, old, new in rows:
        values, changed = [old, new], False
        for number, value in enumerate(values):
            try:
                moved = back_value(table, column, value, roots, name)
            except paths.RootsError:
                bad.add(change_id)
                continue
            if moved != value:
                values[number], changed = moved, True
                converted += 1
        if changed:
            conn.execute("UPDATE change_rows SET old = ?, new = ? WHERE id = ?", (values[0], values[1], row_id))
    return converted, bad


def undo_in(conn, change_id):
    """Undo the adoption `change_id` on `conn`, in the caller's transaction: every path under
    the root back to the native path this machine's map gives it, the paths later changes
    recorded converted with them, the root taken away. Refused (journal.Refusal) when this
    machine does not place the root, or a later change recorded a path of the root that cannot
    be converted back (it is named). Returns the rows converted, by table. Verified before it
    returns: the row counts are what they were, and no row still names the root."""
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
    done = _tables_pass(conn, roots, name, True, "unadopt")
    recorded, bad = _journal_pass(conn, roots, name)
    if bad:
        raise journal.Refusal(["change(s) %s recorded a path of the root %r that cannot be converted back, so the "
                               "journal could not read them in a library without it: undo them first"
                               % (", ".join(str(each) for each in sorted(bad)), name)])
    left = _rooted_left(conn, name)
    if left:
        raise journal.Refusal(["%d row(s) still name the root %r after converting them back" % (left, name)])
    store_roots.delete(conn, name)
    derived.rebuild_folders(conn)   # the folder tree in the native form the rows are in again
    if any(derived.stale_folders(conn)):
        raise journal.Refusal(["the folder tree is not what the paths converted back give"])
    after = _counted(conn)
    if after != before:
        raise journal.Refusal(["the tables' row counts changed: %r then %r" % (before, after)])
    converted = {table: counts["convert"] for table, counts in done["tables"].items()}
    converted["recorded values"] = recorded
    return converted


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

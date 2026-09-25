"""The journal: each bulk edit of a library recorded as what it found and what it left.

Every bulk operation copied the whole library before it wrote (db.backup: 1.4 GB for
photo_index, five kept), and could be undone only by restoring that copy over
everything done since. Here a change is recorded instead, in the library, one row of
`change_rows` per changed column (migration 9; docs/ARCHITECTURE.md, phase 7.5), with
SQLite's session extension as the model for its semantics -- old and new values,
inverted to undo, applied only where the rows are what the change expects. The
extension itself is reachable from Python only through APSW, a second owner of the
database beside tagpup.store.db, so its semantics are copied, not the library.

A change is a list of `Edit`s: insert, update or delete one row of a table in KEYS,
each with what the plan read of the row (`expect`). Then:

- **apply**: under the write lock, in one transaction, read each row, refuse the whole
  change if any is not what the plan read, write, record, mark it `derived_pending`,
  commit; then rebuild what the change touched of the derived data (each touched
  photo's people and dates) and mark it `applied`. A crash between the two leaves it
  `derived_pending`, and `settle` finishes it the next time a process opens the library.
  An edit marked `skippable` whose row is not what the plan read is left out of the
  change instead, and listed (`Applied.skipped`): only a refresh, whose rows are
  independent, uses it.
- **undo**: the same with old and new swapped, refused when a row is not what the
  change left, when a newer change touched the same rows (named), or when the schema
  has moved on since.
- **rehearse**: the change and its undo inside a transaction that is rolled back,
  saying whether the undo restored every row exactly. Nothing is written.

Either way a write is refused, before anything is written, when it would leave a row
naming a row that is not there -- a face put back on a photo deleted since, a node under
a parent deleted since -- or break a UNIQUE constraint (`_blocked`): the journal writes
on connections with foreign keys off, so SQLite would not say. Both are read from the
schema, as the cascade guard reads it. An IntegrityError SQLite raises all the same
becomes a Refusal, the transaction rolled back.

A delete takes more than its row: triggers delete a face's crop and a photo's vectors,
people and suggestions, and ON DELETE CASCADE, on a connection with foreign keys on,
a node's children and a photo's faces. The journal writes on connections with foreign
keys off and makes each of those explicit (CASCADES): recorded as deletes of their own,
rebuilt, or forbidden. `tests/test_journal_keys_and_cascades.py` holds every cascade
of a new library to one of the three, and every journaled table to keys SQLite never
hands out again, so an undo that puts a row back cannot meet a newer one.

Refusals name tables, row keys and columns, never values: the library is photographs of
real people, many of them minors.
"""
import collections
import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tagpup.store import db, people, schema
from tagpup.store import photos as store_photos

logger = logging.getLogger(__name__)

#: The tables a change may write, by the columns that key a row. Each key is one SQLite
#: never gives out again: an AUTOINCREMENT id, or the id of a row that has one.
KEYS = {
    "photos": ("id",),
    "faces": ("id",),
    "tag_taxonomy": ("id",),
    "face_crops": ("face_id",),
    "embeddings": ("photo_id", "model"),
    "suggestions": ("photo_id",),
}

#: Derived tables: never journaled, rebuilt from what a change touched.
DERIVED = ("photo_people",)

#: Derived columns of journaled tables, rebuilt from the row's other columns after each
#: write (a photo's dates, from its metadata and path: store.photos.date_photos). An
#: inserted photo is recorded as written, before they are filled in, so an undo does not
#: hold a row to them: it would find every inserted photo changed since.
DERIVED_COLUMNS = {"photos": ("taken", "year")}

RECORDED, REBUILT, FORBIDDEN = "recorded", "rebuilt", "forbidden"

#: What deleting a row of one table takes from another -- (parent, child): (the child's
#: column naming the parent's key, what the journal does). Recorded: deleted first, as
#: rows of the change, so an undo puts them back byte for byte; a deleted face's crop and
#: a deleted photo's vectors cannot be made again without the file and the models.
#: Rebuilt: derived, made again from the rows. Forbidden: a change deleting the parent
#: is refused unless it deletes the children too -- a node with nodes under it.
CASCADES = {
    ("faces", "face_crops"): ("face_id", RECORDED),
    ("photos", "faces"): ("photo_id", RECORDED),
    ("photos", "embeddings"): ("photo_id", RECORDED),
    ("photos", "suggestions"): ("photo_id", RECORDED),
    ("photos", "photo_people"): ("photo_id", REBUILT),
    ("tag_taxonomy", "tag_taxonomy"): ("parent_id", FORBIDDEN),
}

#: How long a change stays undoable. Pruning then deletes its values and keeps its
#: summary, and the change becomes `pruned`. An undo is for a mistake noticed in use,
#: and three months is long enough to notice one; a deleted face's crop and vector are
#: 8 KB a face, which is what a change holds for longer (docs/findings.md).
RETENTION_DAYS = 90

#: The steps of an apply and an undo, in order; `_reached` is told of each.
STEPS = ("forward written", "forward committed", "undo written", "undo committed")

#: How many keys one query asks for.
CHUNK = 500

#: How many rows a rehearsal lists that an undo did not restore.
SHOWN = 20

TIME = "%Y-%m-%d %H:%M:%S"


@dataclass
class Edit:
    """One row a change writes. `expect` is what the plan read of it, by column; `values`
    are the columns an update writes, or an inserted row's. Made by insert(), update()
    and delete()."""
    action: str
    table: str
    key: Optional[tuple] = None
    expect: Dict[str, Any] = field(default_factory=dict)
    values: Dict[str, Any] = field(default_factory=dict)
    #: What the operation counts the edit as, if it counts by kind.
    kind: str = ""
    #: When the row is not what the plan read, leave this edit out of the change and list
    #: it (Applied.skipped) instead of refusing the whole change. For an operation whose
    #: rows do not depend on each other -- a refresh, where one row saved in the app while
    #: the files were read must not throw away the rest of the read.
    skippable: bool = False


def insert(table, values, kind=""):
    return Edit("insert", table, None, {}, dict(values), kind)


def update(table, key, expect, values, kind="", skippable=False):
    return Edit("update", table, tuple(key), dict(expect), dict(values), kind, skippable)


def delete(table, key, expect, kind="", skippable=False):
    return Edit("delete", table, tuple(key), dict(expect), {}, kind, skippable)


@dataclass
class RowChange:
    """A row as a change found and left it. `old` is None for an insert, `new` for a
    delete; an update holds only the columns it changed."""
    action: str
    table: str
    key: Optional[tuple]
    old: Optional[Dict[str, Any]]
    new: Optional[Dict[str, Any]]
    kind: str = ""
    #: An edit's own row, not a cascade the journal added.
    top: bool = True
    children: List["RowChange"] = field(default_factory=list)


class Refusal(Exception):
    """A change, or an undo, refused before anything was written. `reasons` names each
    row, newer change or schema that stands in the way."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        text = "; ".join(self.reasons[:10])
        if len(self.reasons) > 10:
            text += "; and %d more" % (len(self.reasons) - 10)
        super().__init__(text)


@dataclass
class Applied:
    """What an apply wrote. `change_id` is None when nothing needed writing."""
    change_id: Optional[int] = None
    #: The edits that changed a row.
    changed: int = 0
    #: Every row written, cascades included.
    rows: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    #: The derived data is rebuilt. False leaves the change `derived_pending`, finished
    #: the next time the library is opened.
    settled: bool = True
    #: (row, why) of each skippable edit left out: its row was not what the plan read.
    skipped: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class Undone:
    change_id: int
    rows: int
    settled: bool


@dataclass
class Rehearsal:
    """A change applied and undone inside a transaction rolled back."""
    #: Why applying it would be refused now; nothing else is then filled in.
    refused: Optional[str] = None
    rows: int = 0
    changed: int = 0
    #: The undo restored every row the change touched, byte for byte.
    exact: bool = False
    #: "<table> <key>: <columns>" of the rows it did not.
    differences: List[str] = field(default_factory=list)
    #: The touched photos' people came back as the rule makes them from the rows as
    #: they were.
    derived_exact: bool = False
    #: Photos whose people were not what the rule makes them before the change: a writer
    #: that did not rebuild. The change rebuilds them, and its undo does not unbuild them.
    derived_stale: int = 0
    #: [row, why] of each skippable edit applying would leave out.
    skipped: List[List[str]] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def _reached(step):
    """A step of an apply or an undo is done (STEPS). Nothing happens here; the tests stop
    the process at each step by patching it."""


def _now():
    return time.strftime(TIME)


def _same(a, b):
    """Equal as SQLite stores them: 1 is not 1.0, nor b'1' '1'."""
    return type(a) is type(b) and a == b


def _key_text(key):
    return json.dumps(list(key))


def _named(table, key):
    return "%s %s" % (table, "/".join(str(k) for k in key))


def _columns(conn, table):
    return [row[1] for row in conn.execute('PRAGMA table_info("%s")' % table)]


def _where(table):
    return " AND ".join('"%s" = ?' % column for column in KEYS[table])


def _read(conn, table, key, columns):
    row = conn.execute('SELECT %s FROM "%s" WHERE %s' % (", ".join('"%s"' % c for c in columns), table,
                                                         _where(table)), tuple(key)).fetchone()
    return None if row is None else dict(zip(columns, row))


def has_journal(conn):
    """Has the library on `conn` the journal's tables (migration 9)?"""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'changes'").fetchone() is not None


# ---- Reading a change against the rows --------------------------------------------------

def _resolve(conn, edits):
    """(the rows `edits` would write, cascades first, as they stand; (row, why) of each
    skippable edit left out). Refusal when any other is not what the plan read, or when
    writing them would leave a row naming one that is not there or break a UNIQUE
    constraint (_blocked). Reads only."""
    columns = {}

    def cols(table):
        if table not in columns:
            columns[table] = _columns(conn, table)
        return columns[table]

    refusals, top, seen, skipped = [], [], set(), []
    deletes = collections.defaultdict(dict)
    for edit in edits:
        if edit.table not in KEYS:
            raise ValueError("%s is not a table a change writes%s" % (
                edit.table, ": it is derived, and rebuilt" if edit.table in DERIVED else ""))
        if edit.action not in ("insert", "update", "delete"):
            raise ValueError("an edit inserts, updates or deletes, not %r" % (edit.action,))
        unknown = (set(edit.expect) | set(edit.values)) - set(cols(edit.table))
        if unknown:
            raise ValueError("%s has no column %s" % (edit.table, ", ".join(sorted(unknown))))
        if edit.action == "insert":
            key = None
            if all(column in edit.values for column in KEYS[edit.table]):
                key = tuple(edit.values[column] for column in KEYS[edit.table])
                if (edit.table, key) in seen:
                    raise ValueError("%s is written twice in one change" % _named(edit.table, key))
                seen.add((edit.table, key))
                if _read(conn, edit.table, key, KEYS[edit.table]) is not None:
                    refusals.append("%s is already there" % _named(edit.table, key))
                    continue
            elif len(KEYS[edit.table]) > 1:
                raise ValueError("a row of %s is inserted with its key" % edit.table)
            top.append(RowChange("insert", edit.table, key, None, dict(edit.values), edit.kind))
            continue

        key = tuple(edit.key)
        if (edit.table, key) in seen:
            raise ValueError("%s is written twice in one change" % _named(edit.table, key))
        seen.add((edit.table, key))
        wanted = cols(edit.table) if edit.action == "delete" else list(dict.fromkeys(list(edit.expect)
                                                                                     + list(edit.values)))
        row = _read(conn, edit.table, key, wanted)
        if row is None:
            if edit.skippable:
                skipped.append((_named(edit.table, key), "gone since the plan read it"))
            else:
                refusals.append("%s is gone" % _named(edit.table, key))
            continue
        differs = [column for column, value in edit.expect.items() if not _same(row[column], value)]
        if differs:
            if edit.skippable:
                skipped.append((_named(edit.table, key), "not what the plan read: %s changed" % ", ".join(differs)))
            else:
                refusals.append("%s is not what the plan read: %s changed"
                                % (_named(edit.table, key), ", ".join(differs)))
            continue
        if edit.action == "update":
            changed = {column: value for column, value in edit.values.items() if not _same(row[column], value)}
            if changed:
                top.append(RowChange("update", edit.table, key, {c: row[c] for c in changed}, changed, edit.kind))
            continue
        change = RowChange("delete", edit.table, key, row, None, edit.kind)
        top.append(change)
        deletes[edit.table][key] = change

    # What the deletes take with them, a level at a time: photos, then their faces, then
    # the faces' crops. One query per table and chunk, not one per row.
    planned = {(table, key) for table, rows in deletes.items() for key in rows}
    level = deletes
    while level:
        found = collections.defaultdict(dict)
        for (parent, child), (column, how) in CASCADES.items():
            parents = level.get(parent)
            if not parents or how == REBUILT:
                continue
            by_value = {change.old[KEYS[parent][0]]: change for change in parents.values()}
            child_columns = cols(child) if how == RECORDED else list(dict.fromkeys(KEYS[child] + (column,)))
            values = list(by_value)
            for start in range(0, len(values), CHUNK):
                chunk = values[start:start + CHUNK]
                for row in conn.execute('SELECT %s FROM "%s" WHERE "%s" IN (%s)' % (
                        ", ".join('"%s"' % c for c in child_columns), child, column, ",".join("?" * len(chunk))),
                        chunk).fetchall():
                    row = dict(zip(child_columns, row))
                    child_key = tuple(row[c] for c in KEYS[child])
                    if (child, child_key) in planned:
                        continue
                    owner = by_value[row[column]]
                    if how == FORBIDDEN:
                        refusals.append("%s has %s under it, which the change does not delete"
                                        % (_named(parent, owner.key), _named(child, child_key)))
                        continue
                    cascade = RowChange("delete", child, child_key, row, None, top=False)
                    owner.children.append(cascade)
                    planned.add((child, child_key))
                    found[child][child_key] = cascade
        level = found
    if refusals:
        raise Refusal(refusals)

    ordered = []

    def emit(change):
        for child in change.children:
            emit(child)
        ordered.append(change)

    for change in top:
        emit(change)
    refusals = _blocked(conn, ordered)
    if refusals:
        raise Refusal(refusals)
    return ordered, skipped


# ---- What the schema asks of a row ---------------------------------------------------------

def _primary(conn, table):
    return tuple(row[1] for row in sorted(conn.execute('PRAGMA table_info("%s")' % table),
                                          key=lambda row: row[5]) if row[5])


def _foreign_keys(conn, table):
    """[(columns, parent table, parent columns)] of every foreign key of `table`, from the
    schema."""
    found = {}
    for row in conn.execute('PRAGMA foreign_key_list("%s")' % table):
        number, _seq, parent, column, parent_column = row[:5]
        found.setdefault(number, (parent, [], []))
        found[number][1].append(column)
        found[number][2].append(parent_column)
    keys = []
    for parent, columns, parent_columns in found.values():
        if any(c is None for c in parent_columns):
            parent_columns = _primary(conn, parent)   # REFERENCES t: its primary key
        keys.append((tuple(columns), parent, tuple(parent_columns)))
    return keys


def _uniques(conn, table):
    """[(columns, collations)] of every UNIQUE constraint and unique index of `table` but
    its key, which the journal checks itself. A partial index, or one on an expression, is
    left to the backstop: SQLite raises, and the write becomes a Refusal."""
    found = []
    for row in conn.execute('PRAGMA index_list("%s")' % table):
        name, unique, origin, partial = row[1], row[2], row[3], row[4]
        if not unique or origin == "pk" or partial:
            continue
        info = [r for r in conn.execute('PRAGMA index_xinfo("%s")' % name) if r[5]]
        if any(r[2] is None for r in info):
            continue
        found.append((tuple(r[2] for r in info), tuple(r[4] or "BINARY" for r in info)))
    return found


def _who(change):
    return _named(change.table, change.key) if change.key is not None else "a new row of %s" % change.table


def _existing(conn, table, columns, wanted):
    """{values: [key of each row of `table` holding them in `columns`]} for each tuple in
    `wanted`. One query per chunk when the columns are one, as a foreign key's are."""
    keys = KEYS.get(table) or _primary(conn, table)
    selected = ", ".join('"%s"' % c for c in keys + tuple(columns))
    found = collections.defaultdict(list)
    wanted = list(wanted)
    if len(columns) == 1:
        for start in range(0, len(wanted), CHUNK):
            chunk = [values[0] for values in wanted[start:start + CHUNK]]
            for row in conn.execute('SELECT %s FROM "%s" WHERE "%s" IN (%s)' % (
                    selected, table, columns[0], ",".join("?" * len(chunk))), chunk):
                found[tuple(row[len(keys):])].append(tuple(row[:len(keys)]))
        return found
    where = " AND ".join('"%s" = ?' % c for c in columns)
    for values in wanted:
        for row in conn.execute('SELECT %s FROM "%s" WHERE %s' % (selected, table, where), values):
            found[values].append(tuple(row[:len(keys)]))
    return found


def _blocked(conn, writes):
    """Why writing `writes`, in order, would leave a row naming a row that is not there,
    or break a UNIQUE constraint: [] when it would not. Every foreign key and UNIQUE of the
    tables written, read from the schema -- faces.photo_id, tag_taxonomy.parent_id,
    face_crops.face_id, tag_taxonomy.tag, photos.path... A parent the same writes insert
    counts as there, one they delete as gone; a value a row they delete or move held is
    free. Names rows and columns, never values. Reads only."""
    constraints = {}

    def of(table):
        if table not in constraints:
            constraints[table] = (_foreign_keys(conn, table), _uniques(conn, table))
        return constraints[table]

    def values_of(change, columns):
        """The row's `columns` once `change` is written; None when one is NULL, which
        neither a foreign key nor a UNIQUE constraint holds to anything."""
        if change.action == "insert" or all(c in change.new for c in columns):
            values = tuple(change.new.get(c) for c in columns)
        else:
            row = _read(conn, change.table, change.key, list(columns)) or {}
            values = tuple(change.new[c] if c in change.new else row.get(c) for c in columns)
        return None if any(v is None for v in values) else values

    written = [change for change in writes if change.action != "delete"]
    deleted = {(change.table, change.key) for change in writes if change.action == "delete"}
    reasons = []

    # Foreign keys: each value a written row names, looked up once per parent and chunk.
    needed = collections.defaultdict(lambda: collections.defaultdict(list))
    for change in written:
        for columns, parent, parent_columns in of(change.table)[0]:
            if change.action == "update" and not set(columns) & set(change.new):
                continue
            values = values_of(change, columns)
            if values is not None:
                needed[(parent, parent_columns)][values].append((change, columns))
    for (parent, parent_columns), by_values in needed.items():
        inserted = {tuple(c.new.get(p) for p in parent_columns) for c in written
                    if c.action == "insert" and c.table == parent}
        missing = [values for values in by_values if values not in inserted]
        there = _existing(conn, parent, parent_columns, missing) if missing else {}
        by_key = parent_columns == (KEYS.get(parent) or _primary(conn, parent))
        for values in missing:
            if any((parent, key) not in deleted for key in there.get(values, ())):
                continue
            for change, columns in by_values[values]:
                reasons.append("%s: %s names %s, which is not there" % (
                    _who(change), ", ".join(columns), _named(parent, values) if by_key else "a row of %s" % parent))

    # UNIQUE: a value another row holds, or another written row claims.
    claimed = {}
    for change in written:
        for columns, collations in of(change.table)[1]:
            if change.action == "update" and not set(columns) & set(change.new):
                continue
            values = values_of(change, columns)
            if values is None:
                continue
            slot = (change.table, columns, values)
            if slot in claimed:
                reasons.append("%s: %s is the same as %s's, which the change also writes"
                               % (_who(change), ", ".join(columns), _who(claimed[slot])))
                continue
            claimed[slot] = change
            moved = {c.key for c in written if c.action == "update" and c.table == change.table
                     and set(columns) & set(c.new)}
            where = " AND ".join('"%s" = ? COLLATE %s' % (c, collation) for c, collation in zip(columns, collations))
            for row in conn.execute('SELECT %s FROM "%s" WHERE %s' % (
                    ", ".join('"%s"' % c for c in KEYS[change.table]), change.table, where), values):
                key = tuple(row)
                if key == change.key or (change.table, key) in deleted or key in moved:
                    continue
                reasons.append("%s: %s is taken by %s" % (_who(change), ", ".join(columns),
                                                          _named(change.table, key)))
    return reasons


def _integrity(error):
    """A Refusal for an IntegrityError SQLite raised all the same: its message names the
    constraint, the table and the column, never a value."""
    return Refusal(["the database refused a row: %s" % error])


def _write(conn, changes):
    """Write `changes` in order. An insert without its key learns it, and every inserted
    row is read back whole, as stored."""
    for change in changes:
        table = change.table
        if change.action == "insert":
            columns = list(change.new)
            cursor = conn.execute('INSERT INTO "%s" (%s) VALUES (%s)' % (
                table, ", ".join('"%s"' % c for c in columns), ",".join("?" * len(columns))),
                [change.new[c] for c in columns])
            if change.key is None:
                change.key = (cursor.lastrowid,)
            change.new = _read(conn, table, change.key, _columns(conn, table))
        elif change.action == "update":
            columns = list(change.new)
            conn.execute('UPDATE "%s" SET %s WHERE %s' % (table, ", ".join('"%s" = ?' % c for c in columns),
                                                          _where(table)),
                         [change.new[c] for c in columns] + list(change.key))
        else:
            conn.execute('DELETE FROM "%s" WHERE %s' % (table, _where(table)), tuple(change.key))


def _record(conn, change_id, changes):
    rows = []
    for change in changes:
        key = _key_text(change.key)
        if change.action == "update":
            rows += [(change_id, "update", change.table, key, c, change.old[c], change.new[c]) for c in change.new]
        elif change.action == "delete":
            rows += [(change_id, "delete", change.table, key, c, v, None) for c, v in change.old.items()]
        else:
            rows += [(change_id, "insert", change.table, key, c, None, v) for c, v in change.new.items()]
    conn.executemany("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _load(conn, change_id):
    """The rows change `change_id` wrote, in the order it wrote them."""
    changes, index = [], {}
    for action, table, key_text, column, old, new in conn.execute(
            "SELECT action, table_name, row_key, column_name, old, new FROM change_rows"
            " WHERE change_id = ? ORDER BY id", (change_id,)):
        slot = (action, table, key_text)
        change = index.get(slot)
        if change is None:
            change = RowChange(action, table, tuple(json.loads(key_text)),
                               None if action == "insert" else {}, None if action == "delete" else {})
            index[slot] = change
            changes.append(change)
        if change.old is not None:
            change.old[column] = old
        if change.new is not None:
            change.new[column] = new
    return changes


def _inverse(change):
    action = {"insert": "delete", "delete": "insert", "update": "update"}[change.action]
    return RowChange(action, change.table, change.key,
                     None if change.new is None else dict(change.new),
                     None if change.old is None else dict(change.old), change.kind, change.top)


def _again(change):
    """A copy of `change` to write once more: _write fills an insert's row in."""
    return RowChange(change.action, change.table, change.key,
                     None if change.old is None else dict(change.old),
                     None if change.new is None else dict(change.new), change.kind, change.top)


def _not_as_left(conn, change_id, changes):
    """Why the rows are not what change `change_id` left them: [] when they are."""
    reasons = []
    for change in changes:
        if change.action == "delete":
            if _read(conn, change.table, change.key, KEYS[change.table]) is not None:
                reasons.append("%s is there again" % _named(change.table, change.key))
            continue
        row = _read(conn, change.table, change.key, list(change.new))
        if row is None:
            reasons.append("%s is gone" % _named(change.table, change.key))
            continue
        derived = DERIVED_COLUMNS.get(change.table, ())
        differs = [c for c in change.new if c not in derived and not _same(row[c], change.new[c])]
        if differs:
            reasons.append("%s is not what change %d left: %s changed"
                           % (_named(change.table, change.key), change_id, ", ".join(differs)))
            continue
        if change.action == "insert":
            # Undoing an insert deletes the row: not while anything since depends on it.
            for (parent, child), (column, how) in CASCADES.items():
                if parent != change.table or how == REBUILT:
                    continue
                if conn.execute('SELECT 1 FROM "%s" WHERE "%s" = ? LIMIT 1' % (child, column),
                                (change.new[KEYS[parent][0]],)).fetchone():
                    reasons.append("%s has rows in %s made since" % (_named(change.table, change.key), child))
    return reasons


def _newer_overlapping(conn, change_id):
    """(id, operation) of each change after `change_id`, applied and not undone, that
    touched a row it touched."""
    return conn.execute(
        "SELECT DISTINCT c.id, c.operation FROM"
        " (SELECT DISTINCT table_name, row_key FROM change_rows WHERE change_id = ?) mine"
        " JOIN change_rows theirs ON theirs.table_name = mine.table_name AND theirs.row_key = mine.row_key"
        " JOIN changes c ON c.id = theirs.change_id"
        " WHERE theirs.change_id > ? AND c.undone IS NULL AND c.status IN ('applied', 'derived_pending')"
        " ORDER BY c.id", (change_id, change_id)).fetchall()


# ---- Derived data ------------------------------------------------------------------------

def _touched(conn, changes):
    """(the photos whose people come from rows the change touched, the photos whose date
    it may have moved, the tree nodes it touched as (tag, name) before and after)."""
    photo_ids, dated, nodes = set(), set(), []
    for change in changes:
        values = [d for d in (change.old, change.new) if d]
        columns = set().union(*values) if values else set()
        # An insert not yet written has no key and nothing derived from it: its photo
        # does not exist yet. Once the forward writes it, it has both.
        if change.table == "photos":
            if change.key is None:
                continue
            photo_ids.add(change.key[0])
            if change.action != "update" or columns & {"path", "raw_metadata"}:
                dated.add(change.key[0])
        elif change.table == "faces":
            found = {d["photo_id"] for d in values if "photo_id" in d}
            if not found and change.key is not None:
                row = _read(conn, "faces", change.key, ["photo_id"])
                found = {row["photo_id"]} if row else set()
            photo_ids |= found
        elif change.table == "tag_taxonomy":
            nodes += [(d.get("tag"), d.get("name")) for d in values]
            if not {"tag", "name"} <= columns and change.key is not None:
                row = _read(conn, "tag_taxonomy", change.key, ["tag", "name"])
                if row:
                    nodes.append((row["tag"], row["name"]))
    return photo_ids, dated, nodes


def _derive(conn, changes):
    """Rebuild what `changes` touched of the derived data: the people of each photo whose
    keywords or faces changed or whose keywords a changed node names, and the dates of
    each photo whose metadata or path changed. The generations move by their triggers.
    Returns how many photos' people changed."""
    photo_ids, dated, nodes = _touched(conn, changes)
    changed = 0
    if nodes:
        changed += people.follow_nodes(conn, nodes)
    if photo_ids:
        changed += people.rebuild(conn, sorted(photo_ids))
    if dated:
        store_photos.date_photos(conn, sorted(dated))
    return changed


def _settle_change(conn, change_id, changes):
    """Rebuild the derived data of a change left derived_pending, and mark it applied or
    undone. False, logged, when the rebuild fails: it stays derived_pending."""
    try:
        db.begin(conn, immediate=True)
        try:
            _derive(conn, changes)
            conn.execute("UPDATE changes SET status = CASE WHEN undone IS NULL THEN 'applied' ELSE 'undone' END"
                         " WHERE id = ? AND status = 'derived_pending'", (change_id,))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    except Exception:
        logger.exception("Change %d: rebuilding what it touched failed; it is finished the next time the"
                         " library is opened", change_id)
        return False
    return True


# ---- Apply, undo, rehearse -----------------------------------------------------------

def _forward(conn, operation, changes, summary):
    """Write the resolved `changes` and record them as a change, derived_pending. Returns
    its id. The caller holds the transaction."""
    _write(conn, changes)
    rows = collections.Counter(change.table for change in changes)
    recorded = dict(summary or {}, rows=dict(rows))
    now = _now()
    change_id = conn.execute(
        "INSERT INTO changes (operation, status, schema_version, created, applied, summary)"
        " VALUES (?, 'derived_pending', ?, ?, ?, ?)",
        (operation, schema.version(conn), now, now, json.dumps(recorded, sort_keys=True))).lastrowid
    _record(conn, change_id, changes)
    return change_id


def apply(db_path, operation, edits, summary=None):
    """Apply `edits` to the library at `db_path` as one change named `operation`, with
    `summary` (counts, never names) kept with it. Refusal, with nothing written, when a
    row is not what the plan read. Returns what it wrote (Applied)."""
    schema.ensure(db_path)
    with db.lock_for(db_path):
        conn = db.connect(db_path)
        try:
            db.begin(conn, immediate=True)
            try:
                try:
                    changes, skipped = _resolve(conn, edits)
                    change_id = _forward(conn, operation, changes, summary) if changes else None
                except sqlite3.IntegrityError as e:
                    raise _integrity(e) from e
                _reached("forward written")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            applied = Applied(change_id, sum(1 for c in changes if c.top), len(changes),
                              dict(collections.Counter(c.kind for c in changes if c.top and c.kind)),
                              skipped=list(skipped))
            if change_id is None:
                return applied
            _reached("forward committed")
            applied.settled = _settle_change(conn, change_id, changes)
            logger.info("%s: change %d, %s, wrote %d row(s)", db_path, change_id, operation, len(changes))
            return applied
        finally:
            conn.close()


def _undo_in(conn, change_id):
    """Check change `change_id` may be undone, and write its inverse. Returns its rows.
    The caller holds the transaction."""
    row = conn.execute("SELECT operation, status, schema_version FROM changes WHERE id = ?",
                       (change_id,)).fetchone()
    if row is None:
        raise Refusal(["there is no change %d" % change_id])
    _operation, status, version = row
    if status == "pruned":
        raise Refusal(["change %d was pruned: its values are gone, so it cannot be undone" % change_id])
    if status == "derived_pending":
        raise Refusal(["change %d is not finished; it is, the next time the library is opened" % change_id])
    if status != "applied":
        raise Refusal(["change %d is %s" % (change_id, status)])
    current = schema.version(conn)
    if version != current:
        raise Refusal(["change %d was made at schema %d and the library is at %d: its rows may not mean"
                       " what they did" % (change_id, version, current)])
    newer = _newer_overlapping(conn, change_id)
    if newer:
        raise Refusal(["change %d (%s), applied after it, changed the same rows: undo it first" % (other, name)
                       for other, name in newer])
    changes = _load(conn, change_id)
    reasons = _not_as_left(conn, change_id, changes)
    inverse = [_inverse(change) for change in reversed(changes)]
    if not reasons:
        reasons = _blocked(conn, inverse)
    if reasons:
        raise Refusal(reasons)
    try:
        _write(conn, inverse)
    except sqlite3.IntegrityError as e:
        raise _integrity(e) from e
    conn.execute("UPDATE changes SET status = 'derived_pending', undone = ? WHERE id = ?", (_now(), change_id))
    return changes


def undo(db_path, change_id):
    """Undo change `change_id` of the library at `db_path`. Refusal, with nothing
    written, when it may not be (_undo_in). Returns what it wrote (Undone)."""
    schema.ensure(db_path)
    with db.lock_for(db_path):
        conn = db.connect(db_path)
        try:
            db.begin(conn, immediate=True)
            try:
                changes = _undo_in(conn, change_id)
                _reached("undo written")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            _reached("undo committed")
            settled = _settle_change(conn, change_id, changes)
            logger.info("%s: change %d undone, %d row(s)", db_path, change_id, len(changes))
            return Undone(change_id, len(changes), settled)
        finally:
            conn.close()


def _snapshot(conn, changes):
    """({(table, key): the whole row, or None}, the people of every photo the change may
    touch) of the rows `changes` name, as they stand."""
    rows, columns = {}, {}
    for change in changes:
        if change.key is None:
            continue
        if change.table not in columns:
            columns[change.table] = _columns(conn, change.table)
        rows[(change.table, change.key)] = _read(conn, change.table, change.key, columns[change.table])
    photo_ids, _dated, nodes = _touched(conn, changes)
    photo_ids = sorted(photo_ids | set(people.photos_named_by(conn, nodes) if nodes else ()))
    listed = []
    for start in range(0, len(photo_ids), CHUNK):
        chunk = photo_ids[start:start + CHUNK]
        listed += conn.execute("SELECT photo_id, position, name, source FROM photo_people WHERE photo_id IN (%s)"
                               " ORDER BY photo_id, position" % ",".join("?" * len(chunk)), chunk).fetchall()
    return rows, listed


def _differences(before, after):
    found = []
    for (table, key), now in after.items():
        was = before.get((table, key))
        if was is None and now is None:
            continue
        if was is None or now is None:
            found.append("%s: %s" % (_named(table, key), "still there" if was is None else "not put back"))
            continue
        differs = [c for c in was if not _same(was[c], now.get(c))]
        if differs:
            found.append("%s: %s" % (_named(table, key), ", ".join(differs)))
    return found


def _rehearsed(conn, before, changes, restore, count, stale):
    """The Rehearsal of rows `changes` touched, `before` a snapshot of them, once
    `restore` has run."""
    try:
        restore()
    except sqlite3.IntegrityError as e:
        return Rehearsal(rows=len(changes), changed=count,
                         differences=["the undo was refused: %s" % _integrity(e)], derived_stale=stale)
    except Refusal as e:
        return Rehearsal(rows=len(changes), changed=count, differences=["the undo was refused: %s" % e],
                         derived_stale=stale)
    rows, listed = _snapshot(conn, changes)
    differences = _differences(before[0], rows)
    return Rehearsal(rows=len(changes), changed=count, exact=not differences, differences=differences[:SHOWN],
                     derived_exact=before[1] == listed, derived_stale=stale)


def rehearse(db_path, operation, edits, summary=None):
    """Apply `edits` and undo them inside a transaction that is rolled back, and say
    whether the undo restored every row it touched exactly (Rehearsal). Nothing is
    written; `refused` says why applying would be refused now."""
    schema.ensure(db_path)
    with db.lock_for(db_path):
        conn = db.connect(db_path)
        try:
            db.begin(conn, immediate=True)
            try:
                try:
                    changes, skipped = _resolve(conn, edits)
                except Refusal as e:
                    return Rehearsal(refused=str(e))
                skipped = [list(s) for s in skipped]
                if not changes:
                    return Rehearsal(exact=True, derived_exact=True, skipped=skipped)
                # The people as the rule makes them from the rows as they are, so that a
                # photo some writer left stale is not blamed on the undo. An insert has no
                # key yet, and nothing derived: the forward gives it both.
                stale = _derive(conn, changes)
                before = _snapshot(conn, changes)
                try:
                    change_id = _forward(conn, operation, changes, summary)
                except sqlite3.IntegrityError as e:
                    return Rehearsal(refused=str(_integrity(e)))
                _derive(conn, changes)
                conn.execute("UPDATE changes SET status = 'applied' WHERE id = ?", (change_id,))

                def undo_it():
                    _derive(conn, _undo_in(conn, change_id))

                rehearsal = _rehearsed(conn, before, changes, undo_it, sum(1 for c in changes if c.top), stale)
                rehearsal.skipped = skipped
                return rehearsal
            finally:
                conn.rollback()
        finally:
            conn.close()


def rehearse_undo(db_path, change_id):
    """Undo change `change_id` and write it again inside a transaction that is rolled
    back, and say whether that restored every row exactly (Rehearsal). Nothing is
    written; `refused` says why undoing would be refused now."""
    schema.ensure(db_path)
    with db.lock_for(db_path):
        conn = db.connect(db_path)
        try:
            db.begin(conn, immediate=True)
            try:
                changes = _load(conn, change_id) if has_journal(conn) else []
                stale = _derive(conn, changes)
                before = _snapshot(conn, changes)
                try:
                    _undo_in(conn, change_id)
                except Refusal as e:
                    return Rehearsal(refused=str(e))
                _derive(conn, changes)

                def again():
                    _write(conn, [_again(change) for change in changes])
                    _derive(conn, changes)

                return _rehearsed(conn, before, changes, again, len(changes), stale)
            finally:
                conn.rollback()
        finally:
            conn.close()


# ---- Settling at start, history, pruning -------------------------------------------------

def settle(db_path):
    """Finish every change of the library at `db_path` left derived_pending: rebuild what
    it touched and mark it applied, or undone. Returns how many were finished."""
    conn = db.connect(db_path)
    try:
        if not has_journal(conn):
            return 0
        pending = [change_id for (change_id,) in conn.execute(
            "SELECT id FROM changes WHERE status = 'derived_pending' ORDER BY id")]
        finished = 0
        with db.lock_for(db_path):
            for change_id in pending:
                if _settle_change(conn, change_id, _load(conn, change_id)):
                    finished += 1
                    logger.info("%s: change %d, left half-done, is finished", db_path, change_id)
        return finished
    finally:
        conn.close()


_settled = set()
_settled_guard = threading.Lock()


def settle_once(db_path):
    """settle, the first time this process opens the library at `db_path`: at start."""
    key = db._key(db_path)
    with _settled_guard:
        if key in _settled:
            return 0
        _settled.add(key)
    return settle(db_path)


def _shown(value):
    return "<%d bytes>" % len(value) if isinstance(value, bytes) else value


def history(db_path, limit=20, change_id=None, values=False):
    """The library's changes, newest first (or change `change_id` alone): id, operation,
    status, schema version, when made, applied and undone, its summary, and how many rows
    of each table it inserted, updated and deleted. One change also lists its row keys,
    and with `values` each column's old and new value (a BLOB by its size): these can
    hold names. [] for a library without a journal."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not has_journal(conn):
            return []
        query = ("SELECT id, operation, status, schema_version, created, applied, undone, summary FROM changes")
        if change_id is None:
            found = conn.execute(query + " ORDER BY id DESC LIMIT ?", (max(0, limit),)).fetchall()
        else:
            found = conn.execute(query + " WHERE id = ?", (change_id,)).fetchall()
        entries = []
        for cid, operation, status, version, created, applied, undone, summary in found:
            entries.append({"id": cid, "operation": operation, "status": status, "schema_version": version,
                            "created": created, "applied": applied, "undone": undone,
                            "summary": json.loads(summary) if summary else {}, "rows": {}})
        by_id = {entry["id"]: entry for entry in entries}
        ids = list(by_id)
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start:start + CHUNK]
            for cid, table, action, count in conn.execute(
                    "SELECT change_id, table_name, action, COUNT(DISTINCT row_key) FROM change_rows"
                    " WHERE change_id IN (%s) GROUP BY change_id, table_name, action" % ",".join("?" * len(chunk)),
                    chunk):
                by_id[cid]["rows"].setdefault(table, {})[action] = count
        if change_id is not None and entries:
            # The keys alone: the old and new values -- a deleted face's crop and vector,
            # 8 KB a face -- are read only when asked for.
            keys = collections.defaultdict(list)
            for table, key_text in conn.execute(
                    "SELECT table_name, row_key FROM change_rows WHERE change_id = ?"
                    " GROUP BY action, table_name, row_key ORDER BY MIN(id)", (change_id,)):
                keys[table].append(json.loads(key_text))
            entries[0]["keys"] = dict(keys)
            if values:
                changes = _load(conn, change_id)
                entries[0]["values"] = [{
                    "action": change.action, "table": change.table, "key": list(change.key),
                    "old": None if change.old is None else {c: _shown(v) for c, v in change.old.items()},
                    "new": None if change.new is None else {c: _shown(v) for c, v in change.new.items()},
                } for change in changes]
        return entries
    finally:
        conn.close()


def _cutoff(days, now):
    return time.strftime(TIME, time.localtime((time.time() if now is None else now) - days * 86400))


def _prunable(conn, cutoff):
    """The ids of the changes pruning at `cutoff` takes the values of: applied or undone,
    and no newer than the newest made before it. A newer change is never pruned before an
    older one, so an undo's check of the changes after it sees every one that kept its
    rows."""
    last = conn.execute("SELECT MAX(id) FROM changes WHERE created <= ?", (cutoff,)).fetchone()[0]
    if last is None:
        return []
    return [cid for (cid,) in conn.execute(
        "SELECT id FROM changes WHERE id <= ? AND status IN ('applied', 'undone') ORDER BY id", (last,))]


def prunable(db_path, days=RETENTION_DAYS, now=None):
    """(changes, values) pruning after `days` would take away. Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not has_journal(conn):
            return 0, 0
        ids = _prunable(conn, _cutoff(days, now))
        values = 0
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start:start + CHUNK]
            values += conn.execute("SELECT COUNT(*) FROM change_rows WHERE change_id IN (%s)"
                                   % ",".join("?" * len(chunk)), chunk).fetchone()[0]
        return len(ids), values
    finally:
        conn.close()


def prune(db_path, days=RETENTION_DAYS, now=None):
    """Take the values of every change older than `days` out of the journal: its summary
    stays, and it becomes `pruned`, no longer undoable. Returns (changes pruned, values
    deleted)."""
    schema.ensure(db_path)

    def work(conn):
        ids = _prunable(conn, _cutoff(days, now))
        deleted = 0
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start:start + CHUNK]
            marks = ",".join("?" * len(chunk))
            deleted += conn.execute("DELETE FROM change_rows WHERE change_id IN (%s)" % marks, chunk).rowcount
            conn.execute("UPDATE changes SET status = 'pruned' WHERE id IN (%s)" % marks, chunk)
        return len(ids), deleted

    return db.write_with_connection(db_path, work, label="prune the journal")

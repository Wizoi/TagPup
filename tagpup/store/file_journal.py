"""The photo files of the journal: each file a bulk edit writes, with what it held
before and is to hold after, and where the write of it stands (migration 11;
docs/ARCHITECTURE.md, phase 7.5).

A change of rows is one transaction (tagpup.store.journal). A batch of file writes
cannot be: ExifTool writes a file at a time, outside the database. So, as dpkg does for
the packages of one run, each file carries its own state:

    planned   the plan is committed; the file is not touched yet
    writing   ExifTool is about to write it, or has, and its row is not recorded yet
    done      written, and its row recorded in the same transaction that says so
    conflict  it held neither what the plan read nor what it was to hold, or an undo
              found it no longer holding what the change left: reported, never
              overwritten
    undone    put back as it was, its row recorded in the same transaction

and the change is `planned` until every file is done or a conflict, then `applied`. An
undo marks the change `planned` again with `undone` set, and `undone` once every file it
could put back is. `changes.owner` names the process carrying a change out, so a change
another live process is writing is not taken for one a crash left (tagpup.services.
file_changes.settle).

A field change keeps `fields_before` and `fields_after` as JSON, {field: [texts]}; a
rename keeps the file's old and new path, and its size and modified time, which a rename
does not change, to know the file by wherever it is found. Reading and writing the files
is tagpup.services.file_changes'; this is the rows.
"""
import json
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from tagpup.core import processes
from tagpup.store import db, schema

STATES = ("planned", "writing", "done", "conflict", "undone")

TIME = "%Y-%m-%d %H:%M:%S"

#: How many ids one query asks for.
CHUNK = 500


@dataclass
class FileRow:
    id: int
    change_id: int
    photo_id: Optional[int]
    path: str
    new_path: Optional[str]
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    state: str = "planned"
    note: Optional[str] = None

    @property
    def is_rename(self):
        return self.new_path is not None

    def named(self):
        """The file as a refusal names it: by its photo's id, never its path, which can
        hold a caption and so a name."""
        if self.photo_id is not None:
            return "photo %d" % self.photo_id
        return "file %d of change %d" % (self.id, self.change_id)


@dataclass
class Change:
    id: int
    operation: str
    status: str
    undone: Optional[str]
    owner: Optional[str]
    summary: Dict[str, Any]

    @property
    def undoing(self):
        """An undo is under way (or was, when a crash stopped it)."""
        return self.status == "planned" and self.undone is not None


_owner = None


def owner():
    """This process, as `changes.owner` names it: "host:pid:start", its host, its id and
    when it started (tagpup.core.processes.started). Windows reuses an id soon after its
    process ends, and an id alone took a change a crash left for one a live process was
    carrying out (docs/findings.md, #275). Libraries written before name "host:pid", as
    does a process whose start cannot be read."""
    global _owner
    if _owner is None or _owner[0] != os.getpid():
        named = "%s:%d" % (socket.gethostname(), os.getpid())
        started = processes.started(os.getpid())
        _owner = (os.getpid(), named if started is None else "%s:%d" % (named, started))
    return _owner[1]


def owner_parts(named):
    """(host, pid, start) of an owner `named` by owner(); start is None for one named the
    old way, "host:pid", and pid None for a name that is neither."""
    parts = str(named).split(":")
    numbers = []
    while parts and len(numbers) < 2 and parts[-1].isdigit():
        numbers.insert(0, int(parts.pop()))
    if not numbers or not parts:
        return str(named), None, None
    if len(numbers) == 1:
        return ":".join(parts), numbers[0], None
    return ":".join(parts), numbers[0], numbers[1]


def _now():
    return time.strftime(TIME)


def has_table(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'change_files'"
                        ).fetchone() is not None


def _row(found):
    file_id, change_id, photo_id, path, new_path, before, after, state, note = found
    return FileRow(file_id, change_id, photo_id, path, new_path, json.loads(before or "{}"),
                   json.loads(after or "{}"), state, note)


_FILE_COLUMNS = "id, change_id, photo_id, path, new_path, fields_before, fields_after, state, note"


def plan(db_path, operation, files, summary=None):
    """Commit a plan: a change named `operation`, `planned`, owned by this process, and a
    row `planned` for each of `files` -- dicts of photo_id, path, new_path, before and
    after. Returns (change id, [FileRow]) in the order given."""
    schema.ensure(db_path)

    def work(conn):
        change_id = conn.execute(
            "INSERT INTO changes (operation, status, schema_version, created, summary, owner)"
            " VALUES (?, 'planned', ?, ?, ?, ?)",
            (operation, schema.version(conn), _now(), json.dumps(summary or {}, sort_keys=True), owner())).lastrowid
        rows = []
        for planned in files:
            before, after = planned.get("before") or {}, planned.get("after") or {}
            file_id = conn.execute(
                "INSERT INTO change_files (change_id, photo_id, path, new_path, fields_before, fields_after, state)"
                " VALUES (?, ?, ?, ?, ?, ?, 'planned')",
                (change_id, planned.get("photo_id"), planned["path"], planned.get("new_path"),
                 json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))).lastrowid
            rows.append(FileRow(file_id, change_id, planned.get("photo_id"), planned["path"],
                                planned.get("new_path"), dict(before), dict(after)))
        return change_id, rows

    return db.write_with_connection(db_path, work, label="plan of %s" % operation)


def set_state(conn, file_id, state, note=None):
    """Mark one file. The caller commits: a file is marked done in the transaction that
    records its row."""
    if state not in STATES:
        raise ValueError("a file is %s, not %r" % (", ".join(STATES), state))
    conn.execute("UPDATE change_files SET state = ?, note = ? WHERE id = ?", (state, note, file_id))


def set_after(conn, file_id, after):
    """Record what a file holds after its write, where ExifTool did not keep a value as
    written (tagpup.services.file_changes). The caller commits, with the file's row."""
    conn.execute("UPDATE change_files SET fields_after = ? WHERE id = ?", (json.dumps(after, sort_keys=True), file_id))


def mark(db_path, file_ids, state, note=None):
    """Mark files, in a transaction of their own: `writing`, before ExifTool runs."""
    file_ids = list(file_ids)

    def work(conn):
        for file_id in file_ids:
            set_state(conn, file_id, state, note)

    db.write_with_connection(db_path, work, label="%d file(s) %s" % (len(file_ids), state))


def withdraw(db_path, file_ids):
    """Take files that were never written out of their change: planned, and left alone
    after an earlier file failed; or put back as they were by the rename that failed."""
    file_ids = list(file_ids)
    if not file_ids:
        return

    def work(conn):
        for start in range(0, len(file_ids), CHUNK):
            chunk = file_ids[start:start + CHUNK]
            conn.execute("DELETE FROM change_files WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk)

    db.write_with_connection(db_path, work, label="withdraw %d file(s)" % len(file_ids))


def files_of(db_path, change_id):
    """The files of change `change_id`, in the order they were planned."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not has_table(conn):
            return []
        return [_row(found) for found in conn.execute(
            "SELECT " + _FILE_COLUMNS + " FROM change_files WHERE change_id = ? ORDER BY id", (change_id,))]
    finally:
        conn.close()


def change(db_path, change_id):
    """Change `change_id` (Change), or None."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not has_table(conn):
            return None
        found = conn.execute("SELECT id, operation, status, undone, owner, summary FROM changes WHERE id = ?",
                             (change_id,)).fetchone()
    finally:
        conn.close()
    if found is None:
        return None
    return Change(found[0], found[1], found[2], found[3], found[4], json.loads(found[5] or "{}"))


def writes_files(db_path, change_id):
    """Did change `change_id` plan photo files?"""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return has_table(conn) and conn.execute(
            "SELECT 1 FROM change_files WHERE change_id = ? LIMIT 1", (change_id,)).fetchone() is not None
    finally:
        conn.close()


def unfinished(db_path):
    """The changes of photo files not finished: `planned`, forward or undoing, oldest
    first (Change). Only a change of photo files is ever `planned` (a change of rows is
    recorded applied), and one whose every file was taken out again is listed too: a
    process stopped before finishing it left it planned with no files (docs/findings.md,
    #274), and finishing it marks it failed."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not has_table(conn):
            return []
        found = conn.execute(
            "SELECT id, operation, status, undone, owner, summary FROM changes"
            " WHERE status = 'planned' ORDER BY id").fetchall()
    finally:
        conn.close()
    return [Change(r[0], r[1], r[2], r[3], r[4], json.loads(r[5] or "{}")) for r in found]


def claim(db_path, change_id, was):
    """Take change `change_id` over for this process, if it is still `planned` and owned
    by `was` (None, or a process that is gone). True when it was taken."""
    def work(conn):
        return conn.execute("UPDATE changes SET owner = ? WHERE id = ? AND status = 'planned' AND owner IS ?",
                            (owner(), change_id, was)).rowcount == 1

    return db.write_with_connection(db_path, work, label="take over change %d" % change_id)


def release(db_path, change_id):
    """This process no longer carries change `change_id` out: an error stopped it, and
    whatever settles the library next may finish it."""
    def work(conn):
        conn.execute("UPDATE changes SET owner = NULL WHERE id = ? AND owner = ?", (change_id, owner()))

    try:
        db.write_with_connection(db_path, work, label="release change %d" % change_id)
    except Exception:
        # A crash leaves it owned by a process that is gone, which settling takes over
        # just the same.
        pass


def begin_undo(db_path, change_id):
    """Mark an applied change as being undone, by this process. False when it is not
    applied (any more)."""
    def work(conn):
        return conn.execute("UPDATE changes SET status = 'planned', undone = ?, owner = ?"
                            " WHERE id = ? AND status = 'applied'", (_now(), owner(), change_id)).rowcount == 1

    return db.write_with_connection(db_path, work, label="begin undoing change %d" % change_id)


def counts(conn, change_ids):
    """{change id: {state: files}} of `change_ids`. The caller's connection."""
    found = {}
    if not has_table(conn):
        return found
    change_ids = list(change_ids)
    for start in range(0, len(change_ids), CHUNK):
        chunk = change_ids[start:start + CHUNK]
        for change_id, state, count in conn.execute(
                "SELECT change_id, state, COUNT(*) FROM change_files WHERE change_id IN (%s)"
                " GROUP BY change_id, state" % ",".join("?" * len(chunk)), chunk):
            found.setdefault(change_id, {})[state] = count
    return found


def finish(db_path, change_id):
    """Mark change `change_id` applied -- or undone, when it was being undone -- now that
    every file is done, undone or a conflict; its summary takes the files' counts. A change
    every file was taken out of again wrote nothing, and is `failed`. Returns the counts,
    or None when files are left to write."""
    def work(conn):
        states = counts(conn, [change_id]).get(change_id, {})
        undoing = conn.execute("SELECT undone FROM changes WHERE id = ?", (change_id,)).fetchone()[0] is not None
        left = states.get("planned", 0) + states.get("writing", 0) + (states.get("done", 0) if undoing else 0)
        if left:
            return None
        summary = json.loads(conn.execute("SELECT summary FROM changes WHERE id = ?",
                                          (change_id,)).fetchone()[0] or "{}")
        summary["undone_files" if undoing else "files"] = dict(sorted(states.items()))
        if not states:
            # Every file was taken out again: nothing was written.
            conn.execute("UPDATE changes SET status = 'failed', owner = NULL, summary = ? WHERE id = ?",
                         (json.dumps(summary, sort_keys=True), change_id))
        elif undoing:
            conn.execute("UPDATE changes SET status = 'undone', owner = NULL, summary = ? WHERE id = ?",
                         (json.dumps(summary, sort_keys=True), change_id))
        else:
            conn.execute("UPDATE changes SET status = 'applied', applied = ?, owner = NULL, summary = ? WHERE id = ?",
                         (_now(), json.dumps(summary, sort_keys=True), change_id))
        return states

    return db.write_with_connection(db_path, work, label="finish change %d" % change_id)


def prune(conn, change_ids):
    """Delete the files of pruned changes. The caller's transaction."""
    if not has_table(conn):
        return 0
    deleted = 0
    change_ids = list(change_ids)
    for start in range(0, len(change_ids), CHUNK):
        chunk = change_ids[start:start + CHUNK]
        deleted += conn.execute("DELETE FROM change_files WHERE change_id IN (%s)" % ",".join("?" * len(chunk)),
                                chunk).rowcount
    return deleted

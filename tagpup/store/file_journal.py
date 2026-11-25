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

A change of an INTERNAL operation (the index's bake, a Delete) is the record a crash needs and no
more: it is erased, its files and itself, when it finishes forward -- but for a file of it
that ended a conflict the doctor counts (a note of NOTES), which is kept with its change
(owner, 2026-11-03, narrowed by the main session to the doctor's notes); no history lists
it, and no undo check meets it, but while one of its files is `writing` (newer_overlapping:
one writer of a file at a time).

A field change keeps `fields_before` and `fields_after` as JSON, {field: [texts]}, and
the file's stamp, [mtime, size], as it is marked writing, over which settling carries its
vectors (migration 12); a rename keeps the file's old and new path, and its size, modified time and file id, which a
rename does not change, to know the file by wherever it is found -- carried over each later write of the journal's own
that the file under its new name takes, and over the indexer's identity mint (follow_renamed). Reading and writing the files is
tagpup.services.file_changes'; this is the rows.

A file a patch writes in place (tagpup.files.in_place, migration 37) carries, while it is `writing`, the patch's record
(`patch`, JSON) and the bytes its ranges held (`patch_before`), committed before the first byte (save_patch): a crash
part-way leaves ranges half written, which only the bytes settle. patch_of is their one reader; every mark that takes the
file out of `writing` erases them in its own transaction (set_state, writing, withdraw_in): a crash record, not a second
undo. ExifTool's rewrite, which leaves the old file or the new one whole, saves none.
"""
import json
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from tagpup.core import dates, paths, processes
from tagpup.core.result import Refused
from tagpup.store import db, schema
from tagpup.store import roots as store_roots

STATES = ("planned", "writing", "done", "conflict", "undone")

#: How many ids one query asks for.
CHUNK = 500

#: The operation of a bake the index writes after it records a folder (tagpup.services.bake_after_index): the index's
#: values stamped on the file, never an operation the owner sees or undoes (owner, docs/PLAN_KEEP_IN_STEP.md, decision 2).
#: Its change is the crash record a write needs while it is under way (settled at the next start by what each file
#: holds), and once finished it is erased, all but its conflicts (finish). Spelt here, the lowest layer that tells it apart; tagpup.services.
#: baking names it INDEX_OPERATION. The 'bake photos' changes of the backfill are not internal and stay as they are.
INDEX_BAKE = "index bake"
#: The operation of a photo sent to the Recycle Bin and forgotten (tagpup.services.photos.delete): one file, its row
#: planned, `writing` once its Bin step begins, done in the transaction that removes the photo's rows
#: (store.photos.forget_photo's `also`). The crash record a Delete needs between its file and its rows, never an
#: operation History lists or undoes (tagpup.services.journal.UNDO_CHECKS); settled by tagpup.services.photos.
#: settle_delete (file_changes._finish), which looks at each file it left `writing`. `fields_after` holds {"for_good"}.
DELETE = "delete photo"
#: The operations whose changes are internal: listed by no history, met by no undo's check of newer changes.
INTERNAL = (INDEX_BAKE, DELETE)
ERASED = "erased"      # what `finish` returns for a change that wrote nothing and is gone, as None says files are left
#: The operations that rename photo files (tagpup.services.file_changes.rename refuses another): the changes whose files a
#: rename left under a new name, known there by their fingerprint (follow_renamed). By the operation, the change_files of a
#: library are sought by their change (its index), not scanned: 51,451 rows in photo_index, 85 ms warm and 6 s cold, of
#: which 9 are renames (counted 2026-10-29); every rename-holding change of the three libraries is a "smart rename".
RENAMES = ("smart rename", "rename after caption")
#: The operation of a clip's conversion (tagpup.services.convert): one file, its path the original's, its new_path the
#: converted clip's (None when the name is the same); fields_before the original's fingerprint, fields_after what the
#: conversion carries and, once made, the converted copy's fingerprint (`made`). Settled by tagpup.services.convert alone
#: (file_changes._finish), never undone (tagpup.services.journal.UNDO_CHECKS): the original is deleted for good.
CONVERT = "convert"


def not_internal(column):
    """(SQL, params): `column`, an operation, is none of INTERNAL."""
    return "%s NOT IN (%s)" % (column, ", ".join("?" * len(INTERNAL))), INTERNAL


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
    #: (mtime, size) of the file just before its write, or None.
    stamp: Optional[tuple] = None
    #: Not stored: what recording the file made of its photo's rows besides the fields, as the writer reports it (the face
    #: boxes a rotation turned, tagpup.services.rotation).
    followed: int = 0
    #: Not stored: the faces recording the file named (the one-face rule: store.photos.follow_fields), [{"id", "name"}], said in
    #: the write's reply (`named_faces`) so that a page reads the photo's faces again.
    named_faces: list = field(default_factory=list)
    #: Not stored: the turn of face boxes this process made for the file, (stored width, stored height, Orientation before,
    #: Orientation after) (tagpup.services.rotation), so that taking the file out of its change after it was done turns them
    #: back exactly.
    turned: Optional[tuple] = None
    #: Not stored: the faces an undo of this file left as they were because they had changed since (tagpup.store.face_batches).
    faces_left: int = 0
    #: Not stored: a forward write failed and the file is marked read-only, which no retry of the write changes
    #: (tagpup.services.file_changes._after_failure; the one failure a write tells from a passing one).
    read_only: bool = False
    #: Not stored: the file's os.stat just before this process wrote it, whose fingerprint (tagpup.services.file_changes.
    #: _fingerprint_of) a rename that left it under this name knows it by (follow_renamed).
    known_as: Optional[Any] = None
    #: Not stored: this process saved a patch's record of the file for the write under way (save_patch), so what the write
    #: left is settled by the file's bytes (tagpup.services.file_changes._by_bytes).
    patched: bool = False
    #: The fields the write under way left holding what they held (field_values.write's answer, from
    #: tagpup.core.fields.XMP_ONLY), so the file is verified and recorded as fields.as_patched says it holds. Not a
    #: column of its own: it is carried in the patch record's `stale` key, which in_place.left_stale reads back when a
    #: crash leaves the file to be settled by its bytes (tagpup.services.file_changes._by_bytes).
    stale: tuple = ()

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
    carrying out (docs/findings.md, #275). A process that cannot read its own start is
    refused (RuntimeError): an owner without one cannot be told from the next process given its id."""
    global _owner
    if _owner is None or _owner[0] != os.getpid():
        started = processes.started(os.getpid())
        if started is None:
            raise RuntimeError("This process cannot read when it started, so it cannot own a change; nothing was written")
        _owner = (os.getpid(), "%s:%d:%d" % (socket.gethostname(), os.getpid(), started))
    return _owner[1]


def owner_parts(named):
    """(host, pid, start) of an owner `named` by owner(), "host:pid:start"; pid and start
    None for a name that is not."""
    host, _sep, rest = str(named).rpartition(":")
    host, _sep, pid = host.rpartition(":")
    if not host or not pid.isdigit() or not rest.isdigit():
        return str(named), None, None
    return host, int(pid), int(rest)


def owner_alive(named):
    """Is the process `named` by owner() ("host:pid:start") still running? One on another machine is taken to be: this
    one cannot tell. So is this process: what it holds is being carried out on another
    thread. A process with the id but another start is one that had the id before, and
    has ended (docs/findings.md, #275). Whoever takes over what a process left -- a
    change of photo files (tagpup.services.file_changes.settle), a recurring job's run
    (tagpup.store.job_runs) -- asks this."""
    host, pid, start = owner_parts(named)
    if host != socket.gethostname():
        return True
    if pid is None:
        return False
    if pid == os.getpid():
        return named == owner()
    now = processes.started(pid)
    if now is not None:
        return now == start
    # Its start could not be read: gone, or not ours to read.
    return processes.is_alive(pid)


def _now():
    return dates.recorded()


def _native(value, roots):
    """A path change_files holds, as this machine spells it: the row form or, recorded before the library's roots were
    adopted, the native path, either read by the first place of its root -- a change recorded before the adoption holds
    the path as it was then (the place the library lived at); after a move an undo must write where writes go now. The
    one reading of a journaled path (_row, follow_renamed)."""
    return paths.canonical(paths.from_row(value, roots), roots)


def _row(found, roots):
    """A FileRow of a change_files row, its paths as this machine spells them. A change
    recorded before the library's roots were adopted holds native paths, and one recorded
    after holds the row form: either reads as the native path (paths.from_row)."""
    file_id, change_id, photo_id, path, new_path, before, after, state, note, stamp = found
    path = _native(path, roots)
    new_path = _native(new_path, roots) if new_path else new_path
    return FileRow(file_id, change_id, photo_id, path, new_path, json.loads(before or "{}"),
                   json.loads(after or "{}"), state, note, tuple(json.loads(stamp)) if stamp else None)


#: The most a patch's row keeps (save_patch): its record as saved (the new bytes in base64, 4/3 of them) and the bytes
#: its ranges held. Over it the patch is refused, nothing written (PatchTooLarge). A photo's metadata segments are at
#: most 64 KB each; a clip's XMP box has no other bound (owner, 2026-11-12).
PATCH_CAP = 1 << 20
#: The owner's sentence of a patch over PATCH_CAP: the KB it would keep, and the most kept.
PATCH_TOO_LARGE = ("TagPup cannot save into this file: its metadata is too large to keep a copy of, which would put the "
                   "file back if the save were cut short (%s KB; at most %s KB is kept). Nothing was written.")

#: The note of a file whose write changed its picture's bytes twice in a settle or an undo (tagpup.files.field_values.
#: PictureChanged, the picture check of #2377): the new file was discarded each time and the file is as it was; a
#: conflict, which the doctor counts. Also the error a forward write reports for it.
PICTURE_CHANGED = "its write changed the picture's bytes twice; the file is as it was and nothing was written"

#: The notes of a conflict the doctor counts (noted; tagpup.store.checks): a file a write found damaged, by its kind.
NOTES = (PICTURE_CHANGED,)
#: noted's key of the files holding a patch's record though not `writing`: a mark that did not erase it (none, by
#: set_state, writing and withdraw_in).
PATCH_KEPT = ("not writing", "a patch's record")


class PatchTooLarge(Refused):
    """A patch whose row would keep more than PATCH_CAP (its record and old bytes, `size`): nothing was saved, and
    nothing may be written by a patch. A Refused, its sentence the owner's (PATCH_TOO_LARGE): the write's caller withdraws
    the file's row and tells it apart as its writer's refusal (file_changes.WRITER_REFUSED)."""

    def __init__(self, size):
        self.size = size
        super().__init__(PATCH_TOO_LARGE % ("{:,}".format(-(-size // 1024)), "{:,}".format(PATCH_CAP // 1024)))


_FILE_COLUMNS = "id, change_id, photo_id, path, new_path, fields_before, fields_after, state, note, stamp"


def plan(db_path, operation, files, summary=None, also=None):
    """Commit a plan: a change named `operation`, `planned`, owned by this process, and a
    row `planned` for each of `files` -- dicts of photo_id, path, new_path, before and
    after. Returns (change id, [FileRow]) in the order given. `also(conn, change id)` writes
    what else the plan holds (a bulk assignment's faces, tagpup.store.face_batches), in the
    same transaction."""
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
                (change_id, planned.get("photo_id"), store_roots.to_row(conn, planned["path"]),
                 store_roots.to_row(conn, planned["new_path"]) if planned.get("new_path") else planned.get("new_path"),
                 json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))).lastrowid
            rows.append(FileRow(file_id, change_id, planned.get("photo_id"), planned["path"],
                                planned.get("new_path"), dict(before), dict(after)))
        if also is not None:
            also(conn, change_id)
        return change_id, rows

    return db.write_with_connection(db_path, work, label="plan of %s" % operation)


def set_state(conn, file_id, state, note=None):
    """Mark one file. The caller commits: a file is marked done in the transaction that
    records its row."""
    if state not in STATES:
        raise ValueError("a file is %s, not %r" % (", ".join(STATES), state))
    # The patch's record goes with the mark (the module's docstring): a file marked anything is no longer under a patch.
    conn.execute("UPDATE change_files SET state = ?, note = ?, patch = NULL, patch_before = NULL WHERE id = ?",
                 (state, note, file_id))


def set_after(conn, file_id, after):
    """Record what a file holds after its write, where ExifTool did not keep a value as
    written (tagpup.services.file_changes). The caller commits, with the file's row."""
    conn.execute("UPDATE change_files SET fields_after = ? WHERE id = ?", (json.dumps(after, sort_keys=True), file_id))


def writing(db_path, file_id, stamp):
    """Mark one file `writing`, with `stamp`, (mtime, size) of the file just before its
    write: settling after a crash carries the photo's vectors over the write by it. A record of an earlier patch is
    erased: this write saves its own, if it patches."""
    def work(conn):
        conn.execute("UPDATE change_files SET state = 'writing', note = NULL, stamp = ?, patch = NULL, patch_before = NULL"
                     " WHERE id = ?", (json.dumps(list(stamp)) if stamp else None, file_id))

    db.write_with_connection(db_path, work, label="file %d writing" % file_id)


def save_patch(db_path, file_id, record, before):
    """Commit the record of a patch (tagpup.files.in_place.record) and the bytes its ranges hold `before`, of a file
    `writing`, before the patch writes a byte. Raises PatchTooLarge when the row would keep more than PATCH_CAP (the
    record's text and `before`, what it holds), and RuntimeError when the file is not `writing`; either way nothing is
    saved, and the patch must write nothing."""
    text = json.dumps(record, sort_keys=True)
    if len(text) + len(before) > PATCH_CAP:
        raise PatchTooLarge(len(text) + len(before))

    def work(conn):
        return conn.execute("UPDATE change_files SET patch = ?, patch_before = ? WHERE id = ? AND state = 'writing'",
                            (text, bytes(before), file_id)).rowcount

    if db.write_with_connection(db_path, work, label="file %d: its patch saved" % file_id) != 1:
        raise RuntimeError("file %d is not being written, so its patch was not saved; nothing was written" % file_id)


def patch_of(db_path, file_id):
    """(record, before) of the patch file `file_id` is under (save_patch), or None: the one reader of the record."""
    found = db.read_with_connection(db_path, lambda cursor: cursor.execute(
        "SELECT patch, patch_before FROM change_files WHERE id = ? AND patch IS NOT NULL", (file_id,)).fetchone())
    return None if found is None else (json.loads(found[0]), bytes(found[1] or b""))


def noted(conn):
    """{key: [change_files ids]} of the files the doctor counts, in ONE scan of change_files (0.28 s on photo_index's
    51,451 rows, counted 2026-11-02; #2384): ("conflict", note) for each of NOTES, and PATCH_KEPT. Every key is there,
    an empty list when none. The caller's connection (tagpup.store.checks, which keeps the answer while nothing is
    written)."""
    found = {("conflict", note): [] for note in NOTES}
    found[PATCH_KEPT] = []
    for file_id, state, note, patched in conn.execute(
            "SELECT id, state, note, patch IS NOT NULL FROM change_files WHERE (state = 'conflict' AND note IN (%s))"
            " OR (patch IS NOT NULL AND state != 'writing') ORDER BY id" % ", ".join("?" * len(NOTES)), NOTES):
        if patched and state != "writing":
            found[PATCH_KEPT].append(file_id)
        if state == "conflict" and note in NOTES:
            found[("conflict", note)].append(file_id)
    return found


#: The files of renames done (RENAMES): sought by their change, the change_id index, never a scan of change_files.
_RENAMED = ("change_id IN (SELECT id FROM changes WHERE operation IN (%s)) AND new_path IS NOT NULL AND state = 'done'"
            % ", ".join("?" * len(RENAMES)))


def fingerprint_key(was):
    """`was`, a file's fingerprint (size, modified time, file id), as a rename's file holds it (fields_before)."""
    return json.dumps(was, sort_keys=True)


def follow_renamed(conn, path, was, now):
    """The file under `path` was just written, by the journal or the indexer's identity mint, and was `was` (its
    fingerprint: size, modified time, file id) before: a rename done that left it there, knowing it by `was`, knows it by
    `now`, what the write left, from here on. A field write -- ExifTool's, beside the file and renamed over it -- changes
    all three for good, and the rename's undo, after the write's own undo, did not find its file (#1952); after the mint,
    which no undo takes back, it never did (#1957). A rename that knows another file there -- one put in its place since --
    is left as it is, and its undo refuses this one. The renames knowing `was` are sought by it, and their new names read
    as _row reads them (_native): one recorded before the library's roots were adopted holds the native spelling, which a
    comparison in the row form does not match (photo_index's nine). In the caller's transaction, the one that records the
    write. Returns the files of renames followed."""
    roots = store_roots.roots_for(conn)
    here = paths.canonical(path, roots)
    followed = [file_id for file_id, new_path in conn.execute(
        "SELECT id, new_path FROM change_files WHERE " + _RENAMED + " AND fields_before = ?",
        RENAMES + (fingerprint_key(was),)) if paths.same(_native(new_path, roots), here)]
    now = fingerprint_key(now)
    for file_id in followed:
        conn.execute("UPDATE change_files SET fields_before = ?, fields_after = ? WHERE id = ?", (now, now, file_id))
    return len(followed)


def renamed_fingerprints(db_path):
    """The fingerprints (fingerprint_key) the renames done know their files by (follow_renamed): one read of the renames'
    files, on a read connection, for a run of writes outside the journal to ask of each file without a query (a few
    hundred in a library; nine in photo_index)."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return frozenset(known for (known,) in conn.execute(
            "SELECT fields_before FROM change_files WHERE " + _RENAMED, RENAMES))
    finally:
        conn.close()


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
    db.write_with_connection(db_path, lambda conn: withdraw_in(conn, file_ids),
                             label="withdraw %d file(s)" % len(file_ids))


def withdraw_in(conn, file_ids):
    """withdraw, in the caller's transaction: a file whose write changed nothing is taken
    out in the one that records its row (tagpup.services.file_changes). Its patch's record, if any, goes with its row."""
    file_ids = list(file_ids)
    for start in range(0, len(file_ids), CHUNK):
        chunk = file_ids[start:start + CHUNK]
        conn.execute("DELETE FROM change_files WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk)


def files_of(db_path, change_id):
    """The files of change `change_id`, in the order they were planned."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        roots = store_roots.roots_for(conn)
        return [_row(found, roots) for found in conn.execute(
            "SELECT " + _FILE_COLUMNS + " FROM change_files WHERE change_id = ? ORDER BY id", (change_id,))]
    finally:
        conn.close()


def change(db_path, change_id):
    """Change `change_id` (Change), or None."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
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
        return conn.execute(
            "SELECT 1 FROM change_files WHERE change_id = ? LIMIT 1", (change_id,)).fetchone() is not None
    finally:
        conn.close()


def unfinished(db_path):
    """The changes of photo files not finished: `planned`, forward or undoing, oldest
    first (Change). Only a change of photo files is ever `planned` (a change of rows is
    recorded applied), and one whose every file was taken out again is listed too: a
    process stopped before finishing it left it planned with no files (docs/findings.md,
    #274), and finishing it erases it, as finish does any change that wrote nothing."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
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


def newer_overlapping(conn, change_id):
    """(id, operation) of each change after `change_id`, not undone, that wrote a photo
    file it wrote: the same photo, or the same file by its path -- one a rename left
    under its new name included. An undo of `change_id` would take back part of a file
    the newer change planned from, as an undo of rows would rows a newer change wrote
    over (tagpup.store.journal). The caller's connection. A file still `planned` in a change not finished counts too:
    a write under way, or one a kill cut short that a settle is finishing (an undo let in between its chunks, or by
    another process, would put back a file the change is still to write, and the change would find it changed and lose
    its edit on it as a conflict). An internal change (INTERNAL) is one only while it has
    the file `writing` -- one writer of a file at a time, across processes (the undo is refused "being written now");
    once done it is none: it is no operation, and a later bake is written over the file again by the next index."""
    internal, params = not_internal("c.operation")
    return conn.execute(
        "SELECT DISTINCT c.id, c.operation FROM change_files mine"
        " JOIN change_files theirs ON theirs.change_id > mine.change_id"
        " AND (theirs.photo_id = mine.photo_id OR theirs.path = mine.path OR theirs.path = mine.new_path)"
        " JOIN changes c ON c.id = theirs.change_id"
        " WHERE mine.change_id = ? AND mine.state = 'done' AND theirs.state IN ('planned', 'writing', 'done')"
        " AND c.undone IS NULL AND c.status IN ('planned', 'applied') AND (theirs.state = 'writing' OR (" + internal
        + " AND (theirs.state = 'done' OR c.status = 'planned'))) ORDER BY c.id", (change_id,) + params).fetchall()


def states_with_patches(conn):
    """[(state, files, of them holding a patch's record)] of every journal file, by state. The caller's connection."""
    return conn.execute("SELECT state, COUNT(*), COUNT(patch) FROM change_files GROUP BY state ORDER BY state").fetchall()


def counts(conn, change_ids):
    """{change id: {state: files}} of `change_ids`. The caller's connection."""
    found = {}
    change_ids = list(change_ids)
    for start in range(0, len(change_ids), CHUNK):
        chunk = change_ids[start:start + CHUNK]
        for change_id, state, count in conn.execute(
                "SELECT change_id, state, COUNT(*) FROM change_files WHERE change_id IN (%s)"
                " GROUP BY change_id, state" % ",".join("?" * len(chunk)), chunk):
            found.setdefault(change_id, {})[state] = count
    return found


def finish(db_path, change_id, also=None):
    """Mark change `change_id` applied -- or undone, when it was being undone -- now that
    every file is done, undone or a conflict; its summary takes the files' counts. Returns the counts,
    or None when files are left to write, or ERASED when the change is gone, whatever it wrote (an internal change's files
    all gone, as after a bake that wrote files, or a forward change with no file done and no row left). `also(conn, change id)` runs in the same
    transaction when a forward change finishes (the records of faces whose photo was never written go:
    tagpup.store.face_batches.tidy); an undo keeps everything the change recorded.

    An internal change (INTERNAL) that finishes forward is erased in the same transaction, its files and itself: it was
    the record a crash needed, and it is done (an id is never used again: changes.id is AUTOINCREMENT). One with a file
    that ended a conflict noted one of NOTES keeps that file and is applied (owner, 2026-11-03): the note is the only
    record of a file the bake found damaged (a picture its write changed twice, PICTURE_CHANGED), which the doctor counts
    (noted); its other files go, and no history lists it. Any other conflict -- one that may pass (a scratch file of a
    write that may be under way, a file held by another program, one changed outside) -- goes with the change, as a done
    file does: the next index reads the file and bakes it again, and nothing would count or list it if it were kept.

    A forward change every file was taken out of again, with no row of it left once `also` has run (tidy takes the records
    of faces whose photo has no file done), wrote nothing: it is erased too, and ERASED is returned. History
    lists no change that wrote nothing, and a settle of one a stopped process left planned with no files ends the same
    (#274, #2974). The `failed` of the status CHECK stays for the rows an earlier version left so."""
    def work(conn):
        states = counts(conn, [change_id]).get(change_id, {})
        undoing = conn.execute("SELECT undone FROM changes WHERE id = ?", (change_id,)).fetchone()[0] is not None
        left = states.get("planned", 0) + states.get("writing", 0) + (states.get("done", 0) if undoing else 0)
        if left:
            return None
        if also is not None and not undoing:
            also(conn, change_id)
        summary = json.loads(conn.execute("SELECT summary FROM changes WHERE id = ?",
                                          (change_id,)).fetchone()[0] or "{}")
        summary["undone_files" if undoing else "files"] = dict(sorted(states.items()))
        operation = conn.execute("SELECT operation FROM changes WHERE id = ?", (change_id,)).fetchone()[0]
        if operation in INTERNAL and not undoing:
            notes = ", ".join("?" * len(NOTES))
            conn.execute("DELETE FROM change_files WHERE change_id = ? AND (state != 'conflict' OR note IS NULL"
                         " OR note NOT IN (%s))" % notes, (change_id,) + NOTES)
            if not conn.execute("SELECT 1 FROM change_files WHERE change_id = ? LIMIT 1", (change_id,)).fetchone():
                conn.execute("DELETE FROM changes WHERE id = ?", (change_id,))
                return ERASED
        if not states and not undoing and not conn.execute(
                "SELECT 1 FROM change_rows WHERE change_id = ? LIMIT 1", (change_id,)).fetchone():
            # Every file was taken out again and no row of it is left: nothing was written.
            conn.execute("DELETE FROM changes WHERE id = ?", (change_id,))
            return ERASED
        if undoing:
            conn.execute("UPDATE changes SET status = 'undone', owner = NULL, summary = ? WHERE id = ?",
                         (json.dumps(summary, sort_keys=True), change_id))
        else:
            conn.execute("UPDATE changes SET status = 'applied', applied = ?, owner = NULL, summary = ? WHERE id = ?",
                         (_now(), json.dumps(summary, sort_keys=True), change_id))
        return states

    return db.write_with_connection(db_path, work, label="finish change %d" % change_id)

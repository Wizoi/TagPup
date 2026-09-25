"""Bulk edits that write photo files, recorded file by file in the library's journal
(tagpup.store.file_journal; docs/ARCHITECTURE.md, phase 7.5).

A photo file written in a batch had no record at all of what it held before, and a
batch stopped half way -- a crash, a timeout -- left files written whose rows said
otherwise. Photo managers (darktable, digiKam, Lightroom) do not make a batch of file
writes atomic either; they find where the database and a file disagree and settle it
per file. dpkg's per-item states, and its recovery at start, are the model here:

1. **Plan.** Every file's fields are read (the keywords, captions, dates or identity the
   edit writes), what each is to hold is worked out from what it holds, and the plan --
   each file's fields before and after -- is committed, `planned`, before any file is
   touched. A file that already holds what it is to hold is not in it.
2. **Write**, a file at a time: read it again, and if it no longer holds what the plan
   read, it is a `conflict` -- changed outside since -- reported and never overwritten.
   Otherwise mark it `writing`, write it, and in one transaction record its row
   (tagpup.store.photos.follow_fields, which record_tags' rule generalises) and mark it
   `done`. The change is `applied` once every file is done or a conflict.
3. **Settle**, at start (settle_once): a file a crash left `writing` is settled by what it
   holds -- what the plan read: written again; what it was to hold: marked done and its
   row recorded; neither: a conflict. Files the crash left `planned` are written too, so
   the edit the person asked for is finished. A change another live process is writing
   is left to it (`changes.owner`).
4. **Undo**, by the same states: a file that no longer holds what the change left is
   refused, named, and never overwritten; the rest are written back to what they held,
   their rows following. A rehearsal reads every file and says which would be refused,
   writing nothing.

A rename is a file operation too (Smart Rename): its before and after are the file's
path, and the file is known by its size and modified time, which renaming does not
change. The renames of one edit go together (tagpup.files.names.rename_all), marked
`writing` together, and done together in the transaction that moves their rows.

Files are named by their photo's id in what is reported, never by path: a Smart Rename
puts a caption, and so a name, in a file's name.
"""
import logging
import os
import socket
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from tagpup.core import fields, paths, processes
from tagpup.core.result import Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, field_values, names
from tagpup.store import db, embeddings, file_journal, photos, schema

logger = logging.getLogger(__name__)

#: The steps of writing one file, in order; `_reached` is told of each.
STEPS = ("plan committed", "file writing", "file written", "file recorded")


def _reached(step):
    """A step of a file change is done (STEPS). Nothing happens here; the tests stop the
    change at each step by patching it."""


@dataclass
class Plan:
    """What one file is to hold: `after` ({field: value}), and what the operation reports
    for it once it is written (`detail`); or `skip`, why it is left alone."""
    after: Optional[Dict[str, Any]] = None
    detail: Any = None
    skip: Optional[str] = None


def skip(why):
    return Plan(skip=why)


# ---- Forward ---------------------------------------------------------------------------

def write_fields(library, operation, exiftool_path, photo_paths, read, plan_one, summary=None,
                 unreadable="fail", stop_at_first_error=False):
    """Write fields into many photos as one change named `operation` (see the module's
    docstring). `read` is the fields read from each file; `plan_one(path, held)` says
    what one file is to hold (a Plan) from what it holds, {field: [texts]}.

    A photo that cannot be read fails (`unreadable="skip"`: is skipped), as does one
    whose plan raises. `stop_at_first_error` plans and writes nothing after the first
    photo that fails, as Add to all selected always has. A file left alone -- skipped, or
    holding what it is to hold already -- is not in the change, and its row is made to
    say what the file holds.

    A Result: `changed` the files written, errors the photos that failed and the
    conflicts. details: `change` (its id, or None when nothing was written), `written`
    {path: detail} of each photo written or already holding it, `conflicts` the photos,
    named by id, found changed outside since they were read.
    """
    result = Result(attempted=len(photo_paths))
    written = result.details["written"] = {}
    result.details.update(change=None, conflicts=[])
    settle(library, exiftool_path)
    stored = [paths.stored(p) for p in photo_paths]
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        held = field_values.read(et, stored, read)
        planned, followed = [], []
        for path in stored:
            now = held.get(paths.key(path))
            if now is None or isinstance(now, Exception):
                why = now or "ExifTool answered nothing for it"
                if unreadable == "skip":
                    result.skip(path, why)
                    continue
                result.fail(path, why)
                if stop_at_first_error:
                    break
                continue
            try:
                plan = plan_one(path, now)
            except Exception as e:
                result.fail(path, e)
                if stop_at_first_error:
                    break
                continue
            if plan.skip:
                result.skip(path, plan.skip)
                followed.append((path, now))
                continue
            after = {field: fields.field_values(value) for field, value in plan.after.items()}
            before = {field: now.get(field, []) for field in after}
            if fields.same_fields(before, after):
                written[path] = plan.detail
                followed.append((path, now))
                continue
            planned.append((path, before, after, plan.detail))
        _follow(library, followed)
        if not planned:
            return result
        found = _rows_of(library, [path for path, _b, _a, _d in planned])
        change_id, rows = file_journal.plan(library.path, operation, [
            {"photo_id": (found.get(paths.key(path)) or (None,))[0], "path": path, "before": before, "after": after}
            for path, before, after, _detail in planned], summary)
        result.details["change"] = change_id
        try:
            _reached("plan committed")
            for n, row in enumerate(rows):
                path, detail = planned[n][0], planned[n][3]
                outcome, why = _carry(et, library, row, row.before, row.after, "done", on_failure="withdraw")
                if outcome == "done":
                    result.changed += 1
                    written[path] = detail
                    continue
                result.fail(path, why)
                if outcome == "conflict":
                    result.details["conflicts"].append(row.named())
                elif stop_at_first_error:
                    file_journal.withdraw(library.path, [r.id for r in rows[n + 1:]])
                    break
            file_journal.finish(library.path, change_id)
        except BaseException:
            file_journal.release(library.path, change_id)
            raise
    logger.info("%s: change %d, %s, wrote %d file(s)", library.path, change_id, operation, result.changed)
    return result


def _rows_of(library, photo_paths):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return photos.rows_of(conn, photo_paths)
    finally:
        conn.close()


def _follow(library, followed):
    """Make the rows of files left alone say what the files hold: read, not written, so
    their stamps stay."""
    if not followed:
        return

    def work(conn):
        for path, now in followed:
            photos.follow_fields(conn, path, now)

    try:
        db.write_with_connection(library.path, work, label="rows of %d file(s) read" % len(followed))
    except Exception as e:
        # The files are as they were; a row left stale is what the next scan reads again.
        logger.warning("Could not record what %d file(s) hold: %s", len(followed), e)


def _record(library, row, values, state, stamp=None, wrote=True):
    """Record what a file holds now in its row, and mark it `state`, in one transaction."""
    stat = None
    if wrote:
        try:
            stat = os.stat(row.path)
        except OSError:
            stat = None

    def work(conn):
        photos.follow_fields(conn, row.path, values, stat, stamp)
        file_journal.set_state(conn, row.id, state)

    db.write_with_connection(library.path, work, label="%s: %s" % (row.named(), state))


def _conflict(library, row, why):
    file_journal.mark(library.path, [row.id], "conflict", why)
    return "conflict", "%s: %s" % (row.named(), why)


def _carry(et, library, row, origin, target, finished, on_failure):
    """Bring a file holding `origin` to `target`, and mark it `finished`: forward (before
    to after, done) and in an undo (after to before, undone). A file holding `target`
    already is recorded as it is; one holding neither is a conflict. A write that fails
    leaves a file holding `origin` withdrawn from its change (`on_failure="withdraw"`) or
    a conflict. Returns (outcome, why): `finished`, "conflict" or "failed"."""
    try:
        now = field_values.read_one(et, row.path, list(target))
    except field_values.Unreadable as e:
        return _conflict(library, row, "could not be read: %s" % e)
    if fields.same_fields(now, target):
        # Written already: by a run a crash stopped before it recorded the row.
        _record(library, row, target, finished)
        return finished, None
    if not fields.same_fields(now, origin):
        return _conflict(library, row, "changed since it was read: it holds neither what the change found"
                                       " nor what it was to leave, and is not overwritten")
    file_journal.mark(library.path, [row.id], "writing")
    _reached("file writing")
    stamp = embeddings.stamp_of(row.path)
    try:
        field_values.write(et, row.path, target)
    except Exception as e:
        return _after_failure(et, library, row, origin, target, finished, on_failure, e)
    _reached("file written")
    _record(library, row, target, finished, stamp)
    _reached("file recorded")
    return finished, None


def _after_failure(et, library, row, origin, target, finished, on_failure, error):
    """A write that raised: settled by what the file holds after it."""
    try:
        now = field_values.read_one(et, row.path, list(target))
    except field_values.Unreadable:
        now = None
    if now is not None and fields.same_fields(now, target):
        _record(library, row, target, finished)
        return finished, None
    if now is not None and fields.same_fields(now, origin):
        if on_failure == "withdraw":
            file_journal.withdraw(library.path, [row.id])
            return "failed", error
        file_journal.mark(library.path, [row.id], "conflict", "could not be written: %s" % error)
        return "failed", error
    return _conflict(library, row, "a write failed (%s) and left it holding neither what it held nor what"
                                   " it was to hold" % error)


# ---- Renames -----------------------------------------------------------------------------

@dataclass
class Renamed:
    """What rename() did. `done` is old -> new for every photo asked about, those whose
    name did not change included (tagpup.files.names.rename_all)."""
    done: Dict[str, str]
    moved_aside: Dict[str, str]
    moved: int = 0
    skipped: list = None
    change_id: Optional[int] = None


def _fingerprint(path):
    """What a file is known by wherever a rename left it: its size and modified time,
    which renaming does not change. None when there is no file."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _holds(path, row):
    return path is not None and _fingerprint(path) == row.before


def rename(library, operation, renames, aside, exiftool_path=None, summary=None):
    """Rename photos -- `renames`, old -> new, and first the files in the way, `aside`
    (tagpup.files.names.aside_for) -- as one change named `operation`: planned and
    committed, marked writing, renamed all together or not at all, then marked done in
    the transaction that moves their rows (tagpup.store.photos.move_rows_in). Raises
    names.RenameFailed as rename_all does, every file it put back taken out of the
    change. Returns Renamed."""
    if exiftool_path:
        settle(library, exiftool_path)
    moves = dict(aside)
    moves.update({old: new for old, new in renames.items() if old != new})
    if not moves:
        return Renamed(dict(renames), {}, 0, [])
    found = _rows_of(library, list(moves))
    change_id, rows = file_journal.plan(library.path, operation, [
        {"photo_id": (found.get(paths.key(old)) or (None,))[0], "path": old, "new_path": new,
         "before": _fingerprint(old), "after": _fingerprint(old)} for old, new in moves.items()], summary)
    try:
        _reached("plan committed")
        file_journal.mark(library.path, [row.id for row in rows], "writing")
        _reached("file writing")
        try:
            done, moved_aside = names.rename_all(renames, aside=aside)
        except names.RenameFailed:
            # Every file it could put back is where it was, and was never renamed; one it
            # could not is a conflict.
            _settle_renames(library, rows, forward=True, redo=False)
            file_journal.finish(library.path, change_id)
            raise
        _reached("file written")
        moved, skipped = _record_renames(library, rows, "done", forward=True)
        _reached("file recorded")
        file_journal.finish(library.path, change_id)
    except BaseException:
        file_journal.release(library.path, change_id)
        raise
    return Renamed(done, moved_aside, moved, skipped, change_id)


def _record_renames(library, rows, state, forward):
    """Move the rows of renamed files, and mark the files `state`, in one transaction.
    Returns (rows moved, (old, new) pairs whose new name had rows already)."""
    if not rows:
        return 0, []
    moves = {row.path: row.new_path for row in rows} if forward else {row.new_path: row.path for row in rows}

    def work(conn):
        moved, skipped = photos.move_rows_in(conn, moves)
        for row in rows:
            file_journal.set_state(conn, row.id, state)
        return moved, skipped

    return db.write_with_connection(library.path, work, label="%d renamed file(s): %s" % (len(rows), state))


def _settle_renames(library, rows, forward, redo):
    """Settle renames by where each file is: at its new name (the old, undoing), recorded;
    at its old name, renamed again with `redo`, else taken out of the change (or, undoing,
    a conflict); nowhere, a conflict."""
    finished = "done" if forward else "undone"

    def origin(row):
        return row.path if forward else row.new_path

    def target(row):
        return row.new_path if forward else row.path

    arrived, waiting = [], []
    for row in rows:
        if _holds(target(row), row):
            arrived.append(row)
        elif _holds(origin(row), row):
            waiting.append(row)
        else:
            _conflict(library, row, "not found under either name")
    if waiting and redo:
        # Only onto a name that is free, or that another of these is leaving.
        ready = list(waiting)
        while True:
            leaving = {paths.key(origin(row)) for row in ready}
            blocked = [row for row in ready if os.path.exists(target(row)) and paths.key(target(row)) not in leaving]
            if not blocked:
                break
            for row in blocked:
                ready.remove(row)
                _conflict(library, row, "its %s name is taken" % ("new" if forward else "old"))
        if ready:
            try:
                names.rename_all({origin(row): target(row) for row in ready}, aside={})
                arrived += ready
            except names.RenameFailed as e:
                for row in ready:
                    _conflict(library, row, "could not be renamed: %s" % e.message())
    elif waiting:
        if forward:
            file_journal.withdraw(library.path, [row.id for row in waiting])
        else:
            for row in waiting:
                _conflict(library, row, "could not be renamed back")
    _record_renames(library, arrived, finished, forward)


# ---- Settling at start -------------------------------------------------------------------

def _alive(owner):
    """Is the process named `owner` ("host:pid:start", or "host:pid" as libraries written
    before name it) still running? One on another machine is taken to be: this one
    cannot tell. So is this process: a change it holds is being written on another thread
    (an error that stopped one here released it). A process with the id but another
    start is one that had the id before, and has ended (docs/findings.md, #275)."""
    host, pid, start = file_journal.owner_parts(owner)
    if host != socket.gethostname():
        return True
    if pid is None:
        return False
    if start is None:
        return pid == os.getpid() or processes.is_alive(pid)
    if pid == os.getpid():
        return owner == file_journal.owner()
    now = processes.started(pid)
    if now is not None:
        return now == start
    # Its start could not be read: gone, or not ours to read.
    return processes.is_alive(pid)


def settle(library, exiftool_path):
    """Finish every change of photo files that a process no longer running -- or an error
    in this one -- left half done: each file it left planned or writing is settled by
    what it holds, forward or, for an undo under way, back. Returns how many changes
    were finished. A change that cannot be finished now stays as it is for the next time."""
    finished = 0
    for change in file_journal.unfinished(library.path):
        if change.owner and _alive(change.owner):
            continue
        if not file_journal.claim(library.path, change.id, change.owner):
            continue
        try:
            _finish(library, change, exiftool_path)
        except Exception:
            logger.exception("%s: change %d, left half done, could not be finished now", library.path, change.id)
            file_journal.release(library.path, change.id)
            continue
        finished += 1
        logger.info("%s: change %d (%s), left half done, is finished", library.path, change.id, change.operation)
    return finished


def _finish(library, change, exiftool_path):
    undoing = change.undoing
    pending = [row for row in file_journal.files_of(library.path, change.id)
               if row.state in (("writing", "done") if undoing else ("planned", "writing"))]
    renames = [row for row in pending if row.is_rename]
    if renames:
        _settle_renames(library, renames, forward=not undoing, redo=True)
    changed = [row for row in pending if not row.is_rename]
    if changed:
        with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
            for row in changed:
                if undoing:
                    _carry(et, library, row, row.after, row.before, "undone", on_failure="conflict")
                else:
                    _carry(et, library, row, row.before, row.after, "done", on_failure="conflict")
    file_journal.finish(library.path, change.id)


_settled = set()
_settled_guard = threading.Lock()


def settle_once(library, exiftool_path):
    """settle, the first time this process opens the library: at start. Never raises: a
    library that cannot be settled now is settled by the next process."""
    key = paths.key(library.path)
    with _settled_guard:
        if key in _settled:
            return 0
        _settled.add(key)
    try:
        if not file_journal.unfinished(library.path):
            return 0
        return settle(library, exiftool_path)
    except Exception:
        logger.exception("%s: settling its photo files failed", library.path)
        return 0


# ---- Undo --------------------------------------------------------------------------------

def writes_files(library, change_id):
    """Did change `change_id` of `library` plan photo files?"""
    return file_journal.writes_files(library.path, change_id)


def _undoable(library, change_id, exiftool_path):
    """(the change, [(file, None)] it can put back, [(file, why)] it cannot), reading every
    file it left done; or (None, why it cannot be undone at all, None)."""
    change = file_journal.change(library.path, change_id)
    if change is None:
        return None, "there is no change %d" % change_id, None
    if change.status == "pruned":
        return None, "change %d was pruned: what its files held is gone, so it cannot be undone" % change_id, None
    if change.status == "planned":
        return None, ("change %d is not finished; it is, the next time the library is opened" % change_id), None
    if change.status != "applied":
        return None, "change %d is %s" % (change_id, change.status), None
    done = [row for row in file_journal.files_of(library.path, change_id) if row.state == "done"]
    ready, refused = [], []
    renames = [row for row in done if row.is_rename]
    for row in renames:
        (ready if _holds(row.new_path, row) else refused).append(row)
    refused = [(row, "not found under the name the change gave it") for row in refused]
    # A name to go back to must be free, or be left by another of these.
    while True:
        leaving = {paths.key(row.new_path) for row in ready}
        blocked = [row for row in ready if os.path.exists(row.path) and paths.key(row.path) not in leaving]
        if not blocked:
            break
        for row in blocked:
            ready.remove(row)
            refused.append((row, "its old name is taken"))
    changed = [row for row in done if not row.is_rename]
    if changed:
        if not exiftool_path:
            return None, "undoing change %d rewrites photo files, and needs ExifTool" % change_id, None
        with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
            held = field_values.read(et, [row.path for row in changed],
                                     sorted({f for row in changed for f in row.after}))
        for row in changed:
            now = held.get(paths.key(row.path))
            if now is None or isinstance(now, Exception):
                refused.append((row, "could not be read: %s" % now))
            elif fields.same_fields(now, row.after):
                ready.append(row)
            else:
                refused.append((row, "no longer holds what the change left"))
    return change, ready, refused


def rehearse_undo(library, change_id, exiftool_path):
    """What undoing change `change_id` would do, reading every file and writing none. A
    Result like tagpup.services.journal.rehearse's: details["rehearsal"] has `rows` (the
    files it would put back), `exact` (none would be refused), `differences` (each file
    it would refuse, named, and why), and `files`."""
    result = Result(details={"dry_run": True, "change": change_id, "files": True})
    change, ready, refused = _undoable(library, change_id, exiftool_path)
    if change is None:
        result.refuse(ready)
        result.details["rehearsal"] = {"refused": ready, "rows": 0, "changed": 0, "exact": False,
                                       "differences": [], "derived_exact": True, "files": {}}
        return result
    differences = ["%s: %s" % (row.named(), why) for row, why in refused]
    result.details["rehearsal"] = {
        "refused": None, "rows": len(ready), "changed": len(ready), "exact": not refused,
        "differences": differences, "derived_exact": True,
        "files": {"would be put back": len(ready), "refused": len(refused)}}
    result.details["reveal"] = {"refused": [(row.path, why) for row, why in refused]}
    result.attempted = len(ready) + len(refused)
    if not ready and refused:
        result.refuse("Nothing can be undone: %s" % "; ".join(differences))
    return result


def undo(library, change_id, exiftool_path, apply=False):
    """Undo change `change_id`: a rehearsal unless `apply` (rehearse_undo). Applied, each
    file that still holds what the change left is written back to what it held, its row
    following, through the same states; each that does not is refused, named in the
    Result's errors, and never overwritten. `changed` is the files put back."""
    rehearsal = rehearse_undo(library, change_id, exiftool_path)
    if not apply or rehearsal.refused:
        return rehearsal
    result = Result(attempted=rehearsal.attempted, details={"dry_run": False, "change": change_id, "files": True})
    schema.ensure(library.path)
    change, ready, refused = _undoable(library, change_id, exiftool_path)
    if change is None:
        result.refuse(ready)
        return result
    if not file_journal.begin_undo(library.path, change_id):
        result.refuse("change %d is no longer applied" % change_id)
        return result
    try:
        for row, why in refused:
            _conflict(library, row, "not undone: %s" % why)
            result.fail(row.named(), "not undone: %s" % why)
        renames = [row for row in ready if row.is_rename]
        if renames:
            file_journal.mark(library.path, [row.id for row in renames], "writing")
            _reached("file writing")
            try:
                names.rename_all({row.new_path: row.path for row in renames}, aside={})
            except names.RenameFailed as e:
                _settle_renames(library, renames, forward=False, redo=False)
                result.fail("the renames", e.message())
            else:
                _reached("file written")
                _record_renames(library, renames, "undone", forward=False)
                result.changed += len(renames)
        changed = [row for row in ready if not row.is_rename]
        if changed:
            with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
                for row in changed:
                    outcome, why = _carry(et, library, row, row.after, row.before, "undone", on_failure="conflict")
                    if outcome == "undone":
                        result.changed += 1
                    else:
                        result.fail(row.named(), why)
        file_journal.finish(library.path, change_id)
    except BaseException:
        file_journal.release(library.path, change_id)
        raise
    logger.info("%s: change %d undone, %d file(s) put back", library.path, change_id, result.changed)
    return result

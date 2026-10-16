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
   (tagpup.store.photos.follow_fields) and mark it
   `done` (a save reads its file back whole first and records all of it: `whole`). Then the
   files written are read back, in one read, and a file that does not
   hold quite what was written -- IPTC cuts a keyword at 64 bytes -- is recorded, in the
   journal and its row, as it holds it (_read_back). The change is `applied` once every
   file is done or a conflict.
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
path, and the file is known by its size, modified time and file id, which renaming does
not change and a copy does not share. The renames of one edit go together (tagpup.files.names.rename_all), marked
`writing` together, and done together in the transaction that moves their rows.

Files are named by their photo's id in what is reported, never by path: a Smart Rename
puts a caption, and so a name, in a file's name.

**One at a time** (exclusively). Two changes of the same files at once each planned from
the files as they were before the other, and ExifTool's second write to a file it was
writing failed ("Error creating file: <file>_exiftool_tmp"): Add to all selected clicked
three times quickly wrote one set of tags, reported "wrote 0 file(s)" for another, and
lost the third. So every change of photo files in this process -- plan, write and read
back -- holds one lock, and a second waits for the first and plans from the files as it
left them. One lock for the process, not one per library: two libraries can hold the
same folder, and their writes race the same way. Another process (the CLI) is not held
by it.

**A batch** (more than batches.CHUNK photos; tagpup.services.batches) is one change like any other, its files' states its progress.
It holds a claim (one batch at a time in a process, the rest queued), and every CHUNK files it lets a waiting writer take the lock
and asks whether it is to stop: a stopped batch withdraws the files it did not reach, applies what it wrote (undoable) and says
"stopped after N of M". More than batches.MAX_BATCH photos are refused, but for the tag tree's rewrites (`uncapped`).
"""
import contextlib
import functools
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from tagpup.core import fields, paths
from tagpup.core.result import Conflict, Refused, Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, field_values, lock_owners, metadata, names, shares
from tagpup.services import batches, face_batch, rotation
from tagpup.services import roots as roots_service
from tagpup.store import db, derived, embeddings, file_journal, journal, photos, schema

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


#: Held by every change of photo files in this process (the module's docstring). Re-entrant:
#: a save holds it around its read and the write it makes of what it read.
_one_at_a_time = threading.RLock()


#: How many threads are waiting for the lock now (waiting()). Counted under its own small lock.
_waiting = 0
_waiting_guard = threading.Lock()


#: How many times this thread holds the lock (exclusively nests): a chunk lets others in only when it holds it once.
_depth = threading.local()

#: The longest a job waits, between chunks, for a writer that wants the lock, in seconds.
MOST_WAIT = 5.0


def wait_for_waiters(stop=None):
    """With the lock let go of: wait until the changes of photo files waiting for it have taken their turn (at most MOST_WAIT, or
    until `stop` is set). A lock is not fair, and the thread that has just let go of one takes it again before a waiter has
    woken."""
    spent = 0.0
    while _waiting and spent < MOST_WAIT and not (stop is not None and stop.is_set()):
        time.sleep(0.01)
        spent += 0.01


def let_others_in(stop=None):
    """Between the chunks of a long change that holds the lock once: let go of it while the writers waiting for it take their turn,
    then take it again. A change inside another's lock (held twice) keeps it: the caller above needs it held."""
    if getattr(_depth, "n", 0) != 1 or not _waiting:
        return
    _one_at_a_time.release()
    try:
        wait_for_waiters(stop)
    finally:
        _one_at_a_time.acquire()


@contextlib.contextmanager
def exclusively():
    """Hold the one lock of changes of photo files: around the reads a change is planned
    from and its writes. A second waits for the first. Also a decorator:
    `@file_changes.exclusively()`."""
    global _waiting
    if not _one_at_a_time.acquire(blocking=False):
        logger.info("A change of photo files waits for the one under way to finish")
        with _waiting_guard:
            _waiting += 1
        try:
            _one_at_a_time.acquire()
        finally:
            with _waiting_guard:
                _waiting -= 1
    _depth.n = getattr(_depth, "n", 0) + 1
    try:
        yield
    finally:
        _depth.n -= 1
        _one_at_a_time.release()


def too_many(count, counted="were named"):
    """The sentence refusing a write of photo files that names more than batches.MAX_BATCH photos, or None: the one cap and
    its one sentence (write_fields, batch_exclusively, Smart Rename before it reads a caption, a library view's selection
    before it resolves its ids, a bulk face assignment; the tag tree's rewrites are uncapped). `counted` says what the
    count is of: "were named", "are selected"."""
    if count > batches.MAX_BATCH:
        return "Select up to {:,} photos at a time ({:,} {}).".format(batches.MAX_BATCH, count, counted)
    return None


@contextlib.contextmanager
def batch_exclusively(library, operation, count):
    """exclusively() around a write of `count` photos by a caller that tells the pages' records in the order the files were written:
    a batch takes its turn BEFORE the lock, or it would wait for the batch ahead while holding what that one needs. Refused, nothing
    claimed or waited for, over the cap (too_many)."""
    why = too_many(count)
    if why:
        raise Refused(why)
    with contextlib.ExitStack() as held:
        if count > batches.CHUNK:
            held.enter_context(batches.claim(library, operation, count))
        held.enter_context(exclusively())
        yield


def _exclusive(function):
    @functools.wraps(function)
    def held(*args, **kwargs):
        with exclusively():
            return function(*args, **kwargs)
    return held


#: The Result of the change this thread is writing, so that a stop half-way (RootsChanged) can say what
#: was written and journaled before it stopped.
_running = threading.local()


def _settle_stopped(library, change_id):
    """A change a stop cut short is finished now, not at the next settle: the files never written are taken
    out of it and it is applied for the files that were, so that what was written can be undone at once.
    The pin has been let go by now (the roots changed), so these writes see the roots as they are."""
    if not change_id:
        return
    try:
        left = [row.id for row in file_journal.files_of(library.path, change_id) if row.state == "planned"]
        file_journal.withdraw(library.path, left)
        file_journal.finish(library.path, change_id, also=face_batch.tidy)
    except Exception as problem:
        logger.warning("Could not finish change %s after it was stopped: %s", change_id, problem)


def _pinned(function):
    """Hold one map for the whole change (tagpup.services.roots.pinned): a place moved meanwhile
    changes nothing until it ends, and a change of the library's roots by another process stops
    it. A change that stops half-way is left as a crash leaves one -- its files written are
    recorded, the rest settled the next time the library is opened (settle_once). A change that
    returns a Result answers it as a refusal naming why; a rename raises."""
    @functools.wraps(function)
    def held(library, *args, **kwargs):
        _running.result = None
        try:
            with roots_service.pinned(library):
                return function(library, *args, **kwargs)
        except (roots_service.RootsChanged, roots_service.Unplaced) as stop:
            if function.__name__ == "rename":
                raise
            # What was written and journaled before it stopped is in the Result the change was filling.
            result = getattr(_running, "result", None) or Result()
            _running.result = None
            said = roots_service.stopped(stop)
            if result.changed or result.details.get("change"):
                said += (" Before it stopped %d file(s) were written, recorded as change %s in History, which "
                         "can be undone." % (result.changed, result.details.get("change")))
            result.refuse(said)
            _settle_stopped(library, result.details.get("change"))
            return _nothing_more(result)
    return held


def _nothing_more(result):
    """`result`, refused, with the details of a change that wrote nothing (or what it wrote) in place."""
    for name, empty in (("change", None), ("conflicts", []), ("written", {})):
        result.details.setdefault(name, empty)
    return result


#: Where a photo's file is for a write (file_states).
PRESENT, GONE, AWAY = "present", "gone", "away"

#: What a write says of a photo on a network share that did not answer.
AWAY_WHY = "its folder could not be reached just now (a network share that did not answer), so nothing was written to it"


def file_states(photo_paths):
    """[(path, PRESENT | GONE | AWAY)] of `photo_paths`, in the order given: the one answer to "is this photo's file there
    to be written", by one bounded stat of each (shares.stamp_of: a share is asked within a second, and one found away is
    answered at once for 30 seconds). GONE: the disk or share answered that it is not there. AWAY: on a share that did not
    answer, neither there nor gone. A file that cannot be read is PRESENT: the write meets it. write_fields leaves an AWAY
    photo out as its error, and a bulk delete (tagpup.services.bulk_edit) asks it too."""
    states = []
    for path in photo_paths:
        stamp = shares.stamp_of(path)
        states.append((path, GONE if stamp is None else AWAY if stamp is shares.UNANSWERED else PRESENT))
    return states


def between_chunks(batch):
    """False when the batch is asked to stop; else lets the writers that wait take the lock, and True."""
    if batch.stop.is_set():
        return False
    let_others_in(batch.stop)
    return True


# ---- Forward ---------------------------------------------------------------------------

@roots_service.canonical_args("photo_paths")
def write_fields(library, operation, exiftool_path, photo_paths, read, plan_one, summary=None,
                 unreadable="fail", stop_at_first_error=False, et=None, held=None, whole=False, on_planned=None,
                 uncapped=False, alongside=None):
    """Write fields into many photos as one change named `operation` (see the module's
    docstring). `read` is the fields read from each file; `plan_one(path, held)` says
    what one file is to hold (a Plan) from what it holds, {field: [texts]}.

    A photo that cannot be read fails (`unreadable="skip"`: is skipped), as does one
    whose plan raises. `stop_at_first_error` plans and writes nothing after the first
    photo that fails, as Add to all selected always has. A file left alone -- skipped, or
    holding what it is to hold already -- is not in the change, and its row is made to
    say what the file holds.

    `et` is an ExifTool session the caller has open, used instead of one of this call's
    own; `held`, {paths.key(path): {field: [texts]}}, what the caller read of each file
    in it just now, is the plan's before, and the file is not read again before its
    write. With `whole`, each file written is read back whole (every field the scan reads)
    in that session before it is marked done, and its row records all of it, stamped, in the
    transaction that marks it done (store.photos.follow_fields): a save, whose row was written
    a second time after (#1368); that read is its read back. `on_planned(change_id)` is called once the plan is committed and
    before any file is written (a job records which change it is about to carry out); if it
    raises nothing is written and the change is released, planned.

    A Result: `changed` the files written, errors the photos that failed and the
    conflicts. details: `change` (its id, or None when nothing was written), `written`
    {path: detail} of each photo written or already holding it, `conflicts` the photos,
    named by id, found changed outside since they were read.

    `alongside` (tagpup.services.face_batch.Work): rows the change writes beside the files, recorded in the plan's transaction and
    written in the one that marks each file `done` (a bulk assignment's faces); a photo it names is in the change even when its
    file holds what it is to hold already, and then only its rows are written.

    More than batches.MAX_BATCH photos are refused (too_many), nothing read, unless `uncapped` (the tag tree's rewrites only); more than
    batches.CHUNK are a batch (the module's docstring): refused, nothing read, when batches.MOST_WAITING wait already, or when it
    is cancelled before its turn.
    """
    why = None if uncapped else too_many(len(photo_paths))
    if why:
        return _refused_whole(photo_paths, why)
    with contextlib.ExitStack() as turn:
        if len(photo_paths) > batches.CHUNK:
            try:
                # Not waited for by a caller that holds the lock (_depth): the batch ahead needs it.
                turn.enter_context(batches.claim(library, operation, len(photo_paths), wait=getattr(_depth, "n", 0) == 0))
            except (Conflict, Refused) as why:
                refused = _refused_whole(photo_paths, str(why))
                refused.details["cancelled"] = isinstance(why, Refused)     # cancelled while it waited, not a queue that is full
                return refused
        return _write_session(library, operation, exiftool_path, photo_paths, read, plan_one, summary, unreadable,
                              stop_at_first_error, et, held, whole, on_planned, alongside)


def _refused_whole(photo_paths, why):
    result = Result(attempted=len(photo_paths))
    result.refuse(why)
    return _nothing_more(result)


@_exclusive
@_pinned
def _write_session(library, operation, exiftool_path, photo_paths, read, plan_one, summary, unreadable,
                   stop_at_first_error, et, held, whole, on_planned, alongside=None):
    if len(photo_paths) > batches.CHUNK and batches.current() is None:
        raise RuntimeError("%d photos are written without a batch's claim (write_fields takes it)" % len(photo_paths))
    if et is None:
        # Dead for good after a command timed out: a share that went away costs its deadline once, not once a file.
        with exiftool_session.ExifToolSession(executable=exiftool_path) as session:
            return _write_fields(exiftool_session.StaysDead(session), library, operation, exiftool_path, photo_paths, read, plan_one, summary,
                                 unreadable, stop_at_first_error, held, whole, on_planned, alongside)
    return _write_fields(et, library, operation, exiftool_path, photo_paths, read, plan_one, summary,
                         unreadable, stop_at_first_error, held, whole, on_planned, alongside)


def _write_fields(et, library, operation, exiftool_path, photo_paths, read, plan_one, summary, unreadable,
                  stop_at_first_error, fresh, whole, on_planned=None, alongside=None):
    result = Result(attempted=len(photo_paths))
    _running.result = result
    written = result.details["written"] = {}
    result.details.update(change=None, conflicts=[])
    settle(library, exiftool_path)
    problem = alongside.verify(library) if alongside is not None else None
    if problem:
        result.refuse(problem)
        return result
    stored = [paths.stored(p) for p in photo_paths]
    batch = batches.current()
    if fresh is None:
        # A photo on a share that does not answer is its error at once: read, it would cost ExifTool's whole deadline
        # (five minutes) and hold the lock and the request that long (#1492). What the caller read just now answered.
        away = {paths.key(path) for path, state in file_states(stored) if state == AWAY}
        # A batch asked to stop while it reads the files it plans from stops at the next batch of reads (#1200).
        held = field_values.read(et, [path for path in stored if paths.key(path) not in away], read,
                                 stop=batch.stop if batch else None)
        held.update({key: field_values.Unreadable(AWAY_WHY) for key in away})
    else:
        held = fresh
    if batch is not None and batch.stop.is_set():
        return _stopped_before_writing(result)
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
        if fields.reads_same(before, after) and not (alongside is not None and alongside.wants(path)):
            written[path] = plan.detail
            followed.append((path, now))
            continue
        planned.append((path, before, after, plan.detail))
    _follow(library, followed)
    wrote, read_whole = {}, ({} if whole else None)
    if not planned:
        return result
    if batch is not None and batch.stop.is_set():
        return _stopped_before_writing(result)
    found = _rows_of(library, [path for path, _b, _a, _d in planned])
    change_id, rows = file_journal.plan(library.path, operation, [
        {"photo_id": (found.get(paths.key(path)) or (None,))[0], "path": path, "before": before, "after": after}
        for path, before, after, _detail in planned], summary,
        also=None if alongside is None else (lambda conn, new_id: alongside.record(conn, new_id, [each[0] for each in planned])))
    result.details["change"] = change_id
    if batch is not None:
        batch.change_id = change_id
    try:
        _reached("plan committed")
        if on_planned is not None:
            on_planned(change_id)
        for n, row in enumerate(rows):
            if n and n % batches.CHUNK == 0 and not between_chunks(batch):
                file_journal.withdraw(library.path, [r.id for r in rows[n:]])
                result.details["stopped"] = {"after": n, "of": len(rows)}
                break
            path, detail = planned[n][0], planned[n][3]
            # Read just now, in this session, by the caller: that is the check.
            now = planned[n][1] if fresh is not None else None
            outcome, why = _carry(et, library, row, row.before, row.after, "done", on_failure="withdraw",
                                  wrote=wrote, now=now, whole=read_whole)
            if outcome == "done":
                if not fields.reads_same(planned[n][1], planned[n][2]):
                    result.changed += 1       # a file held what it was to hold: its rows alone were written
                if isinstance(detail, dict) and row.followed:
                    detail["faces_turned"] = row.followed
                written[path] = detail
                continue
            result.fail(path, why)
            if outcome == "conflict":
                result.details["conflicts"].append(row.named())
            elif stop_at_first_error:
                file_journal.withdraw(library.path, [r.id for r in rows[n + 1:]])
                break
        for row in _read_back(et, library, wrote, read_whole):
            # ExifTool said it wrote it, and it holds what it held (#276).
            result.changed -= 1
            written.pop(row.path, None)
            result.fail(row.path, "ExifTool reported it written, but it holds what it held before")
        file_journal.finish(library.path, change_id, also=face_batch.tidy)
        stopped = result.details.get("stopped")
        if stopped:
            result.refuse("Stopped as asked after %d of %d photos: %d file(s) were written, recorded as change %d in History, "
                          "which can be undone; the rest were not written."
                          % (stopped["after"], stopped["of"], result.changed, change_id))
    except BaseException as stopped:
        # The files recorded done so far are read back all the same: settling reads
        # back only the files it writes (docs/findings.md, #286).
        _read_back(et, library, wrote)
        if isinstance(stopped, KeyboardInterrupt):
            # Stopped by the person (Ctrl+C, Ctrl+Break in the CLI): what is written stays, applied and undoable; the files
            # not reached are taken out, not written by whatever opens the library next (#1493). A crash is settled forward.
            _settle_stopped(library, change_id)
            # What the journal holds done, not what this loop had counted: the file under way may be done already.
            try:
                stopped.written = sum(1 for row in file_journal.files_of(library.path, change_id) if row.state == "done")
            except Exception:
                stopped.written = None
            stopped.change = change_id
        file_journal.release(library.path, change_id)
        raise
    logger.info("%s: change %d, %s, wrote %d file(s)", library.path, change_id, operation, result.changed)
    return result


def _stopped_before_writing(result):
    """`result`, refused: a batch asked to stop while it read and planned its files; nothing is planned, nothing written."""
    result.refuse("Stopped as asked before anything was written.")
    result.details["cancelled"] = True
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
        batch = derived.Batch(conn)   # the tag tree read once for the whole loop, in this transaction
        for path, now in followed:
            photos.follow_fields(conn, path, now, batch=batch)

    try:
        db.write_with_connection(library.path, work, label="rows of %d file(s) read" % len(followed))
    except Exception as e:
        # The files are as they were; a row left stale is what the next scan reads again.
        logger.warning("Could not record what %d file(s) hold: %s", len(followed), e)


def _followers(row, state):
    """What a file's change brings besides the field, to be done in the transaction that marks the file `state`: a rotation's face
    boxes and vectors, a bulk assignment's faces. The ONE place that lists them, for a file marked done or undone and for one taken
    out of its change after it was done (_read_back)."""
    return [each for each in (rotation.follower(row, state), face_batch.follower(row, state)) if each is not None]


def _stat(path):
    """The file's os.stat, or None when it cannot be taken."""
    try:
        return os.stat(path)
    except OSError:
        return None


def _record(library, row, values, state, stamp=None, wrote=True, after=None, whole=None, stat=None):
    """Record what a file holds now in its row, and mark it `state`, in one transaction;
    with `after`, the journal's after too (file_journal.set_after); with `whole`, the file
    as read back whole (store.photos.follow_fields), and `stat`, its stat taken before that
    read (_carry). Returns the file's stat, as recorded."""
    if wrote and stat is None:
        stat = _stat(row.path)
    follow = _followers(row, state)

    def work(conn):
        # Before the row is recorded: recording can itself change faces (a guess the tag makes), and a follower that accounts
        # for the faces it writes must start from the faces as they were.
        for each in follow:
            getattr(each, "start", lambda _conn: None)(conn)
        photos.follow_fields(conn, row.path, values, stat, stamp, whole=whole)
        for each in follow:
            each(conn)
        if after is not None:
            file_journal.set_after(conn, row.id, after)
        file_journal.set_state(conn, row.id, state)

    db.write_with_connection(library.path, work, label="%s: %s" % (row.named(), state))
    return stat


def _read_back(et, library, wrote, read=None):
    """Record what each file written holds, where ExifTool did not keep a value as it was
    written: IPTC:Keywords cut at 64 bytes, a letter outside Latin-1 as "?", "1.50" as
    1.5. The file was recorded done holding what was asked for, and undoing the change
    refused it for holding neither (docs/findings.md, #273). `wrote` is {file id: (row,
    its stat as recorded)}; the files are read in one read for them all. A file whose stat
    is not what was recorded was written by another program since, and is left as the
    journal has it, for an undo to refuse.

    A file that holds what it held before, though ExifTool said it wrote it, changed
    nothing: it is taken out of the change, its row still made to say what it holds,
    and returned, so that the write does not report it changed (#276). `read` is
    {paths.key(path): what it holds} of the files read back whole already (_read_whole),
    which are not read again. Never raises: the files are written either way. Returns the
    rows of the files that did not change."""
    unchanged = []
    if not wrote:
        return unchanged
    written = list(wrote.values())
    held = dict(read or {})
    unread = [row for row, _stat in written if paths.key(row.path) not in held]
    if unread:
        try:
            held.update(field_values.read(et, [row.path for row in unread],
                                          sorted({field for row in unread for field in row.after})))
        except Exception as e:
            logger.warning("%s: could not read back %d file(s) written: %s", library.path, len(unread), e)
    for row, stat in written:
        now = held.get(paths.key(row.path))
        if now is None or isinstance(now, Exception):
            continue
        kept = {field: now.get(field, []) for field in row.after}
        if fields.same_fields(kept, row.after):
            continue
        try:
            current = os.stat(row.path)
        except OSError:
            continue
        if stat is None or (current.st_mtime_ns, current.st_size) != (stat.st_mtime_ns, stat.st_size):
            continue
        differ = sorted(field for field in row.after if not fields.same_values(kept[field], row.after[field]))
        same_as_before = fields.reads_same(kept, row.before) and not fields.reads_same(kept, row.after)

        def work(conn, row=row, kept=kept, same_as_before=same_as_before):
            photos.follow_fields(conn, row.path, kept)
            if same_as_before:
                # Taken out of its change after it was marked done: what the done brought (its faces, its boxes) goes back in
                # this transaction, or the rows would describe a write the file never took.
                for each in _followers(row, "undone"):
                    each(conn)
                file_journal.withdraw_in(conn, [row.id])
            else:
                file_journal.set_after(conn, row.id, kept)

        try:
            db.write_with_connection(library.path, work, label="%s: read back" % row.named())
        except Exception as e:
            logger.warning("%s: could not record what %s holds: %s", library.path, row.named(), e)
            continue
        if same_as_before:
            unchanged.append(row)
            logger.warning("%s: %s holds what it held before its write; taken out of the change",
                           library.path, row.named())
            continue
        logger.warning("%s: %s does not hold %s quite as written; recorded as it holds it",
                       library.path, row.named(), ", ".join(differ))
    return unchanged


def _conflict(library, row, why):
    file_journal.mark(library.path, [row.id], "conflict", why)
    return "conflict", "%s: %s" % (row.named(), why)


def _carry(et, library, row, origin, target, finished, on_failure, wrote=None, now=None, whole=None):
    """Bring a file holding `origin` to `target`, and mark it `finished`: forward (before
    to after, done) and in an undo (after to before, undone). A file holding `target`
    already is recorded as it is; one holding neither is a conflict. A write that fails
    leaves a file holding `origin` withdrawn from its change (`on_failure="withdraw"`) or
    a conflict. A file written goes in `wrote`, for _read_back. `now` is what the file
    holds when the caller has just read it in this session; otherwise it is read here.
    `whole`, a dict, asks for the file written to be read back whole and recorded so
    (_read_whole). Returns (outcome, why): `finished`, "conflict" or "failed"."""
    seen = None
    if now is None:
        # The stat before the read: a file found holding `target` is recorded stamped as it was before what was read of it.
        seen = _stat(row.path)
        try:
            now = field_values.read_one(et, row.path, list(target))
        except field_values.Unreadable as e:
            return _conflict(library, row, "could not be read: %s" % e)
    if fields.reads_same(now, target):
        # Written already: by a run a crash stopped before it recorded the row, whose
        # stamp from before the write was recorded with it (#265).
        _record_held(library, row, now, target, finished, row.stamp, seen)
        return finished, None
    if not fields.reads_same(now, origin):
        return _conflict(library, row, "changed since it was read: it holds neither what the change found"
                                       " nor what it was to leave, and is not overwritten")
    stamp = row.stamp = embeddings.stamp_of(row.path)
    file_journal.writing(library.path, row.id, stamp)
    _reached("file writing")
    try:
        field_values.write(et, row.path, target)
    except Exception as e:
        return _after_failure(et, library, row, origin, target, finished, on_failure, e)
    _reached("file written")
    # The stat before the read back: a change another program makes after it leaves the row older than its file, which
    # the next scan reads again; taken after, it stamped what was read before the change as the newer file.
    stat = _stat(row.path)
    stat = _record(library, row, target, finished, stamp, stat=stat,
                   whole=_read_whole(et, row, whole) if whole is not None else None)
    if wrote is not None:
        wrote[row.id] = (row, stat)
    _reached("file recorded")
    return finished, None


def _read_whole(et, row, read):
    """The file `row` just wrote, read back whole: its raw_metadata in the shape the indexer
    records (metadata.raw_metadata), what it holds of the change's fields put in `read`
    (for _read_back). None, and nothing put in `read`, when the read fails or ExifTool reports
    an error reading it: the row then records the fields written, stamped only where it
    described the file before."""
    records = {}
    try:
        held = field_values.read(et, [row.path], list(row.after), fields.METADATA_FIELDS, records)
    except Exception as e:
        logger.warning("%s: could not read it back whole: %s", row.named(), e)
        return None
    now, record = held.get(paths.key(row.path)), records.get(paths.key(row.path))
    if now is None or isinstance(now, Exception) or record is None or any(record.get(k) for k in metadata.READ_ERROR_KEYS):
        return None
    read[paths.key(row.path)] = now
    return metadata.raw_metadata(record)


def _record_held(library, row, now, target, finished, stamp, stat):
    """Record a file found holding `target` already, as it holds it: a value ExifTool does
    not keep as written ("1.50" read as 1.5) is recorded as read, in the row and, going
    forward, as the journal's after, as _read_back records a file just written. `stamp`
    is the file's from just before a write of this change, when one was made; `stat`, the file's
    taken before `now` was read, or None when the caller read it (then it is taken now)."""
    held = {field: now.get(field, []) for field in target}
    after = None
    if finished == "done" and not fields.same_fields(held, target):
        after = held
        row.after = held
    # A file that was not to change (its faces were) is not stamped as written.
    wrote = not fields.reads_same(row.before, row.after)
    _record(library, row, held, finished, stamp, wrote=wrote, after=after, stat=stat if wrote else None)


def _after_failure(et, library, row, origin, target, finished, on_failure, error):
    """A write that raised: settled by what the file holds after it."""
    seen = _stat(row.path)
    try:
        now = field_values.read_one(et, row.path, list(target))
    except field_values.Unreadable:
        now = None
    if now is not None and fields.reads_same(now, target):
        _record_held(library, row, now, target, finished, row.stamp, seen)
        return finished, None
    error = lock_owners.explain(error, row.path)   # after a failure only: who holds the file, if it is held
    if now is not None and fields.reads_same(now, origin):
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
    which renaming does not change, and its file id (st_ino: NTFS's file index), which
    renaming on one volume keeps too and a copy never shares. A copy keeps the size and
    modified time -- Explorer's, robocopy's, shutil.copy2's -- and was taken for the
    file (docs/findings.md, #269). None when there is no file; the file id is 0 where
    the volume gives none, and rename() refuses such a volume (owner, 2026-10-15)."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino}


def _holds(path, row):
    """Is the file at `path` the one `row` renames: its size, modified time and file id
    are what the plan recorded."""
    if path is None or not row.before:
        return False
    return _fingerprint(path) == row.before


@roots_service.canonical_args(both=("renames", "aside"))
@_exclusive
@_pinned
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
    fingerprints = {old: _fingerprint(old) for old in moves}
    if any(found and not found["ino"] for found in fingerprints.values()):
        # A renamed file is known by its file id; without one a copy cannot be told from it.
        raise names.RenameFailed("this volume gives its files no file id, so a renamed file could not be told "
                                 "from a copy of it", [])
    found = _rows_of(library, list(moves))
    change_id, rows = file_journal.plan(library.path, operation, [
        {"photo_id": (found.get(paths.key(old)) or (None,))[0], "path": old, "new_path": new,
         "before": fingerprints[old], "after": fingerprints[old]} for old, new in moves.items()], summary)
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
    a conflict); nowhere, a conflict. Under both names -- a copy in the way that the plan
    cannot tell from it -- a conflict, the files left as they are, and so is the move
    aside of the file in its way, which was only to make room for it (#269)."""
    finished = "done" if forward else "undone"

    def origin(row):
        return row.path if forward else row.new_path

    def target(row):
        return row.new_path if forward else row.path

    arrived, waiting, doubtful = [], [], set()
    for row in rows:
        if paths.same(origin(row), target(row)):
            # A rename of the case alone: both names are the one file, and the name the
            # folder lists it by says which it has (#283).
            if not _holds(origin(row), row):
                _conflict(library, row, "not found under either name")
            elif paths.spelled_as(target(row)):
                arrived.append(row)
            else:
                waiting.append(row)
            continue
        # Where it was first: a file there too at its new name is a copy, or in doubt.
        at_origin, at_target = _holds(origin(row), row), _holds(target(row), row)
        if at_origin and at_target:
            _conflict(library, row, "found under both names, one of them a copy it cannot be told from;"
                                    " neither is renamed")
            doubtful.add(paths.key(target(row)))
        elif at_origin:
            waiting.append(row)
        elif at_target:
            arrived.append(row)
        else:
            _conflict(library, row, "not found under either name")
    # A move aside of the file in the way of one left where it is.
    making_room = [row for row in waiting if paths.key(origin(row)) in doubtful]
    if making_room:
        waiting = [row for row in waiting if row not in making_room]
        if forward:
            file_journal.withdraw(library.path, [row.id for row in making_room])
        else:
            for row in making_room:
                _conflict(library, row, "left where it is: the file to take its place was found under both names")
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

@_exclusive
def settle(library, exiftool_path):
    """Finish every change of photo files that a process no longer running -- or an error
    in this one -- left half done: each file it left planned or writing is settled by
    what it holds, forward or, for an undo under way, back. Returns how many changes
    were finished. A change that cannot be finished now is logged, released and left as
    it is for the next time: it does NOT raise, so a caller that must know asks the
    journal afterwards (tagpup.store.file_journal.unfinished)."""
    finished = 0
    for change in file_journal.unfinished(library.path):
        if change.owner and file_journal.owner_alive(change.owner):
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
            wrote = {}
            for row in changed:
                if undoing:
                    _carry(et, library, row, row.after, row.before, "undone", on_failure="conflict")
                else:
                    _carry(et, library, row, row.before, row.after, "done", on_failure="conflict", wrote=wrote)
            _read_back(et, library, wrote)
    file_journal.finish(library.path, change.id, also=face_batch.tidy)


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
    # The journal's one account of what may be undone: a newer change of the same files
    # in the way among it (tagpup.store.journal.refusal).
    reasons = journal.refusals(library.path, [change_id]).get(change_id)
    if reasons:
        return None, "; ".join(reasons), None
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
            elif fields.reads_same(now, row.after):
                ready.append(row)
            else:
                refused.append((row, "no longer holds what the change left"))
    return change, ready, refused


@_exclusive
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


@_exclusive
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
                    result.details["faces_left"] = result.details.get("faces_left", 0) + row.faces_left
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

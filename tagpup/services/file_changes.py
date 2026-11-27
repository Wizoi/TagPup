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
   files written are read back, in one read a chunk (batches.CHUNK), and a file that does not
   hold quite what was written -- IPTC cuts a keyword at 64 bytes -- is recorded, in the
   journal and its row, as it holds it (_read_back). The change is `applied` once every
   file is done or a conflict. A writer that waits takes the lock between two chunks
   (let_others_in); a file the change has planned and not yet written is refused to it, as
   its error (unsettled), or the change would find it changed and its edit be lost on it.
3. **Settle**, at start (settle_once): a file a crash left `writing` is settled by what it
   holds -- what the plan read: written again; what it was to hold: marked done and its
   row recorded; neither: a conflict. A settle of more than batches.CHUNK files is a batch, carried out a chunk at a
   time as a write is (_finish): a writer that waits takes the lock between chunks, and Cancel (or a hand-over) stops
   it there, the rest left as they are for the next start; a writer let in between two chunks leaves a photo the settle
   has still to write alone, as its error (unsettled), or the settle would find it changed and the cut-off edit be lost
   on it as a conflict. The server settles on a thread of its own, so the first open returns at once
   (settle_once's `on_a_thread`; DECISIONS 2026-11-07): the changes are taken and their photos refused before it
   returns, and the thread settles each as a batch, whatever its count (listed, cancelled, stopped by a hand-over); one
   it cannot finish (cancelled, a line that is full, an error) is put off for the next start, its photos still refused
   (_put_aside). A file a patch was writing in place is settled by its bytes first, against the
   record the patch saved before its first byte (_by_bytes): a patch this process cut short is put back and written
   again, one another program wrote over is a conflict, one that cannot be read stays `writing` with its record. Files the crash left `planned` are written too, so
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

**A share that stops answering** in a write (ExifTool does not answer for a file) stops it the same way, at that file, for any
write: never a conflict, since the file under way holds what it held or what it was to hold (_Stalled, _share_stopped). The
scratch file ExifTool leaves beside it, which refuses every later write of it, is removed by the next write of it, which then
tries once more (removed_scratch, #2180).

**A write whose picture changed** (field_values.PictureChanged, the picture check of #2377): its new file was removed and the
photo is as it was, so it is written once more, logged and counted (`pictures_refused` in the result's details); a second
is the photo's error, by the one failure path (_after_failure): taken out of a forward change, a conflict noted
file_journal.PICTURE_CHANGED in a settle or an undo.
"""
import contextlib
import functools
import logging
import os
import threading
import time
from dataclasses import dataclass
from stat import S_IWRITE
from typing import Any, Dict, Optional

from tagpup.core import fields as core_fields, paths
from tagpup.core.result import Conflict, Refused, Result
from tagpup.files import exiftool_session, field_values, images, in_place, lock_owners, names, shares
from tagpup.services import baking, batches, face_batch, rotation
from tagpup.services import roots as roots_service
from tagpup.store import db, derived, file_journal, photos, schema

logger = logging.getLogger(__name__)

#: The steps of writing one file, in order; `_reached` is told of each.
STEPS = ("plan committed", "file writing", "patch saved", "scratch written", "file written", "file recorded")
#: The step only a patch in place reaches (field_values.write's `saving`): ExifTool's rewrite saves no record.
PATCH_SAVED = "patch saved"
#: The step of ExifTool's rewrite once its new file is beside the photo, before the picture check (field_values._landed).
SCRATCH_WRITTEN = "scratch written"


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


def _settle_stopped(library, change_id, fields=None, result=None):
    """A change a stop cut short is finished now, not at the next settle: the files never written are taken
    out of it and it is applied for the files that were, so that what was written can be undone at once.
    The pin has been let go by now (the roots changed), so these writes see the roots as they are.

    With `fields`, the session the change wrote through (a Ctrl+C), the one file it left `writing` is read in it --
    after the write it was given, if any -- and settled by what it holds, never written: done if it holds what it
    was to hold, as a crash settle records it (_held_or_conflict); taken out if it holds what it held (#1507). A Ctrl+C
    in the CLI often ends ExifTool too (one console): a file that cannot be read now stays `writing` and the change
    unfinished, for the next open to settle as a crash leaves it (#1508). Returns how many files are left so. A change that
    wrote nothing is erased by finishing it (file_journal.finish): `result`'s `change` then says None."""
    if not change_id:
        return 0
    left = 0
    try:
        rows = file_journal.files_of(library.path, change_id)
        file_journal.withdraw(library.path, [row.id for row in rows if row.state == "planned"])
        for row in rows:
            if fields is not None and row.state == "writing":
                settled = _held_or_conflict(fields, library, row, row.before, row.after, "done", unread_stays=True)
                if settled is None:
                    file_journal.withdraw(library.path, [row.id])
                elif settled[0] == "writing":
                    left += 1
        if not left:
            finish_forward(library, change_id, result)
    except Exception as problem:
        logger.warning("Could not finish change %s after it was stopped: %s", change_id, problem)
    return left


def _pinned(function):
    """Hold one map for the whole change (tagpup.services.roots.pinned): a place moved meanwhile
    changes nothing until it ends, and a change of the library's roots by another process stops
    it. A change that stops half-way is left as a crash leaves one -- its files written are
    recorded, the rest settled the next time the library is opened (settle_once). A change that
    returns a Result answers it as a refusal naming why; a rename raises."""
    @functools.wraps(function)
    def held(library, *args, **kwargs):
        _running.result, _running.internal = None, False
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
            # Settled first: a change that wrote nothing is erased, and the sentence names no change.
            _settle_stopped(library, result.details.get("change"), result=result)
            if result.changed or result.details.get("change"):
                said += " Before it stopped %d file(s) were written%s." % (
                    result.changed, _kept_as(getattr(_running, "internal", False), result.details.get("change")))
            result.refuse(said)
            if isinstance(stop, roots_service.RootsChanged):
                result.details["roots_stopped"] = True     # a run of many writes ends here (bake_after_index)
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


#: The details key of a write's files its writer refused before a byte was written (write_fields).
WRITER_REFUSED = "writer_refused"


def leave_out_clips(result, photo_paths, unreadable="fail", read=None):
    """The paths of `photo_paths` that are not clips (images.is_clip), in order, and the clips a journaled write of
    `read`, the fields it reads of each file, may patch: one reading only the fields of what a clip's write may change
    (field_values.writes: its tags, title and the name it had before its first rename; DECISIONS 2026-11-05, M7) and those its tags are
    read from (fields.TAG_SOURCE_FIELDS). Any other clip is not written (images.CLIP_NOT_EDITED): it is the write's error, with that sentence -- or, `unreadable="skip"` (a
    write over every photo of a camera or a folder that leaves out what it cannot write), skipped with it -- read for
    nothing, planned and journaled for nothing, and never the error that stops a write at its first
    (`stop_at_first_error`): the photos named beside it are written. A time shift reads a clip's EXIF dates, so it
    leaves every clip out. The one place of the refusal, asked by every writer of photo files before it reads one (write_fields,
    which gives `read`; file_only.write_fields, which does not: a clip of a folder the library does not hold has no
    journal to settle a patch a crash cut short) and a Smart Rename (tagpup.services.photos.smart_rename) before it keeps
    a name, which leaves out a clip of a folder the library does not hold; a save of a clip is refused before its rename."""
    photos = []
    for path in photo_paths:
        if images.is_clip(path) and not _clip_admitted(path, read):
            (result.skip if unreadable == "skip" else result.fail)(path, images.CLIP_NOT_EDITED)
        else:
            photos.append(path)
    return photos


def _clip_admitted(path, read):
    """May a journaled write reading `read` patch the clip at `path` (leave_out_clips)?"""
    named = core_fields.fields_of(field_values.writes(path))
    return bool(named) and read is not None and set(read) <= named | frozenset(core_fields.TAG_SOURCE_FIELDS)


#: What a forward write's file found holding its after since the plan comes to (_held_or_conflict's `held_out`): out of
#: the change, its row recorded.
OUT = "out"


class _Stalled(Exception):
    """ExifTool did not answer for a file of a forward write (field_values.NotAnswered: a share gone away, a disk that
    stopped). A stop, as a Ctrl+C is, never a conflict: ExifTool writes beside a file and renames, so the file under way
    holds what it held or what it was to hold, and the files after it were never touched (#1921)."""


def file_states(photo_paths):
    """[(path, PRESENT | GONE | AWAY)] of `photo_paths`, in the order given: the one answer to "is this photo's file there
    to be written", by one bounded stat of each (shares.stamp_of: a share is asked within a second, and one found away is
    answered at once for 30 seconds). GONE: the disk or share answered that it is not there. AWAY: on a share that did not
    answer, neither there nor gone. A file that cannot be read is PRESENT: the write meets it. write_fields leaves an AWAY
    photo out as its error."""
    return [(path, state) for path, state, _stamp in file_stamps(photo_paths)]


def file_stamps(photo_paths):
    """[(path, state, stamp)]: file_states, with the (mtime, size) the same stat found (None when it is not known: not
    there, not readable, away). A bulk delete (tagpup.services.bulk_edit) reads each file's size from it."""
    found = []
    for path in photo_paths:
        stamp = shares.stamp_of(path)
        state = GONE if stamp is None else AWAY if stamp is shares.UNANSWERED else PRESENT
        found.append((path, state, stamp if isinstance(stamp, tuple) else None))
    return found


def between_chunks(batch):
    """False when the batch is asked to stop; else lets the writers that wait take the lock, and True."""
    if batch.stop.is_set():
        return False
    let_others_in(batch.stop)
    return True


# ---- Forward ---------------------------------------------------------------------------

@roots_service.canonical_args("photo_paths")
def write_fields(library, operation, exiftool_path, photo_paths, read, plan_one, summary=None,
                 unreadable="fail", stop_at_first_error=False, fields=None, held=None, whole=False, on_planned=None,
                 uncapped=False, alongside=None, on_chunk=None, patient=False, unpooled=None):
    """Write fields into many photos as one change named `operation` (see the module's
    docstring). `read` is the fields read from each file; `plan_one(path, held)` says
    what one file is to hold (a Plan) from what it holds, {field: [texts]}.

    A photo that cannot be read fails (`unreadable="skip"`: is skipped), as does one
    whose plan raises. `stop_at_first_error` plans and writes nothing after the first
    photo that fails, as Add to all selected always has. A file left alone -- skipped, or
    holding what it is to hold already -- is not in the change, and its row is made to
    say what the file holds.

    `fields` is the caller's Fields (field_values.opened), used instead of one of this call's
    own; `held`, {paths.key(path): {field: [texts]}}, what the caller read of each file
    through it just now, is the plan's before, and the file is not read again before its
    write. With `whole`, each file written is read back whole (every field the scan reads)
    in that session before it is marked done, and its row records all of it, stamped, in the
    transaction that marks it done (store.photos.follow_fields): a save, whose row was written
    a second time after (#1368); that read is its read back. `on_planned(change_id)` is called once the plan is committed and
    before any file is written (a job records which change it is about to carry out); if it
    raises nothing is written and the change is released, planned. `on_chunk(done, of)` is called as each batches.CHUNK
    of the change's `of` files has been carried out (`done` so far), before the next chunk begins: a long write is heard
    to move (the index's bake, tagpup.services.bake_after_index), and one that a share stopped mid-chunk says nothing
    more. It writes nothing; one that raises ends the write as a crash does (the rest settled forward at the next open).

    A Result: `changed` the files written, errors the photos that failed and the
    conflicts. details: `change` (its id, or None when the change is gone: nothing was written, or an internal one's files are done and it is erased), `written`
    {path: detail} of each photo written or already holding it, `conflicts` the photos,
    named by id, found changed outside since they were read; `read_only`, when one failed so, the paths of the photos
    whose write failed and whose file is marked read-only: a failure no retry changes, where every other (a file held
    open by another program, lock_owners) may pass the next time.

    `alongside` (tagpup.services.face_batch.Work): rows the change writes beside the files, recorded in the plan's transaction and
    written in the one that marks each file `done` (a bulk assignment's faces); a photo it names is in the change even when its
    file holds what it is to hold already, and then only its rows are written.

    `unpooled` (tagpup.services.face_batch.Unpooled): the caller asks which faces each file's record took out of identity work
    (the guesses its tag made, a bulk assignment's faces), counted in the transaction that marks it done, for the cached
    Identify Faces grids. A file the change does not write (its row followed as it is) is not counted: the fingerprint's
    generation leaves the cache stale.

    More than batches.MAX_BATCH photos are refused (too_many), nothing read, unless `uncapped` (the tag tree's rewrites, and
    the one-off sweep of captions repeating the title, tagging.clear_duplicate_captions). A photo on a share found away is
    its error at once, unless `patient` (the sweep alone; the tag tree's rewrites answer at once, owner 2026-11-08), which
    gives it one more look once the share's window is over (_looked_at_again); more than
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
                              stop_at_first_error, fields, held, whole, on_planned, alongside, on_chunk, patient=patient,
                              unpooled=unpooled)


def _holds_already(fields, path, before, after):
    """Does the file at `path` holding `before` hold what a write of `after` would leave it holding: `after`, but for the
    fields the writer leaves as they were (fields.as_patched of Fields.left_as_they_were; `after` itself unless the
    own writer patches, or a clip's)? The plan's one question of a file it need not write."""
    return core_fields.reads_same(before, core_fields.as_patched(before, after, fields.left_as_they_were(after, path)))


def _refused_whole(photo_paths, why):
    result = Result(attempted=len(photo_paths))
    result.refuse(why)
    return _nothing_more(result)


@_exclusive
@_pinned
def _write_session(library, operation, exiftool_path, photo_paths, read, plan_one, summary, unreadable,
                   stop_at_first_error, fields, held, whole, on_planned, alongside=None, on_chunk=None, patient=False,
                   unpooled=None):
    if len(photo_paths) > batches.CHUNK and batches.current() is None:
        raise RuntimeError("%d photos are written without a batch's claim (write_fields takes it)" % len(photo_paths))
    since = _counted_so_far()
    with contextlib.ExitStack() as own:
        if fields is None:
            fields = own.enter_context(field_values.opened(exiftool_path))
        # One deadline for the write, a caller's Fields (a save's) too (Fields.together): a share that went away costs its
        # deadline once, not once a file, nor again for the read that settles the file whose write timed out
        # (_after_failure, #1927). Inside a caller's own block (a save's, a bake run's), the caller's.
        with fields.together():
            result = _write_fields(fields, library, operation, exiftool_path, photo_paths, read, plan_one, summary,
                                   unreadable, stop_at_first_error, held, whole, on_planned, alongside, on_chunk, patient,
                                   unpooled)
    return _said_removed(result, since)


def away_now(photo_paths, patient=False, stop=None):
    """The keys (paths.key) of the photos of `photo_paths` on a network share that did not answer (file_states: AWAY);
    `patient`, those still away after one more look (_looked_at_again; `stop`, a callable, ends its wait). The one look a
    write takes before it reads (write_fields)."""
    away = {paths.key(path) for path, state in file_states(photo_paths) if state == AWAY}
    return _looked_at_again(photo_paths, away, stop) if away and patient else away


def _looked_at_again(stored, away, stop):
    """The keys of `away` (photos of `stored` on a share found away) still away after one more look, once each share's
    window is over (shares.wait_back: at most AWAY_SECONDS, sooner when the look that timed out answers): a `patient`
    write's first look at an idle share is slow, and every photo looked at in the 30 s it is then taken as away was
    left out (#2622: a sweep of 43,754 photos skipped all of them). A batch asked to stop (`stop`) waits no longer."""
    for share in {shares.share_of(path) for path in stored if paths.key(path) in away}:
        shares.wait_back(share, shares.AWAY_SECONDS, stop=stop)
    return {paths.key(path) for path, state in file_states([path for path in stored if paths.key(path) in away])
            if state == AWAY}


def _write_fields(fields, library, operation, exiftool_path, photo_paths, read, plan_one, summary, unreadable,
                  stop_at_first_error, fresh, whole, on_planned=None, alongside=None, on_chunk=None, patient=False,
                  unpooled=None):
    result = Result(attempted=len(photo_paths))
    _running.result, _running.internal = result, operation in file_journal.INTERNAL
    written = result.details["written"] = {}
    # The photos ExifTool did not answer for (a share away, a read or a write that timed out, and the photos after it):
    # nothing is known of their files, as against a photo it answered with its own error (#1942).
    not_answered = result.details["not_answered"] = []
    result.details.update(change=None, conflicts=[])
    try:
        # Before anything is recorded: the first ExifTool command may otherwise come after the plan (a save hands in its
        # read), a change planned that no ExifTool can carry.
        fields.ready_to_write()
    except Refused as why:
        result.refuse(str(why))
        return result
    settle(library, exiftool_path)
    problem = alongside.verify(library) if alongside is not None else None
    if problem:
        result.refuse(problem)
        return result
    # A clip is left out before anything is read of it (leave_out_clips), unless this write may patch it.
    stored = leave_out_clips(result, [paths.stored(p) for p in photo_paths], unreadable, read)
    batch = batches.current()
    if fresh is None:
        # A photo on a share that does not answer is its error, at once in a capped write: read, it would cost ExifTool's
        # whole deadline (five minutes) and hold the lock and the request that long (#1492). A `patient` write
        # looks once more after the share's window, up to shares.AWAY_SECONDS (_looked_at_again). What the caller read just
        # now answered.
        away = away_now(stored, patient, batch.stop.is_set if batch is not None else None)
        # A batch asked to stop while it reads the files it plans from stops at the next batch of reads (#1200).
        held = fields.read([path for path in stored if paths.key(path) not in away], read,
                       stop=batch.stop if batch else None)
        held.update({key: field_values.Unreadable(AWAY_WHY) for key in away})
    else:
        held, away = fresh, set()
    if batch is not None and batch.stop.is_set():
        return _stopped_before_writing(result)
    planned, followed = [], []
    for path in stored:
        why = unsettled(path)
        if why:
            result.fail(path, why)
            if stop_at_first_error:
                break
            continue
        now = held.get(paths.key(path))
        if isinstance(now, field_values.NotAnswered) or paths.key(path) in away:
            not_answered.append(path)
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
        after = {field: core_fields.field_values(value) for field, value in plan.after.items()}
        before = {field: now.get(field, []) for field in after}
        if _holds_already(fields, path, before, after) and not (alongside is not None and alongside.wants(path)):
            written[path] = plan.detail
            followed.append((path, now))
            continue
        planned.append((path, before, after, plan.detail))
    _follow(library, followed)
    # What each file read back whole holds, for _read_back not to read it again: every file of a `whole` write, a clip's
    # of any (_carry).
    wrote, read_whole = {}, {}
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
    def read_back():
        """The files written since the last read back, read back (_read_back): a chunk's at its end, so that a stop
        reads back one chunk, not every file of a long change."""
        for row in _read_back(fields, library, wrote, read_whole):
            # ExifTool said it wrote it, and it holds what it held (#276).
            result.changed -= 1
            written.pop(row.path, None)
            result.fail(row.path, "ExifTool reported it written, but it holds what it held before")
        wrote.clear()
        read_whole.clear()

    if unpooled is not None:
        unpooled.watch(change_id)
    mine = (library.key, change_id)
    # One connection for the chunks' writes and one for their reads, on this thread (db.connections_held): each write
    # is still its own transaction, committed in the write-ahead order.
    with db.connections_held(library.path):
        try:
            # Its files are refused to a writer let in between two chunks (unsettled) until their chunk is carried out: written
            # ahead of it, a file would hold neither what the change read nor what it was to leave, its edit lost as a conflict.
            _unsettled.update((paths.key(row.path), mine) for row in rows)
            _reached("plan committed")
            if on_planned is not None:
                on_planned(change_id)
            stalled = None
            for n, row in enumerate(rows):
                if n and n % batches.CHUNK == 0:
                    read_back()
                    _free(mine, rows[n - batches.CHUNK:n])
                if n and n % batches.CHUNK == 0 and on_chunk is not None:
                    on_chunk(n, len(rows))
                if n and n % batches.CHUNK == 0 and not between_chunks(batch):
                    file_journal.withdraw(library.path, [r.id for r in rows[n:]])
                    result.details["stopped"] = {"after": n, "of": len(rows)}
                    break
                path, detail = planned[n][0], planned[n][3]
                # Read just now, in this session, by the caller: that is the check.
                now = planned[n][1] if fresh is not None else None
                try:
                    outcome, why = _carry(fields, library, row, row.before, row.after, "done", on_failure="withdraw",
                                          wrote=wrote, now=now, whole=read_whole if whole or images.is_clip(path) else None,
                                          stall_stops=True,
                                          held_out=not (alongside is not None and alongside.wants(path)))
                except _Stalled:
                    stalled = row
                    result.details["stopped"] = {"after": n, "of": len(rows), "share": True}
                    not_answered.extend(each[0] for each in planned[n:])
                    break
                if outcome == OUT:
                    written[path] = detail      # held already, as a file found holding it at the plan is
                    continue
                if outcome == "done":
                    if not _holds_already(fields, path, planned[n][1], planned[n][2]):
                        result.changed += 1       # a file held what it was to hold: its rows alone were written
                    if isinstance(detail, dict) and row.followed:
                        detail["faces_turned"] = row.followed
                    if row.named_faces:
                        result.details.setdefault("named_faces", []).extend(row.named_faces)   # the one-face rule's, for the reply
                    if row.id in wrote:
                        # The stat its row was recorded with, taken after the write: the reply's stamp, not looked at again.
                        seen = wrote[row.id][1]
                        result.details.setdefault("stamps", {})[paths.key(path)] = (
                            (seen.st_mtime, seen.st_size) if seen is not None else None)
                    written[path] = detail
                    continue
                result.fail(path, why)
                if outcome == "conflict":
                    result.details["conflicts"].append(row.named())
                    continue
                if row.read_only:
                    # Told apart, and an error all the same: a run that stops at its first error stops here too.
                    result.details.setdefault("read_only", []).append(path)
                if isinstance(why, Refused):
                    # Told apart: its writer refused it before a byte was written (a clip not patchable, open in another
                    # program), which Smart Rename leaves out rather than stops at (services.photos._refused_clips).
                    result.details.setdefault(WRITER_REFUSED, []).append(path)
                if stop_at_first_error and not isinstance(why, Refused):
                    # A clip the writer refused (open in another program, not patchable, its last QuickTime keyword) is its
                    # own error, as a clip leave_out_clips refuses is: the photos after it are written.
                    file_journal.withdraw(library.path, [r.id for r in rows[n + 1:]])
                    break
            read_back()
            stopped = result.details.get("stopped")
            if stopped and stopped.get("share"):
                _share_stopped(library, operation in file_journal.INTERNAL, change_id, result, stopped, stalled)
            else:
                if finish_forward(library, change_id, result) is None:
                    # A patched file that could not be read after its write stays `writing` with its record (_by_bytes):
                    # the change is let go, for the next settle to finish.
                    file_journal.release(library.path, change_id)
                if stopped:
                    result.refuse("Stopped as asked after %d of %d photos: %d file(s) were written%s; the rest were not written."
                                  % (stopped["after"], stopped["of"], result.changed,
                                     _kept_as(operation in file_journal.INTERNAL, change_id) if result.details["change"] else ""))
        except BaseException as stopped:
            if isinstance(stopped, KeyboardInterrupt):
                # First, in one transaction, the files not reached are taken out (_settle_stopped takes out what is left): a
                # second Ctrl+C in what follows leaves no file planned for the next open to write, only the one under way.
                try:
                    file_journal.withdraw(library.path, [r.id for r in file_journal.files_of(library.path, change_id)
                                                         if r.state == "planned"])
                except Exception as problem:
                    logger.warning("Could not take the files not reached out of change %s: %s", change_id, problem)
            # The files recorded done since the last chunk's read back are read back all the same: settling reads
            # back only the files it writes (docs/findings.md, #286).
            _read_back(fields, library, wrote)
            if isinstance(stopped, KeyboardInterrupt):
                # Stopped by the person (Ctrl+C, Ctrl+Break in the CLI): what is written stays, applied and undoable; the files
                # not reached are taken out, not written by whatever opens the library next (#1493). A crash is settled forward.
                stopped.left = _settle_stopped(library, change_id, fields, result)
                # What the journal holds done, not what this loop had counted: the file under way may be done already.
                try:
                    stopped.written = files_done(library, change_id)
                except Exception:
                    stopped.written = None
                stopped.change = result.details["change"]      # None: it wrote nothing, and is erased
            file_journal.release(library.path, change_id)
            raise
        finally:
            # Done, stopped, withdrawn, or cut off by an error: what a cut-off change left planned is the next settle's.
            _free(mine, rows)
            if unpooled is not None:
                unpooled.release()
    logger.info("%s: change %d, %s, wrote %d file(s)", library.path, change_id, operation, result.changed)
    return result


def finish_forward(library, change_id, result=None):
    """file_journal.finish of the forward change `change_id` of a write, its faces' records tidied: its counts, or None when
    files are left. One that wrote nothing is erased by finish (file_journal.ERASED): `result`'s `change` says None, as the
    Result of a write that planned nothing does, so that no write names a change that is gone."""
    finished = file_journal.finish(library.path, change_id, also=face_batch.tidy)
    if finished is file_journal.ERASED and result is not None:
        result.details["change"] = None
    return finished


def files_done(library, change_id):
    """How many files of change `change_id` the journal holds done: what a write that is being stopped says it has written
    (a count, no path). The one under way may be written and not yet counted."""
    return sum(1 for row in file_journal.files_of(library.path, change_id) if row.state == "done")


def what_a_stop_left(library, stopped, planned=None, noun="file", who=None):
    """The sentence a write a person ran (a one-off script, a CLI command) says when Ctrl+C (`stopped`, the
    KeyboardInterrupt) ended it: what it wrote and whether History holds a change to undo; the one place it is said
    (#2997, #3020). `planned`: the id of the change the run's write_fields planned (its `on_planned`), None before the
    plan. `noun`: what is written ("clip", "photo file"). `who`: the library's name the line starts with; without it, the
    line starts "Stopped".

    The stop's `change` is set by _write_fields' own handler: None, nothing was written and the change is erased (#2974);
    an id, the files written (`written`, files_done) applied, `left` of them still to settle at the next open. A stop with
    no `change` came before the write loop, after write_fields returned, or is a second press during the first one's read
    back: the journal answers for the change planned: gone (it wrote nothing), applied, or still planned (the next open
    settles the file under way). With none planned, the run planned and wrote nothing (writing first finishes a change an
    earlier run left half done: settle)."""
    said = None
    if hasattr(stopped, "change"):
        change, written, left = stopped.change, stopped.written, getattr(stopped, "left", 0)
    elif planned is None:
        said = ("stopped before this run planned or wrote anything; a change an earlier run left half done, which a run "
                "finishes first, is finished by the next run.")
    else:
        found = file_journal.change(library.path, planned)
        change, written, left = (None, 0, 0) if found is None else (planned, files_done(library, planned), 0)
        if found is not None and found.status == "planned":
            said = ("stopped: at least %d %s(s) written (the one under way may be too), recorded as change %d in History; "
                    "the next open of the library finishes settling them. Run it again to continue."
                    % (written, noun, planned))
    if said is None and change is None:
        said = "stopped: no %s written, no change recorded, nothing to undo. Run it again to continue." % noun
    elif said is None:
        said = ("stopped: %s %s(s) written%s. Run it again to continue."
                % ("some" if written is None else written, noun, _kept_as(False, change, left)))
    return "%s: %s" % (who, said) if who else said[0].upper() + said[1:]


def _kept_as(internal, change_id, left=0):
    """What a write stopped part-way says the files it wrote are kept as: change `change_id` in History, which can be
    undone (once the `left` files still under way are settled); or, `internal` (its operation one of
    file_journal.INTERNAL: the index's bake), listed nowhere and never undone, nothing more than the files to settle."""
    more = "; %d more is settled the next time it can be read" % left if left else ""
    if internal:
        return more
    if left:
        return ", recorded as change %s in History%s, and then the change can be undone" % (change_id, more)
    return ", recorded as change %s in History, which can be undone" % change_id


def _share_stopped(library, internal, change_id, result, stopped, stalled):
    """A forward write ExifTool stopped answering for part-way at the file of `stalled` (_Stalled), ended as a Ctrl+C ends
    one (_settle_stopped): the files not reached are taken out, the change is applied with what was written, and the write
    is refused saying so. The file whose write was under way, if it was, cannot be read now: it stays `writing`, the change
    unfinished, for the next settle to decide by what it holds (as a crash leaves it); it is not read again: the write's
    Fields is dead (Fields.together) and would only say so. What stopped answering is ExifTool: a share
    gone away, a drive that stopped, or ExifTool itself exiting (exiftool_session); the sentences say no more than that."""
    _settle_stopped(library, change_id, result=result)
    left = sum(1 for row in file_journal.files_of(library.path, change_id) if row.state == "writing")
    if left:
        file_journal.release(library.path, change_id)
    # The photo it stopped on is the write's error too: a caller that reports errors and not a refusal (a save) says it.
    result.fail(stalled.path, "%s: ExifTool stopped answering %s it was written, and nothing after it was; check that the "
                              "share or drive is there" % (stalled.named(), "as" if left else "before"))
    if not result.changed and not left:
        result.refuse("ExifTool stopped answering before anything was written; check that the share or drive is there, "
                      "then run it again.")
        return
    result.refuse("ExifTool stopped answering after %d of %d photos: %d file(s) were written%s; the rest were not "
                  "written. Check that the share or drive is there, then run it again."
                  % (stopped["after"], stopped["of"], result.changed, _kept_as(internal, change_id, left)))


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
    boxes and vectors, a bulk assignment's faces, a bake's fingerprint row, and last, when the caller asked, the count of the faces
    all of it took out of identity work (face_batch.PoolFollower). The ONE place that lists them, for a file marked done or undone
    and for one taken out of its change after it was done (_read_back)."""
    return [each for each in (rotation.follower(row, state), face_batch.follower(row, state), baking.follower(row, state),
                              face_batch.pool_follower(row, state))
            if each is not None]


def _stat(path):
    """The file's os.stat, or None when it cannot be taken."""
    try:
        return os.stat(path)
    except OSError:
        return None


def stamp_after_write(path):
    """(mtime, size) of a file a write just wrote, taken by the write itself as the journaled one takes its row's, or None
    when it cannot be taken: the reply's stamp (details["stamps"]), so nothing looks at the file again once its share may
    have stopped answering (file_only's write of a folder the library does not hold)."""
    seen = _stat(path)
    return (seen.st_mtime, seen.st_size) if seen is not None else None


def _read_only(seen):
    """Is the file whose stat is `seen` (None: not there) marked read-only? The one rule of it (row.read_only, writable)."""
    return seen is not None and not seen.st_mode & S_IWRITE


def writable(path):
    """Is the file at `path` there and not marked read-only? Asked after a write to it failed: then an access denied of
    the write is a program holding the file open (lock_owners.looks_held's `writable`), not the file's own mark."""
    seen = _stat(path)
    return seen is not None and not _read_only(seen)


def _record(library, row, values, state, stamp=None, wrote=True, after=None, whole=None, stat=None):
    """Record what a file holds now in its row, and mark it `state`, in one transaction;
    with `after`, the journal's after too (file_journal.set_after); with `whole`, the file
    as read back whole (store.photos.follow_fields), and `stat`, its stat taken before that
    read (_carry). Returns the file's stat, as recorded."""
    if wrote and stat is None:
        stat = _stat(row.path)
    follow = _followers(row, state)

    def work(conn):
        # Before the row is recorded: recording can itself change faces (a guess the tag makes), and a follower that counts
        # the faces the transaction takes out of identity work must start from the faces as they were.
        for each in follow:
            getattr(each, "start", lambda _conn: None)(conn)
        row.named_faces = photos.follow_fields(conn, row.path, values, stat, stamp, whole=whole) or []
        _follow_rename(conn, row.path, row.known_as, stat)
        for each in follow:
            each(conn)
        if after is not None:
            file_journal.set_after(conn, row.id, after)
        file_journal.set_state(conn, row.id, state)

    db.write_with_connection(library.path, work, label="%s: %s" % (row.named(), state))
    # Committed: what the attempt that committed counted is handed on (a transaction run again began its count again).
    for each in follow:
        getattr(each, "committed", lambda: None)()
    return stat


def _read_back(fields, library, wrote, read=None):
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
            held.update(fields.read([row.path for row in unread], sorted({field for row in unread for field in row.after})))
        except Exception as e:
            logger.warning("%s: could not read back %d file(s) written: %s", library.path, len(unread), e)
    for row, stat in written:
        now = held.get(paths.key(row.path))
        if now is None or isinstance(now, Exception):
            continue
        kept = {field: now.get(field, []) for field in row.after}
        if core_fields.same_fields(kept, row.after):
            continue
        try:
            current = os.stat(row.path)
        except OSError:
            continue
        if stat is None or (current.st_mtime_ns, current.st_size) != (stat.st_mtime_ns, stat.st_size):
            continue
        differ = sorted(field for field in row.after if not core_fields.same_values(kept[field], row.after[field]))
        same_as_before = core_fields.reads_same(kept, row.before) and not core_fields.reads_same(kept, row.after)

        def work(conn, row=row, kept=kept, same_as_before=same_as_before):
            photos.follow_fields(conn, row.path, kept)
            if same_as_before:
                _take_out_in(conn, row)
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


def _take_out_in(conn, row):
    """Take the file of `row` out of its change after it was marked done, in the caller's transaction: what the done brought
    (its faces, its boxes) goes back in it, or the rows would describe a write the change never made."""
    for each in _followers(row, "undone"):
        each(conn)
    file_journal.withdraw_in(conn, [row.id])


def _conflict(library, row, why):
    file_journal.mark(library.path, [row.id], "conflict", why)
    return "conflict", "%s: %s" % (row.named(), why)


def _carry(fields, library, row, origin, target, finished, on_failure, wrote=None, now=None, whole=None, stall_stops=False,
           held_out=False):
    """Bring a file holding `origin` to `target`, and mark it `finished`: forward (before
    to after, done) and in an undo (after to before, undone). A file holding `target`
    already is recorded as it is; one holding neither is a conflict. A write that fails
    leaves a file holding `origin` withdrawn from its change (`on_failure="withdraw"`) or
    a conflict. A file written goes in `wrote`, for _read_back. `now` is what the file
    holds when the caller has just read it in this session; otherwise it is read here.
    `whole`, a dict, asks for the file written to be read back whole and recorded so
    (_read_whole); a clip's always is (its patch appends an XMP box: its row says what a scan reads, stamped with the new
    size). Returns (outcome, why): `finished`, "conflict" or "failed"; OUT, with `held_out`, as _held_or_conflict
    says. `stall_stops`, a forward write's: a file ExifTool does not answer for raises _Stalled (_read_now)."""
    if whole is None and images.is_clip(row.path):
        whole = {}
    settled = _held_or_conflict(fields, library, row, origin, target, finished, now, stall_stops=stall_stops, held_out=held_out)
    if settled is not None:
        return settled
    # One look before the write, two projections: the vectors' stamp and the journal's fingerprint describe one file.
    # The stamp, from the journal, of a write of the file that a crash or a share interrupted (it left it `writing`).
    interrupted = row.stamp if row.state == "writing" else None
    before = _stat(row.path)
    stamp = row.stamp = (before.st_mtime, before.st_size) if before is not None else None
    row.known_as = before
    file_journal.writing(library.path, row.id, stamp)
    row.patched = False
    _reached("file writing")
    saving = _saving(library, row)
    failed = _written(fields, row, target, saving)
    if exiftool_session.scratch_in_the_way(failed):
        left = removed_scratch(row.path, stamp, interrupted)
        if left is None:
            failed = _written(fields, row, target, saving)      # once: a second failure is the write's own
        elif left == SCRATCH_UNDER_WAY:
            failed = exiftool_session.written_lately(failed)
    if isinstance(failed, field_values.PictureChanged):
        # Made twice (field_values.written_once_more): the journal's note of it, which the doctor counts.
        failed = field_values.PictureChanged(file_journal.PICTURE_CHANGED)
    # What the file holds now: `target`, but for the fields the write left as they were (row.stale).
    held = core_fields.as_patched(origin, target, row.stale)
    looked = {}

    def read_back():
        """The file's stat, then the file read back whole when `whole` asks (_read_whole), kept in `looked` for the
        row: what it holds of the change's fields, or None when that read failed (_verified then reads them alone)."""
        # The stat before the read back: a change another program makes after it leaves the row older than its file,
        # which the next scan reads again; taken after, it stamped what was read before the change as the newer file.
        looked["stat"] = _stat(row.path)
        looked["whole"] = _read_whole(fields, row, whole) if whole is not None else None
        return whole.get(paths.key(row.path)) if looked["whole"] is not None else None

    if failed is None and row.patched:
        # A patch's check of its fields is the read back whole, when one is asked: one read of the file, not two.
        failed = _verified(fields, library, row, held, read_back if whole is not None else None)
    if failed is not None:
        if whole is not None:
            whole.pop(paths.key(row.path), None)       # a patch put back holds nothing of what was read of it
        return _after_failure(fields, library, row, origin, target, finished, on_failure, failed, stall_stops)
    _reached("file written")
    if not looked:
        read_back()
    # Going forward, a field left stale is the journal's after too: an undo starts from what the file holds.
    after = None
    if row.stale and finished == "done":
        after = row.after = held
    stat = _record(library, row, held, finished, stamp, stat=looked["stat"], after=after, whole=looked["whole"])
    if wrote is not None:
        wrote[row.id] = (row, stat)
    _reached("file recorded")
    return finished, None


def _written(fields, row, target, saving=None):
    """None when Fields.write wrote `target` into the file of `row`, the fields it left as they were in
    `row.stale`; else what it raised. A new file whose picture changed is written once more, logged and counted
    (Fields.write's `again`; `pictures_refused`)."""
    def again():
        logger.warning("%s: its write changed the picture's bytes; the new file was discarded, and it is written once more",
                       row.named())
        _running.pictures_refused = _counted_so_far()[1] + 1
    row.stale = ()
    try:
        row.stale = tuple(fields.write(row.path, target, saving=saving, scratch_written=lambda: _reached(SCRATCH_WRITTEN),
                                   again=again))
    except Exception as e:
        return e
    return None


def _saving(library, row):
    """field_values.write's `saving` for the file of `row`: a patch's record committed (file_journal.save_patch) before
    the patch writes a byte of it."""
    def saving(record, before):
        file_journal.save_patch(library.path, row.id, record, before)
        row.patched = True
        _reached(PATCH_SAVED)
    return saving


def _verified(fields, library, row, held, read_back=None):
    """None when the patch just written leaves the file of `row` holding what it wrote, its size and its ranges
    (in_place.held: NEW), and its fields read back as `held` (what the write left: fields.as_patched); else what
    failed, for _after_failure to settle the file by its bytes. A file whose bytes are as written but whose fields do
    not read so is put back here first. No byte outside the ranges is read (DECISIONS 2026-11-03, "AN IN-PLACE PATCH CHECKS ONLY THE BYTES IT CHANGED").
    `read_back()`, called once the bytes are as written, is what the file holds of `held`'s fields as the caller reads
    it back for its row (_carry's, whole), or None when that read failed: then they are read here alone, as before."""
    saved = file_journal.patch_of(library.path, row.id)
    try:
        found = in_place.held(row.path, *saved)
        if found != in_place.NEW:
            return RuntimeError("the patch did not leave the file as it wrote it (%s)" % found)
        now = read_back() if read_back is not None else None
        if now is None:
            now = fields.read_back(row.path, list(held))
        if core_fields.reads_same(now, held):
            return None
        in_place.put_back(row.path, *saved)
    except (OSError, field_values.Unreadable) as e:
        return e
    return RuntimeError("the patch was written, but its fields do not read back as written; its bytes were put back")


def _by_bytes(library, row):
    """The one rule of a file a patch was writing (its record saved: file_journal.save_patch), settled by its bytes before
    any field of it is read. None when it holds every range as it was (to be written again) or as the patch wrote it (then
    read as any file is), a range this process tore put back first (in_place.TORN). Else (outcome, why): a conflict, never
    overwritten, for a file another program wrote (a byte neither found nor written, another size) or whose record
    an earlier TagPup saved (in_place.held: NEITHER);
    ("writing", why) for one that cannot be read or put back now: left `writing` with its record for the next settle,
    never a conflict, since the record is the only way back. `row.stale` is made the fields the record says the patch
    left as they were (in_place.left_stale): a file holding every range as written holds fields.as_patched of the write,
    which the caller reads it against, after a crash as in the write."""
    saved = file_journal.patch_of(library.path, row.id)
    if saved is None:
        return None
    row.stale = in_place.left_stale(saved[0])
    try:
        found = in_place.held(row.path, *saved)
        if found == in_place.TORN:
            in_place.put_back(row.path, *saved)
    except OSError as e:
        return "writing", "%s: could not be read or put back now (%s); it is settled by its bytes the next time" % (
            row.named(), e.strerror or type(e).__name__)
    if found == in_place.NEITHER:
        return _conflict(library, row, "changed since its patch was saved: it holds bytes the patch neither found nor"
                                       " wrote, or another size, or its patch was saved by an earlier TagPup whose"
                                       " record this one does not read; it is not overwritten")
    return None


#: How long ago the scratch file of a photo must have been written for it to be no write's under way: longer than any
#: ExifTool command of TagPup's runs, since each is killed, its process with it, at that deadline (exiftool_session).
SCRATCH_STALE = exiftool_session.DEFAULT_TIMEOUT
#: Why removed_scratch leaves a scratch file written in the last SCRATCH_STALE seconds; the owner is told to try again.
SCRATCH_UNDER_WAY = "it was written in the last %d s, by a write that may be under way" % SCRATCH_STALE


def _counted_so_far():
    """(scratch files removed_scratch removed, writes whose picture changed made again: _carry) in this thread so far."""
    return getattr(_running, "scratch_removed", 0), getattr(_running, "pictures_refused", 0)


def _said_removed(result, since):
    """`result`, saying how many scratch files removed_scratch removed (`scratch_removed`) and how many writes whose
    picture changed were discarded and made again (`pictures_refused`) in this thread since `since` (_counted_so_far)."""
    removed, refused = (now - then for now, then in zip(_counted_so_far(), since))
    if removed:
        result.details["scratch_removed"] = removed
        logger.info("A write removed %d leftover scratch file(s) of interrupted writes", removed)
    if refused:
        result.details["pictures_refused"] = refused
    return result


def removed_scratch(path, saw, interrupted, scratch=None):
    """Remove the scratch file an interrupted write of the photo at `path` left beside it (`scratch`; by default
    exiftool_session.scratch_of, ExifTool's; a conversion's temp file, tagpup.services.convert),
    which made ExifTool refuse this write ("Temporary file already exists"; #2180): None when it was removed, and the
    write is made once more; else why it was left (SCRATCH_UNDER_WAY: the owner is told to try again later). Only when
    all four of these hold (DECISIONS 2026-10-29): 1. it is that photo's, never one a listing found; 2. the photo is there
    and described (photos.describes) by the stamp this write saw before it began (`saw`) and, when the journal has one,
    the stamp of the write it interrupted (`interrupted`), and was not modified after the scratch file was last written
    (a write of it renames the scratch file over it, and takes a new modified time): so it is not the photo's only copy
    (#1410) and the photo is not changed since; 3. no write of the photo is under way: this process's are held by the
    one lock (exclusively), and one of another process (the CLI, a second library holding the folder) would have written
    it in the last SCRATCH_STALE seconds (by the share's clock against ours: the residual DECISIONS accepts); 4. both
    looks answered (shares.stamp_of: a share away is not "no scratch file"). It is ExifTool's file, not the photo: the
    photo and its row are untouched here. Logged; counted in a write's or an undo's result
    (_said_removed), not in the settle of a change left `writing` (_finish), which has no result to carry it."""
    scratch = exiftool_session.scratch_of(path) if scratch is None else scratch
    left, photo = shares.stamp_of(scratch), shares.stamp_of(path)
    if not isinstance(left, tuple) or not isinstance(photo, tuple):
        why = "it or its photo could not be looked at"
    elif saw is None or not photos.describes(saw[0], saw[1], photo) \
            or (interrupted is not None and not photos.describes(interrupted[0], interrupted[1], photo)) \
            or photo[0] > left[0]:
        why = "the photo has changed since the write that left it"
    elif time.time() - left[0] < SCRATCH_STALE:
        why = SCRATCH_UNDER_WAY
    else:
        try:
            os.remove(scratch)
        except OSError as e:
            why = "it could not be removed: %s" % e
        else:
            logger.warning("Removed %s (%d bytes), the scratch file an interrupted write left beside its photo", scratch, left[1])
            _running.scratch_removed = _counted_so_far()[0] + 1
            return None
    logger.info("Left %s beside its photo: %s", scratch, why)
    return why


def _held_or_conflict(fields, library, row, origin, target, finished, now=None, unread_stays=False, stall_stops=False,
                      held_out=False):
    """The one rule of a file settled by what it holds, before anything is written to it: holding `target`, it is recorded
    `finished` as it holds it; holding neither, or unreadable, a conflict. Returns (outcome, why) so; or None when it holds
    `origin`: still to be written (_carry writes it, a stop takes it out). `now`, as _carry's. `unread_stays` (a stop, whose
    session a Ctrl+C may have ended, not a read that waited its deadline): unreadable, it is left as it is, ("writing", why).

    `held_out`, a forward write's file that is to be in the change for its file alone (not a bulk assignment's kept for its
    rows): holding `target`, it was written since the plan by another program (a second CLI window's bake of the folder),
    and is left alone and taken out of the change, (OUT, None), as a file found holding it at the plan is never in it;
    recorded done, it would be done in two changes and each's undo take back the other's write (#1937). Its row is made to
    say what it holds in the one transaction that takes it out, never done first (#1947): a crash before it leaves the file
    planned, settled at the next start as any planned file is."""
    seen = None
    if now is None:
        if row.state == "writing":
            settled = _by_bytes(library, row)
            if settled is not None:
                return settled
        # The stat before the read: a file found holding `target` is recorded stamped as it was before what was read of it.
        seen = _stat(row.path)
        try:
            now = _read_now(fields, row, target, stall_stops)
        except field_values.Unreadable as e:
            if unread_stays:
                return "writing", str(e)
            return _conflict(library, row, "could not be read: %s" % e)
    # What the write leaves: `target`, but for the fields a patch whose record was saved left (row.stale, _by_bytes).
    left = core_fields.as_patched(origin, target, row.stale)
    if core_fields.reads_same(now, left) and held_out:
        _take_held_out(library, row, now, target, seen)
        return OUT, None
    if core_fields.reads_same(now, left):
        # Written already: by a run a crash (or a stop) cut off before it recorded the row, whose
        # stamp from before the write was recorded with it (#265).
        _record_held(library, row, now, target, finished, row.stamp, seen)
        return finished, None
    if not core_fields.reads_same(now, origin):
        return _conflict(library, row, "changed since it was read: it holds neither what the change found"
                                       " nor what it was to leave, and is not overwritten")
    return None


def _read_now(fields, row, target, stall_stops):
    """What the file of `row` holds of `target`'s fields, read now; raises field_values.Unreadable. The one rule of a share
    that stops answering in a write: a file ExifTool did not answer for (NotAnswered) stops a forward write
    (`stall_stops`, raising _Stalled); an undo and a settle keep theirs."""
    try:
        return fields.read_one(row.path, list(target))
    except field_values.NotAnswered as e:
        if stall_stops:
            raise _Stalled(str(e)) from None
        raise


def _read_whole(fields, row, read):
    """The file `row` just wrote, read back whole (Fields.read_back): its raw_metadata in the shape the indexer records,
    and what it holds of the change's fields put in `read` (for _read_back). None, and nothing put in `read`, when the
    read fails or ExifTool reports an error reading it: the row then records the fields written, stamped only where it
    described the file before."""
    try:
        now, whole = fields.read_back(row.path, list(row.after), whole=True)
    except Exception as e:
        logger.warning("%s: could not read it back whole: %s", row.named(), e)
        return None
    read[paths.key(row.path)] = now
    return whole


def _record_held(library, row, now, target, finished, stamp, stat):
    """Record a file found holding `target` already, as it holds it: a value ExifTool does
    not keep as written ("1.50" read as 1.5) is recorded as read, in the row and, going
    forward, as the journal's after, as _read_back records a file just written. `stamp`
    is the file's from just before a write of this change, when one was made; `stat`, the file's
    taken before `now` was read, or None when the caller read it (then it is taken now)."""
    held = {field: now.get(field, []) for field in target}
    after = None
    if finished == "done" and not core_fields.same_fields(held, target):
        after = held
        row.after = held
    # A file that was not to change (its faces were) is not stamped as written.
    wrote = not core_fields.reads_same(row.before, row.after)
    _record(library, row, held, finished, stamp, wrote=wrote, after=after, stat=stat if wrote else None)


def _take_held_out(library, row, now, target, stat):
    """Take the file of `row`, planned and found holding `target` (_held_or_conflict's `held_out`), out of its change, and
    make its row say what the file holds, stamped with `stat` (taken before `now` was read), in one transaction. Nothing it
    brings when done (_followers) is made: it is not this change's write."""
    held = {field: now.get(field, []) for field in target}

    def work(conn):
        photos.follow_fields(conn, row.path, held, stat)
        file_journal.withdraw_in(conn, [row.id])

    db.write_with_connection(library.path, work, label="%s: written by another program" % row.named())


def _after_failure(fields, library, row, origin, target, finished, on_failure, error, stall_stops=False):
    """A write that raised: settled by what the file holds after it. `row.read_only`: the file is marked read-only
    (its stat after the failure), the one failure told from one that may pass the next time.

    A forward write's (`stall_stops`) whose share did not answer (NotAnswered: a patch's, or ExifTool's picture check;
    in_place.Away: a patch's look after its write) stops there, raising _Stalled, as one ExifTool stops answering for
    (_read_now): before its record was saved, no byte was written; after, the file is left `writing` when its bytes
    cannot be read now (_by_bytes), for the next settle. Read through ExifTool instead, it, and the next photo on that
    share, would each wait ExifTool's whole deadline (#2400)."""
    seen = _stat(row.path)
    row.read_only = _read_only(seen)
    away = stall_stops and isinstance(error, (field_values.NotAnswered, in_place.Away))
    if row.patched:
        settled = _by_bytes(library, row)
        if away and settled is not None and settled[0] == "writing":
            raise _Stalled(settled[1])
        if settled is not None:
            return settled
    elif away:
        raise _Stalled(str(error))
    try:
        now = _read_now(fields, row, target, stall_stops)
    except field_values.Unreadable:
        now = None
    if now is not None and core_fields.reads_same(now, core_fields.as_patched(origin, target, row.stale)):
        _record_held(library, row, now, target, finished, row.stamp, seen)
        return finished, None
    # After a failure only: who holds the file, if it is held; access denied is a holder's only of a file there and not
    # read-only (lock_owners.looks_held).
    error = lock_owners.explain(error, row.path, writable=seen is not None and not row.read_only)
    if now is not None and core_fields.reads_same(now, origin):
        if on_failure == "withdraw":
            file_journal.withdraw(library.path, [row.id])
            return "failed", error
        picture = isinstance(error, field_values.PictureChanged)
        file_journal.mark(library.path, [row.id], "conflict",
                          file_journal.PICTURE_CHANGED if picture else "could not be written: %s" % error)
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


def fingerprint(path):
    """What a file is known by wherever a rename left it: its size and modified time,
    which renaming does not change, and its file id (st_ino: NTFS's file index), which
    renaming on one volume keeps too and a copy never shares. A copy keeps the size and
    modified time -- Explorer's, robocopy's, shutil.copy2's -- and was taken for the
    file (docs/findings.md, #269). None when there is no file; the file id is 0 where
    the volume gives none, and rename() refuses such a volume (owner, 2026-10-15). A write
    of the file's fields changes all three; the journal's own carries the rename's over it
    (_record, file_journal.follow_renamed)."""
    try:
        return _fingerprint_of(os.stat(path))
    except OSError:
        return None


def _fingerprint_of(stat):
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino}


def _follow_rename(conn, path, before, after):
    """The one rule of a photo file this process just wrote under the name a rename gave it: a rename done that knows it by
    its fingerprint before the write (`before`, its os.stat) knows it by the one the write left (`after`) from here on
    (file_journal.follow_renamed). Nothing when either stat could not be taken. In the caller's transaction: for the
    journal's own writes, the one that records the file (_record); for a write outside the journal, its own (OutsideWrites).
    Returns the files of renames followed."""
    if before is None or after is None:
        return 0
    return file_journal.follow_renamed(conn, path, _fingerprint_of(before), _fingerprint_of(after))


class OutsideWrites:
    """The writes of photo files a process makes outside the journal's file-change path: the indexer's identity mint
    (tagpup.files.field_values.IdentityWriter over field_values.ensure_document_id; the charter's named exception, #1355). Each
    is carried over the journal's renames as the journal's own writes are (_follow_rename), so that a rename's undo still finds the
    file it renamed (#1957). Forward only: a file minted before this was made is not looked for (no backfill), and a file
    another program rewrites stays as it is: History reads no file. Nothing else of the journal knows of the write: it is
    no change, and no undo takes it back.

    Which fingerprints the renames know is read once, at the first write that changed a file (renamed_fingerprints), and
    each file after asks the set, not the library: a full index mints tens of thousands of files, and the answer is the
    run's. A rename another process (the server) finishes during the run is not in the set, so the mint leaves it knowing
    its file as it was and its undo refuses the file, as before this carry; a rename of this run's files is rare, and
    nothing more is built for it."""

    def __init__(self, library):
        self.library = library
        self.known = None

    def _renames_know(self, fingerprint):
        if self.known is None:
            self.known = file_journal.renamed_fingerprints(self.library.path)
        return file_journal.fingerprint_key(fingerprint) in self.known

    def around(self, path, write):
        """`write()`, a write of the file at `path` that returns something true when it wrote, with the file's stat taken
        just before and after it and the rename that knew it by the one before carried to the one after, in a transaction
        of its own right after the write: a Save of the photo that follows (the server, another process) finds the rename
        knowing the file as it is. A photo no rename knows (almost every one) takes a lookup and no write transaction.
        Never raises for the carry: the file is written either way. What is left: a process killed between the write and
        this transaction leaves the rename knowing the file as it was, and its undo refuses it, as before; a stat a share
        does not answer is no carry. Returns what `write` returned."""
        before = _stat(path)
        result = write()
        if not result or before is None:
            return result
        after = _stat(path)
        if after is None or _fingerprint_of(after) == _fingerprint_of(before):
            return result
        try:
            if self._renames_know(_fingerprint_of(before)):
                followed = db.write_with_connection(self.library.path, lambda conn: _follow_rename(conn, path, before, after),
                                                    label="a rename told of a write outside the journal")
                logger.info("%s: %d rename(s) told of a write outside the journal", self.library.path, followed)
        except Exception as e:
            logger.warning("%s: a rename could not be told of a write outside the journal; its undo will refuse the file:"
                           " %s", self.library.path, e)
        return result


def _holds(path, row):
    """Is the file at `path` the one `row` renames: its size, modified time and file id
    are what the plan recorded, or the journal's last write of it left."""
    if path is None or not row.before:
        return False
    return fingerprint(path) == row.before


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
    if operation not in file_journal.RENAMES:
        # A rename is known by its operation (file_journal.RENAMES): one under another would not be followed by the writes
        # of its files after it.
        raise ValueError("%r is not one of the renames the journal knows (file_journal.RENAMES)" % operation)
    if exiftool_path:
        settle(library, exiftool_path)
    moves = dict(aside)
    moves.update({old: new for old, new in renames.items() if old != new})
    if not moves:
        return Renamed(dict(renames), {}, 0, [])
    for old in moves:
        if unsettled(old):
            raise names.RenameFailed(unsettled(old), [])
    fingerprints = {old: fingerprint(old) for old in moves}
    if any(found and not found["ino"] for found in fingerprints.values()):
        # A renamed file is known by its file id; without one a copy cannot be told from it.
        raise names.RenameFailed("this volume gives its files no file id, so a renamed file could not be told "
                                 "from a copy of it", [])
    found = _rows_of(library, list(moves))
    change_id, rows = file_journal.plan(library.path, operation, [
        {"photo_id": (found.get(paths.key(old)) or (None,))[0], "path": old, "new_path": new,
         "before": fingerprints[old], "after": fingerprints[old]} for old, new in moves.items()], summary)
    with db.connections_held(library.path):
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
    were finished. A change that cannot be finished now -- one of its files on a share that does not answer, or one
    ExifTool does not answer for, as _finish says, or one whose settle was stopped as asked -- is logged, released and
    left as it is for the next time: it does NOT raise, so a caller that must know asks the journal afterwards
    (tagpup.store.file_journal.unfinished). A change whose settle this process was asked to stop is not taken up again
    by this process (_put_off): the next start settles it, not the next save."""
    return sum(1 for change in file_journal.unfinished(library.path)
               if _take(library, change) and _settle_taken(library, change, exiftool_path))


def _take(library, change):
    """Take `change`, left half done, over for this process to settle: not one this process put off (_put_off), nor
    one a live process carries out (this one included: another thread of it has it). True when it was taken."""
    if (library.key, change.id) in _put_off:
        return False
    if change.owner and file_journal.owner_alive(change.owner):
        return False
    return file_journal.claim(library.path, change.id, change.owner)


def _settle_taken(library, change, exiftool_path):
    """Finish `change`, taken (_take). True when it is finished; else it is logged and released for the next settle,
    never marked done."""
    try:
        return _finished(library, change, exiftool_path)
    except Exception:
        logger.exception("%s: change %d, left half done, could not be finished now", library.path, change.id)
        file_journal.release(library.path, change.id)
        return False


def _finished(library, change, exiftool_path):
    """_finish `change`, taken: True when it is finished, said in a line; False when it is left, released for the next
    settle. An error is the caller's (_settle_taken; the settle thread's _settle_in_turn puts the change off)."""
    if not _finish(library, change, exiftool_path):
        file_journal.release(library.path, change.id)
        return False
    logger.info("%s: change %d (%s), left half done, is finished", library.path, change.id, change.operation)
    return True


#: The changes (library key, change id) whose settle this process was asked to stop (Cancel, a hand-over): left half
#: done for the next start, not taken up again by the settle every write begins with.
_put_off = set()

#: The files a change of this process has still to write, {paths.key(path): (library key, change id)}: a running write's
#: planned files (_write_fields), a settle's (_settle_files, _settle_on_a_thread); a chunk's taken out once it is carried
#: out, the rest when the write ends, a stopped settle's kept until the next start (_put_off). A writer let in between two
#: chunks (let_others_in) refuses them (unsettled), in any library holding the file: written ahead of the change, the
#: change would find the file holding neither what it read nor what it was to leave, and mark it a conflict, its edit
#: lost on it. Read and changed only under the lock of changes of photo files.
_unsettled = {}

#: Why a write leaves a photo of _unsettled alone; no change id, which may be another library's.
UNSETTLED = ("its file is still to be written by an earlier change of the same files, under way or cut short: it is being "
             "finished now, or, stopped, the next time TagPup starts; nothing was written to it, try again once it is "
             "finished")


def unsettled(path):
    """The sentence a write gives for a photo whose file a change of this process has still to write (_unsettled), or None."""
    return UNSETTLED if paths.key(path) in _unsettled else None


def _free(mine, rows):
    """Take the files of `rows` (journal rows) that `mine`, (library key, change id), holds in _unsettled out of it."""
    for row in rows:
        if _unsettled.get(paths.key(row.path)) == mine:
            del _unsettled[paths.key(row.path)]


class _NoTurn(Exception):
    """A long settle could not take a batch's turn: another batch of this process is running, or the server is handing
    over to a new version. The change is left as it is, for the next settle."""


@contextlib.contextmanager
def _settle_turn(library, change, count):
    """The stop a settle of `count` files of `change` honours between its chunks (_finish): the batch this thread carries
    out already (a write's settle, or the settle thread's own: _settle_in_turn), or, for more than batches.CHUNK files, a batch of its own, listed and cancelled as
    any (batches.listing, batches.cancel; a hand-over's batches.stop_all). Never waited for: the lock is held, and the
    batch ahead needs it (_NoTurn). None, no batch, for a short settle."""
    if batches.current() is not None:
        # A write's own batch is listed with the change it settles while it does (batches.listing), then its own.
        batch = batches.current()
        planned_already, batch.change_id = batch.change_id, change.id
        try:
            yield batch.stop
        finally:
            batch.change_id = planned_already
        return
    if count <= batches.CHUNK:
        yield None
        return
    with contextlib.ExitStack() as held:
        try:
            batch = held.enter_context(batches.claim(library, change.operation, count, wait=False))
        except (Conflict, Refused) as why:
            raise _NoTurn(str(why)) from None
        batch.change_id = change.id
        yield batch.stop


def _finish(library, change, exiftool_path):
    """Settle the files `change` left; True when it is finished. A file on a share that does not answer (file_states), or
    that ExifTool does not answer for (_Stalled), is left as it is, and the change unfinished, for a settle once it can be
    read: it is never a conflict for not answering, and the write whose settle met it waits no deadline for it (#1921).

    A batches.CHUNK of files at a time, as a write is carried out: each chunk's files are looked at (file_states), settled
    and read back before the next; between two chunks a line says how far it has got (counts only), a writer that waits
    takes the lock (let_others_in), and a settle asked to stop (_settle_turn) stops, its files not reached left as they
    are for the next start (_put_off), the change unfinished. A settle that cannot take its turn is left whole.

    Its files, renames and the others, are read and settled by one map (tagpup.services.roots.pinned), as a forward
    write's are: a place moved meanwhile is taken by the next run, never half-way through this one (#3395). A change of
    the library's roots by another process part-way (RootsChanged), or a root this machine does not place (Unplaced),
    leaves the change as a crash leaves it, released for the next settle, which converts by the map as it is then."""
    undoing = change.undoing
    if change.operation == file_journal.CONVERT and not undoing:
        # One clip, settled by where its copy is (tagpup.services.convert); never undone (journal.UNDO_CHECKS).
        from tagpup.services import convert
        return convert.settle_change(library, change, exiftool_path)
    if change.operation == file_journal.DELETE:
        # Photos sent to the Recycle Bin, settled by a look at each (tagpup.services.photos); never undone.
        from tagpup.services import photos as photo_service
        return photo_service.settle_delete(library, change)
    try:
        with roots_service.pinned(library):
            renames, changed = _pending(library, change)
            with _settle_turn(library, change, len(changed)) as stop:
                if renames:
                    _settle_renames(library, renames, forward=not undoing, redo=True)
                left = _settle_files(library, change, exiftool_path, changed, stop) if changed else False
    except (_NoTurn, roots_service.RootsChanged, roots_service.Unplaced) as why:
        logger.info("%s: change %d is left half done for the next settle: %s", library.path, change.id,
                    why if isinstance(why, _NoTurn) else roots_service.stopped(why))
        return False
    if left:
        if (library.key, change.id) not in _put_off:      # a stop said so itself (_settle_files)
            logger.info("%s: change %d is left half done until its files can be read", library.path, change.id)
        return False
    file_journal.finish(library.path, change.id, also=face_batch.tidy)
    return True


def _pending(library, change):
    """(the renames, the other files) `change` has still to settle: forward, those planned or writing; undoing, those
    writing or done."""
    pending = [row for row in file_journal.files_of(library.path, change.id)
               if row.state in (("writing", "done") if change.undoing else ("planned", "writing"))]
    if change.operation == file_journal.CONVERT:
        # A conversion's clip is no rename of the renames' settle (its new_path is the converted copy's), and its
        # original is refused to every other write until the conversion is settled (_unsettled).
        return [], pending
    return [row for row in pending if row.is_rename], [row for row in pending if not row.is_rename]


def _settle_files(library, change, exiftool_path, changed, stop):
    """_finish's files that are not renames, a chunk at a time. True when any is left as it is (away, not answered for,
    unreadable now, or not reached before a stop). Inside _finish's pin: its held connection (db.connections_held)
    converts every file by the map the settle began with, not the one roots._held would take at its next recheck."""
    undoing, left = change.undoing, False
    # A planned file was never written by this change (it is marked writing before its write): one found holding the change's
    # after was written by another program and is taken out, as the forward loop takes it out (#1937), unless the change holds
    # it for its faces too (a bulk assignment's, face_batch.Work.wants). A file left writing that holds the after is this
    # change's own write a crash cut off, told from no other, and is done.
    kept = (face_batch.photos_kept(library, change.id)
            if not undoing and any(row.state == "planned" for row in changed) else set())
    put_off, mine = False, (library.key, change.id)
    with field_values.opened(exiftool_path) as fields, fields.together(), db.connections_held(library.path):
        wrote = {}
        try:
            for row in changed:
                _unsettled[paths.key(row.path)] = mine
            for start in range(0, len(changed), batches.CHUNK):
                if start:
                    # Each chunk read back before the next: a stop, or a kill, leaves no more than one chunk unread.
                    _read_back(fields, library, wrote)
                    wrote.clear()
                    logger.info("%s: change %d, left half done: %d of %d file(s) settled", library.path, change.id,
                                start, len(changed))
                    if stop is not None and stop.is_set():
                        _put_off.add((library.key, change.id))
                        put_off = True
                        logger.info("%s: change %d: its settle was stopped as asked after %d of %d file(s); the %d left "
                                    "are settled the next time the library is opened", library.path, change.id, start,
                                    len(changed), len(changed) - start)
                        return True
                    let_others_in(stop)
                chunk = changed[start:start + batches.CHUNK]
                away = {paths.key(path) for path, state in file_states([row.path for row in chunk]) if state == AWAY}
                for row in chunk:
                    if paths.key(row.path) in away:
                        left = True
                        continue
                    try:
                        if undoing:
                            outcome, _why = _carry(fields, library, row, row.after, row.before, "undone", on_failure="conflict",
                                                   stall_stops=True)
                        else:
                            outcome, _why = _carry(fields, library, row, row.before, row.after, "done", on_failure="conflict",
                                                   wrote=wrote, stall_stops=True,
                                                   held_out=row.state == "planned" and row.photo_id not in kept)
                        # A patched file that cannot be read now stays `writing` with its record (_by_bytes).
                        left = left or outcome == "writing"
                    except _Stalled:
                        return True
                _free(mine, chunk)
        finally:
            if not put_off:
                _free(mine, changed)
            _read_back(fields, library, wrote)
    return left


_settled = set()
_settled_guard = threading.Lock()


def settle_once(library, exiftool_path, on_a_thread=False):
    """settle, the first time this process opens the library: at start. Never raises: a
    library that cannot be settled now is settled by the next process. Returns how many changes were finished here.

    `on_a_thread` (the web server: tagpup.runtime.settle_on_a_thread; DECISIONS 2026-11-07): settled on a thread of its
    own (_settle_on_a_thread), and 0 returned at once, so the request that opens the library does not wait for a killed
    long change's whole settle (#2610). The CLI settles inline: a daemon thread would die at its exit."""
    key = paths.key(library.path)
    with _settled_guard:
        if key in _settled:
            return 0
        _settled.add(key)
    try:
        if not file_journal.unfinished(library.path):
            return 0
        if on_a_thread:
            _settle_on_a_thread(library, exiftool_path)
            return 0
        return settle(library, exiftool_path)
    except Exception:
        logger.exception("%s: settling its photo files failed", library.path)
        return 0


#: The name of a settle's thread (_settle_on_a_thread), with its library's.
SETTLE_THREAD = "SettleThread %s"


def _settle_on_a_thread(library, exiftool_path):
    """settle_once's settle, on a thread of its own. Before it returns, under the lock of changes of photo files, each
    change left half done is taken for this process (_take: a write's settle, or another process's -- the CLI --, leaves
    it alone from then on) and its files not yet settled are refused to every write of this process (_unsettled), so that
    none is written ahead of it. The thread then settles each change in turn (_settle_in_turn). Returns the thread, or
    None when there was nothing to take."""
    with exclusively():
        taken = [change for change in file_journal.unfinished(library.path) if _take(library, change)]
        for change in taken:
            for row in _pending(library, change)[1]:
                _unsettled[paths.key(row.path)] = (library.key, change.id)
    if not taken:
        return None
    thread = threading.Thread(target=_settle_in_turn, args=(library, taken, exiftool_path),
                              name=SETTLE_THREAD % library.name, daemon=True)
    thread.start()
    return thread


def _settle_in_turn(library, taken, exiftool_path):
    """The settle thread's work: each change `taken` settled (_finished) as a batch, whatever its count, that takes its
    turn BEFORE the lock (batch_exclusively's order): listed with its change from the moment it waits (batches.listing),
    cancelled as any, asked to stop and waited for by an update's drain (batches.stop_all, batches.sentences). A batch of
    another library's settle, or the owner's own, runs ahead of it.

    Cancelled (or a hand-over) while it waits, refused a place in line (Conflict: MOST_WAITING wait already), failed
    (ExifTool, the disk, the journal; logged), or left (_finished False: a file on a share away, ExifTool stalled, a
    patched file left `writing`): put off for the next start (_put_aside), its photos still refused, as a settle stopped
    at a chunk is. This process's next write does not take it up: freed, its own settle would take the whole change in
    that request (the wait this thread is for), or, with another batch running (_NoTurn), write a photo ahead of it, a
    conflict at the next start."""
    for change in taken:
        mine = (library.key, change.id)
        try:
            with batches.claim(library, change.operation, sum(map(len, _pending(library, change))), change_id=change.id):
                with exclusively():
                    if not _finished(library, change, exiftool_path):
                        _put_aside(library, change)
        except Refused:
            _put_aside(library, change)
            logger.info("%s: change %d: its settle was stopped as asked before it began; it is settled the next time "
                        "the library is opened", library.path, change.id)
        except Exception:
            logger.exception("%s: change %d, left half done, could not be settled now; it is settled the next time the "
                             "library is opened", library.path, change.id)
            _put_aside(library, change)
        finally:
            with exclusively():
                if mine not in _put_off:
                    for key in [key for key, whose in _unsettled.items() if whose == mine]:
                        del _unsettled[key]


def _put_aside(library, change):
    """`change`, which the settle thread could not finish, put off for the next start (_put_off) and released for the
    next process: the files it has still to write refused to every write of this process until then (_unsettled; an
    error part-way had _settle_files free them), so none is written ahead of it."""
    mine = (library.key, change.id)
    with exclusively():
        _put_off.add(mine)
        for row in _pending(library, change)[1]:
            _unsettled[paths.key(row.path)] = mine
    file_journal.release(library.path, change.id)


# ---- Undo --------------------------------------------------------------------------------

def writes_files(library, change_id):
    """Did change `change_id` of `library` plan photo files?"""
    return file_journal.writes_files(library.path, change_id)


def _undoable(library, change_id, exiftool_path, check):
    """(the change, [(file, None)] it can put back, [(file, why)] it cannot), reading every
    file it left done; or (None, why it cannot be undone at all, None). `check(conn,
    change_id)` is the one account of whether it may be undone now
    (tagpup.services.journal.why_not: a newer change of the same files among it), asked on
    a read-only connection, once, before anything is written. A clip the writer would refuse to put back (a field outside
    field_values.writes: field_values.clip_refused) refuses the whole undo, so the change stays applied, never half undone
    nor a conflict (#2546)."""
    change = file_journal.change(library.path, change_id)
    if change is None:
        return None, "there is no change %d" % change_id, None
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        why = check(conn, change_id)
    finally:
        conn.close()
    if why:
        return None, why, None
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
        wanted = {f for row in changed for f in row.after}
        with field_values.opened(exiftool_path) as fields:
            held = fields.read([row.path for row in changed], sorted(wanted))
        for row in changed:
            now = held.get(paths.key(row.path))
            if now is None or isinstance(now, Exception):
                refused.append((row, "could not be read: %s" % now))
            elif not core_fields.reads_same(now, row.after):
                refused.append((row, "no longer holds what the change left"))
            elif images.is_clip(row.path) and (why := field_values.clip_refused(row.path, row.before)):
                # The writer would refuse it: the undo is refused whole, the change left applied, to be undone later.
                return None, "%s: %s" % (row.named(), why), None
            else:
                ready.append(row)
    return change, ready, refused


@_exclusive
def rehearse_undo(library, change_id, exiftool_path, check):
    """What undoing change `change_id` would do, reading every file and writing none. A
    Result like tagpup.services.journal.rehearse's: details["rehearsal"] has `rows` (the
    files it would put back), `exact` (none would be refused), `differences` (each file
    it would refuse, named, and why), and `files`. Refused out of hand when `check` says
    so (_undoable)."""
    result = Result(details={"dry_run": True, "change": change_id, "files": True})
    change, ready, refused = _undoable(library, change_id, exiftool_path, check)
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
def undo(library, change_id, exiftool_path, check, apply=False):
    """Undo change `change_id`: a rehearsal unless `apply` (rehearse_undo). Applied, each
    file that still holds what the change left is written back to what it held, its row
    following, through the same states; each that does not is refused, named in the
    Result's errors, and never overwritten. `changed` is the files put back. `check` is
    asked before anything is written (_undoable).

    One map for the whole undo (tagpup.services.roots.pinned), as a forward write holds one: a place moved meanwhile is
    taken by the next run, never between two files of this one (#3395). A change of the library's roots by another process
    part-way (RootsChanged) stops it, left as a crash leaves an undo -- the files not yet put back are put back by the next
    settle of the library (the next change of photo files, or the next start) -- and refused saying how many it put back;
    a root this machine does not place (Unplaced) refuses it in words, nothing written."""
    result = Result(details={"dry_run": not apply, "change": change_id, "files": True})
    begun = []
    try:
        with roots_service.pinned(library):
            return _undo(library, change_id, exiftool_path, check, apply, result, begun)
    except (roots_service.RootsChanged, roots_service.Unplaced) as stop:
        said = roots_service.stopped(stop)
        if begun:
            # The file under way when it stopped may be put back and not yet recorded: the settle records it.
            said += (" Before it stopped at least %d file(s) were put back; the others are put back the next time the"
                     " library is settled (the next change of photo files, or the next start)." % result.changed)
        result.refuse(said)
        return result


def _undo(library, change_id, exiftool_path, check, apply, result, begun):
    """undo's work, inside its pin: `result` filled as it goes, `begun` given True once the change is marked as being
    undone (what a stop part-way says)."""
    rehearsal = rehearse_undo(library, change_id, exiftool_path, check)
    if not apply or rehearsal.refused:
        return rehearsal
    result.attempted = rehearsal.attempted
    since = _counted_so_far()
    schema.ensure(library.path)
    change, ready, refused = _undoable(library, change_id, exiftool_path, check)
    if change is None:
        result.refuse(ready)
        return result
    if not file_journal.begin_undo(library.path, change_id):
        result.refuse("change %d is no longer applied" % change_id)
        return result
    begun.append(True)
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
            with field_values.opened(exiftool_path) as fields, db.connections_held(library.path):
                for row in changed:
                    outcome, why = _carry(fields, library, row, row.after, row.before, "undone", on_failure="conflict")
                    result.details["faces_left"] = result.details.get("faces_left", 0) + row.faces_left
                    if outcome == "undone":
                        result.changed += 1
                    else:
                        result.fail(row.named(), why)
        if file_journal.finish(library.path, change_id) is None:
            file_journal.release(library.path, change_id)      # a patched file left `writing` (_by_bytes)
    except BaseException:
        file_journal.release(library.path, change_id)
        raise
    logger.info("%s: change %d undone, %d file(s) put back", library.path, change_id, result.changed)
    return _said_removed(result, since)

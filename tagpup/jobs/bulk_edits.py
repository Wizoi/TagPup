"""A bulk edit of many photos, by photo id, as a job with progress and a cancel (docs/ARCHITECTURE.md, phase 9d-1).

The folder view's bulk write is one request holding the lock of changes of photo files for all of it, capped at 5,000 photos.
A library view selects across folders, up to 200,000, and cannot send that many paths; so the page sends a SELECTION
(tagpup.services.selection) and the work runs here, on a thread of its own in the server, and the page asks how it is getting on.

**How it runs.** The photos are resolved once, when the job starts, to a list of ids. They are written in CHUNKS of 25
(tagpup.services.bulk_edit.run_chunk): each chunk resolves its ids to their rows' paths at that moment (a photo renamed since is
found by its id, one deleted is skipped and counted) and takes the one lock of changes of photo files for the chunk only, so a
single-photo save, another library's write or the CLI's interleaves and nothing waits hours; between chunks the job lets a write
that is waiting in (file_changes.waiting) before it asks for the lock again. One chunk is one journaled change of its held photos
(History lists a job as one change a chunk, named after the job; the files of a folder the library does not hold are written to
their files only, as ever). Each file is read again before it is written and one another program changed meanwhile is a conflict,
reported and never overwritten: a job adds and removes against each file's OWN tags and never replaces a list.

**What is counted** is what happened to each photo: `changed` (a file written and read back), `unchanged`, `skipped_missing` (no
row, or no file), `skipped_damaged`, or an error entry (the first 50 are kept, every one counted). A photo that cannot be read or
written is an error and the job goes on; five chunks in a row in which nothing could be done, or an exception that is not one
photo's (ExifTool that cannot start), stop it as `failed`, with the sentence.

**One at a time.** One bulk job runs in a library: a second start is a Conflict naming the first. The claim is in the library's
`job_runs` (tagpup.services.job_runs), as Verify's is, so another process is refused too, and the Activity page lists the run with
its counts, rewritten at most every 2 seconds and at the end -- never per photo; the status the page polls is an in-memory counter.

**A restart.** The thread dies with its process and leaves a `running` row, which the next claim marks `abandoned`; the status says
so before then, from the row's owner not being alive. A job that can be started again with no harm (tags, people: adding a tag a
photo holds, or taking one off that it lacks, changes nothing) simply is. A TIME SHIFT is not idempotent, so its list of photos and
how far it got are kept in the library's cache folder (tagpup.files.job_files) -- the state before each chunk, saying which was in
flight -- and `resume` continues from there. The chunk in flight when it stopped is settled by the journal, the one record of
what was written: each photo of it that a change named after the job left done is not shifted again. A photo named by id has a
row, so its folder is the library's and its write is journaled, and the journal can always tell. A chunk the journal has not
finished (a change left planned or writing, owned by a process that is gone or by this one after an error) is finished by the
resume's settle; if it cannot be, the resume is refused (COULD_NOT_SETTLE) and nothing is written, because planning a photo again
from a file that may already be shifted shifts it twice. The state records the change of the chunk in flight (`journal_chunk`),
before that change writes a file, and a resume refuses when the journal no longer holds it (a snapshot restored over it).

The journal's prune never takes the changes of a time shift that has a record to resume from (services.journal.kept_operations
decides, store.journal._prunable applies it to every prune), so the account a resume reads cannot be pruned away; the
refusals on a pruned or missing change are a defence for a journal changed some other way. A change planned and committed
whose record of it was not written (a death between the two) is not named, and a snapshot restore that removes exactly that
change goes unseen: accepted.

**What is accepted.** The state file is flushed (fsync) and replaced atomically, but its folder is not flushed, and the journal
runs with WAL synchronous=NORMAL: after a POWER LOSS (not a crash of the process, which loses neither) a photo written on another
volume can survive while the record before its chunk, or the journal's plan, rolls back, and a resume then shifts it again. A
power loss between volumes is accepted; nothing more is built against it (docs/findings.md, #597).

The job holds its Library, not the page's: the page may switch library or be closed and the job goes on, its status reachable.
"""
import logging
import os
import re
import threading
import time

from tagpup.core import runs
from tagpup.core.result import Conflict, NotFound, Refused
from tagpup.services import bulk_edit, file_changes
from tagpup.services import job_runs as runs_service

logger = logging.getLogger(__name__)

#: The job's name in `job_runs`.
JOB = "bulk edit"

#: Photos in a chunk: written under one hold of the lock, as one journaled change.
CHUNK = 25

#: The longest the job lets the run's counts go unrecorded in the library, in seconds.
FLUSH_SECONDS = 2.0

#: Errors kept with names, for the page; the rest are counted.
MOST_ERRORS = 50

#: Chunks in a row in which nothing at all could be done that stop the job.
GIVE_UP_AFTER = 5

#: The most a job waits between chunks for a write that wants the lock, in seconds.
MOST_WAIT = 5.0

#: Jobs remembered in this process for each library (the latest).
KEPT = 20

RUNNING, DONE, CANCELLED, FAILED, ABANDONED = "running", "done", "cancelled", "failed", "abandoned"
#: The state of a time shift whose record was removed for age: too old to resume.
EXPIRED = "expired"

#: The record a chunk is written under is written, durably, BEFORE the chunk's first file: this many tries, waiting this long
#: (doubling) between. A resume knows of a chunk only by that record.
PERSIST_TRIES = 5
PERSIST_SECONDS = 0.1

#: What is said when the record could not be written and so the chunk was not.
NOT_RECORDED = ("It stopped before writing the next chunk because it could not record where it had got to; nothing of that chunk "
                "was written. %s of %s photos were done.")

#: What is said of a bulk edit that could not be set up, and of a resume that could not be.
SETUP_FAILED = "The bulk edit could not be set up (%s): nothing was changed. Try again in a moment."
EXPIRED_SAYS = "Too old to resume: its record was removed after 30 days."

_jobs = {}            # {library.key: {handle: Job}}
_lock = threading.Lock()


def _number(count):
    return "{:,}".format(count)


class Job:
    """One bulk edit: what it does, to which photos, how far it has got, what it counted."""

    def __init__(self, library, handle, run_id, edit, ids, exiftool_path, after_write=None, state=None):
        self.library, self.handle, self.run_id = library, handle, run_id
        self.edit, self.ids = edit, ids
        self.total = len(ids)
        self.exiftool_path, self.after_write = exiftool_path, after_write
        self.operation = bulk_edit.operation_of(edit.op, handle)
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        #: The thread that runs it, once started (a test joins it; nothing else does).
        self.thread = None
        self.state = RUNNING
        self.message = None
        self.started = time.time()
        self.finished = None
        self.run_ids = [run_id]
        # What it has counted. `done` is how many photos are settled: the next to do is ids[done].
        self.done = 0
        self.began = 0              # where this run of the job started (a resumed one does not start at 0)
        self.began_at = self.started
        self.changed = self.unchanged = self.skipped_missing = self.skipped_damaged = self.error_count = 0
        self.errors = []
        self.skip = set()           # ids a resume leaves out: shifted already
        self.inflight = 0
        #: How many of `changed` a resume counted from the journal for the chunk that was in flight: not yet in the state's own
        #: count while that chunk is unfinished, or a second resume of it would count them again.
        self.credit = 0
        #: The journal change of the chunk in flight, once the chunk is planned (None before, and cleared at the write before each
        #: chunk): kept in the record so that a resume can tell a journal that lost it (a snapshot restored) from one that never had
        #: a chunk in flight.
        self.chunk = None
        #: The record's sequence number: one more on every write of the state file (a resume that finds another has been beaten
        #: to it by another process).
        self.seq = 0
        self.last_flush = 0.0
        if state:
            self._take_state(state)

    # ---- what is kept, and read back ----------------------------------------------------------

    def _take_state(self, state):
        """A resumed job's counts: those its state file recorded."""
        self.started = state.get("started", self.started)
        self.done = self.began = int(state.get("done", 0))
        self.inflight = int(state.get("inflight", 0))
        self.seq = int(state.get("seq", 0))
        self.chunk = state.get("journal_chunk")
        for name in ("changed", "unchanged", "skipped_missing", "skipped_damaged", "error_count"):
            setattr(self, name, int(state.get(name, 0)))
        self.errors = list(state.get("errors") or [])
        self.run_ids = list(state.get("run_ids") or []) + [self.run_id]

    def snapshot(self, inflight=None):
        """What the state file holds, as a dict, with the sequence number the next write will carry (this one is not used up
        until the write has worked)."""
        with self.lock:
            return {"job": self.handle, "op": self.edit.op, "edit": self.edit.to_json(), "state": self.state, "seq": self.seq + 1,
                    "journal_chunk": self.chunk, "message": self.message, "total": self.total, "done": self.done,
                    "inflight": self.inflight if inflight is None else inflight, "changed": self.changed - self.credit,
                    "unchanged": self.unchanged, "skipped_missing": self.skipped_missing,
                    "skipped_damaged": self.skipped_damaged, "error_count": self.error_count, "errors": list(self.errors),
                    "started": self.started, "finished": self.finished,
                    "run_id": self.run_id, "run_ids": list(self.run_ids)}

    def _what(self):
        text = "%s: %s, %s of %s photos" % (bulk_edit.OPERATIONS[self.edit.op], self.edit.describe(),
                                           _number(self.done), _number(self.total))
        return text if self.state == RUNNING else "%s [%s]" % (text, self.state)

    def counts(self):
        """The numbers (and the one sentence, `what`) the library's record of the run holds: no name of a tag or a person."""
        with self.lock:
            return {"what": self._what(), "state": self.state, "job": self.handle, "op": self.edit.op, "total": self.total,
                    "done": self.done,
                    "changed": self.changed, "unchanged": self.unchanged, "skipped_missing": self.skipped_missing,
                    "skipped_damaged": self.skipped_damaged, "errors": self.error_count}

    def status(self):
        """For the page: {job, op, state, total, done, changed, unchanged, skipped_missing, skipped_damaged, errors: [first
        50 {id, name, why}], error_count, started, finished, eta_seconds, message, cancelling, resumable, what}. An in-memory read; nothing is asked of the library."""
        with self.lock:
            running = self.state == RUNNING
            made = self.done - self.began
            eta = None
            if running and made > 0:
                eta = round((time.time() - self.began_at) / made * (self.total - self.done))
            return {"job": self.handle, "op": self.edit.op, "state": self.state, "total": self.total, "done": self.done,
                    "changed": self.changed, "unchanged": self.unchanged, "skipped_missing": self.skipped_missing,
                    "skipped_damaged": self.skipped_damaged, "errors": list(self.errors[:MOST_ERRORS]),
                    "error_count": self.error_count, "started": self.started, "finished": self.finished,
                    "eta_seconds": eta, "message": self.message, "cancelling": running and self.cancel.is_set(),
                    "resumable": resumable(self.edit.op, self.state), "what": self._what()}

    def checked_status(self):
        """status(), with `resumable` true only where a resume would work: the record (list of photos and state) is there."""
        found = self.status()
        if found["resumable"] and not bulk_edit.has_record(self.library, self.handle):
            found["resumable"] = False
            found["message"] = (found["message"] or "") + " Its record is gone, so it cannot be resumed."
        return found

    # ---- running ------------------------------------------------------------------------------

    def _persist(self, inflight=None, mandatory=False):
        """Write the state file. True when it was written. A `mandatory` write (the record of the chunk about to be written) is
        tried PERSIST_TRIES times; any other (progress) once, and a failure is logged: the next mandatory write supersedes it,
        and what it would have said is in the journal. The sequence number is used up only by a write that worked."""
        delay = PERSIST_SECONDS
        for attempt in range(PERSIST_TRIES if mandatory else 1):
            state = self.snapshot(inflight)
            try:
                bulk_edit.write_state(self.library, self.handle, state)
            except Exception as problem:
                logger.warning("Could not record how far bulk edit %s of %s got (try %d): %s", self.handle, self.library.name,
                               attempt + 1, problem)
                if mandatory and attempt < PERSIST_TRIES - 1:
                    time.sleep(delay)
                    delay *= 2
                continue
            with self.lock:
                self.seq = state["seq"]
            return True
        return False

    def _planned(self, change_id):
        """A chunk's change is planned and no file of it written yet: the record names it, durably, or the chunk is not written
        (the exception releases the change, planned, and a resume settles it). A record that names the change is what lets a
        resume tell that the journal lost it."""
        before = self.chunk
        self.chunk = change_id
        if not self._persist(mandatory=True):
            self.chunk = before
            raise OSError("the job's record could not be written")

    def _flush(self, force=False):
        """Record the counts in the library's run and the state file, at most every FLUSH_SECONDS; and tell the page's caches
        that photos were rewritten."""
        now = time.monotonic()
        if not force and now - self.last_flush < FLUSH_SECONDS:
            return
        self.last_flush = now
        try:
            runs_service.progress(self.library, self.run_id, self.counts())
        except Exception as problem:
            logger.warning("Could not record the progress of bulk edit %s of %s: %s", self.handle, self.library.name, problem)
        self._persist()
        self._wrote()

    def _wrote(self):
        if self.after_write is not None:
            try:
                self.after_write()
            except Exception as problem:
                logger.warning("After bulk edit %s of %s: %s", self.handle, self.library.name, problem)

    def _take(self, out, size):
        with self.lock:
            self.changed += out.changed
            self.unchanged += out.unchanged
            self.skipped_missing += out.skipped_missing
            self.skipped_damaged += out.skipped_damaged
            self.error_count += len(out.errors)
            for photo_id, name, why in out.errors:
                if len(self.errors) < MOST_ERRORS:
                    self.errors.append({"id": photo_id, "name": str(name)[:bulk_edit.MOST_TEXT], "why": str(why)[:bulk_edit.MOST_TEXT]})
            self.done += size
            self.credit = 0

    def _end(self, state, message=None):
        with self.lock:
            self.state, self.message, self.finished = state, message, time.time()
        if resumable(self.edit.op, state):
            self._persist()     # kept for a resume, and only while it can be one
        else:
            # Over for good. What the state file holds -- an edit's tags and people, the names of files that failed -- is not
            # kept past the job that needed it (the library's own record, and the journal, are what remain).
            bulk_edit.forget(self.library, self.handle)
        try:
            runs_service.end(self.library, self.run_id, time.time(), self.counts(), failed=state == FAILED,
                             note=message if state == FAILED else None, keep=bulk_edit.resumable_heads(self.library))
        except Exception as problem:
            logger.warning("Could not record the end of bulk edit %s of %s: %s", self.handle, self.library.name, problem)
        self._wrote()

    def run(self):
        """The thread's work: chunk after chunk, then the end, whatever happens."""
        try:
            with runs.running(runs.job_tag(self.library.name, self.run_id)):
                self._loop()
        except BaseException as problem:   # a thread that raised leaves a job that says why, not one still 'running'
            logger.exception("Bulk edit %s of %s stopped", self.handle, self.library.name)
            self._end(FAILED, _stopped_by(problem) + " Nothing more was written; %s of %s photos were done."
                      % (_number(self.done), _number(self.total)))

    def _loop(self):
        streak = 0
        position = self.done
        keeps_cursor = self.edit.op == bulk_edit.TIME_SHIFT
        while position < self.total:
            if self.cancel.is_set():
                return self._end(CANCELLED, "Cancelled after %s of %s photos." % (_number(self.done), _number(self.total)))
            chunk = self.ids[position:position + CHUNK]
            wanted = [photo_id for photo_id in chunk if photo_id not in self.skip]
            if keeps_cursor:
                # Which chunk is in flight, kept BEFORE it is written, and DURABLY: a resume tells what the chunk wrote only by
                # this record and the journal, and a record older than the files would let it plan a shifted photo again. If it
                # cannot be written the chunk is not.
                before, change = self.inflight, self.chunk
                self.inflight, self.chunk = position + len(chunk), None
                if not self._persist(mandatory=True):
                    self.inflight, self.chunk = before, change
                    return self._end(FAILED, NOT_RECORDED % (_number(self.done), _number(self.total)))
            try:
                out = bulk_edit.run_chunk(self.library, self.edit, wanted, self.exiftool_path, self.operation,
                                    self._planned if keeps_cursor else None) if wanted \
                    else bulk_edit.Outcome()
            except Exception as problem:
                logger.exception("Bulk edit %s of %s: a chunk failed", self.handle, self.library.name)
                return self._end(FAILED, "%s Nothing more was written; %s of %s photos were done."
                                 % (_stopped_by(problem), _number(self.done), _number(self.total)))
            self._take(out, len(chunk))
            position += len(chunk)
            if keeps_cursor:
                self.inflight = position
                self._persist()
            if out.changed or out.unchanged:
                streak = 0
            elif out.errors:
                streak += 1
                if streak >= GIVE_UP_AFTER:
                    return self._end(FAILED, "Stopped: %d chunks in a row could not be written (see the errors listed). %s of %s "
                                             "photos were done." % (streak, _number(self.done), _number(self.total)))
            self._flush()
            _let_writes_in(self.cancel)
        self._end(DONE)


def _stopped_by(problem):
    """The sentence for an exception that is not one photo's: a fixed one and the kind of exception, never its text, which can
    name a path or a file (it is in the server's log, with the traceback)."""
    if isinstance(problem, bulk_edit.ChunkUndecided):
        return ("ExifTool stopped answering in the middle of a chunk and its files could not be read afterwards, so it is not known "
                "which of them were shifted. Resume decides from the files and the journal.")
    return "It could not go on (%s); the server's log says why." % type(problem).__name__


def _let_writes_in(cancel):
    """Between chunks, let a change of photo files that waits for the lock have it: the lock is not fair, and this thread would
    take it again before the waiter woke. Bounded, so a stream of them cannot hold a job for ever."""
    spent = 0.0
    while file_changes.waiting() and spent < MOST_WAIT and not cancel.is_set():
        time.sleep(0.01)
        spent += 0.01


def resumable(op, state):
    """Can a job of `op` in `state` be resumed? Only a time shift (the others are simply started again), and only one that
    stopped part-way, not one that is running or ended."""
    return op == bulk_edit.TIME_SHIFT and state in (ABANDONED, CANCELLED, FAILED)


# ---- The registry -----------------------------------------------------------------------------

def _held(library):
    return _jobs.setdefault(library.key, {})


def _running_one(library):
    return next((job for job in _held(library).values() if job.state == RUNNING), None)


def _refuse_if_running(library):
    job = _running_one(library)
    if job is not None:
        status = job.status()
        raise Conflict("A bulk edit is already running in %s (%s; %s of %s photos done). Wait for it to finish, or cancel it."
                       % (library.name, bulk_edit.OPERATIONS[job.edit.op], _number(status["done"]), _number(status["total"])))


def _claim(library):
    """The claim on the library's `job_runs` for a new run: a Conflict, with a sentence, when a process still alive has one
    (the other server, or a click that came a moment after) or the library has not been brought up to date."""
    claim = runs_service.claim(library, JOB, library.name, time.time())
    if not claim:
        if claim.why == "behind":
            raise Conflict("%s has not been brought up to date yet: open it in TagPup once and try again." % library.name)
        where = "in this TagPup" if bulk_edit.is_this_process(claim.holder) else "in another TagPup process"
        raise Conflict("A bulk edit is already running in %s, %s. Wait for it to finish." % (library.name, where))
    return claim


def _give_back(library, claim, why="it was not begun"):
    """End a claim that is not going to be a run (a refusal after it was made): the row is deleted, as though never claimed, so
    that refusals do not use up the rows a job's history is kept in (and the next claim is not refused by it); where that fails the
    run is failed with a fixed sentence. Never raises."""
    try:
        if not runs_service.discard(library, claim.run_id):
            runs_service.end(library, claim.run_id, time.time(), {"what": "bulk edit not begun", "state": FAILED}, failed=True,
                             note="It was not begun: %s." % why)
    except Exception as problem:
        logger.warning("Could not give back the claim of run %s of %s: %s", claim.run_id, library.name, problem)


def _register(library, job):
    held = _held(library)
    held[job.handle] = job
    while len(held) > KEPT:
        oldest = next((handle for handle, each in held.items() if each.state != RUNNING), None)
        if oldest is None:
            break
        del held[oldest]


def _launch(job):
    job.thread = threading.Thread(target=job.run, name="BulkEdit", daemon=True)
    job.thread.start()


def _unregister(library, job, previous=None):
    """Take `job` out of the registry again (a start or resume that did not get as far as a running thread), putting back the
    Job it replaced, if any. A registered job with no thread would be 'running' for ever: the next start refused for it, a cancel
    that ends nothing, an update drain that waits for it."""
    with _lock:
        held = _held(library)
        if held.get(job.handle) is job:
            if previous is not None:
                held[job.handle] = previous
            else:
                del held[job.handle]


def _set_up_failed(problem):
    """The Conflict a start or resume that failed while being set up raises in place of the exception, which can name a path:
    only its kind, and the log has the rest."""
    return Conflict(SETUP_FAILED % type(problem).__name__)


def _mark_expired(library, expired):
    """Say, in the library's record of each job whose files were swept for age, that it is too old to resume: its status then
    says so and offers nothing that would be refused."""
    for handle in expired:
        with _lock:
            job = _held(library).get(handle)
            if job is not None and job.state != RUNNING:
                job.state, job.message = EXPIRED, EXPIRED_SAYS
        try:
            _head, latest = _chain(library, handle)
            if latest is None:
                continue
            counts = dict(latest.changed or {})
            counts["state"] = EXPIRED
            counts["what"] = re.sub(r" \[\w+\]$", "", counts.get("what") or "bulk time shift") + " [expired]"
            runs_service.amend(library, latest.id, counts, EXPIRED_SAYS)
        except Exception as problem:
            logger.warning("Could not note that bulk edit %s of %s is too old to resume: %s", handle, library.name, problem)


def start(library, edit, ids, exiftool_path, after_write=None):
    """Begin `edit` on the photos `ids` (resolved, in order) on a thread of its own, and return the Job. Conflict, nothing
    begun, when a bulk edit is running in the library (here, or in another process). A time shift keeps its list of photos
    for a resume. All or nothing: if anything between the claim and the running thread fails, the job is unregistered, the claim
    given back and the files removed, and the caller gets a Conflict with a sentence."""
    if not ids:
        raise Refused("The selection holds no photos.")
    with _lock:
        _refuse_if_running(library)
    claim = _claim(library)           # outside the lock: it may ask the system who is alive, which takes seconds
    job = None
    try:
        with _lock:
            _refuse_if_running(library)
            job = Job(library, claim.run_id, claim.run_id, edit, list(ids), exiftool_path, after_write)
            _register(library, job)
        _mark_expired(library, bulk_edit.sweep(library))
        if edit.op == bulk_edit.TIME_SHIFT:
            bulk_edit.write_ids(library, job.handle, job.ids)
        if not job._persist(inflight=0, mandatory=True):
            raise OSError("the job's record could not be written")
        try:
            runs_service.progress(library, job.run_id, job.counts())
        except Exception as problem:
            logger.warning("Could not record the start of bulk edit %s of %s: %s", job.handle, library.name, problem)
        _launch(job)
    except BaseException as problem:
        if job is not None:
            _unregister(library, job)
            bulk_edit.forget(library, job.handle)
        _give_back(library, claim, "it could not be set up")
        if isinstance(problem, Exception) and not isinstance(problem, (Refused, Conflict, NotFound)):
            logger.exception("Could not set up a bulk edit of %s", library.name)
            raise _set_up_failed(problem) from None
        raise
    return job


def status(library, handle):
    """The status of job `handle` of the library (Job.status), from memory if this process runs or ran it, else from what
    the library and the cache folder kept -- an `abandoned` job says how far it got. None for a job that is not one of this
    library's bulk edits."""
    with _lock:
        job = _held(library).get(handle)
    if job is not None and job.state == RUNNING:
        return job.status()
    if job is not None:
        # Over in this process. Another process may have carried it on since (the installed app and the repo-run app share
        # the library): the state file, written on every change, is the record; this process's memory is only older or the same.
        moved = bulk_edit.read_state(library, handle)
        if moved is None or int(moved.get("seq", 0)) <= job.seq:
            return job.checked_status()
    return _stored_status(library, handle)


#: How many of a job's latest runs are looked through for its chain.
CHAIN_LOOK = 50


def _chain(library, handle):
    """(the run whose id is the job's, if it is still there; the NEWEST run of the job). A job is one identity across its runs:
    a resume is a run of its own, and says which job it carries on in its counts (`job`). The newest run is the one that says how
    the job stands."""
    head = runs_service.get(library, handle)
    latest = head
    for run in runs_service.runs(library, JOB, CHAIN_LOOK):     # newest first
        if run.id == handle or (run.changed or {}).get("job") == handle:
            latest = run
            break
    return (head if head is not None and head.job == JOB else None), (latest if latest is not None and latest.job == JOB else None)


def _stored_status(library, handle):
    head, latest = _chain(library, handle)
    if latest is None:
        return None
    state = bulk_edit.read_state(library, handle)
    counts = latest.changed or {}
    op = (state or {}).get("op") or counts.get("op") or ((head.changed or {}).get("op") if head is not None else None)
    source = state if state is not None else {
        "total": counts.get("total", 0), "done": counts.get("done", 0), "changed": counts.get("changed", 0),
        "unchanged": counts.get("unchanged", 0), "skipped_missing": counts.get("skipped_missing", 0),
        "skipped_damaged": counts.get("skipped_damaged", 0), "error_count": counts.get("errors", 0), "errors": [],
        "started": latest.started_at, "finished": None, "message": latest.note}
    if counts.get("state") == EXPIRED:
        shown = EXPIRED
    elif state is not None and state.get("state") in (DONE, CANCELLED, FAILED) and state.get("run_id") == latest.id:
        shown = state["state"]
    elif latest.outcome == "running":
        # A thread of this process would be in memory. Another process's is running while that process lives.
        shown = RUNNING if bulk_edit.process_alive(latest.owner) else ABANDONED
    elif latest.outcome == "abandoned":
        shown = ABANDONED
    elif counts.get("state") in (DONE, CANCELLED, FAILED):
        shown = counts["state"]
    else:
        shown = DONE if latest.outcome == "done" else FAILED
    can = resumable(op, shown) and bulk_edit.has_record(library, handle)
    message = source.get("message")
    if shown == EXPIRED:
        message = EXPIRED_SAYS
    elif shown == ABANDONED:
        message = ("TagPup was closed before this finished: %s of %s photos were done."
                   % (_number(source.get("done", 0)), _number(source.get("total", 0))))
        if can:
            message += " Resume it to carry on from there; no photo already shifted is shifted again."
        elif op == bulk_edit.TIME_SHIFT:
            message += " Its record is gone, so it cannot be resumed (History lists what was shifted)."
    elif resumable(op, shown) and not can:
        message = (message or "") + " Its record is gone, so it cannot be resumed."
    return {"job": handle, "op": op, "state": shown, "total": source.get("total", 0), "done": source.get("done", 0),
            "changed": source.get("changed", 0), "unchanged": source.get("unchanged", 0),
            "skipped_missing": source.get("skipped_missing", 0), "skipped_damaged": source.get("skipped_damaged", 0),
            "errors": list(source.get("errors") or [])[:MOST_ERRORS], "error_count": source.get("error_count", 0),
            "started": source.get("started"), "finished": source.get("finished"), "eta_seconds": None, "message": message,
            "cancelling": False, "resumable": can, "what": counts.get("what")}


def cancel(library, handle):
    """Ask job `handle` to stop after the chunk it is writing, and return its status (`cancelling` true). A job that has
    finished, or is not running in this process, is no error -- the click may have come as it ended -- and its status says
    what it is, `cancelling` false. NotFound for one that is none of the library's."""
    with _lock:
        job = _held(library).get(handle)
    if job is None:
        found = _stored_status(library, handle)
        if found is None:
            raise NotFound("There is no bulk edit %s in this library." % handle)
        return found
    if job.state == RUNNING:
        job.cancel.set()
    return job.checked_status()


#: What a resume that could not settle the chunk in flight says. Nothing was changed: the record is as it was.
COULD_NOT_SETTLE = "Could not settle the last chunk that was being written: try again in a moment."

#: What a resume says when the journal no longer holds the change the job's record names: the record of what the chunk in flight
#: wrote was in it.
JOURNAL_LOST = ("The library's journal no longer holds the last change of this bulk edit (a snapshot was restored over it?), so "
                "which photos of the last chunk were already shifted cannot be told, and a resume could shift them twice. "
                "It was not resumed; nothing was changed.")

#: ... when the journal's retention took the files of the change: the record of what the last chunk wrote is gone.
JOURNAL_PRUNED = ("The library's journal pruned the record of this bulk edit's last chunk, so which photos of it were already "
                  "shifted cannot be told, and a resume could shift them twice. It cannot be resumed safely; nothing was changed.")

#: What a resume that finds the record moved on says.
MOVED = "Another TagPup process moved this job on: reload and look at it again."


def resume(library, handle, exiftool_path, after_write=None):
    """Carry on a time shift that stopped part-way (cancelled, abandoned by a restart, or failed), from where its record says,
    shifting no photo twice. The Job; Refused for a job that is not resumable, or whose list of photos is gone; Conflict for
    one that is running, when another bulk edit runs in the library, when the last chunk could not be settled just now
    (COULD_NOT_SETTLE), when another process moved the job on meanwhile (MOVED) or when it could not be set up (SETUP_FAILED).

    The record is the state file and the journal, read here and never this process's memory of the job, which may be older (two
    processes share a library). The claim is taken first, so no other process is running one; the state is read under it, and the
    journal settled, and the job is registered, written and started only when all of it has worked, and if anything fails
    before the thread runs it is unregistered, the claim given back (its row deleted) and the state file left exactly as it was:
    the next resume starts from the same record. The sequence number the state was read at must still be the file's just before
    it is written."""
    with _lock:
        _refuse_if_running(library)
    found = _stored_status(library, handle)
    if found is None:
        raise NotFound("There is no bulk edit %s in this library." % handle)
    if found["op"] != bulk_edit.TIME_SHIFT:
        raise Refused("Only a time shift is resumed; a bulk edit of tags or people is simply started again, as it changes "
                      "nothing a photo already holds.")
    if found["state"] == DONE:
        raise Refused("Bulk edit %s has finished: there is nothing to resume." % handle)
    if found["state"] == EXPIRED:
        raise Refused(EXPIRED_SAYS)
    if not resumable(found["op"], found["state"]):
        raise Conflict("Bulk edit %s is %s: only a job that was cancelled, stopped or abandoned is resumed." % (handle, found["state"]))
    if not found["resumable"]:
        raise Refused("The record of bulk edit %s (its list of photos or its state) is gone, so it cannot be resumed without "
                      "risking shifting a photo twice. Its journal changes are in History." % handle)
    claim = _claim(library)           # outside the lock
    with _lock:
        previous = _held(library).get(handle)
    job = None
    try:
        job = _prepare_resume(library, handle, claim, exiftool_path, after_write)
        job.began_at = time.time()
        if not job._persist(mandatory=True):
            raise OSError("the job's record could not be written")
        try:
            runs_service.progress(library, job.run_id, job.counts())     # the run says which job it carries on, at once
        except Exception as problem:
            logger.warning("Could not record the resume of bulk edit %s of %s: %s", handle, library.name, problem)
        _launch(job)
    except BaseException as problem:
        if job is not None:
            _unregister(library, job, previous)
        _give_back(library, claim, "the job was not resumed")
        if isinstance(problem, Exception) and not isinstance(problem, (Refused, Conflict, NotFound)):
            logger.exception("Could not resume bulk edit %s of %s", handle, library.name)
            raise (Conflict(COULD_NOT_SETTLE) if job is None else _set_up_failed(problem)) from None
        raise
    return job


def _prepare_resume(library, handle, claim, exiftool_path, after_write):
    """The Job a resume would run, made from the state file and the journal under the claim; nothing is written, registered or
    started here but by the settle, which finishes what the journal itself left half done."""
    state = bulk_edit.read_state(library, handle)
    ids = bulk_edit.read_ids(library, handle)
    if state is None or ids is None:
        raise Refused("The record of bulk edit %s (its list of photos) is gone, so it cannot be resumed without risking "
                      "shifting a photo twice. Its journal changes are in History." % handle)
    job = Job(library, handle, claim.run_id, bulk_edit.Edit.from_json(state["edit"]), ids, exiftool_path, after_write, state)
    job.state, job.message, job.finished = RUNNING, None, None
    with _lock:
        _refuse_if_running(library)
    _settle_flight(library, job, state)
    again = bulk_edit.read_state(library, handle)
    if again is None or int(again.get("seq", 0)) != int(state.get("seq", 0)):
        raise Conflict(MOVED)
    with _lock:
        _refuse_if_running(library)
        _register(library, job)
    return job


def _settle_flight(library, job, state):
    """The chunk a stop left in flight: refuse (Refused) when the journal no longer holds the change the record names; finish what
    the journal left half done (settle, which takes over this job's own changes whoever is named as owning them -- the claim
    says nothing carries them out -- and which does not raise when one cannot be finished), and refuse (Conflict,
    COULD_NOT_SETTLE) if any file of the chunk is still planned or writing: written or not, nobody recorded which, and
    planning it again could shift it twice; read the files the journal holds as
    conflicts for this job -- a command that stalled may have written a file and not said so -- and record those that hold the
    shift as done; then take out of the work every photo a change of this job left done, counted as changed (`credit`: until that
    chunk is finished the state's own count does not include them), and every photo whose file could not be told either way
    (changed by something else), counted as an error and left alone. Raises if the journal cannot be read, or a file cannot be
    read to decide, or the chunk is not settled: the caller then writes nothing."""
    done, flight = int(state.get("done", 0)), int(state.get("inflight", 0))
    if flight <= done:
        return
    if job.chunk is not None:
        lost = bulk_edit.journal_lost(library, job.operation, int(job.chunk))
        if lost is not None:
            raise Refused(JOURNAL_PRUNED if lost == bulk_edit.PRUNED else JOURNAL_LOST)
    file_changes.settle(library, job.exiftool_path, job.operation)
    asked = job.ids[done:flight]
    wanted = set(asked)
    if bulk_edit.unsettled(library, job.operation, asked):
        logger.warning("Bulk edit %s of %s: the journal could not finish the last chunk now", job.handle, library.name)
        raise Conflict(COULD_NOT_SETTLE)
    doubtful = [row for row in bulk_edit.conflicts(library, job.operation) if row.photo_id in wanted]
    if doubtful:
        _landed, _not_written, elsewhere = bulk_edit.decide(library, doubtful, job.exiftool_path)
        known = {each.get("id") for each in job.errors}
        for row in elsewhere:
            job.skip.add(row.photo_id)
            if row.photo_id not in known:
                job.error_count += 1
                if len(job.errors) < MOST_ERRORS:
                    job.errors.append({"id": row.photo_id, "name": os.path.basename(row.path)[:bulk_edit.MOST_TEXT],
                                       "why": bulk_edit.UNCONFIRMED})
    written = bulk_edit.shifted_ids(library, job.operation)
    shifted = [photo_id for photo_id in asked if photo_id in written]
    job.skip.update(shifted)
    job.changed += len(shifted)
    job.credit = len(shifted)


def shifted_ids(library, handle):
    """The ids of the photos job `handle` has shifted, by the journal -- the record of what was written, and exact. A set."""
    job = _held(library).get(handle)
    operation = job.operation if job is not None else None
    if operation is None:
        found = _stored_status(library, handle)
        if found is None or found["op"] is None:
            return set()
        operation = bulk_edit.operation_of(found["op"], handle)
    return bulk_edit.shifted_ids(library, operation)


def running(library=None):
    """How many bulk edits are running in this process (the library's, or every one's): what an update waits for."""
    with _lock:
        every = [_jobs.get(library.key, {})] if library is not None else list(_jobs.values())
        return sum(1 for jobs in every for job in jobs.values() if job.state == RUNNING)


def forget(library):
    """Drop what is remembered of a library's jobs in this process (the library was removed)."""
    with _lock:
        _jobs.pop(library.key, None)

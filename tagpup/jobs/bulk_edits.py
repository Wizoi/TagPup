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
row, so its folder is the library's and its write is journaled, and the journal can always tell.

The job holds its Library, not the page's: the page may switch library or be closed and the job goes on, its status reachable.
"""
import logging
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
        self.last_flush = 0.0
        if state:
            self._take_state(state)

    # ---- what is kept, and read back ----------------------------------------------------------

    def _take_state(self, state):
        """A resumed job's counts: those its state file recorded."""
        self.started = state.get("started", self.started)
        self.done = self.began = int(state.get("done", 0))
        for name in ("changed", "unchanged", "skipped_missing", "skipped_damaged", "error_count"):
            setattr(self, name, int(state.get(name, 0)))
        self.errors = list(state.get("errors") or [])
        self.run_ids = list(state.get("run_ids") or []) + [self.run_id]

    def snapshot(self, inflight=None):
        """What the state file and the library's record of the run hold, as a dict."""
        with self.lock:
            return {"job": self.handle, "op": self.edit.op, "edit": self.edit.to_json(), "state": self.state,
                    "message": self.message, "total": self.total, "done": self.done,
                    "inflight": self.inflight if inflight is None else inflight, "changed": self.changed,
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
            return {"what": self._what(), "state": self.state, "job": self.handle, "total": self.total, "done": self.done,
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

    # ---- running ------------------------------------------------------------------------------

    def _persist(self, inflight=None):
        try:
            bulk_edit.write_state(self.library, self.handle, self.snapshot(inflight))
        except Exception as problem:
            logger.warning("Could not record how far bulk edit %s of %s got: %s", self.handle, self.library.name, problem)

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
                    self.errors.append({"id": photo_id, "name": name, "why": why})
            self.done += size

    def _end(self, state, message=None):
        with self.lock:
            self.state, self.message, self.finished = state, message, time.time()
        self._persist()
        if state == DONE and self.edit.op == bulk_edit.TIME_SHIFT:
            bulk_edit.forget_ids(self.library, self.handle)
        try:
            runs_service.end(self.library, self.run_id, time.time(), self.counts(), failed=state == FAILED,
                             note=message if state == FAILED else None)
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
            self._end(FAILED, "It stopped on an error: %s" % problem)

    def _loop(self):
        streak = 0
        position = self.done
        keeps_cursor = self.edit.op == bulk_edit.TIME_SHIFT
        last_why = None
        while position < self.total:
            if self.cancel.is_set():
                return self._end(CANCELLED, "Cancelled after %s of %s photos." % (_number(self.done), _number(self.total)))
            chunk = self.ids[position:position + CHUNK]
            wanted = [photo_id for photo_id in chunk if photo_id not in self.skip]
            if keeps_cursor:
                # Which chunk is in flight, kept BEFORE it is written: a resume tells what it wrote by the journal.
                self.inflight = position + len(chunk)
                self._persist()
            try:
                out = bulk_edit.run_chunk(self.library, self.edit, wanted, self.exiftool_path, self.operation) if wanted \
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
                last_why = out.errors[-1][2]
                if streak >= GIVE_UP_AFTER:
                    return self._end(FAILED, "Stopped: %d chunks in a row could not be written (the last said: %s). %s of %s "
                                             "photos were done." % (streak, last_why, _number(self.done), _number(self.total)))
            self._flush()
            _let_writes_in(self.cancel)
        self._end(DONE)


def _stopped_by(problem):
    """The sentence for an exception that is not one photo's."""
    text = str(problem).strip() or type(problem).__name__
    return "It could not go on: %s." % text.rstrip(".")


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
        raise Conflict("A bulk edit is already running in %s, in another TagPup process. Wait for it to finish." % library.name)
    return claim


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


def start(library, edit, ids, exiftool_path, after_write=None):
    """Begin `edit` on the photos `ids` (resolved, in order) on a thread of its own, and return the Job. Conflict, nothing
    begun, when a bulk edit is running in the library (here, or in another process). A time shift keeps its list of photos
    for a resume."""
    if not ids:
        raise Refused("The selection holds no photos.")
    with _lock:
        _refuse_if_running(library)
        claim = _claim(library)
        job = Job(library, claim.run_id, claim.run_id, edit, list(ids), exiftool_path, after_write)
        bulk_edit.sweep(library)
        if edit.op == bulk_edit.TIME_SHIFT:
            bulk_edit.write_ids(library, job.handle, job.ids)
        job._persist(inflight=0)
        _register(library, job)
    try:
        runs_service.progress(library, job.run_id, job.counts())
    except Exception as problem:
        logger.warning("Could not record the start of bulk edit %s of %s: %s", job.handle, library.name, problem)
    _launch(job)
    return job


def status(library, handle):
    """The status of job `handle` of the library (Job.status), from memory if this process runs or ran it, else from what
    the library and the cache folder kept -- an `abandoned` job says how far it got. None for a job that is not one of this
    library's bulk edits."""
    with _lock:
        job = _held(library).get(handle)
    if job is not None:
        return job.status()
    return _stored_status(library, handle)


def _stored_status(library, handle):
    run = runs_service.get(library, handle)
    if run is None or run.job != JOB:
        return None
    state = bulk_edit.read_state(library, handle)
    latest = runs_service.get(library, state["run_id"]) if state and state.get("run_id") else run
    latest = latest or run
    if state is None:
        # Nothing but the library's record of the run: its counts, as the Activity page has them.
        state = {"job": handle, "op": None, "state": latest.changed.get("state"), "total": latest.changed.get("total", 0),
                 "done": latest.changed.get("done", 0), "changed": latest.changed.get("changed", 0),
                 "unchanged": latest.changed.get("unchanged", 0), "skipped_missing": latest.changed.get("skipped_missing", 0),
                 "skipped_damaged": latest.changed.get("skipped_damaged", 0), "error_count": latest.changed.get("errors", 0),
                 "errors": [], "started": latest.started_at, "finished": None, "message": latest.note}
    if state.get("state") in (DONE, CANCELLED, FAILED):
        shown = state["state"]
    elif latest.outcome == "running":
        # A thread of this process would be in memory. Another process's is running while that process lives.
        shown = RUNNING if bulk_edit.process_alive(latest.owner) else ABANDONED
    elif latest.outcome == "abandoned":
        shown = ABANDONED
    else:
        shown = DONE if latest.outcome == "done" else FAILED   # its end was recorded in the library and not in the file
    message = state.get("message")
    if shown == ABANDONED:
        message = ("TagPup was closed before this finished: %s of %s photos were done."
                   % (_number(state.get("done", 0)), _number(state.get("total", 0))))
        if state.get("op") == bulk_edit.TIME_SHIFT:
            message += " Resume it to carry on from there; no photo already shifted is shifted again."
    return {"job": handle, "op": state.get("op"), "state": shown, "total": state.get("total", 0), "done": state.get("done", 0),
            "changed": state.get("changed", 0), "unchanged": state.get("unchanged", 0),
            "skipped_missing": state.get("skipped_missing", 0), "skipped_damaged": state.get("skipped_damaged", 0),
            "errors": list(state.get("errors") or [])[:MOST_ERRORS], "error_count": state.get("error_count", 0),
            "started": state.get("started"), "finished": state.get("finished"), "eta_seconds": None, "message": message,
            "cancelling": False, "resumable": resumable(state.get("op"), shown),
            "what": (latest.changed or {}).get("what")}


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
    return job.status()


def resume(library, handle, exiftool_path, after_write=None):
    """Carry on a time shift that stopped part-way (cancelled, abandoned by a restart, or failed), from where its record says,
    shifting no photo twice. The Job; Refused for a job that is not resumable, or whose list of photos is gone; Conflict for
    one that is running or when another bulk edit runs in the library."""
    with _lock:
        existing = _held(library).get(handle)
        _refuse_if_running(library)
    found = status(library, handle)
    if found is None:
        raise NotFound("There is no bulk edit %s in this library." % handle)
    if found["op"] != bulk_edit.TIME_SHIFT:
        raise Refused("Only a time shift is resumed; a bulk edit of tags or people is simply started again, as it changes "
                      "nothing a photo already holds.")
    if found["state"] == DONE:
        raise Refused("Bulk edit %s has finished: there is nothing to resume." % handle)
    if not resumable(found["op"], found["state"]):
        raise Conflict("Bulk edit %s is %s: only a job that was cancelled, stopped or abandoned is resumed." % (handle, found["state"]))
    state = bulk_edit.read_state(library, handle) if existing is None else existing.snapshot()
    ids = bulk_edit.read_ids(library, handle)
    if state is None or ids is None:
        raise Refused("The record of bulk edit %s (its list of photos) is gone, so it cannot be resumed without risking "
                      "shifting a photo twice. Its journal changes are in History." % handle)
    with _lock:
        _refuse_if_running(library)
        claim = _claim(library)
        job = Job(library, handle, claim.run_id, bulk_edit.Edit.from_json(state["edit"]), ids, exiftool_path, after_write, state)
        job.state, job.message = RUNNING, None
        job.finished = None
        job.began_at = time.time()
        _register(library, job)
    try:
        _settle_flight(library, job, state)
    except Exception as problem:
        # Nothing is run on a guess: the files of the chunk in flight are not known.
        logger.exception("Could not settle bulk edit %s of %s", handle, library.name)
        job.state = FAILED
        job._end(FAILED, "The chunk that was being written when it stopped could not be settled (%s); nothing was written." % problem)
        return job
    job._persist()
    _launch(job)
    return job


def _settle_flight(library, job, state):
    """The chunk a stop left in flight: finish what the journal left half done (settle), then take out of the work every
    photo a change of this job left done."""
    done, flight = int(state.get("done", 0)), int(state.get("inflight", 0))
    if flight <= done:
        return
    file_changes.settle(library, job.exiftool_path)
    asked = job.ids[done:flight]
    written = bulk_edit.shifted_ids(library, job.operation)
    shifted = [photo_id for photo_id in asked if photo_id in written]
    job.skip.update(shifted)
    job.changed += len(shifted)


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

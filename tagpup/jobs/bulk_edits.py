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
so before then, from the row's owner not being alive, with how far it got (the row's counts). Nothing else is kept and nothing is
resumed: a tags or people edit started again changes nothing a photo already holds, and a delete started again asks its question
afresh. The journal holds what each finished chunk wrote (History). Time shift was resumable and is no longer an edit of a
selection (Organize shifts a folder's photos); the abandoned runs of one that an older version left are shown as abandoned.

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
#: The state an older version noted on a time shift whose record it had removed for age.
EXPIRED = "expired"

#: What is said of a bulk edit that could not be set up.
SETUP_FAILED = "The bulk edit could not be set up (%s): nothing was changed. Try again in a moment."

_jobs = {}            # {library.key: {handle: Job}}
_lock = threading.Lock()


def _number(count):
    return "{:,}".format(count)


class Job:
    """One bulk edit: what it does, to which photos, how far it has got, what it counted."""

    def __init__(self, library, handle, run_id, edit, ids, exiftool_path, after_write=None):
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
        # What it has counted. `done` is how many photos are settled: the next to do is ids[done].
        self.done = 0
        self.changed = self.unchanged = self.skipped_missing = self.skipped_damaged = self.error_count = 0
        self.errors = []
        self.last_flush = 0.0

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
        50 {id, name, why}], error_count, started, finished, eta_seconds, message, cancelling, what}. An in-memory read; nothing
        is asked of the library."""
        with self.lock:
            running = self.state == RUNNING
            eta = None
            if running and self.done > 0:
                eta = round((time.time() - self.started) / self.done * (self.total - self.done))
            return {"job": self.handle, "op": self.edit.op, "state": self.state, "total": self.total, "done": self.done,
                    "changed": self.changed, "unchanged": self.unchanged, "skipped_missing": self.skipped_missing,
                    "skipped_damaged": self.skipped_damaged, "errors": list(self.errors[:MOST_ERRORS]),
                    "error_count": self.error_count, "started": self.started, "finished": self.finished,
                    "eta_seconds": eta, "message": self.message, "cancelling": running and self.cancel.is_set(),
                    "what": self._what()}

    # ---- running ------------------------------------------------------------------------------

    def _flush(self, force=False):
        """Record the counts in the library's run, at most every FLUSH_SECONDS; and tell the page's caches that photos were
        rewritten."""
        now = time.monotonic()
        if not force and now - self.last_flush < FLUSH_SECONDS:
            return
        self.last_flush = now
        try:
            runs_service.progress(self.library, self.run_id, self.counts())
        except Exception as problem:
            logger.warning("Could not record the progress of bulk edit %s of %s: %s", self.handle, self.library.name, problem)
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

    def _end(self, state, message=None):
        with self.lock:
            self.state, self.message, self.finished = state, message, time.time()
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
            self._end(FAILED, _stopped_by(problem) + " Nothing more was written; %s of %s photos were done."
                      % (_number(self.done), _number(self.total)))

    def _loop(self):
        streak = 0
        position = 0
        while position < self.total:
            if self.cancel.is_set():
                return self._end(CANCELLED, "Cancelled after %s of %s photos." % (_number(self.done), _number(self.total)))
            chunk = self.ids[position:position + CHUNK]
            try:
                out = bulk_edit.run_chunk(self.library, self.edit, chunk, self.exiftool_path, self.operation)
            except Exception as problem:
                logger.exception("Bulk edit %s of %s: a chunk failed", self.handle, self.library.name)
                return self._end(FAILED, "%s Nothing more was written; %s of %s photos were done."
                                 % (_stopped_by(problem), _number(self.done), _number(self.total)))
            self._take(out, len(chunk))
            position += len(chunk)
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
    return "It could not go on (%s); the server's log says why." % type(problem).__name__


def _let_writes_in(cancel):
    """Between chunks, let a change of photo files that waits for the lock have it: the lock is not fair, and this thread would
    take it again before the waiter woke. Bounded, so a stream of them cannot hold a job for ever."""
    spent = 0.0
    while file_changes.waiting() and spent < MOST_WAIT and not cancel.is_set():
        time.sleep(0.01)
        spent += 0.01


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


def _unregister(library, job):
    """Take `job` out of the registry again (a start that did not get as far as a running thread). A registered job with no
    thread would be 'running' for ever: the next start refused for it, a cancel that ends nothing, an update drain that waits
    for it."""
    with _lock:
        held = _held(library)
        if held.get(job.handle) is job:
            del held[job.handle]


def start(library, edit, ids, exiftool_path, after_write=None):
    """Begin `edit` on the photos `ids` (resolved, in order) on a thread of its own, and return the Job. Conflict, nothing
    begun, when a bulk edit is running in the library (here, or in another process). All or nothing: if anything between the
    claim and the running thread fails, the job is unregistered and the claim given back, and the caller gets a Conflict with a
    sentence."""
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
        bulk_edit.sweep(library)          # what an older version kept of its jobs
        try:
            runs_service.progress(library, job.run_id, job.counts())
        except Exception as problem:
            logger.warning("Could not record the start of bulk edit %s of %s: %s", job.handle, library.name, problem)
        _launch(job)
    except BaseException as problem:
        if job is not None:
            _unregister(library, job)
        _give_back(library, claim, "it could not be set up")
        if isinstance(problem, Exception) and not isinstance(problem, (Refused, Conflict, NotFound)):
            logger.exception("Could not set up a bulk edit of %s", library.name)
            raise Conflict(SETUP_FAILED % type(problem).__name__) from None
        raise
    return job


def status(library, handle):
    """The status of job `handle` of the library (Job.status), from memory if this process runs or ran it, else from the
    library's record of the run -- an `abandoned` job says how far it got. None for a job that is not one of this library's bulk
    edits."""
    with _lock:
        job = _held(library).get(handle)
    if job is not None:
        return job.status()
    return _stored_status(library, handle)


def _stored_status(library, handle):
    run = runs_service.get(library, handle)
    if run is None or run.job != JOB:
        return None
    counts = run.changed or {}
    if counts.get("state") in (DONE, CANCELLED, FAILED, EXPIRED):
        shown = counts["state"]
    elif run.outcome == "running":
        # A thread of this process would be in memory. Another process's is running while that process lives.
        shown = RUNNING if bulk_edit.process_alive(run.owner) else ABANDONED
    elif run.outcome == "abandoned":
        shown = ABANDONED
    else:
        shown = DONE if run.outcome == "done" else FAILED
    message = run.note
    if shown == ABANDONED:
        message = ("TagPup was closed before this finished: %s of %s photos were done."
                   % (_number(counts.get("done", 0)), _number(counts.get("total", 0))))
    return {"job": handle, "op": counts.get("op"), "state": shown, "total": counts.get("total", 0), "done": counts.get("done", 0),
            "changed": counts.get("changed", 0), "unchanged": counts.get("unchanged", 0),
            "skipped_missing": counts.get("skipped_missing", 0), "skipped_damaged": counts.get("skipped_damaged", 0),
            "errors": [], "error_count": counts.get("errors", 0), "started": run.started_at, "finished": None,
            "eta_seconds": None, "message": message, "cancelling": False, "what": counts.get("what")}


#: A job that stopped part-way is offered to the page for this long.
CURRENT_DAYS = 30


def current(library):
    """The bulk edit a page opening the library should show: the one running (here, or in another process), else the latest
    one that stopped part-way -- cancelled, failed or abandoned by a restart -- within CURRENT_DAYS; None when the latest
    finished, or there has been none. Its status, as `status` gives it. One read of the library's latest run, and only when
    nothing runs in this process."""
    with _lock:
        running_here = next((job for job in _held(library).values() if job.state == RUNNING), None)
    if running_here is not None:
        return running_here.status()
    found = runs_service.runs(library, JOB, limit=1)
    if not found:
        return None
    seen = status(library, found[0].id)
    if seen is None or seen["state"] in (DONE, EXPIRED):
        return None
    if seen["state"] != RUNNING and (time.time() - (seen.get("started") or 0)) > CURRENT_DAYS * 86400:
        return None
    return seen


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


def running(library=None):
    """How many bulk edits are running in this process (the library's, or every one's): what an update waits for."""
    with _lock:
        every = [_jobs.get(library.key, {})] if library is not None else list(_jobs.values())
        return sum(1 for jobs in every for job in jobs.values() if job.state == RUNNING)


def forget(library):
    """Drop what is remembered of a library's jobs in this process (the library was removed)."""
    with _lock:
        _jobs.pop(library.key, None)

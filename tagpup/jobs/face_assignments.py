"""A bulk assignment of faces as a job: faces and the photos' person tags together, a step at a time, resumable (docs/ARCHITECTURE.md,
"Faces and the photo's person tag", #907).

Identify Faces' Assign, Unmatch and Ignore cluster, and Re-examine this folder, used to change faces only. Now each is the
decision of a single face (tagpup.services.face_people) for many photos, so it writes photo files, and runs here on a thread of its own
in the server while the request that started it waits for the end (`finished`), as a bulk edit's page polls (tagpup.jobs.bulk_edits).

**How it runs.** The plan (tagpup.services.face_assignment) is decided and refused as a whole first, then written to the library's
cache folder BEFORE the first step, with the state. Each step is a few faces (a few photos' files, one journaled change of photo
files each, under the one lock of changes of photo files for the step only); between steps a write that waits for the lock is let in.
The state (how many steps are done, what they counted) is rewritten after each step.

**Interrupted.** A crash, a restart or a closed window leaves the plan, the state and `running` in the library's `job_runs`; the
next claim marks it `abandoned`, and the status says how far it got. `resume` carries on from the first step not recorded done. A
step is idempotent (face_assignment), so the step that was under way is simply run again: its photos' tags and faces are what
they are, and nothing is written twice. Cancel stops after the step under way.

**One at a time.** One assignment runs in a library (the claim is in `job_runs`, so another process is refused too); a second
click is a Conflict naming it. Writes of other faces go on; each step decides again under the faces' write lock.

**What is counted** is what happened: faces named, unnamed or ruled out, tag files written or taken off, and an error entry for every
photo whose file could not be written (its face is left as it was). A step that raises is the job's failure, with a fixed sentence.
Counts only in the library's record; never a name or a path.
"""
import logging
import os
import threading
import time

from tagpup.core import runs
from tagpup.core.result import Conflict, NotFound, Refused, Result
from tagpup.services import bulk_edit, face_assignment, file_changes
from tagpup.services import face_people
from tagpup.services import job_runs as runs_service

logger = logging.getLogger(__name__)

#: The job's name in `job_runs`.
JOB = "assign faces"

RUNNING, DONE, CANCELLED, FAILED, ABANDONED = "running", "done", "cancelled", "failed", "abandoned"

#: Errors kept with a sentence, for the page; the rest are counted.
MOST_ERRORS = 50
#: The longest the job waits between steps for a write that wants the lock, in seconds.
MOST_WAIT = 5.0
KEPT = 10

_jobs = {}            # {library.key: {handle: Job}}
_lock = threading.Lock()


class Job:
    def __init__(self, library, handle, run_id, plan, exiftool_path, told=None, after_step=None, state=None):
        self.library, self.handle, self.run_id = library, handle, run_id
        self.plan, self.exiftool_path = plan, exiftool_path
        self.told, self.after_step = told, after_step
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.state = RUNNING
        self.message = None
        self.started = time.time()
        self.ended = None
        self.run_ids = [run_id]
        self.done = 0               # steps recorded done
        self.changed = self.tags_written = self.tags_removed = 0
        self.errors, self.error_count = [], 0
        self.warnings = []
        self.matched_ids, self.photos = [], []
        self.seq = 0
        if state:
            self.started = state.get("started", self.started)
            self.done = int(state.get("done", 0))
            self.changed, self.tags_written = int(state.get("changed", 0)), int(state.get("tags_written", 0))
            self.tags_removed = int(state.get("tags_removed", 0))
            self.errors, self.error_count = list(state.get("errors") or []), int(state.get("error_count", 0))
            self.warnings = list(state.get("warnings") or [])
            self.matched_ids = list(state.get("matched_ids") or [])
            self.photos = list(state.get("photos") or [])
            self.seq = int(state.get("seq", 0))
            self.run_ids = list(state.get("run_ids") or []) + [run_id]

    # ---- what is kept and told -------------------------------------------------------------------------

    def _faces_done(self):
        return sum(len(step) for step in self.plan["steps"][:self.done])

    def _what(self):
        text = "%s: %s of %s faces" % (face_assignment.DESCRIPTIONS[self.plan["op"]], self._faces_done(), self.plan["faces"])
        return text if self.state == RUNNING else "%s [%s]" % (text, self.state)

    def counts(self):
        with self.lock:
            return {"what": self._what(), "state": self.state, "job": self.handle, "op": self.plan["op"],
                    "total": self.plan["faces"], "done": self._faces_done(), "changed": self.changed,
                    "tags_written": self.tags_written, "tags_removed": self.tags_removed, "errors": self.error_count}

    def snapshot(self):
        with self.lock:
            return {"job": self.handle, "op": self.plan["op"], "state": self.state, "seq": self.seq + 1, "done": self.done,
                    "changed": self.changed, "tags_written": self.tags_written, "tags_removed": self.tags_removed,
                    "errors": self.errors, "error_count": self.error_count, "warnings": self.warnings,
                    "matched_ids": self.matched_ids, "photos": self.photos, "message": self.message,
                    "started": self.started, "finished": self.ended, "run_id": self.run_id, "run_ids": self.run_ids}

    def _persist(self):
        state = self.snapshot()
        try:
            face_assignment.write_state(self.library, self.handle, state)
        except Exception as problem:
            # The next step supersedes it; a resume from an older cursor repeats an idempotent step.
            logger.warning("Could not record how far assignment %s of %s got: %s", self.handle, self.library.name, problem)
            return False
        with self.lock:
            self.seq = state["seq"]
        return True

    def status(self):
        """For the page: an in-memory read. Counts and sentences, never a name."""
        with self.lock:
            steps = len(self.plan["steps"])
            return {"job": self.handle, "op": self.plan["op"], "state": self.state, "total": self.plan["faces"],
                    "done": self._faces_done(), "steps": steps, "steps_done": self.done, "changed": self.changed,
                    "tags_written": self.tags_written, "tags_removed": self.tags_removed,
                    "errors": list(self.errors[:MOST_ERRORS]), "error_count": self.error_count,
                    "warnings": list(self.warnings[:MOST_ERRORS]), "message": self.message,
                    "started": self.started, "finished": self.ended, "cancelling": self.state == RUNNING and self.cancel.is_set(),
                    "resumable": self.state in (CANCELLED, FAILED, ABANDONED), "what": self._what()}

    def outcome(self):
        """What the request that waited answers: the faces done, the faces not, the sentence."""
        with self.lock:
            planned = [face_id for step in self.plan["steps"] for face_id in (
                [each[0] for each in step] if self.plan["op"] == face_assignment.GUESS else step)]
            return {"matched_ids": list(self.matched_ids), "planned_ids": planned, "photos": list(self.photos),
                    "changed": self.changed, "tags_written": self.tags_written, "tags_removed": self.tags_removed,
                    "error_count": self.error_count, "warnings": list(self.warnings), "state": self.state,
                    "message": self.message, "job": self.handle}

    # ---- running ---------------------------------------------------------------------------------------

    def _take(self, step, result):
        with self.lock:
            self.changed += result.changed
            details = result.details
            self.tags_written += details.get("tags_written", 0)
            self.tags_removed += details.get("tags_removed", 0)
            if details.get("matched_ids"):
                self.matched_ids.extend(details["matched_ids"])
            named = details.get("named_ids") or {}
            if named:
                self.matched_ids.extend(named)
            if details.get("tag_problem"):
                self.warnings.append(str(details["tag_problem"])[:200])
            problems = list(result.errors) + ([("the people", result.refused)] if result.refused else [])
            self.error_count += len(problems)
            for what, why in problems:
                if len(self.errors) < MOST_ERRORS:
                    self.errors.append({"name": os.path.basename(str(what))[:120], "why": str(why)[:200]})
            self.done += 1

    def _end(self, state, message=None):
        with self.lock:
            self.state, self.message, self.ended = state, message, time.time()
        self._persist()
        if state == DONE:
            face_assignment.forget(self.library, self.handle)
        try:
            runs_service.end(self.library, self.run_id, time.time(), self.counts(), failed=state == FAILED,
                             note=message if state == FAILED else None)
        except Exception as problem:
            logger.warning("Could not record the end of assignment %s of %s: %s", self.handle, self.library.name, problem)
        self.finished.set()

    def run(self):
        try:
            with runs.running(runs.job_tag(self.library.name, self.run_id)):
                self._loop()
        except BaseException as problem:
            logger.exception("Assignment %s of %s stopped", self.handle, self.library.name)
            self._end(FAILED, "It could not go on (%s); the server's log says why. %s of %s faces were done."
                      % (type(problem).__name__, self._faces_done(), self.plan["faces"]))

    def _loop(self):
        writer = face_people.Writer(self.exiftool_path, told=self.told)
        last = 0.0
        while self.done < len(self.plan["steps"]):
            if self.cancel.is_set():
                return self._end(CANCELLED, "Cancelled after %s of %s faces." % (self._faces_done(), self.plan["faces"]))
            index = self.done
            try:
                result = face_assignment.run_step(self.library, self.plan, index, writer)
            except Exception as problem:
                if isinstance(problem, (NotFound, Conflict)):
                    # This step's faces went away or changed meanwhile (a face ruled out in another window): theirs, not the job's.
                    result = Result()
                    result.fail("a face", str(problem))
                else:
                    raise
            self._take(self.plan["steps"][index], result)
            if self.after_step is not None:
                try:
                    self.after_step(result)
                except Exception as problem:
                    logger.warning("After assignment %s of %s: %s", self.handle, self.library.name, problem)
            self._persist()
            if time.monotonic() - last > 2.0:
                last = time.monotonic()
                try:
                    runs_service.progress(self.library, self.run_id, self.counts())
                except Exception as problem:
                    logger.warning("Could not record the progress of assignment %s: %s", self.handle, problem)
            spent = 0.0
            while file_changes.waiting() and spent < MOST_WAIT and not self.cancel.is_set():
                time.sleep(0.01)
                spent += 0.01
        self._end(DONE)


# ---- The registry ---------------------------------------------------------------------------------------------

def _held(library):
    return _jobs.setdefault(library.key, {})


def _running_one(library):
    return next((job for job in _held(library).values() if job.state == RUNNING), None)


def _claim(library):
    claim = runs_service.claim(library, JOB, library.name, time.time())
    if not claim:
        if claim.why == "behind":
            raise Conflict("%s has not been brought up to date yet: open it in TagPup once and try again." % library.name)
        where = "in this TagPup" if bulk_edit.is_this_process(claim.holder) else "in another TagPup process"
        raise Conflict("Faces are already being assigned in %s, %s. Wait for it to finish." % (library.name, where))
    return claim


def _give_back(library, claim):
    try:
        if not runs_service.discard(library, claim.run_id):
            runs_service.end(library, claim.run_id, time.time(), {"what": "assignment not begun", "state": FAILED}, failed=True,
                             note="It was not begun.")
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


def _refuse_if_running(library):
    job = _running_one(library)
    if job is not None:
        raise Conflict("Faces are already being assigned in %s (%s of %s done). Wait for it to finish, or cancel it."
                       % (library.name, job._faces_done(), job.plan["faces"]))


def refuse_if_running(library):
    """Conflict, with a sentence, while an assignment runs in this process: asked before anything slow is done for a new one."""
    with _lock:
        _refuse_if_running(library)


def start(library, plan, exiftool_path, told=None, after_step=None):
    """Begin `plan` (tagpup.services.face_assignment) on a thread of its own and return the Job, whose `finished` is set at the
    end. Conflict, nothing begun, when an assignment runs in the library (here or in another process); a plan with no steps is a
    Job already done. All or nothing: the plan and state are written before the thread runs, and if that fails the claim is given
    back and nothing was changed."""
    with _lock:
        _refuse_if_running(library)
    claim = _claim(library)
    job = None
    try:
        with _lock:
            _refuse_if_running(library)
            job = Job(library, claim.run_id, claim.run_id, plan, exiftool_path, told, after_step)
            _register(library, job)
        face_assignment.sweep(library)
        face_assignment.write_plan(library, job.handle, plan)
        if not job._persist():
            raise OSError("the job's record could not be written")
        job.thread = threading.Thread(target=job.run, name="AssignFaces", daemon=True)
        job.thread.start()
    except BaseException as problem:
        if job is not None:
            with _lock:
                _held(library).pop(job.handle, None)
            face_assignment.forget(library, job.handle)
        _give_back(library, claim)
        if isinstance(problem, Exception) and not isinstance(problem, (Refused, Conflict, NotFound)):
            logger.exception("Could not set up an assignment of faces in %s", library.name)
            raise Conflict("The assignment could not be set up (%s): nothing was changed. Try again in a moment."
                           % type(problem).__name__) from None
        raise
    return job


def _stored(library, handle):
    """(plan, state) a restart left for job `handle`, or (None, None)."""
    return face_assignment.read_plan(library, handle), face_assignment.read_state(library, handle)


def status(library, handle):
    """The status of job `handle`: from memory if this process runs or ran it, else from what the cache folder kept (a job whose
    process is gone says `abandoned` and how far it got). None for none of the library's."""
    with _lock:
        job = _held(library).get(handle)
    if job is not None:
        return job.status()
    plan, state = _stored(library, handle)
    if plan is None or state is None:
        return None
    shown = state.get("state", ABANDONED)
    if shown == RUNNING:
        run = runs_service.get(library, state.get("run_id", handle))
        shown = RUNNING if run is not None and bulk_edit.process_alive(run.owner) else ABANDONED
    done = int(state.get("done", 0))
    return {"job": handle, "op": plan["op"], "state": shown, "total": plan["faces"],
            "done": sum(len(step) for step in plan["steps"][:done]), "steps": len(plan["steps"]), "steps_done": done,
            "changed": state.get("changed", 0), "tags_written": state.get("tags_written", 0),
            "tags_removed": state.get("tags_removed", 0), "errors": list(state.get("errors") or [])[:MOST_ERRORS],
            "error_count": state.get("error_count", 0), "warnings": list(state.get("warnings") or []),
            "message": state.get("message") or ("TagPup was closed before this finished: %s of %s faces were done; resume it to "
                                                "carry on from there." % (sum(len(s) for s in plan["steps"][:done]), plan["faces"])),
            "started": state.get("started"), "finished": state.get("finished"), "cancelling": False,
            "resumable": shown in (CANCELLED, FAILED, ABANDONED), "what": None}


def current(library):
    """The assignment a page opening the library should show: the one running here, else the latest that stopped part-way and can
    be resumed; None otherwise."""
    with _lock:
        running = _running_one(library)
        held = list(_held(library).values())
    if running is not None:
        return running.status()
    try:
        found = runs_service.runs(library, JOB, 1)
    except Exception as problem:
        logger.warning("Could not read the assignment runs of %s: %s", library.name, problem)
        return None
    if not found:
        return None
    handle = (found[0].changed or {}).get("job") or found[0].id
    seen = status(library, handle)
    if seen is None or seen["state"] in (DONE,) or not (seen["resumable"] or seen["state"] == RUNNING):
        return None
    return seen


def cancel(library, handle):
    """Ask job `handle` to stop after the step under way; its status. NotFound for none of the library's."""
    with _lock:
        job = _held(library).get(handle)
    if job is None:
        found = status(library, handle)
        if found is None:
            raise NotFound("There is no assignment %s in this library." % handle)
        return found
    if job.state == RUNNING:
        job.cancel.set()
    return job.status()


def resume(library, handle, exiftool_path, told=None, after_step=None):
    """Carry on a job that stopped part-way (cancelled, failed, abandoned by a restart) from the first step not recorded done. The
    Job. Refused for one that cannot be (finished, or its plan gone), Conflict while another runs."""
    with _lock:
        _refuse_if_running(library)
    found = status(library, handle)
    if found is None:
        raise NotFound("There is no assignment %s in this library." % handle)
    if not found["resumable"]:
        raise Refused("Assignment %s is %s: only one that was cancelled, stopped or abandoned is resumed." % (handle, found["state"]))
    plan, state = _stored(library, handle)
    if plan is None or state is None:
        raise Refused("The record of assignment %s is gone, so it cannot be resumed." % handle)
    claim = _claim(library)
    job = None
    try:
        with _lock:
            _refuse_if_running(library)
            job = Job(library, handle, claim.run_id, plan, exiftool_path, told, after_step, state)
            _register(library, job)
        if not job._persist():
            raise OSError("the job's record could not be written")
        try:
            runs_service.progress(library, job.run_id, job.counts())
        except Exception as problem:
            logger.warning("Could not record the resume of assignment %s: %s", handle, problem)
        job.thread = threading.Thread(target=job.run, name="AssignFaces", daemon=True)
        job.thread.start()
    except BaseException as problem:
        _give_back(library, claim)
        if job is not None:
            with _lock:
                _held(library).pop(handle, None)
        if isinstance(problem, Exception) and not isinstance(problem, (Refused, Conflict, NotFound)):
            logger.exception("Could not resume assignment %s of %s", handle, library.name)
            raise Conflict("The assignment could not be resumed (%s). Try again in a moment." % type(problem).__name__) from None
        raise
    return job


def running(library=None):
    """How many assignments are running in this process: what an update waits for."""
    with _lock:
        every = [_jobs.get(library.key, {})] if library is not None else list(_jobs.values())
        return sum(1 for jobs in every for job in jobs.values() if job.state == RUNNING)


def forget(library):
    with _lock:
        _jobs.pop(library.key, None)

"""Name faces from tags, as one job with a question in the middle (docs/ARCHITECTURE.md, "Name faces from tags", #789).

A photo's person tag names its face on a save (#788), and `faces-from-tags` and `cluster-faces` do it for the photos
already in a library, from the command line. The apps' button, here, does both, on a thread of its own in the process that
was asked, and the page asks how it is getting on. Nothing is run on the owner's data without an answer:

1. **Plan.** The plan of faces-from-tags (tagpup.services.faces_from_tags.plan) -- how many faces a photo's tag names alone,
   how many by looking like the person's confirmed faces, how many photos are left -- read from the library and written
   nowhere. On photo_index it is about 13 s, so it is the job's first step, with its progress and Cancel, never a request.
   The job then WAITS FOR AN ANSWER with the plan in memory and no claim held (a question left open for a day holds nothing up):
   the owner is asked "Name N faces in M photos ...?".
2. **Apply** (on Yes). The plan the question showed is applied by the same code the CLI's `--apply` uses
   (faces_from_tags.faces_from_tags, `planned=`): one journaled change of the library, which History undoes. It is
   all or nothing: a face named or ruled out since the plan was read refuses the whole change and nothing is written. A second
   apply of a library is the CLI's `--again` (#840): the question says "applied before", and the Yes is that.
3. **Group** (if the owner ticked it; off by default). tagpup.services.identities.resolve: the faces of the whole library
   are grouped by who they look like and each group is named from its photos' tags. It re-derives every automatic name in the
   library, keeps the names given by hand, and is not journaled, so History cannot take it back. It uses no model and no
   graphics card.

**What Cancel does.** In the plan: stops it, nothing changed. In the question: No, nothing changed. At the write of step 2:
the write is one SQLite transaction and cannot be stopped once begun; Cancel asked before it stops the job with nothing
written, asked during it lets the write finish (all of it) and skips step 3, and the status says which. In step 3: stops it
before the names are written (resolve announces "saving" and writes in one commit; a Cancel before the announcement writes
nothing, after it the commit finishes). So Cancel never leaves a half change.

**One at a time.** A library has at most one of these jobs, and none while it is indexed, synced, being worked on by Suggest,
verified or edited in bulk, or while its faces are being clustered (the flag TagTuner's writes honour); the claim is
in the library's `job_runs` (tagpup.services.job_runs) so another process is refused too, and each phase is a run of its own
that the Activity page lists with counts, never a name. The faces are held against writes from the Yes to the end: TagTuner's
assignments and TagPup's face writes are refused (`hold`) while names are being given.

**A restart.** The thread dies with its process: the job is gone from memory, its run is `running` and the next claim marks it
`abandoned`. The change of step 2 either committed or did not (one transaction; the journal settles what was committed); step 3
either wrote its names (one commit) or none. The status of a job this process no longer knows says so, from the run.

The job holds its Library, not the page's: the page may switch library or be closed and the job goes on.
"""
import contextlib
import logging
import threading
import time

from tagpup.core import paths, runs
from tagpup.core.result import Conflict, NotFound, Refused
from tagpup.jobs import bulk_edits
from tagpup.services import bulk_edit
from tagpup.services import faces as face_service
from tagpup.services import faces_from_tags, identities, search
from tagpup.services import job_runs as runs_service

logger = logging.getLogger(__name__)

#: The job's name in `job_runs`.
JOB = "name faces"

PLANNING, ASKING, APPLYING, GROUPING = "planning", "asking", "applying", "grouping"
DONE, CANCELLED, FAILED, EXPIRED, ABANDONED = "done", "cancelled", "failed", "expired", "abandoned"
#: The states in which a thread works.
WORKING = (PLANNING, APPLYING, GROUPING)

#: How long a plan waits for its answer, in seconds. Older, the faces are likely not what it read: ask again.
ASK_SECONDS = 15 * 60

#: Jobs remembered in this process for each library (the latest).
KEPT = 5

#: What each step says it is doing, and what share of its phase it is (planning: reading to deciding; grouping: reading to
#: saving). Only roughly right: the bar has to keep moving forward at a believable rate, not predict the end. From
#: photo_index, 2026-10-07: the plan is mostly comparing faces; grouping is mostly one call of DBSCAN.
STAGES = {
    "reading": ("Reading the photos' tags and faces", 0.0, 0.08),
    "checking": ("Checking the photos with one face and one person", 0.08, 0.17),
    "comparing": ("Comparing faces with the confirmed faces of the people", 0.25, 0.65),
    "deciding": ("Deciding which faces to name", 0.90, 0.10),
}
#: Grouping, measured on a copy of photo_index (225,000 faces, 2026-10-07; 53 min in all): reading 2 s, DBSCAN 220 s, voting 2 s,
#: propagating 112 s, matching the rest 2,807 s (89%), writing 19 s.
GROUPING_STAGES = {
    "reading": ("Reading every face of the library", 0.0, 0.005),
    "grouping": ("Grouping the faces by who they look like (this step shows no progress inside; a few minutes)", 0.005, 0.07),
    "voting": ("Naming the groups from their photos' tags", 0.075, 0.005),
    "propagating": ("Placing the faces of photos with several people", 0.08, 0.035),
    "matching": ("Checking the faces still unnamed against the named (most of the time)", 0.115, 0.875),
    "saving": ("Writing the names", 0.99, 0.01),
}

#: What the status says of a restart, and of a plan that was not answered.
RESTARTED = ("TagPup was closed before this finished. A change of the names is either written whole (History lists it, with "
             "Undo) or not at all; grouping either wrote its names or wrote none.")
EXPIRED_SAYS = "The plan was not answered in %d minutes and was let go: the faces may have changed. Nothing was changed." % (
    ASK_SECONDS // 60)
REFUSED_SAYS = ("Something changed while the question was open (a name given, or a person's tag taken off, in TagTuner or "
                "TagPup, say): nothing was written. Ask again.")
APPLIED_MEANWHILE = ("The names were written by another run since this plan was read (in another window or process); they are "
                     "references for this one now. Nothing was written. Start again to see what can be named now.")
UNDO_LOST = ("Grouping rewrote names of faces that change wrote, so History can no longer be counted on to undo it "
             "(and it cannot undo grouping).")
NO_FACES = "This library holds no faces yet: index photos first. Nothing was changed."

_jobs = {}            # {library.key: {handle: Job}}
_lock = threading.Lock()


class Stopped(Exception):
    """Raised from the progress hook of a plan or of grouping, to stop it before it has written anything."""


class AlreadyWorking(Conflict):
    """Another of these jobs is working in the library: `job` is its status, so a page that asked again can show it."""

    def __init__(self, message, job):
        super().__init__(message)
        self.job = job


def _number(count):
    return "{:,}".format(count)


def _plural(count, one, many=None):
    return "%s %s" % (_number(count), one if count == 1 else (many or one + "s"))


class Job:
    """One run of the button: what it is for, where it has got to, what it counted."""

    def __init__(self, library, handle, folder, hold):
        self.library, self.handle = library, handle
        self.folder, self.hold = folder, hold
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.state = PLANNING
        self.phase = PLANNING
        self.stage, self.done, self.total = "reading", 0, 1
        self.writing = False            # past the last place a Cancel can stop it
        self.message = None
        self.started = time.time()
        self.asked = None
        self.finished = None
        self.run_id = handle
        #: The plan in memory while it waits for an answer, and what was read of it.
        self.planned = None
        self.earlier = False
        self.plan = None
        self.group = False
        #: What was written: the change and the faces of step 2, the faces step 3 left named that were not.
        self.applied = None
        self.grouped = None
        #: The open folder's named and unnamed faces before and after, if the page named a folder.
        self.in_folder = None
        self.library_before = None

    # ---- what the page reads ------------------------------------------------------------------

    def _expire(self):
        if self.state == ASKING and time.time() - self.asked > ASK_SECONDS:
            self.state, self.message, self.finished = EXPIRED, EXPIRED_SAYS, time.time()
            self.planned = None

    def _percent(self):
        """How far the phase has got, 0 to 100; None for a step with no count (the write of the names)."""
        stages = STAGES if self.phase == PLANNING else GROUPING_STAGES if self.phase == GROUPING else {}
        if self.stage not in stages:
            return None
        _label, start, width = stages[self.stage]
        within = max(0.0, min(1.0, self.done / self.total)) if self.total else 0.0
        return int(round((start + width * within) * 100))

    def status(self):
        """For the page: {job, state, phase, stage, label, percent, done, total, elapsed, cancelling, can_cancel, message,
        plan, group, applied, grouped, in_folder, folder, library, started, finished}. An in-memory read; nothing is
        asked of the library. Counts only, never a name or a path."""
        with self.lock:
            self._expire()
            working = self.state in WORKING
            labels = STAGES if self.phase == PLANNING else GROUPING_STAGES
            if self.phase == APPLYING:
                label = "Writing the names of the faces (one change, which History can undo)"
            else:
                label = labels.get(self.stage, (self.stage,))[0]
            return {"job": self.handle, "library": self.library.name, "state": self.state, "phase": self.phase,
                    "stage": self.stage, "label": label if working else None, "percent": self._percent() if working else None,
                    "done": self.done, "total": self.total, "started": self.started, "finished": self.finished,
                    "elapsed": round((self.finished or time.time()) - self.started),
                    "cancelling": working and self.cancel.is_set(),
                    "can_cancel": self.state == ASKING or (working and not self.writing),
                    "message": self.message, "plan": self.plan, "group": self.group, "applied": self.applied,
                    "grouped": self.grouped, "in_folder": self.in_folder, "folder": self.folder is not None,
                    "ask_seconds": ASK_SECONDS if self.state == ASKING else None}

    def counts(self, what):
        """What the library's record of a run holds: numbers and one sentence, never a name."""
        found = {"what": what, "state": self.state, "job": self.handle}
        if self.plan:
            found.update(faces=self.plan["faces"], photos=self.plan["photos"], left=self.plan["left"])
        if self.applied:
            found.update(written=self.applied["changed"], change=self.applied["change"] or 0)
        if self.grouped:
            found.update(grouped_people=self.grouped["people"], grouped_named_more=self.grouped["named_more"])
        return found

    # ---- the steps ----------------------------------------------------------------------------

    def _step(self, stage, done=0, total=1):
        """The hook of a plan and of grouping: where it has got, and the place to stop. The last stage of grouping is the
        write: stopped before it is announced, not after."""
        if self.cancel.is_set():
            raise Stopped()
        with self.lock:
            self.stage, self.done, self.total = stage, done, total
            if self.phase == GROUPING and stage == "saving":
                self.writing = True

    def _end_run(self, what, failed=False, note=None):
        try:
            runs_service.end(self.library, self.run_id, time.time(), self.counts(what), failed=failed, note=note)
        except Exception as problem:
            logger.warning("Could not record the end of name-faces job %s of %s: %s", self.handle, self.library.name, problem)

    def _record(self, what):
        try:
            runs_service.progress(self.library, self.run_id, self.counts(what))
        except Exception as problem:
            logger.warning("Could not record the progress of name-faces job %s of %s: %s", self.handle, self.library.name,
                           problem)

    def _finish(self, state, message, what, failed=False):
        with self.lock:
            self.state, self.message, self.finished = state, message, time.time()
            self.planned = None
        self._end_run(what, failed=failed, note=message if failed else None)

    def _library_folder(self):
        """The open folder's named and unnamed faces, or None: not asked, or not readable (logged)."""
        if self.folder is None:
            return None
        try:
            return face_service.named_counts(self.library, self.folder)
        except Exception as problem:
            logger.warning("Could not count the faces of the open folder of %s: %s", self.library.name, problem)
            return None

    def run_plan(self):
        """Phase 1's thread: read the plan, then wait for the answer (or end, with nothing to ask)."""
        what = "name faces from tags: plan"
        try:
            with runs.running(runs.job_tag(self.library.name, self.run_id)):
                library_counts = face_service.named_counts(self.library)
                if not library_counts["named"] and not library_counts["unnamed"]:
                    self.in_folder = None
                    return self._finish(DONE, NO_FACES, what)
                self.library_before = library_counts
                self.in_folder = {"before": self._library_folder()}
                planned = faces_from_tags.plan(self.library, on_step=self._step)
                earlier = faces_from_tags.earlier_apply(self.library)
                counts = planned.counts
                by_tag = counts["named_by_the_tag_alone"]
                with self.lock:
                    self.planned, self.earlier = planned, earlier
                    self.plan = {"faces": planned.size, "by_tag": by_tag, "by_comparison": planned.size - by_tag,
                                 "photos": by_tag + counts["photos_named_by_comparison"],
                                 "left": counts["photos_left_for_identify_faces"], "earlier_apply": earlier,
                                 "again": faces_from_tags.AGAIN if earlier else None}
                    self.state, self.asked = ASKING, time.time()
                    self.done = self.total = 1
                self._end_run(what)
        except Stopped:
            self._finish(CANCELLED, "Cancelled: nothing was changed.", what)
        except BaseException as problem:   # a thread that raised leaves a job that says why, not one still 'planning'
            logger.exception("Name faces from tags in %s: the plan failed", self.library.name)
            self._finish(FAILED, "The plan could not be read (%s); the server's log says why. Nothing was changed."
                         % type(problem).__name__, what, failed=True)

    def run_apply(self):
        """Phase 2's and 3's thread: the change of the names, then, if asked, grouping. `hold` keeps writes of faces out."""
        what = "name faces from tags" + (" and group the rest" if self.group else "")
        try:
            with runs.running(runs.job_tag(self.library.name, self.run_id)), self.hold():
                written = 0
                if self.planned is not None and self.planned.size:
                    if self.cancel.is_set():
                        return self._finish(CANCELLED, "Cancelled before anything was written: nothing was changed.", what)
                    with self.lock:
                        self.phase, self.stage, self.writing = APPLYING, "writing", True
                    result = faces_from_tags.faces_from_tags(self.library, apply=True, again=self.earlier, planned=self.planned)
                    if result.refused or result.errors:
                        logger.warning("Name faces from tags in %s: the change was not written: %s", self.library.name,
                                       result.refused or result.errors)
                        return self._finish(FAILED, APPLIED_MEANWHILE if result.refused == faces_from_tags.AGAIN else
                                            REFUSED_SAYS if result.refused else
                                            "The change could not be written; the server's log says why. Nothing was written.",
                                            what, failed=True)
                    written = result.changed
                    with self.lock:
                        self.applied = {"changed": written, "change": result.details.get("change")}
                    self._record(what)
                if self.group:
                    if self.cancel.is_set():
                        return self._finish(CANCELLED, self._said_written("Grouping was not run."), what)
                    self._group(what)
                else:
                    self._after(what)
        except Stopped:
            self._finish(CANCELLED, self._said_written("Grouping was stopped before it wrote any name."), what)
        except BaseException as problem:
            logger.exception("Name faces from tags in %s failed", self.library.name)
            self._finish(FAILED, "It could not go on (%s); the server's log says why. %s" % (
                type(problem).__name__, self._said_written("")), what, failed=True)

    def _said_written(self, then):
        """What was written before the job stopped, then `then`."""
        if self.applied:
            return ("%s written as one change (History lists it, with Undo). %s"
                    % (_plural(self.applied["changed"], "face name was", "face names were"), then)).strip()
        return ("Nothing was changed. " + then).strip()

    def _group(self, what):
        with self.lock:
            self.phase, self.stage, self.done, self.total, self.writing = GROUPING, "reading", 0, 1, False
        before = face_service.named_counts(self.library)
        index = search.PhotoIndex(self.library.path, model=None)      # no model: grouping reads the faces' own vectors
        try:
            if not index.load():
                raise OSError("the library could not be opened")
            people = identities.resolve(index, on_step=self._step)
        finally:
            index.close()
        after = face_service.named_counts(self.library)
        with self.lock:
            self.grouped = {"people": len(people), "named_more": after["named"] - before["named"],
                            "named": after["named"]}
        self._after(what)

    def _after(self, what):
        """The job is over: what the open folder holds now, and the sentence."""
        if self.in_folder is not None and self.in_folder.get("before") is not None:
            self.in_folder["after"] = self._library_folder()
        parts = []
        if self.applied and self.grouped:
            parts.append("%s given from the photos' tags (one change in History). %s"
                         % (_plural(self.applied["changed"], "face name"), UNDO_LOST))
        elif self.applied:
            parts.append("%s given from the photos' tags (one change in History, which Undo takes back)."
                         % _plural(self.applied["changed"], "face name"))
        if self.grouped:
            delta = self.grouped["named_more"]
            parts.append("Grouping re-derived the library's automatic names (%s): %s." % (
                _plural(self.grouped["people"], "person", "people"),
                ("%s more named faces than before it ran" % _number(delta)) if delta > 0 else
                ("%s fewer named faces than before it ran" % _number(-delta)) if delta < 0 else
                "the same number of named faces as before it ran"))
        if not parts:
            parts.append("Nothing needed naming.")
        self._finish(DONE, " ".join(parts), what)


# ---- The registry -------------------------------------------------------------------------------

def _held(library):
    return _jobs.setdefault(library.key, {})


def _working(library):
    return next((job for job in _held(library).values() if job.state in WORKING), None)


def _waiting(library):
    found = next((job for job in _held(library).values() if job.state == ASKING), None)
    if found is not None:
        found.status()          # lets an old one go
    return found if found is not None and found.state == ASKING else None


def other_work(library):
    """Why not now, as sentences, from what this module can see: a bulk edit of the library's photos here, or in another
    process (its claim in the library). [] when none."""
    said = []
    if bulk_edits.running(library):
        said.append("a bulk edit is running")
    else:
        try:
            last = runs_service.latest(library).get((bulk_edits.JOB, library.name), (None, None))[0]
        except Exception as problem:
            logger.warning("Could not read the running jobs of %s: %s", library.name, problem)
            last = None
        if last is not None and last.outcome == runs_service.RUNNING and bulk_edit.process_alive(last.owner):
            said.append("a bulk edit is running in another TagPup process")
    return said


def _refuse_if_working(library):
    job = _working(library)
    if job is not None:
        what = {PLANNING: "is reading its plan", APPLYING: "is writing the names", GROUPING: "is grouping the faces"}[job.state]
        raise AlreadyWorking("Name faces from tags already %s in %s. Wait for it to finish, or cancel it."
                             % (what, library.name), job.status())


def _claim(library):
    claim = runs_service.claim(library, JOB, library.name, time.time())
    if not claim:
        if claim.why == "behind":
            raise Conflict("%s has not been brought up to date yet: open it in TagPup once and try again." % library.name)
        where = "in this TagPup" if bulk_edit.is_this_process(claim.holder) else "in another TagPup process"
        raise Conflict("Name faces from tags is already running in %s, %s. Wait for it to finish." % (library.name, where))
    return claim


def _give_back(library, claim):
    try:
        if not runs_service.discard(library, claim.run_id):
            runs_service.end(library, claim.run_id, time.time(), {"what": "name faces from tags not begun", "state": FAILED},
                             failed=True, note="It was not begun.")
    except Exception as problem:
        logger.warning("Could not give back the claim of run %s of %s: %s", claim.run_id, library.name, problem)


def _refuse_if_busy(library, busy):
    said = list(busy() if busy is not None else []) + other_work(library)
    if said:
        raise Conflict("Not now in %s: %s. Naming faces reads and rewrites the library's faces and tags, so it waits for "
                       "that to finish (it needs no graphics card)." % (library.name, "; ".join(said)))


def start(library, folder=None, hold=None, busy=None):
    """Begin the plan, on a thread of its own, and return the Job (its status is the page's). `folder`: the folder the page
    has open, whose faces are counted before and after. `hold`: a context manager held from the Yes to the end, keeping
    writes of faces out (the web layer's clustering flag). `busy`: a function giving the sentences for what runs in the
    library that this module cannot see (indexing, Suggest, a sync, a Verify, the flag).

    A job already waiting for its answer in the library is returned as it is (the click came again, or from another page);
    Conflict, nothing begun, when one is planning, writing or grouping, or when the library is busy (here or in another
    process: the claim in the library says so before anything is read)."""
    if folder is not None:
        folder = paths.stored(folder)
    hold = hold or contextlib.nullcontext
    with _lock:
        _refuse_if_working(library)
        waiting = _waiting(library)
        if waiting is not None:
            return waiting
    _refuse_if_busy(library, busy)
    claim = _claim(library)
    job = None
    try:
        with _lock:
            _refuse_if_working(library)
            waiting = _waiting(library)
            if waiting is not None:
                _give_back(library, claim)
                return waiting
            job = Job(library, claim.run_id, folder, hold)
            _register(library, job)
        job._record("name faces from tags: plan")
        job.thread = threading.Thread(target=job.run_plan, name="NameFaces", daemon=True)
        job.thread.start()
    except BaseException:
        if job is not None:
            _unregister(library, job)
        _give_back(library, claim)
        raise
    return job


def _register(library, job):
    held = _held(library)
    held[job.handle] = job
    while len(held) > KEPT:
        oldest = next((handle for handle, each in held.items() if each.state not in WORKING + (ASKING,)), None)
        if oldest is None:
            break
        del held[oldest]


def _unregister(library, job):
    with _lock:
        held = _held(library)
        if held.get(job.handle) is job:
            del held[job.handle]


def confirm(library, handle, group=False, busy=None):
    """The answer Yes to the plan of job `handle`: write its names and, if `group`, group the rest of the faces, on a thread
    of its own. Returns the Job. NotFound for a job this process does not know (a restart took it); Conflict when the job is
    not waiting for an answer, another is working, or the library is busy; Refused when the plan was let go (EXPIRED_SAYS)
    or there is nothing to do."""
    with _lock:
        job = _held(library).get(handle)
    if job is None:
        raise NotFound("There is no plan %s in this TagPup: it was closed since the question was asked. Nothing was changed; "
                       "start again." % handle)
    status = job.status()
    if status["state"] == EXPIRED:
        raise Refused(EXPIRED_SAYS)
    if status["state"] != ASKING:
        raise Conflict("This plan is not waiting for an answer (it is %s)." % status["state"])
    if not job.plan["faces"] and not group:
        raise Refused("There is nothing to write: no face can be named from its tag. Tick the grouping to try the rest.")
    _refuse_if_busy(library, busy)
    claim = _claim(library)
    try:
        with _lock:
            _refuse_if_working(library)
            if job.state != ASKING:
                raise Conflict("This plan is not waiting for an answer (it is %s)." % job.state)
            job.state = job.phase = APPLYING if job.plan["faces"] else GROUPING
            job.group, job.run_id = bool(group), claim.run_id
            job.stage, job.done, job.total, job.message = "writing" if job.state == APPLYING else "reading", 0, 1, None
            job.writing = job.state == APPLYING
        job._record("name faces from tags")
        job.thread = threading.Thread(target=job.run_apply, name="NameFaces", daemon=True)
        job.thread.start()
    except BaseException:
        _give_back(library, claim)
        raise
    return job


def cancel(library, handle):
    """Ask job `handle` to stop, and return its status. A plan waiting for its answer is let go at once (nothing was changed). A
    working job stops at its next place to stop (see the module's docstring: what Cancel does at each step); `cancelling`
    says so. A job that has ended is no error: the click may have come as it ended. NotFound for one this process does not
    know and the library has no record of."""
    with _lock:
        job = _held(library).get(handle)
    if job is None:
        found = stored_status(library, handle)
        if found is None:
            raise NotFound("There is no name-faces job %s in this library." % handle)
        return found
    job.status()
    if job.state == ASKING:
        _let_go(job)
    elif job.state in WORKING:
        job.cancel.set()
    return job.status()


def _let_go(job):
    with job.lock:
        job.state, job.message, job.finished, job.planned = CANCELLED, "Cancelled: nothing was changed.", time.time(), None


def status(library, handle=None):
    """The status of job `handle` (the library's latest when None): from memory if this process runs or ran it, else from
    the library's record of the run. None when there is none."""
    with _lock:
        held = _held(library)
        job = held.get(handle) if handle is not None else (list(held.values())[-1] if held else None)
    if job is not None:
        return job.status()
    return stored_status(library, handle) if handle is not None else None


def stored_status(library, handle):
    """What the library's `job_runs` say of a job this process does not hold: a run whose process ended is `abandoned` (the
    status says what that means for the names), one that ended is as it ended. None when no run is the job's."""
    try:
        listed = runs_service.runs(library, JOB, 50)
    except Exception as problem:
        logger.warning("Could not read the runs of %s: %s", library.name, problem)
        return None
    chain = [run for run in listed if run.id == handle or (run.changed or {}).get("job") == handle]
    if not chain:
        return None
    latest = chain[0]
    counts = latest.changed or {}
    state = counts.get("state") if latest.outcome != "running" else (
        counts.get("state") if bulk_edit.process_alive(latest.owner) else ABANDONED)
    if latest.outcome == "abandoned":
        state = ABANDONED
    return {"job": handle, "library": library.name, "state": state or (DONE if latest.outcome == "done" else FAILED),
            "phase": None, "stage": None, "label": None, "percent": None, "done": 0, "total": 0, "started": None,
            "finished": None, "elapsed": None, "cancelling": False, "can_cancel": False,
            "message": RESTARTED if state == ABANDONED else counts.get("what"),
            "plan": None, "group": False, "applied": None, "grouped": None, "in_folder": None, "folder": False,
            "ask_seconds": None}


def current(library):
    """The job a page opening the library should pick up: one planning, waiting for its answer, writing or grouping in this
    process, else None."""
    with _lock:
        found = _working(library) or _waiting(library)
    return found.status() if found is not None else None


def running(library=None):
    """How many jobs are working in this process (the library's, or every one's): what an update waits for. A plan waiting for
    its answer is not work."""
    with _lock:
        every = [_jobs.get(library.key, {})] if library is not None else list(_jobs.values())
        return sum(1 for jobs in every for job in jobs.values() if job.state in WORKING)


def forget(library):
    """Drop what is remembered of a library's jobs in this process (the library was removed)."""
    with _lock:
        _jobs.pop(library.key, None)

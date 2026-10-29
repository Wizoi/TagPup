"""Recurring jobs: what each is, when it is due, and the one runner that runs what is due
inside whatever TagPup process is up (docs/ARCHITECTURE.md, phase 8, "Recurring jobs").

Each job declares its name, its period, why it is scheduled at all, whether it runs per
library, the service it calls and, when a Result that is ok can still mean the run did not
do its work, how to tell (`failed`); registering one is one line:

    @JOBS.job("sync", DAILY, reason=CATCH_UP)
    def _sync(library, run):
        return sync.run(library, ...)          # a Result

A job run per library is called with the Library and a Run (`now`, `forced`, and what
the entry point `given` the runner, the process's Runtime); one not run per library with
the list of libraries, and its runs are recorded in the first of them by name.

Its runs are recorded in the library (`job_runs`, tagpup.store.job_runs), so any
process knows what is due. A job is due when the last run that ended started a period
ago or more (an hour less, SLACK, for a period of a day or more) -- or, when that run
failed, RETRY_AFTER ago, if sooner -- so a missed
period runs once, not once per period missed. A run is claimed in the library before it
starts, so two processes, or two threads, never run one job for one library at once; a
claim left by a process that has ended is taken over. A library behind this version's
schema is left alone until an app opens it and migrates it: running a job never
migrates a library.

Only the web server runs them: it looks every CHECK_EVERY on
a thread of its own (Runner.start), stopped at shutdown (owner, 2026-09-26). The Activity page
lists them and runs one by hand; neither the CLI nor the MCP server
runs what is due as it starts. No Windows Task Scheduler. A server the tests started
runs none, unless a test asks (tagpup.runtime.runs_recurring_jobs).

The folder watcher's sync of a whole library is a run of the `sync` job too (sync_whole), claimed
and ended as this runner's are (run_claimed): one walk of a library at a time, whichever of the two
wants it (#2102), and a whole sync by either counts as the daily one.
"""
import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from tagpup.core import runs
from tagpup.core.result import Result
from tagpup.services import job_runs
from tagpup.services import snapshots as snapshot_service
from tagpup.services import sync as sync_service

logger = logging.getLogger(__name__)

HOUR = 3600
DAY = 24 * HOUR


@dataclass(frozen=True)
class Period:
    name: str
    seconds: int


DAILY = Period("daily", DAY)
WEEKLY = Period("weekly", 7 * DAY)
MONTHLY = Period("monthly", 30 * DAY)


def every_hours(hours):
    """A period of `hours` hours."""
    return Period("every %d hours" % hours, int(hours * HOUR))


#: Why a job runs on a schedule. Work that follows an event is done when the event
#: happens, never left for a schedule to find (docs/ARCHITECTURE.md, phase 8, "Event-driven
#: first"); a schedule is only for what no event announces -- the library's safety (the
#: snapshots) and a catch-up in case an event was missed (sync). A job that names none of these is refused as it is registered.
SAFETY, CATCH_UP = "safety", "catch-up"
REASONS = (SAFETY, CATCH_UP)

#: How much sooner than its period a job of a day or more is due. The server looks every
#: CHECK_EVERY, not on the second: a daily that started at 09:00 was not due at 08:55 the
#: next day, ran at 09:05, and slipped later every day. The snapshots allow the same
#: (tagpup.store.snapshots.SLACK), so the daily the job asks for is taken.
SLACK = HOUR

#: How soon a job whose last run failed is tried again, when its period is longer.
RETRY_AFTER = HOUR

#: How often the web server's thread looks for what is due, and how long after the
#: server starts it first looks: a server starting has enough to do.
CHECK_EVERY = 10 * 60
FIRST_CHECK_AFTER = 60


@dataclass(frozen=True)
class Job:
    name: str
    period: Period
    #: Why it is scheduled: one of REASONS.
    reason: str
    per_library: bool
    #: call(library, run) -> Result; for a job not run per library, call(libraries, run).
    call: Callable
    about: str = ""
    #: failed(result) -> why the run did not do its work although its Result is ok (a job_runs.Failure: its sentence the
    #: run's note), or None when it did: such a run ends failed, and is tried again RETRY_AFTER later, not a period later.
    failed: Optional[Callable] = None


@dataclass
class Run:
    """What a job is called with beside its library."""
    now: float
    #: Asked for now (the Activity page's Run now), not found due.
    forced: bool = False
    given: Dict[str, Any] = field(default_factory=dict)


class Registry:
    """The recurring jobs, by name, in the order registered."""

    def __init__(self):
        self._jobs = {}

    def job(self, name, period, reason=None, per_library=True, about="", failed=None):
        """Register the function it decorates as the job `name`, scheduled for `reason`,
        one of REASONS: ValueError for none, or another. `failed`: Job.failed."""
        if reason not in REASONS:
            raise ValueError("the job %r says no reason it is scheduled (%s), and what an event announces is"
                             " done when it happens, not on a schedule" % (name, ", ".join(REASONS)))

        def register(call):
            if name in self._jobs:
                raise ValueError("a job named %r is registered already" % name)
            self._jobs[name] = Job(name, period, reason, per_library, call, about or (call.__doc__ or "").strip(),
                                   failed)
            return call
        return register

    def get(self, name):
        return self._jobs.get(name)

    def names(self):
        return list(self._jobs)

    def __iter__(self):
        return iter(list(self._jobs.values()))


JOBS = Registry()


@JOBS.job("snapshots", DAILY, reason=SAFETY)
def _snapshots(library, run):
    """A daily snapshot of the library, and from it the weekly and the monthly when theirs
    are due (tagpup.services.snapshots)."""
    return snapshot_service.take(library, now=run.now, force=run.forced)


#: The daily catch-up sync, and the job the folder watcher's sync of a whole library is claimed as (sync_whole).
SYNC = "sync"


@JOBS.job(SYNC, DAILY, reason=CATCH_UP, failed=sync_service.could_not_look)
def _sync(library, run):
    """The whole library brought in step with its folders, applied (tagpup.services.sync,
    through the process's Runtime: the library's ExifTool and its index queue). A catch-up
    check: the watcher syncs each folder as it changes (owner, 2026-09-26), and this finds
    what a missed notification left. The Runtime is the entry point's to give; a runner
    given none cannot sync. A sync that could not look at a folder (a share that did not
    answer) ends failed, and is tried again in an hour (#2103)."""
    runtime = run.given.get("runtime")
    if runtime is None:
        raise RuntimeError("the sync job needs the process's Runtime, which this runner was not given")
    return runtime.sync(library, apply=True)


# ---- When a job is due -----------------------------------------------------------------

def wait_after(job, last):
    """How long after `last` (the last run that ended) `job` is due again."""
    if last is None:
        return 0
    if last.outcome == job_runs.DONE:
        return job.period.seconds - (SLACK if job.period.seconds >= DAY else 0)
    return min(RETRY_AFTER, job.period.seconds)


def is_due(job, last, now):
    """Is `job` due at `now`, its last run that ended `last` (a Run, or None)?"""
    return last is None or now - last.started_at >= wait_after(job, last)


def next_due(job, last):
    """When `job` is due next, seconds since the epoch: now for one never run."""
    return None if last is None else last.started_at + wait_after(job, last)


def _record_library(job, library, libraries):
    """(where a run of `job` is recorded, the library name it is recorded for)."""
    if job.per_library:
        return library, library.name
    return (sorted(libraries, key=lambda each: each.name)[0] if libraries else None), None


def status(library, registry=JOBS, now=None):
    """Each job's last run in `library`, and when it is due next: [{"name", "period",
    "reason", "per_library", "about", "last": {"started", "finished", "outcome", "changed"} or None,
    "running", "next_due"}]. Counts only (job_runs.shown_counts: never the id of a sync's record): a run's note, which
    can name a path, is left out.
    A job not run per library shows what `library` records of it: its runs are recorded
    in the first library by name."""
    now = time.time() if now is None else now
    # By name without case, as the library's runs are kept: `--db harbour` is Harbour.db.
    found = {(job, name.lower() if name else None): runs for (job, name), runs in job_runs.latest(library).items()}
    listed = []
    for job in registry:
        last, ended = found.get((job.name, library.name.lower() if job.per_library else None), (None, None))
        due = next_due(job, ended)
        listed.append({
            "name": job.name, "period": job.period.name, "reason": job.reason, "per_library": job.per_library,
            "about": job.about,
            "last": None if last is None else {"started": last.started, "finished": last.finished,
                                               "outcome": last.outcome,
                                               "changed": job_runs.shown_counts(last.changed)},
            "running": last is not None and last.outcome == "running",
            "next_due": job_runs.stamp(max(now, due)) if due is not None else job_runs.stamp(now),
        })
    return listed


def overview(library, registry=JOBS, now=None, limit=10):
    """What the Activity page shows of each job for `library`: status()'s entry, with its
    last `limit` runs for the library, newest first -- {"id", "started", "finished",
    "seconds", "outcome", "changed", "error", "failure", "run"} (`error`, the note a failed or
    abandoned run left, which can name a path: for the page that answers this PC alone; `failure`, a failed run's kind,
    job_runs.FAILURES) -- and `failing`: it is one of Needs you (job_runs.needs_you: its newest run that ended failed,
    asking the owner to act; an abandoned one neither adds nor settles), read from the rows Needs you reads
    (job_runs.runs_of) whatever `limit` lists. It stays so until a later run is done."""
    listed = status(library, registry, now)
    for entry in listed:
        job = registry.get(entry["name"])
        found = job_runs.runs_of(library, job.name, library.name if job.per_library else None)
        entry["runs"] = [{"id": run.id, "started": run.started, "finished": run.finished,
                          "seconds": (int(max(0, job_runs.seconds(run.finished) - job_runs.seconds(run.started)))
                                      if run.finished else None),
                          "outcome": run.outcome, "changed": job_runs.shown_counts(run.changed), "error": run.note,
                          "failure": job_runs.kind_of(run), "run": runs.job_tag(run.library, run.id)}
                         for run in found[:limit]]
        entry["failing"] = bool(job_runs.needs_you(found))
    return listed


# ---- The runner ----------------------------------------------------------------------------

@dataclass
class Outcome:
    """What became of one job for one library: `result` when it ran, else `why` not
    ("running", "not due", "behind", "no library", "unclaimed"); `error` when its service
    raised."""
    job: str
    library: Optional[str]
    run_id: Optional[int] = None
    result: Optional[Result] = None
    why: Optional[str] = None
    error: Optional[BaseException] = None

    @property
    def ran(self):
        return self.run_id is not None


def run_claimed(job, where, run_for, call, clock=time.time, due=None, started=None):
    """Run `job` once for `run_for` (a library's name; None for a job not run per library), its run recorded in the
    library `where` (job_runs.run, which claims and ends it): refused while a live process holds a run of it, or when
    `due(last, now)`, asked with the last run that ended, says no; else `call(now)`, which returns the Result, and the
    run ended done, or failed when the call raised, its Result is not ok, or `job.failed` says why it did not do its
    work. `started(outcome, now)`, a context manager, is entered once the run is claimed and held while it runs. An
    Outcome. The runner's (Runner.run_job) and the folder watcher's sync of a whole library (sync_whole)."""
    with job_runs.run(where, job.name, run_for, clock, due) as run:
        if not run:
            return Outcome(job.name, run_for, why=run.why, error=run.error)
        outcome = Outcome(job.name, run_for, run_id=run.run_id)
        logger.info("Running the job %s for %s", job.name, run_for or "every library")
        with (started or _nothing)(outcome, run.now):
            try:
                # Every line the run logs carries its tag: the Activity page's "Logs for this run".
                with runs.running(runs.job_tag(run_for, run.run_id)):
                    outcome.result = call(run.now)
            except Exception as e:
                logger.exception("The job %s for %s failed", job.name, run_for or "every library")
                outcome.error = e
        run.ends(outcome.result, outcome.error,
                 job.failed(outcome.result) if job.failed is not None and outcome.result is not None else None)
    return outcome


def _nothing(outcome, now):
    return contextlib.nullcontext()


def covered_since(noted):
    """due(last, now) for a sync wanted at `noted` (time.time()): due unless the last run that ended was done and started
    since -- it looked at every folder after it was wanted (a run that could not ends failed, Job.failed). A run's
    start is kept to the second, so one that started in the second it was wanted covers it."""
    return lambda last, now: last is None or last.outcome != job_runs.DONE or last.started_at < int(noted)


def sync_whole(library, call, noted):
    """The folder watcher's sync of the whole of `library`: `call()`, its Result, as a run of the `sync` job, claimed
    and ended as the runner's, so the watcher and the runner never walk one library at once (#2102: photo_index's
    runs 94 and 95, two walks of the share at once). Not run when a run of it that ended done started since `noted`,
    when the watcher wanted it (covered_since); refused "running" while another holds it: the watcher asks again
    later. A run of it is the job's daily run."""
    return run_claimed(JOBS.get(SYNC), library, library.name, lambda now: call(), due=covered_since(noted))


class Runner:
    """Runs what is due of `registry` over the libraries `libraries()` returns, read at
    each look (a library made meanwhile is found), at the times `clock()` says. `given`
    is handed to every job (Run.given): the process's Runtime."""

    def __init__(self, libraries, registry=JOBS, clock=time.time, given=None):
        self._libraries = libraries
        self._registry = registry
        self._clock = clock
        self._given = dict(given or {})
        self._stop = threading.Event()
        self._thread = None
        #: How many jobs are running now, on any thread: busy() while one is.
        self._running = 0
        self._running_lock = threading.Lock()
        #: {run id: {"job", "library", "run_id", "started", "forced"}} of the runs under way here.
        self._current = {}

    @property
    def registry(self):
        return self._registry

    def libraries(self):
        return list(self._libraries())

    def run_job(self, job, library=None, libraries=None, force=False, claimed=None):
        """Run `job` once, for `library` (a job run per library) or over `libraries` (one
        that is not): when it is due, or when `force`d, unless another run holds it. An
        Outcome. `claimed(outcome)` is told, once the run is claimed or refused, what the
        Outcome is so far: Run now's answer (start_job)."""
        told = claimed or (lambda outcome: None)
        libraries = self.libraries() if libraries is None else libraries
        where, run_for = _record_library(job, library, libraries)
        if where is None:
            told(Outcome(job.name, run_for, why="no library"))
            return Outcome(job.name, run_for, why="no library")
        target = library if job.per_library else libraries

        @contextlib.contextmanager
        def under_way(outcome, now):
            with self._running_lock:
                self._running += 1
                self._current[outcome.run_id] = {"job": job.name, "library": run_for, "run_id": outcome.run_id,
                                                 "started": job_runs.stamp(now), "forced": force,
                                                 "run": runs.job_tag(run_for, outcome.run_id)}
            told(outcome)
            try:
                yield
            finally:
                with self._running_lock:
                    self._running -= 1
                    self._current.pop(outcome.run_id, None)
        outcome = run_claimed(job, where, run_for,
                              lambda now: job.call(target, Run(now=now, forced=force, given=self._given)),
                              self._clock, None if force else (lambda last, now: is_due(job, last, now)), under_way)
        if not outcome.ran:
            told(outcome)
        return outcome

    def run(self, name, library=None, force=True):
        """Run the job `name` now: for `library`, or for every library when it is None and
        the job runs per library. [Outcome]. KeyError for a name not registered."""
        job = self._registry.get(name)
        if job is None:
            raise KeyError(name)
        libraries = self.libraries()
        if not job.per_library:
            return [self.run_job(job, libraries=libraries, force=force)]
        return [self.run_job(job, each, libraries, force=force) for each in ([library] if library else libraries)]

    def start_job(self, name, library=None, wait=10.0):
        """Run the job `name` now, for `library` (or over every library, for a job not run
        per library), on a thread of its own: the Activity page's Run now. Returns once the
        run is claimed or refused, within `wait` seconds: the Outcome so far -- `run_id` when
        it started, else `why` not ("running": a run holds it) -- or None when neither came in
        time. KeyError for a name not registered."""
        job = self._registry.get(name)
        if job is None:
            raise KeyError(name)
        told = threading.Event()
        box = {}

        def claimed(outcome):
            box["outcome"] = outcome
            told.set()

        def work():
            try:
                self.run_job(job, library if job.per_library else None, None, force=True, claimed=claimed)
            except Exception:
                logger.exception("Running the job %s now failed", name)
            finally:
                told.set()

        threading.Thread(target=work, name="RunNowThread", daemon=True).start()
        told.wait(wait)
        return box.get("outcome")

    def status(self):
        """The runs under way in this process: [{"job", "library", "run_id", "started",
        "forced", "run"}]."""
        with self._running_lock:
            return [dict(each) for each in self._current.values()]

    def run_due(self):
        """Run every job that is due, for every library. [Outcome], one per job and library
        looked at. Stops between jobs once stop() is called."""
        outcomes = []
        libraries = self.libraries()
        for job in self._registry:
            for library in (libraries if job.per_library else [None]):
                if self._stop.is_set():
                    return outcomes
                outcomes.append(self.run_job(job, library, libraries))
        return outcomes

    def _run_due_logged(self):
        try:
            self.run_due()
        except Exception:
            logger.exception("Looking for the recurring jobs that are due failed")

    def busy(self):
        """Is a job running now? What an update waits for (tagpup.web.lifecycle)."""
        with self._running_lock:
            return self._running > 0

    def start(self, every=CHECK_EVERY, first_after=FIRST_CHECK_AFTER):
        """Look for what is due every `every` seconds, the first time `first_after` from
        now, on a daemon thread, until stop(). Started again after stop() -- an update
        that could not finish its drain takes work again -- it looks again."""
        if self._thread is not None:
            return self._thread
        if self._stop.is_set():
            self._stop = threading.Event()
        stop = self._stop

        def loop():
            wait = first_after
            while not stop.wait(wait):
                self._run_due_logged()
                wait = every

        self._thread = threading.Thread(target=loop, name="RecurringJobsThread", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout=30):
        """Stop looking, and wait up to `timeout` for a job under way to end. True when the
        thread has ended; one that has not is a daemon, and ends with the process: its run
        is taken over as abandoned by the next."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

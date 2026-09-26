"""Recurring jobs: what each is, when it is due, and the one runner that runs what is due
inside whatever TagPup process is up (docs/ARCHITECTURE.md, phase 8, "Recurring jobs").

Each job declares its name, its period, why it is scheduled at all, whether it runs per
library, and the service it calls; registering one is one line:

    @JOBS.job("sync", DAILY, reason=CATCH_UP)
    def _sync(library, run):
        return sync.run(library, ...)          # a Result

A job run per library is called with the Library and a Run (`now`, `forced`, and what
the entry point `given` the runner, the process's Runtime); one not run per library with
the list of libraries, and its runs are recorded in the first of them by name.

Its runs are recorded in the library (`job_runs`, tagpup.store.job_runs), so any
process knows what is due. A job is due when the last run that ended started a period
ago or more -- or, when that run failed, RETRY_AFTER ago, if sooner -- so a missed
period runs once, not once per period missed. A run is claimed in the library before it
starts, so two processes, or two threads, never run one job for one library at once; a
claim left by a process that has ended is taken over. A library behind this version's
schema is left alone until an app opens it and migrates it: running a job never
migrates a library.

Only the web server -- the always-on process -- runs them: it looks every CHECK_EVERY on
a thread of its own (Runner.start), stopped at shutdown (owner, 2026-09-26). The CLI
lists them and runs one by hand (`jobs`, `jobs run NAME`); neither it nor the MCP server
runs what is due as it starts. No Windows Task Scheduler. A server the tests started
runs none, unless a test asks (tagpup.runtime.runs_recurring_jobs).
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from tagpup.core.result import Result
from tagpup.services import job_runs
from tagpup.services import journal as journal_service
from tagpup.services import snapshots as snapshot_service

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
#: snapshots), the retention of what it keeps (the journal), and a catch-up in case an
#: event was missed (sync). A job that names none of these is refused as it is registered.
SAFETY, RETENTION, CATCH_UP = "safety", "retention", "catch-up"
REASONS = (SAFETY, RETENTION, CATCH_UP)

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


@dataclass
class Run:
    """What a job is called with beside its library."""
    now: float
    #: Asked for now (`jobs run`), not found due.
    forced: bool = False
    given: Dict[str, Any] = field(default_factory=dict)


class Registry:
    """The recurring jobs, by name, in the order registered."""

    def __init__(self):
        self._jobs = {}

    def job(self, name, period, reason=None, per_library=True, about=""):
        """Register the function it decorates as the job `name`, scheduled for `reason`,
        one of REASONS: ValueError for none, or another."""
        if reason not in REASONS:
            raise ValueError("the job %r says no reason it is scheduled (%s), and what an event announces is"
                             " done when it happens, not on a schedule" % (name, ", ".join(REASONS)))

        def register(call):
            if name in self._jobs:
                raise ValueError("a job named %r is registered already" % name)
            self._jobs[name] = Job(name, period, reason, per_library, call, about or (call.__doc__ or "").strip())
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


@JOBS.job("prune-journal", WEEKLY, reason=RETENTION)
def _prune_journal(library, run):
    """Let the journal's changes older than its retention go (tagpup.services.journal.prune)."""
    return journal_service.prune(library, apply=True)


# Sync (phase 8c) registers here.


# ---- When a job is due -----------------------------------------------------------------

def wait_after(job, last):
    """How long after `last` (the last run that ended) `job` is due again."""
    if last is None:
        return 0
    if last.outcome == job_runs.DONE:
        return job.period.seconds
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
    "running", "next_due"}]. Counts only: a run's note, which can name a path, is left out.
    A job not run per library shows what `library` records of it: its runs are recorded
    in the first library by name."""
    now = time.time() if now is None else now
    found = job_runs.latest(library)
    listed = []
    for job in registry:
        last, ended = found.get((job.name, library.name if job.per_library else None), (None, None))
        due = next_due(job, ended)
        listed.append({
            "name": job.name, "period": job.period.name, "reason": job.reason, "per_library": job.per_library,
            "about": job.about,
            "last": None if last is None else {"started": last.started, "finished": last.finished,
                                               "outcome": last.outcome, "changed": last.changed},
            "running": last is not None and last.outcome == "running",
            "next_due": job_runs.stamp(max(now, due)) if due is not None else job_runs.stamp(now),
        })
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

    @property
    def registry(self):
        return self._registry

    def libraries(self):
        return list(self._libraries())

    def run_job(self, job, library=None, libraries=None, force=False):
        """Run `job` once, for `library` (a job run per library) or over `libraries` (one
        that is not): when it is due, or when `force`d, unless another run holds it. An
        Outcome."""
        libraries = self.libraries() if libraries is None else libraries
        where, run_for = _record_library(job, library, libraries)
        if where is None:
            return Outcome(job.name, run_for, why="no library")
        now = self._clock()
        try:
            claim = job_runs.claim(where, job.name, run_for, now,
                                   None if force else (lambda last: is_due(job, last, now)))
        except Exception as e:
            logger.error("Could not claim the job %s in %s: %s", job.name, where.name, e)
            return Outcome(job.name, run_for, why="unclaimed", error=e)
        if not claim:
            return Outcome(job.name, run_for, why=claim.why)
        outcome = Outcome(job.name, run_for, run_id=claim.run_id)
        logger.info("Running the job %s for %s", job.name, run_for or "every library")
        run = Run(now=now, forced=force, given=self._given)
        try:
            outcome.result = job.call(library if job.per_library else libraries, run)
        except Exception as e:
            logger.exception("The job %s for %s failed", job.name, run_for or "every library")
            outcome.error = e
        try:
            ended = job_runs.finish(where, claim.run_id, self._clock(), outcome.result, outcome.error)
            if not ended:
                logger.warning("The run %d of %s was no longer this process's to end", claim.run_id, job.name)
        except Exception as e:
            logger.error("Could not record the end of the job %s for %s: %s", job.name, run_for, e)
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

    def start(self, every=CHECK_EVERY, first_after=FIRST_CHECK_AFTER):
        """Look for what is due every `every` seconds, the first time `first_after` from
        now, on a daemon thread, until stop()."""
        if self._thread is not None:
            return self._thread

        def loop():
            wait = first_after
            while not self._stop.wait(wait):
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

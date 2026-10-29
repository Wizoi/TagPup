"""The runs of the library's jobs, recorded in the library (tagpup.store.job_runs; docs/ARCHITECTURE.md, phase 8).

A run's lifecycle is `run`'s, the one place: claimed, its progress recorded while its block runs, its failure mapped, its
end written as the block ends, and an end that cannot be written kept and written as the next run of the job in the
library is claimed in this process. The jobs keep their own scheduling: the recurring runner (tagpup.jobs.recurring), the
index queue (tagpup.jobs.indexing), a root's move (tagpup.services.roots_location), the naming from tags
(tagpup.jobs.naming_faces). A run that is not recorded -- refused, the library behind this version's schema or being
written, the claim failing -- is a null run, whose block runs as its caller decides and which writes nothing.

What a run changed is recorded as counts from the Result the job's service returned --
attempted, changed, skipped, errors, and the numbers among its details -- never its
texts, which can name a path or a person.

How far a run has got while it runs (`progress`) is a phase of PHASES and counts, never a path, tried at most once every
PROGRESS_EVERY seconds a run unless its phase changes, and skipped, not waited for, while the library is being written:
a write that was skipped or failed waits its PROGRESS_EVERY like one made.
A service deep in a run says it with `tell`, into the run the code that claimed it holds on the thread (`recording`): the
sync's walk, the index's phases read from its indexer's lines.

Why a run failed is one Failure (`failure`): a kind of FAILURES and the sentence that is the run's note. The kind is
written in the run's `changed` (FAILURE), a text, so never one of its counts; what it means and the next step are read
from the kind, never stored (`next_step` names how the job is run again). A run that failed before kinds were written, or
with a kind this version does not know, reads as ERROR (`kind_of`).
"""
import contextlib
import contextvars
import logging
import numbers
import re
import threading
import time
from dataclasses import dataclass

from tagpup.core import runs as run_tags
from tagpup.core.result import SHARE_AWAY
from tagpup.store import db, job_runs

logger = logging.getLogger(__name__)

DONE, FAILED, RUNNING = job_runs.DONE, job_runs.FAILED, job_runs.RUNNING
INDEX = job_runs.INDEX
Run = job_runs.Run
stamp = job_runs.stamp
seconds = job_runs.seconds


#: Where a failed run's `changed` holds its kind, and where a Result's details carry it, set by the code that knows it.
FAILURE = "failure"

SHARE, LOCKED, WAITED, REFUSED, ERROR = "share", "locked", "waited", "refused", "error"

#: The one vocabulary of why a run failed: {kind: (what it means, the next step, whether the owner must act)}. The next
#: step's `{again}` is how the job is run again (`next_step`). WAITED is TagPup's own work in the way (the roots changing,
#: names being given from tags, a row another writer changed between the plan and the write), which the next run settles:
#: never one of Needs you.
FAILURES = {
    SHARE: ("A network share did not answer, so not every folder was looked at: the library is not in step.",
            "Check that the computer sharing the photos is awake and on the network, then {again}.", True),
    LOCKED: ("Another program was writing the library for longer than TagPup waits, so nothing was written.",
             "Let the other program finish (a CLI run, another TagPup), then {again}.", True),
    WAITED: ("TagPup's own work was in the way (the roots changing, names being given from tags, a row changed by"
             " another writer), so it stopped.", "Nothing: the next run does it.", False),
    REFUSED: ("It was refused before anything was written.", "Do what the sentence says, then {again}.", True),
    ERROR: ("Something went wrong that TagPup did not expect.",
            "Try once more: {again}. If it fails again, the log of the run says what happened.", True),
}

#: The job naming faces from their tags records its runs as (tagpup.jobs.naming_faces).
NAME_FACES = "name faces"

#: How the owner runs a job again where the Activity page has no Run now for it, by job: an index is queued again by a
#: sync of its library, which finds the files it did not read; a naming from tags is asked again in its dialog.
AGAIN = {INDEX: "run a sync of the library, which queues its folders again", NAME_FACES: "name faces from tags again"}
#: How a job the page's Run now starts is run again (the recurring runner's: a failed run is tried again RETRY_AFTER
#: later), and any other job.
RUN_NOW, DO_IT_AGAIN = "run it now (it is tried again within the hour)", "do it again"


@dataclass(frozen=True)
class Failure:
    """Why a run failed: its `kind` (FAILURES) and the `sentence` its note says."""
    kind: str
    sentence: str


def failure(error=None, result=None, failed=None, sentence=None):
    """Why a run failed, or None when it did not: the `error` it raised (LOCKED for SQLite's database locked or busy,
    else ERROR, said as its type and text); else, for a Result not ok, the kind its details say (FAILURE, set where it
    is known), SHARE for a share that did not answer (SHARE_AWAY), REFUSED for a refusal, ERROR for errors, said as the
    Result says it; else `failed`, why the job says an ok Result did not do its work (recurring.Job.failed). Said as
    `sentence` instead when the job says it its own way (the naming dialog's)."""
    if error is not None:
        found = Failure(LOCKED if db.locked(error) else ERROR, "%s: %s" % (type(error).__name__, error))
    elif result is not None and not result.ok:
        details = result.details or {}
        kind = details.get(FAILURE) or (SHARE if details.get(SHARE_AWAY) else REFUSED if result.refused else ERROR)
        found = Failure(kind, result.message())
    else:
        found = failed
    return found if found is None or sentence is None else Failure(found.kind, sentence)


def kind_of(run):
    """The kind of failure of `run`, a Run, or None when it did not fail; ERROR for one written before kinds were, and
    for a kind this version does not know (a row a newer TagPup wrote: the CLI run from the repository beside the
    installed server)."""
    if run.outcome != FAILED:
        return None
    kind = run.changed.get(FAILURE)
    return kind if kind in FAILURES else ERROR


def next_step(kind, job, run_now=False):
    """The next step after a failed run of `job` of the `kind` (FAILURES), naming how it is run again: Run now when the
    page has it for the job (`run_now`), else the job's own way (AGAIN) -- never a button that is not there."""
    return FAILURES[kind][1].format(again=RUN_NOW if run_now else AGAIN.get(job, DO_IT_AGAIN))


def runs_of(library, job, run_for):
    """The runs of `job` in `library` for the library named `run_for` (in any case; None for a job not run per library),
    newest first, from the job's last KEEP_RUNS: the rows a job's runs and `failing` on the Activity page
    (recurring.overview) and Needs you (tagpup.services.activity.needs_you) both read."""
    wanted = run_for.lower() if run_for else None
    return [run for run in job_runs.runs(library.path, job, job_runs.KEEP_RUNS)
            if (run.library.lower() if run.library else None) == wanted]


def needs_you(runs):
    """The failed runs of one job and library since its last done one, newest first, when the newest's kind asks the
    owner to act (FAILURES), else []: `runs` its runs, newest first. A run abandoned or still running neither adds nor
    settles, as `latest`'s last that ended. What makes a job `failing` (recurring.overview) and one of Needs you
    (tagpup.services.activity.needs_you)."""
    failed = []
    for run in runs:
        if run.outcome == DONE:
            break
        if run.outcome == FAILED:
            failed.append(run)
    return failed if failed and FAILURES[kind_of(failed[0])][2] else []


def counts(result):
    """What a Result says was changed, as counts: never its `percent`, where an index queue's bar stopped, which is not a
    count of what the run did (#2193)."""
    found = {"attempted": result.attempted, "changed": result.changed,
             "skipped": len(result.skipped), "errors": len(result.errors)}
    for name, value in (result.details or {}).items():
        if isinstance(value, numbers.Number) and not isinstance(value, bool) and name not in found and name != "percent":
            found[name] = value
    return found


def claim(library, job, run_for, now, due=None, changed=None):
    """Begin a run of `job` in `library`, for the library named `run_for` (None for a job
    not run per library), unless a live process has one or `due` says it is not due
    (tagpup.store.job_runs.claim), holding `changed` from the start. Its parent is the run of `library` this thread is
    part of (tagpup.core.runs.parent_run). A Claim: true with its `run_id` when begun."""
    return job_runs.claim(library.path, job, run_for, now, due, parent=run_tags.parent_run(library.name),
                          changed=changed)


def sweep_abandoned(library, now=None):
    """Mark abandoned every run of `library` still running whose process has ended (tagpup.store.job_runs.sweep_abandoned):
    what a server's start does. `now` is seconds since the epoch, now by default. Returns how many."""
    return job_runs.sweep_abandoned(library.path, time.time() if now is None else now)


def counts_so_far(library, run_id, counts):
    """Record the counts of run `run_id` so far (tagpup.store.job_runs.counts_so_far): False when it is no longer this
    process's to record."""
    return job_runs.counts_so_far(library.path, run_id, counts)


#: The phases a run says it is in, the one vocabulary of `job_runs.progress`: a sync's walk of the folders and its walk
#: again, its work after the walk, and the index's reading of metadata, its embeddings, its faces and the bake.
PHASES = ("walking", "walking_again", "after_the_walk", "metadata", "embeddings", "faces", "bake")

#: The seconds a run's progress is written at most once in, unless its phase changes.
PROGRESS_EVERY = 10.0

_COUNT_NAME = re.compile(r"[a-z][a-z0-9_]*")

#: {(the library's db.key, run id): (when its progress was last tried, written or not, its phase then)}; a run's entry
#: goes when it ends.
_written = {}
_written_lock = threading.Lock()


def _counts_only(counts):
    """`counts` with only what may be written: names of lower-case letters, digits and _, numbers that are not booleans.
    A path, a name or any text is dropped, and only how many were is logged."""
    kept = {name: value for name, value in (counts or {}).items()
            if isinstance(name, str) and _COUNT_NAME.fullmatch(name)
            and isinstance(value, (int, float)) and not isinstance(value, bool)}
    dropped = len(counts or {}) - len(kept)
    if dropped:
        logger.debug("progress: %d count(s) dropped, not a name and a number", dropped)
    return kept


def progress(library, run_id, counts, phase, now=None):
    """Record how far run `run_id` of `library` has got: in `phase`, one of PHASES (anything else is a code's mistake,
    ValueError), with `counts` ({name: number}; anything else in it is dropped). Tried at most once every
    PROGRESS_EVERY seconds a run unless the phase changed, and a try the library was busy for, or that failed, counts
    (#2196). A change of phase is rare and a sync tells its last once, so it alone waits for the library to be free, as
    long as a claim does (store.job_runs.RECORD_WITHIN); one still not written is logged and not tried again. Counts are
    not written while the library is being written; `now` is seconds since the epoch, now by default. True when written;
    False when not -- too soon, the library busy, the run no longer this process's and running, or a library a restore
    has put back at 35. Never raises for those."""
    if phase not in PHASES:
        raise ValueError("a run's phase is one of %s, not %r" % (", ".join(PHASES), phase))
    now = time.time() if now is None else now
    held = (db.key(library.path), run_id)
    with _written_lock:
        last = _written.get(held)
        if last is not None and last[1] == phase and 0 <= now - last[0] < PROGRESS_EVERY:
            return False
        # Before the write: one the library is busy for is not tried again at the next line (#2196).
        _written[held] = (now, phase)
    changed = last is not None and last[1] != phase
    written = job_runs.progress(library.path, run_id, phase, _counts_only(counts), now,
                                job_runs.RECORD_WITHIN if changed else 0.0)
    if changed and written is db.BUSY:
        logger.warning("Run %d of %s did not record its phase %s: the library was being written for %.0f s.", run_id,
                       library.name, phase, job_runs.RECORD_WITHIN)
    return written is True


#: The run under way on this thread that `tell` records into: [library, run id, whether a failure was said], or None.
_under_way = contextvars.ContextVar("tagpup_run_under_way", default=None)


@contextlib.contextmanager
def recording(library, run_id):
    """While the block runs, `tell` on this thread records into run `run_id` of `library`: what a recorded run's block
    is run inside (Recorded). A thread started meanwhile is not in it: a run of the indexer a sync queued is its own run."""
    token = _under_way.set([library, run_id, False])
    try:
        yield
    finally:
        _under_way.reset(token)


def tell(phase, counts):
    """Record how far the run under way on this thread (`recording`) has got, through `progress`: nothing when none is.
    A write that fails -- a library gone, unreadable, its job_runs not there -- is logged, a warning the first time in a
    run, and the work goes on: True only when written. A phase not in PHASES is a mistake in the code (ValueError)."""
    held = _under_way.get()
    if held is None:
        return False
    library, run_id, said = held
    try:
        return progress(library, run_id, counts, phase)
    except ValueError:
        raise
    except Exception as e:
        held[2] = True
        (logger.debug if said else logger.warning)("Could not record how far run %d of %s got: %s", run_id,
                                                    library.name, e)
        return False


def _forget(library, run_id):
    with _written_lock:
        _written.pop((db.key(library.path), run_id), None)


def end(library, run_id, now, counts, failed=None, note=None):
    """End run `run_id` with its `counts`: done with `note`, or failed with `failed`, a Failure (its sentence the note,
    its kind in the counts; tagpup.store.job_runs.finish). A run that was cancelled is done: its counts say how far it
    got. False when it was no longer this process's to end."""
    _forget(library, run_id)
    if failed is None:
        return job_runs.finish(library.path, run_id, DONE, now, counts, note)
    return job_runs.finish(library.path, run_id, FAILED, now, dict(counts, **{FAILURE: failed.kind}), failed.sentence)


def discard(library, run_id):
    """Delete run `run_id` of this process, still running, as though never claimed (tagpup.store.job_runs.discard)."""
    _forget(library, run_id)
    return job_runs.discard(library.path, run_id)


#: Why a run is not recorded when its claim raised (the library gone, unreadable, written by another for longer than
#: the claim waits): Recorded.error says what.
UNCLAIMED = "unclaimed"

#: The ends this process could not write, {(the library's db.key, job): {run id: (library, run id, when, counts, failed,
#: note)}}: each is written as the next run of that job in that library is claimed here (`run`), and leaves only once its
#: own is written or its row is found gone (#2199). It grows only by a run whose end failed, which needs a library
#: unwritable as the run ends.
_unended = {}
_unended_lock = threading.Lock()


def _write_end(library, job, run_id, when, counts, failed, note):
    """Write the end of run `run_id` of `job`; kept in _unended when it cannot be, to be written as the next run of the
    job in the library is claimed."""
    try:
        ended = end(library, run_id, when, counts, failed=failed, note=note)
    except Exception as e:
        with _unended_lock:
            _unended.setdefault((db.key(library.path), job), {})[run_id] = (library, run_id, when, counts, failed, note)
        logger.warning("Could not record the end of run %d of %s in %s, written as its next run begins: %s", run_id, job,
                       library.name, e)
        return
    if not ended:
        logger.info("Run %d of %s in %s was no longer this process's to end.", run_id, job, library.name)


def _write_ends_kept(library, job):
    """Write the ends of `job` in `library` this process could not (_unended); one that fails again is kept."""
    with _unended_lock:
        kept = _unended.pop((db.key(library.path), job), {})
    for each in kept.values():
        _write_end(each[0], job, *each[1:])


class Recorded:
    """A run of a job as `run` claimed it: true when it is recorded (`run_id`); else a null run, which writes nothing,
    `why` it is not ("running", "not due", "behind", "busy", UNCLAIMED, "no library"), `holder` the process holding a run
    of the job and `error` what the claim raised. `now` is when it was claimed, seconds since the epoch.

    Its block (`with`) is the run: what its services tell on this thread is its progress (`recording`), and as the block
    ends the run is ended -- as `ends` says, or failed when the block raised (`failure` of what it raised; `stopped`, the
    sentence, when it is not an Exception: a thread stopped), the exception going on. It is claimed on one thread and
    its block may run on another (the naming's): the block's thread is the one its progress is told on."""

    def __init__(self, library, job, now, run_id=None, why=None, holder=None, error=None, changed=None,
                 clock=time.time, stopped=None):
        self.library, self.job, self.now, self.run_id = library, job, now, run_id
        self.why, self.holder, self.error = why, holder, error
        self._changed, self._clock, self._stopped = dict(changed or {}), clock, stopped
        self._said = (None, None, None, None, None)
        self._recording = None

    def __bool__(self):
        return self.run_id is not None

    def ends(self, result=None, error=None, failed=None, counts=None, note=None):
        """What the run's end says, written as its block ends: done, or failed as `failure` says of the `error` the work
        raised, the `result` it returned or `failed` (a Failure the job gave: recurring.Job.failed, a refusal's kind);
        its counts the Result's (one error when it raised), what it held from its claim and `counts`; a done run's note
        `note`, or its Result's message."""
        self._said = (result, error, failed, counts, note)

    def counts_so_far(self, counts):
        """Record `counts` as the run's so far (tagpup.store.job_runs.counts_so_far); False when not written -- the run
        not recorded, no longer this process's, or the write failing (logged). Never raises."""
        if not self:
            return False
        try:
            return counts_so_far(self.library, self.run_id, counts)
        except Exception as e:
            logger.warning("Could not record the counts of run %d of %s in %s: %s", self.run_id, self.job,
                           self.library.name, e)
            return False

    def give_back(self):
        """Delete the run, before its block began, as though never claimed: work refused after the claim (a second
        click of the naming). Never raises; logged when it cannot be deleted."""
        if not self:
            return
        try:
            if not discard(self.library, self.run_id):
                logger.info("Run %d of %s in %s was no longer this process's to give back.", self.run_id, self.job,
                            self.library.name)
        except Exception as e:
            logger.warning("Could not give back run %d of %s in %s: %s", self.run_id, self.job, self.library.name, e)

    def __enter__(self):
        if self:
            self._recording = recording(self.library, self.run_id)
            self._recording.__enter__()
        return self

    def __exit__(self, kind, raised, trace):
        if not self:
            return False
        self._recording.__exit__(kind, raised, trace)
        self._recording = None
        result, error, failed, extra, note = self._said
        if raised is not None:
            result, error, extra, note = None, raised, None, None
            failed = Failure(ERROR, self._stopped) if self._stopped and not isinstance(raised, Exception) else None
        found = failed if raised is not None and failed is not None else failure(error, result, failed)
        counted = counts(result) if result is not None else {"errors": 1} if error is not None else {}
        counted.update(self._changed)
        counted.update(extra or {})
        if found is None and note is None and result is not None:
            note = result.message() or None
        _write_end(self.library, self.job, self.run_id, self._clock(), counted, found, None if found else note)
        return False


def run(library, job, run_for, clock=time.time, due=None, changed=None, stopped=None):
    """A run of `job` in `library` for the library named `run_for` (None for a job not run per library), claimed now
    (`claim`: its parent the run of the library this thread is part of) unless `due(last, now)`, asked with the last run
    that ended, says no, holding `changed` from the claim to its end: a Recorded, whose block is the run. The ends of
    `job` in `library` this process could not write are written first. A run that is not recorded is a null run, its
    claim's refusal logged; a library of None is never recorded (an index queue made without one). `clock()` says when,
    seconds since the epoch; `stopped`: Recorded."""
    if library is None:
        return Recorded(None, job, clock(), why="no library")
    _write_ends_kept(library, job)
    now = clock()
    try:
        claimed = claim(library, job, run_for, now, None if due is None else (lambda last: due(last, now)), changed)
    except Exception as e:
        logger.warning("The run of %s in %s is not recorded: its claim failed: %s", job, library.name, e)
        return Recorded(library, job, now, why=UNCLAIMED, error=e)
    if not claimed:
        logger.log(logging.INFO if claimed.why == "busy" else logging.DEBUG, "The run of %s in %s is not recorded (%s).",
                   job, library.name, claimed.why)
        return Recorded(library, job, now, why=claimed.why, holder=claimed.holder)
    return Recorded(library, job, now, run_id=claimed.run_id, changed=changed, clock=clock, stopped=stopped)


def get(library, run_id):
    """Run `run_id` of the library, or None."""
    return job_runs.get(library.path, run_id)


def latest(library):
    """{(job, library name or None): (the latest run, the latest that ended)} in `library`."""
    return job_runs.latest(library.path)


def runs(library, job=None, limit=20):
    """`library`'s runs, newest first, of `job` or of every job."""
    return job_runs.runs(library.path, job, limit)


def tree(library, limit=20):
    """`library`'s newest `limit` top runs and every run under them, newest first, with their parent and progress
    (tagpup.store.job_runs.tree)."""
    return job_runs.tree(library.path, limit)

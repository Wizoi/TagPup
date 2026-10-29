"""The runs of the recurring jobs, recorded in the library: what tagpup.jobs.recurring
claims, ends and reads (tagpup.store.job_runs; docs/ARCHITECTURE.md, phase 8).

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
from the kind, never stored. A run that failed before kinds were written reads as ERROR.
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

#: The one vocabulary of why a run failed: {kind: (what it means, the next step, whether the owner must act)}. WAITED is
#: TagPup's own work in the way, which the next run settles: never one of Needs you.
FAILURES = {
    SHARE: ("A network share did not answer, so not every folder was looked at: the library is not in step.",
            "Check that the computer sharing the photos is awake and on the network, then run it now; it is tried again"
            " within the hour.", True),
    LOCKED: ("Another program was writing the library for longer than TagPup waits, so nothing was written.",
             "Let the other program finish (a CLI run, another TagPup), then run it now.", True),
    WAITED: ("TagPup's own work was in the way (the roots changing, names being given from tags), so it stopped.",
             "Nothing: the next run does it.", False),
    REFUSED: ("It was refused before anything was written.", "Do what the sentence says, then run it now.", True),
    ERROR: ("Something went wrong that TagPup did not expect.",
            "Run it now; if it fails again, the log of the run says what happened.", True),
}


@dataclass(frozen=True)
class Failure:
    """Why a run failed: its `kind` (FAILURES) and the `sentence` its note says."""
    kind: str
    sentence: str


def failure(error=None, result=None, failed=None):
    """Why a run failed, or None when it did not: `failed` as the job gave it (recurring.Job.failed); else the `error` it
    raised (LOCKED for SQLite's locked or busy, else ERROR, said as its type and text); else, for a Result not ok, the
    kind its details say (FAILURE, set where it is known), SHARE for a share that did not answer (SHARE_AWAY), REFUSED
    for a refusal, ERROR for errors, said as the Result says it."""
    if failed is not None:
        return failed
    if error is not None:
        return Failure(LOCKED if db.locked(error) else ERROR, "%s: %s" % (type(error).__name__, error))
    if result is None or result.ok:
        return None
    details = result.details or {}
    kind = details.get(FAILURE) or (SHARE if details.get(SHARE_AWAY) else REFUSED if result.refused else ERROR)
    return Failure(kind, result.message())


def kind_of(run):
    """The kind of failure of `run`, a Run, or None when it did not fail; ERROR for one written before kinds were."""
    return run.changed.get(FAILURE, ERROR) if run.outcome == FAILED else None


def counts(result):
    """What a Result says was changed, as counts."""
    found = {"attempted": result.attempted, "changed": result.changed,
             "skipped": len(result.skipped), "errors": len(result.errors)}
    for name, value in (result.details or {}).items():
        if isinstance(value, numbers.Number) and not isinstance(value, bool) and name not in found:
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


def finish(library, run_id, now, result=None, error=None, failed=None):
    """End run `run_id` with the `result` its service returned, or the `error` it raised: done when the Result is ok,
    else failed, as `failure` says why -- and failed with `failed`, a Failure, when the job says why an ok Result did not
    do its work (recurring.Job.failed). False when it was no longer this process's to end."""
    _forget(library, run_id)
    found = failure(error, result, failed)
    changed = {"errors": 1} if error is not None else counts(result)
    if found is None:
        return job_runs.finish(library.path, run_id, DONE, now, changed, result.message() or None)
    changed[FAILURE] = found.kind
    return job_runs.finish(library.path, run_id, FAILED, now, changed, found.sentence)


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
    """While the block runs, `tell` on this thread records into run `run_id` of `library`: what the code that claimed a
    run holds around its work (tagpup.jobs.recurring.run_claimed, tagpup.jobs.indexing). A thread started meanwhile is
    not in it: a run of the indexer a sync queued is its own run."""
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

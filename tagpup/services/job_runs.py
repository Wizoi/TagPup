"""The runs of the recurring jobs, recorded in the library: what tagpup.jobs.recurring
claims, ends and reads (tagpup.store.job_runs; docs/ARCHITECTURE.md, phase 8).

What a run changed is recorded as counts from the Result the job's service returned --
attempted, changed, skipped, errors, and the numbers among its details -- never its
texts, which can name a path or a person.
"""
import numbers

from tagpup.store import job_runs

DONE, FAILED = job_runs.DONE, job_runs.FAILED
Run = job_runs.Run
stamp = job_runs.stamp
seconds = job_runs.seconds


def counts(result):
    """What a Result says was changed, as counts."""
    found = {"attempted": result.attempted, "changed": result.changed,
             "skipped": len(result.skipped), "errors": len(result.errors)}
    for name, value in (result.details or {}).items():
        if isinstance(value, numbers.Number) and not isinstance(value, bool) and name not in found:
            found[name] = value
    return found


def claim(library, job, run_for, now, due=None):
    """Begin a run of `job` in `library`, for the library named `run_for` (None for a job
    not run per library), unless a live process has one or `due` says it is not due
    (tagpup.store.job_runs.claim). A Claim: true with its `run_id` when begun."""
    return job_runs.claim(library.path, job, run_for, now, due)


def finish(library, run_id, now, result=None, error=None):
    """End run `run_id` with the `result` its service returned, or the `error` it raised:
    done when the Result is ok, else failed. False when it was no longer this process's
    to end."""
    if error is not None:
        return job_runs.finish(library.path, run_id, FAILED, now, {"errors": 1}, "%s: %s" % (type(error).__name__, error))
    outcome = DONE if result.ok else FAILED
    return job_runs.finish(library.path, run_id, outcome, now, counts(result), result.message() or None)


def latest(library):
    """{(job, library name or None): (the latest run, the latest that ended)} in `library`."""
    return job_runs.latest(library.path)


def runs(library, job=None, limit=20):
    """`library`'s runs, newest first, of `job` or of every job."""
    return job_runs.runs(library.path, job, limit)

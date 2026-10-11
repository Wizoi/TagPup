"""The small files a resumable job keeps in the library's cache folder so that a restart can find it (docs/ARCHITECTURE.md,
phase 9d-1): for each job, `<job>.state.json` -- what it is, how far it got, what it counted -- and `<job>.plan.json`, what it is
to do. Only the face assignments (tagpup.jobs.face_assignments) write them now. A bulk edit of a selection kept `<job>.state.json`
and `<job>.ids` until it stopped being resumable; the sweep still lets go of what an older version left.

Nothing here knows a library's database. A file is written to a temporary name in the folder and renamed over its own
(`os.replace`), so a reader (the status route, a resume after a crash) sees a whole file or the one before it, never half of
one. A file that is missing, cut short or not what was written reads as None: the caller says the job is not known, and
nothing is guessed from a damaged record.
"""
import itertools
import json
import os
import time

_counter = itertools.count()

#: How long (seconds) a job's files are kept after its last activity (the newest of its files) before the next job's start lets
#: them go: a month for a job that has its plan and its state (the face assignments', which can be resumed), a week for any other.
KEEP_SECONDS = 7 * 86400
KEEP_RESUMABLE_SECONDS = 30 * 86400

#: A record is replaced over a file another handle may hold open (the other TagPup process asking for the status, Defender, the
#: search indexer): on Windows that is an error until it lets go, a few milliseconds. Tried this many times, waiting this long
#: (doubling) between.
TRIES = 5
RETRY_SECONDS = 0.05

_replace = os.replace          # looked up through here so a test can fail it
_pause = time.sleep


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.tmp-%d-%d" % (path, os.getpid(), next(_counter))
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    delay = RETRY_SECONDS
    for attempt in range(TRIES):
        try:
            _replace(temporary, path)
            return
        except PermissionError:
            if attempt == TRIES - 1:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
                raise
            _pause(delay)
            delay *= 2


def _read(path):
    """The JSON in `path`, or None when it is missing or damaged. The file is open only for the read; one a writer is replacing
    at that moment is asked for again a few times."""
    for attempt in range(3):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except PermissionError:
            if attempt == 2:
                return None
            _pause(RETRY_SECONDS)
            continue
        except OSError:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            return None
    return None


def state_path(folder, job):
    return os.path.join(folder, "%d.state.json" % job)


def ids_path(folder, job):
    return os.path.join(folder, "%d.ids" % job)


def write_state(folder, job, state):
    """Keep `state`, a dict of numbers, texts, lists and dicts, as job `job`'s record."""
    _write(state_path(folder, job), json.dumps(state, sort_keys=True).encode("utf-8"))


def read_state(folder, job):
    """Job `job`'s record, or None when there is none or it cannot be read."""
    found = _read(state_path(folder, job))
    return found if isinstance(found, dict) else None


def plan_path(folder, job):
    return os.path.join(folder, "%d.plan.json" % job)


def write_plan(folder, job, plan):
    """Keep `plan`, a dict of numbers, texts, lists and dicts, as job `job`'s plan: what a job that is not safe to begin again
    from scratch is to do (the face assignments': which faces, to be what), written once, before anything is done. Like a
    state, whole or not there."""
    _write(plan_path(folder, job), json.dumps(plan, separators=(",", ":")).encode("utf-8"))


def read_plan(folder, job):
    """Job `job`'s plan, or None when there is none or it cannot be read."""
    found = _read(plan_path(folder, job))
    return found if isinstance(found, dict) else None


def forget(folder, job):
    """Let go of everything kept of job `job`: its state, its plan and (an older bulk edit's) its list of photos. Missing files
    are no matter."""
    for path in (state_path(folder, job), ids_path(folder, job), plan_path(folder, job)):
        try:
            os.remove(path)
        except OSError:
            pass


def sweep(folder, now=None):
    """Let go of the files of jobs untouched for too long, and the temporary files a crash left. A job's files are ONE unit, kept
    or deleted together, by the job's last activity (the newest of them: a state file is rewritten whenever the job runs or is
    resumed): KEEP_RESUMABLE_SECONDS for one that has both its plan and its state, KEEP_SECONDS
    for any other. Never raises."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(folder)
    except OSError:
        return
    jobs = {}
    for name in names:
        path = os.path.join(folder, name)
        try:
            if ".tmp-" in name:
                if now - os.path.getmtime(path) > 3600:
                    os.remove(path)
                continue
            job, _dot, kind = name.partition(".")
            if job.isdigit() and kind in ("ids", "state.json", "plan.json"):
                entry = jobs.setdefault(int(job), {"kinds": set(), "newest": 0})
                entry["kinds"].add(kind)
                entry["newest"] = max(entry["newest"], os.path.getmtime(path))
        except OSError:
            pass
    for job, entry in jobs.items():
        # A face assignment's plan and state (tagpup.jobs.face_assignments): resumable, so kept a month.
        stopped = {"plan.json", "state.json"} <= entry["kinds"]
        if now - entry["newest"] > (KEEP_RESUMABLE_SECONDS if stopped else KEEP_SECONDS):
            forget(folder, job)

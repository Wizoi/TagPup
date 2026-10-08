"""The small files a bulk edit keeps in the library's cache folder so that a restart can find it (docs/ARCHITECTURE.md, phase
9d-1): for each job, `<job>.state.json` -- what it is, how far it got, what it counted -- and, for a job that cannot safely be
started again (a time shift is not idempotent), `<job>.ids`, the photos it resolved when it began.

Nothing here knows a library's database. A file is written to a temporary name in the folder and renamed over its own
(`os.replace`), so a reader (the status route, a resume after a crash) sees a whole file or the one before it, never half of
one. A file that is missing, cut short or not what was written reads as None: the caller says the job is not known, and
nothing is guessed from a damaged record. The files are small (the state a few KB, the ids 7 bytes a photo: 200,000 photos
1.4 MB, written once).
"""
import itertools
import json
import os
import time

_counter = itertools.count()

#: How long (seconds) a job's files are kept after its last activity (the newest of its files) before the next job's start lets
#: them go. They can name the tags and the people of an edit and the files that failed, so nothing is kept long: a week, except a
#: time shift that can still be resumed (its list of photos AND its state are there), which is kept a month.
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


def write_ids(folder, job, ids):
    _write(ids_path(folder, job), json.dumps(list(ids), separators=(",", ":")).encode("utf-8"))


def has_ids(folder, job):
    """Is there a list of photos for job `job` that looks whole -- a JSON list, read from its two ends and not parsed: a list of
    200,000 ids is 1.4 MB and a status asks this for every poll? (read_ids parses it, for the resume that needs it.)"""
    try:
        with open(ids_path(folder, job), "rb") as handle:
            head = handle.read(1)
            handle.seek(-1, os.SEEK_END)
            tail = handle.read(1)
        return head == b"[" and tail == b"]"
    except OSError:
        return False


def read_ids(folder, job):
    """The photo ids job `job` resolved, as a list, or None when there are none or they cannot be read."""
    found = _read(ids_path(folder, job))
    return found if isinstance(found, list) and all(type(each) is int for each in found) else None


def forget(folder, job):
    """Let go of everything kept of job `job`: its state and its list of photos. Missing files are no matter."""
    for path in (state_path(folder, job), ids_path(folder, job), plan_path(folder, job)):
        try:
            os.remove(path)
        except OSError:
            pass


def forget_ids(folder, job):
    """Let go of job `job`'s list of photos (it is over for good). A list already gone is no matter."""
    try:
        os.remove(ids_path(folder, job))
    except OSError:
        pass


def jobs_with_records(folder):
    """The ids of the jobs that have BOTH a list of photos and a state: the time shifts that a resume can carry on."""
    found = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        job, _dot, kind = name.partition(".")
        if job.isdigit() and kind in ("ids", "state.json"):
            found.setdefault(int(job), set()).add(kind)
    return sorted(job for job, kinds in found.items() if kinds == {"ids", "state.json"} and has_ids(folder, job)
                  and read_state(folder, job) is not None)


def sweep(folder, now=None):
    """Let go of the files of jobs untouched for too long, and the temporary files a crash left. A job's files are ONE unit, kept
    or deleted together, by the job's last activity (the newest of them: a state file is rewritten whenever the job runs or is
    resumed): KEEP_RESUMABLE_SECONDS for one that has both its list and its state (a time shift that can be resumed), KEEP_SECONDS
    for any other. Returns the ids of the resumable jobs it let go, for the library's record of them to say so. Never raises."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(folder)
    except OSError:
        return []
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
    expired = []
    for job, entry in jobs.items():
        pair = entry["kinds"] == {"ids", "state.json"}
        # A face assignment's plan and state (tagpup.jobs.face_assignments): resumable, so kept as long as a time shift's.
        stopped = {"plan.json", "state.json"} <= entry["kinds"]
        if now - entry["newest"] > (KEEP_RESUMABLE_SECONDS if pair or stopped else KEEP_SECONDS):
            forget(folder, job)
            if pair:
                expired.append(job)
    return sorted(expired)

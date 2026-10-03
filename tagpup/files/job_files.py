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

#: A state file this old (seconds) is let go at the next job's start; its job is long over.
KEEP_SECONDS = 30 * 86400


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.tmp-%d-%d" % (path, os.getpid(), next(_counter))
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read(path):
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
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


def write_ids(folder, job, ids):
    _write(ids_path(folder, job), json.dumps(list(ids), separators=(",", ":")).encode("utf-8"))


def read_ids(folder, job):
    """The photo ids job `job` resolved, as a list, or None when there are none or they cannot be read."""
    found = _read(ids_path(folder, job))
    return found if isinstance(found, list) and all(type(each) is int for each in found) else None


def forget_ids(folder, job):
    """Let go of job `job`'s list of photos (it is over for good). A list already gone is no matter."""
    try:
        os.remove(ids_path(folder, job))
    except OSError:
        pass


def sweep(folder, now=None):
    """Remove the files of jobs untouched for KEEP_SECONDS, and the temporary files a crash left. Never raises."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(path) > (3600 if ".tmp-" in name else KEEP_SECONDS):
                os.remove(path)
        except OSError:
            pass

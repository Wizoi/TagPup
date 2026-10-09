"""Verify of every row of a root, as a job: it reports how far it has got and can be cancelled
(docs/ARCHITECTURE.md, "Roots and machines", TagTuner's Roots).

A sample is a request (tagpup.services.roots_location.run_verify); looking at every one of
68,466 rows -- and walking the folders for the photos no row has -- on a share is minutes, so
that one runs here, on a thread of its own in the process that was asked, and the page asks how it
is getting on. What is kept in this process is only the progress and the cancel flag; the run
itself is claimed in the library's `job_runs` (so two processes, or a double click, do not run
two) and ends there with its counts, which the Activity page lists. A process that ends meanwhile
leaves a `running` row, which the next claim marks abandoned: nothing was written, so there is
nothing to settle.

Each library has at most one run of each root at a time. Cancel is asked between folders: what
was counted until then is kept, and said to be partial.
"""
import logging
import threading
import time

from tagpup.core import paths
from tagpup.core.result import Conflict
from tagpup.services import roots_location

logger = logging.getLogger(__name__)

_runs = {}
_lock = threading.Lock()


class Run:
    """One full Verify of a root: what it is, how far it has got, what it found."""

    def __init__(self, library, name, location):
        self.library, self.name, self.location = library.name, name, location
        self.started = time.time()
        self.finished = None
        self.state = "running"      # running | done | cancelled | failed
        self.checked = 0
        self.rows = 0
        self.folders = 0
        self.markers_read = 0       # the folder markers read so far, of `markers_of`: they are read before the rows
        self.markers_of = 0
        self.cancel = threading.Event()
        self.answer = None
        self.error = None

    def update(self, checked, rows, folders):
        self.checked, self.rows, self.folders = checked, rows, folders

    def update_markers(self, read, of):
        self.markers_read, self.markers_of = read, of

    def status(self):
        """For the page: {"root", "location", "state", "checked", "rows", "folders", "markers_read", "markers_of", "cancelling",
        "started", "result", "error"}."""
        return {"root": self.name, "location": self.location, "state": self.state, "checked": self.checked,
                "rows": self.rows, "folders": self.folders,
                "markers_read": self.markers_read, "markers_of": self.markers_of,
                "cancelling": self.cancel.is_set() and self.state == "running",
                "started": self.started, "finished": self.finished, "result": self.answer, "error": self.error}


def _held(library):
    with _lock:
        return _runs.setdefault(library.key, {})


def start(library, name, location, machine):
    """Begin a full Verify of root `name` at `location` on a thread of its own. Returns the Run;
    Conflict, nothing begun, when one of that root is under way (here, or in another process:
    the claim in the library says so before anything is read)."""
    name = paths.root_name(name)
    runs = _held(library)
    with _lock:
        current = runs.get(name)
        if current is not None and current.state == "running":
            raise Conflict("A verify of %s is under way already; wait for it, or cancel it." % name)
        # The claim in the library is made here, before anything is read: another process's run,
        # or a click that came a moment after, is refused at once and nothing starts.
        claim = roots_location.begin_verify(library, name)
        run = Run(library, name, paths.stored(location))
        runs[name] = run

    def work():
        try:
            run.answer = roots_location.run_verify(library, name, location, machine, full=True,
                                                   cancel=run.cancel.is_set, progress=run.update, claim=claim,
                                                   marker_progress=run.update_markers)
            run.state = "cancelled" if run.answer["stopped"] == "cancelled" else (
                "failed" if run.answer["stopped"] else "done")
        except Exception as problem:
            logger.exception("The verify of %s in %s failed", name, library.name)
            run.error = str(problem)
            run.state = "failed"
        finally:
            run.finished = time.time()

    thread = threading.Thread(target=work, name="VerifyRoot", daemon=True)
    thread.start()
    return run


def status(library, name=None):
    """{root name: its latest run's status} for the library, or that root's alone (None when it
    has had none in this process)."""
    runs = _held(library)
    with _lock:
        if name is not None:
            run = runs.get(paths.root_name(name))
            return run.status() if run else None
        return {each: run.status() for each, run in runs.items()}


def cancel(library, name):
    """Ask the run of root `name` to stop, between folders. True when one was under way."""
    runs = _held(library)
    with _lock:
        run = runs.get(paths.root_name(name))
    if run is None or run.state != "running":
        return False
    run.cancel.set()
    return True


def running(library=None):
    """How many full verifies are under way in this process (the library's, or every one's)."""
    with _lock:
        every = [_runs.get(library.key, {})] if library is not None else list(_runs.values())
        return sum(1 for runs in every for run in runs.values() if run.state == "running")


def forget(library):
    """Drop what is remembered of a library's runs."""
    with _lock:
        _runs.pop(library.key, None)

"""Adding a folder's photos to a library: the CLI's `index`, which clusters the library's faces after an index
that recorded a photo, with what the CLI prints turned into progress a person can read.

Both apps ran their own copy of these steps, until TagTuner's called TagPup's. When a
folder is indexed is tagpup.jobs.indexing's to decide.
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import ExitStack, contextmanager

from tagpup.core import gpu_turns
from tagpup.core import paths
from tagpup.core import processes
from tagpup.core import runs
from tagpup.core import validation
from tagpup.core.per_library import PerLibrary
from tagpup.core.result import Result
from tagpup.services import roots as roots_service
from tagpup.services import settings as library_settings

#: Where faces are named from the photos' tags when adding a folder did not cluster them: the button of both apps, whose
#: label this is (tests/test_indexing_names_a_real_control.py holds the messages and the pages together). The messages named
#: a Recluster button no page has (docs/findings.md, #22), then the Tk runner's button; the apps have their own now (#789).
CLUSTERING_BUTTON = "Name faces from tags"
CLUSTER_ELSEWHERE = "%s in TagTuner's header" % CLUSTERING_BUTTON

#: How a clustering after an index that failed or stopped is run again: the command alone clusters (Name faces from
#: tags names faces from tags, and an index again of photos already read clusters nothing).
CLUSTER_AGAIN = "cluster-faces in the TagPup CLI"

#: Set while a library's faces are being clustered after an index this process runs, or named from tags
#: (tagpup.web.name_faces_routes): writes of faces are refused meanwhile (tagpup.web.tuner_routes.clustering_refusal),
#: since clustering rewrites the names they would be setting. Here, beside the one runner of the indexer, so that
#: every way this process indexes holds it -- sync's queue (tagpup.runtime) as well as both apps' routes.
clustering = PerLibrary(lambda library: threading.Event())


@contextmanager
def while_clustering(library):
    """Refuse writes of faces in `library` while this is held. The library is named, not taken from a request,
    so it holds on a thread no request set up."""
    flag = clustering.of(library)
    flag.set()
    try:
        yield
    finally:
        flag.clear()


#: The kind of log the indexer writes, one file a run (tagpup.logs.run_log).
INDEXER_LOG = "indexer"

#: The file whose appearing tells the indexer to stop while it waits for its turn on the
#: graphics card (the CLI's `index`; tagpup.ml.gpu), and what it exits with when it did:
#: nothing was indexed. Once it has the card it carries on: a run part-way through writing
#: rows is not stopped (tagpup.jobs.indexing.IndexQueue.cancel).
STOP_FILE = "TAGPUP_STOP_FILE"
EXIT_CANCELLED = 76

#: What the CLI's `index` exits with when the library is set to a face model whose vectors it cannot keep
#: (tagpup.services.settings.FACE_MODEL_NOT_KEPT): it wrote nothing, and the folder's status says why.
EXIT_FACE_MODEL_NOT_READY = 78

#: What a run stopped while it waited says.
CANCELLED_WAITING = "Cancelled while it waited for the graphics card; nothing was indexed."

#: What the CLI's `index` prints, a line of its own, as it begins to cluster the library's faces after an index that
#: recorded a photo (owner, DECISIONS 2026-10-16: an index is followed by `cluster-faces`), and what it exits with
#: when the clustering failed: the photos stay indexed. index_folder holds the library's faces as being clustered
#: (while_clustering) from this line to the process's end.
CLUSTERING_STARTS = "Resolving and matching face identities..."
EXIT_CLUSTERING_FAILED = 77

#: Where the bar stands while the faces are clustered: the index's own bars are scaled to 90.
CLUSTERING_PERCENT = 95

_INDEXER_TQDM =re.compile(r"^(.*?):\s*(\d+)%\|[^|]*\|\s*(\d+)/(\d+)")

#: Longest message worth putting on a progress bar. Past this it is ellipsised in
#: the page anyway, so a truncated sentence is all anyone can read.
_INDEXER_MAX_MESSAGE = 90

#: The shapes library chatter arrives in. These are forms, not particular messages:
#: blacklisting the text of one warning only waits for the next library to add one.
_INDEXER_NOISE = (
    # "2026-09-19 21:31:50,515 [INFO] root - Instantiating..."
    re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+\["),
    # "WARNING:huggingface_hub.utils._http:Warning: You are sending..."
    re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):[\w.]+:"),
    # "Warning: You are sending unauthenticated requests to the HF Hub."
    # Printed bare, with no prefix at all, which is how it reached the progress bar.
    re.compile(r"^(User|Future|Deprecation|Runtime|Import|Resource)?Warning:", re.I),
    # warnings.warn's source line, and the echoed statement under it.
    re.compile(r"^.*:\d+:\s*\w*Warning:"),
    re.compile(r"^\s*warnings\.warn\("),
    # A bare traceback frame, which without its exception says nothing useful here.
    # The line is stripped before matching, so its indentation is already gone.
    re.compile(r"^File \".*\", line \d+"),
)


def summarize_indexer_line(line):
    """Turn one line of indexer output into progress text, or None to ignore it.

    The indexer's stdout carries three kinds of line: console output written for a
    person, tqdm progress bars, and library chatter. Only the first two say anything
    about progress, and the third is the bulk of it -- model loading alone logs
    dozens of lines nobody watching a progress bar wants.
    """
    if not line:
        return None
    # tqdm redraws with carriage returns; only the newest frame matters.
    clean = line.split("\r")[-1].strip()
    if not clean:
        return None
    if any(pattern.match(clean) for pattern in _INDEXER_NOISE):
        return None
    if gpu_turns.is_waiting(clean):
        # Whole: who has the card and since when is the line's point.
        return clean

    match = _INDEXER_TQDM.match(clean)
    if match:
        label, percent, done, total = match.groups()
        return f"{label.strip()}: {percent}% ({done}/{total})"

    if len(clean) > _INDEXER_MAX_MESSAGE:
        return clean[:_INDEXER_MAX_MESSAGE - 1].rstrip() + "\u2026"
    return clean


def index_folder(library, folder, code_folder, report=None, subfolders=True):
    """Add a folder's photos to the library: the CLI's `index`, which clusters the library's faces after an index
    that recorded a photo (tagpup_cli.py; owner, DECISIONS 2026-10-16). Adding a folder, in either app, and sync's
    new files.

    The CLI runs in a process of its own, from `code_folder` -- the code this program
    runs, which the caller has from tagpup.config -- so the GPU work stays out of the
    server. It reads the library's settings itself (tagpup.runtime). Whether a run clusters is the CLI's to decide,
    the one place every entry point reaches: this reads that it does (CLUSTERING_STARTS).

    `report(message, percent)` hears progress as it comes: the index's bars scaled to 90, then CLUSTERING_PERCENT
    while the faces are clustered. From the CLI's CLUSTERING_STARTS to the end of its process the library's faces are
    held as being clustered (while_clustering): writes of faces are refused, since clustering rewrites the names an
    assignment would be setting.

    A clustering that failed does not undo the index: the photos stay indexed, and the Result's error says so
    (EXIT_CLUSTERING_FAILED). Both copies of this once reported "identities resolved" when cluster-faces had failed.

    details: `message`, what to tell the person; `percent`, where the bar stops;
    `cancelled`, true when the indexer was stopped while it waited for the graphics card;
    `clustered`, whether the CLI clustered the faces after its index.
    `folder` may be a list of folders, indexed in one run of the indexer.

    A `report` with a `stop_file` attribute (tagpup.jobs.indexing) is told the file that
    stops the indexer while it waits for its turn on the graphics card: the queue's Cancel
    creates it.

    Refused, and nothing run, for a folder that is not named by its full path
    (tagpup.core.validation). Without `subfolders`, only the photos directly in the
    folder are indexed (`index --no-subfolders`): sync's new files in a folder the library
    holds, whose subfolders may be folders to review or ignored.
    """
    report = report or (lambda message=None, percent=None: None)
    result = Result(attempted=1)
    # Several folders are one run of the indexer: the models are loaded once.
    folders = [folder] if isinstance(folder, str) else list(folder)
    problem = validation.first_problem("folder", folders) if folders else validation.problem("folder", None)
    if problem:
        result.refuse(problem)
        result.details["percent"] = 0
        return result
    env = os.environ.copy()
    env["TAGPUP_DB_PATH"] = library.path
    # Lines compared whole (CLUSTERING_STARTS) arrive as printed: rich colours a line's "..." when told the pipe is a
    # terminal (FORCE_COLOR), and no colour is wanted on a progress bar.
    env.update(NO_COLOR="1", TERM="dumb")
    # The indexer logs to a file of its run's own in data/logs as well as to this pipe, each
    # line carrying the tags of the run it is part of (tagpup.core.runs).
    runs.child_environment(env, log_to=INDEXER_LOG)
    stop_file = os.path.join(tempfile.gettempdir(), "tagpup-stop-%s" % uuid.uuid4().hex)
    env[STOP_FILE] = stop_file
    if hasattr(report, "stop_file"):
        report.stop_file = stop_file

    clustered = []

    def run(args):
        proc = processes.start(
            [sys.executable, "tagpup_cli.py"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1, cwd=code_folder,
        )
        # The flag is let go after the process has ended, whatever ended it.
        with ExitStack() as held, proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                clean = summarize_indexer_line(line)
                if clean == CLUSTERING_STARTS and not clustered:
                    clustered.append(True)
                    held.enter_context(while_clustering(library))
                    report(clean, CLUSTERING_PERCENT)
                elif clean:
                    match = None if clustered else re.search(r"(\d+)%", clean)
                    report(clean, int(float(match.group(1)) * 0.9) if match else None)
            proc.wait()
        return proc.returncode

    # The indexer stores the paths it walks as given, so it is handed the stored form.
    try:
        code = run(["index"] + [paths.stored(each) for each in folders] + ([] if subfolders else ["--no-subfolders"]))
    finally:
        if hasattr(report, "stop_file"):
            report.stop_file = None
        try:
            os.remove(stop_file)
        except OSError:
            pass
    result.details["clustered"] = bool(clustered)
    if code == EXIT_CANCELLED:
        result.skip(", ".join(folders), CANCELLED_WAITING)
        result.details.update(percent=0, cancelled=True, message=CANCELLED_WAITING)
        return result
    if code == EXIT_FACE_MODEL_NOT_READY:
        # Said as it is, not as an exit code: the setting to change back.
        result.fail(", ".join(folders), "Nothing was indexed: %s" % library_settings.FACE_MODEL_NOT_KEPT)
        result.details["percent"] = 0
        return result
    if clustered and code != 0:
        # EXIT_CLUSTERING_FAILED, the roots' stop, or the process ended otherwise once its index was done: the photos
        # are indexed.
        why = ("stopped: the library's roots were changed by another process" if code == roots_service.EXIT_ROOTS_CHANGED
               else "failed (exit code %s)" % code)
        result.fail(", ".join(folders), "Folder indexed, but resolving face identities %s. To try again, run %s."
                    % (why, CLUSTER_AGAIN))
        result.details["percent"] = 100
        return result
    if code == roots_service.EXIT_ROOTS_CHANGED:
        # The indexer holds the library's roots for its whole run (tagpup.services.roots.pinned) and
        # stopped: another process changed them. Said as it is, not as an exit code.
        result.fail(", ".join(folders), roots_service.STOPPED)
        result.details["percent"] = 0
        return result
    if code != 0:
        result.fail(", ".join(folders), "Indexing failed with exit code %s." % code)
        result.details["percent"] = 0
        return result

    result.changed = 1
    result.details.update(percent=100, message=(
        "Folder indexed and face identities resolved." if clustered
        else "Folder indexed: no photo was added or read again, so faces were not clustered again. To name faces "
             "from the photos' tags, use %s." % CLUSTER_ELSEWHERE))
    return result

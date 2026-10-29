"""Adding a folder's photos to a library: the CLI's `index`, which matches the faces an index made with the named ones
and writes nothing, with what the CLI prints turned into progress a person can read.

Both apps ran their own copy of these steps, until TagTuner's called TagPup's. When a
folder is indexed is tagpup.jobs.indexing's to decide.
"""
import os
import re
import subprocess
import sys
import tempfile
import uuid

from tagpup.core import gpu_turns
from tagpup.core import paths
from tagpup.core import processes
from tagpup.core import runs
from tagpup.core import validation
from tagpup.core.result import Result
from tagpup.services import bake_after_index, bake_reading
from tagpup.services import job_runs
from tagpup.services import roots as roots_service

#: Where faces are named from the photos' tags: the button of both apps, whose label this is (tests/test_indexing_names_a_real_control.py holds the messages and the pages together). The messages named
#: a Recluster button no page has (docs/findings.md, #22), then the Tk runner's button; the apps have their own now (#789).
CLUSTERING_BUTTON = "Name faces from tags"
CLUSTER_ELSEWHERE = "%s in TagTuner's header" % CLUSTERING_BUTTON

#: The command that clusters the library's faces, run by hand: an index matches only the faces it made.
CLUSTER_AGAIN = "cluster-faces in the TagPup CLI"

#: The kind of log the indexer writes, one file a run (tagpup.logs.run_log).
INDEXER_LOG = "indexer"

#: The file whose appearing tells the indexer to stop while it waits for its turn on the
#: graphics card (the CLI's `index`; tagpup.ml.gpu), and what it exits with when it did:
#: nothing was indexed. Once it has the card it carries on: a run part-way through writing
#: rows is not stopped (tagpup.jobs.indexing.IndexQueue.cancel).
STOP_FILE = "TAGPUP_STOP_FILE"
EXIT_CANCELLED = 76

#: The option that hands the indexer its folders in a file, UTF-8, one a line (the CLI's `index`): on its command line a
#: sync's few hundred passed Windows's 32,767 characters and the indexer never started (#2149). What its file is called.
FOLDERS_FILE = "--folders-file"
_FOLDERS_FILE_NAME = "tagpup-folders-%s.txt"

#: What a run stopped while it waited says.
CANCELLED_WAITING = "Cancelled while it waited for the graphics card; nothing was indexed."

#: What the CLI's `index` begins a line of its own with, after an index that recorded a photo: what Re-examine would name
#: among the faces it made, matched with the named ones and written nowhere (owner, DECISIONS 2026-10-23;
#: tagpup.services.faces.automatch_new), as new_faces_said says it, or that the matching failed. index_folder reads the
#: line whole, before the bar cuts it, and ends its message with it.
NEW_FACES = "New faces: "
#: The line the CLI prints when photos failed in its per-photo loop (#1995), read whole like NEW_FACES: the message says
#: how many, since a run whose every photo failed exits 0 having recorded nothing.
FAILED = "Photos that failed: "
#: What the CLI's `index` says as it begins matching the faces it made (before NEW_FACES): the faces phase of the live line.
MATCHING = "Matching the new faces with the named ones..."
#: The labels of the two bars the CLI's `index` draws (tqdm `desc`), which the live line and a run's phase are read from.
READING_METADATA = "Reading metadata"
GENERATING_EMBEDDINGS = "Generating embeddings"


def new_faces_said(matched):
    """What an index says of the faces it made, from tagpup.services.faces.automatch_new's Result: counted, plainly."""
    looked, decided, keyword = matched.attempted, matched.details["faces"], matched.details["keyword_photos"]
    if not looked:
        said = "No new face is waiting for a name."
    elif not decided:
        said = ("None of the %d new faces looks enough like anyone named to be proposed: they wait in Identify Faces."
                % looked)
    else:
        said = ("%d of the %d new faces have a likely name: open Re-examine on their folder to confirm them."
                % (decided, looked))
    if keyword:
        said += (" %d photo(s) carry a person's keyword that no face has: %s, or %s, may name them."
                 % (keyword, CLUSTER_ELSEWHERE, CLUSTER_AGAIN))
    return said

_INDEXER_TQDM =re.compile(r"^(.*?):\s*(\d+)%\|[^|]*\|\s*(\d+)/(\d+)")

#: The phase of an index (job_runs.PHASES) each of the CLI's bars is, by its label.
_BAR_PHASES = {READING_METADATA: "metadata", GENERATING_EMBEDDINGS: "embeddings"}

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
    """Add a folder's photos to the library: the CLI's `index`, which matches the faces an index made with the named
    ones and writes nothing (tagpup_cli.py; owner, DECISIONS 2026-10-23). Adding a folder, in either app, and sync's
    new files.

    The CLI runs in a process of its own, from `code_folder` -- the code this program
    runs, which the caller has from tagpup.config -- so the GPU work stays out of the
    server. It reads the library's settings itself (tagpup.runtime). What the CLI says of the faces it made (NEW_FACES)
    ends the message, and after it what it read from the photos' bakes (bake_reading.SAID) and what its bake wrote
    (bake_after_index.SAID), each read whole.

    `report(message, percent)` hears progress as it comes, each message naming its phase: the percent of each of the
    CLI's bars (metadata, embeddings), the faces' matching (MATCHING), and each folder of the bake as it begins, whole,
    with the percent of the folders done before it (bake_after_index.folder_heard). A line cut short or garbled is passed
    over. The run under way on this thread, when one is recorded (job_runs.recording: the index queue's), is told the
    same as a phase and counts, never a path (job_runs.tell): a bar's `done`, `of` and `percent`; a bake folder's
    `folders_done`, `folders`, `written`, `current`, `conflicts` and `percent`.

    details: `message`, what to tell the person; `percent`, where the bar stops;
    `cancelled`, true when the indexer was stopped while it waited for the graphics card; and, as counts, what the CLI
    said it did: `photos_read` (its embeddings bar's total), `baked_written`, `baked_current`, `baked_conflicts` (its
    bake's line, bake_after_index.said_heard), `failed_photos` (FAILED).
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
    # A line read whole (NEW_FACES) arrives as printed: rich colours a line when told the pipe is a terminal
    # (FORCE_COLOR), and no colour is wanted on a progress bar.
    env.update(NO_COLOR="1", TERM="dumb")
    # The indexer logs to a file of its run's own in data/logs as well as to this pipe, each
    # line carrying the tags of the run it is part of (tagpup.core.runs).
    runs.child_environment(env, log_to=INDEXER_LOG)
    stop_file = os.path.join(tempfile.gettempdir(), "tagpup-stop-%s" % uuid.uuid4().hex)
    # Each run its own, like the stop file: two jobs at once (two libraries) do not share one.
    folders_file = os.path.join(tempfile.gettempdir(), _FOLDERS_FILE_NAME % uuid.uuid4().hex)
    env[STOP_FILE] = stop_file
    if hasattr(report, "stop_file"):
        report.stop_file = stop_file

    said, baked, wrote, failed = [], [], [], []
    # What the CLI said it did, as counts: the result's details, which the run's row keeps (tagpup.jobs.indexing).
    counted = {}

    def run(args):
        proc = processes.start(
            [sys.executable, "tagpup_cli.py"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1, cwd=code_folder,
        )
        with proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                whole = line.split("\r")[-1].strip()
                if whole.startswith(bake_after_index.FOLDER_SAID):
                    heard = bake_after_index.folder_heard(whole)
                    if heard is not None:
                        job_runs.tell("bake", {"folders_done": heard["number"] - 1, "folders": heard["of"],
                                               "written": heard["written"], "current": heard["current"],
                                               "conflicts": heard["conflicts"], "percent": heard["percent"]})
                        # Whole, path and all: the bar's summary would cut it at 90 characters.
                        report(whole, heard["percent"])
                    continue
                bar = _INDEXER_TQDM.match(whole)
                if bar and bar.group(1).strip() in _BAR_PHASES:
                    phase, of = _BAR_PHASES[bar.group(1).strip()], int(bar.group(4))
                    job_runs.tell(phase, {"done": int(bar.group(3)), "of": of, "percent": int(bar.group(2))})
                    if phase == "embeddings":
                        counted["photos_read"] = of
                elif whole.startswith(MATCHING):
                    job_runs.tell("faces", {})
                if whole.startswith(NEW_FACES):
                    # Whole: the bar's summary cuts a line at 90 characters.
                    said.append(whole[len(NEW_FACES):])
                elif whole.startswith(FAILED):
                    failed.append(whole[len(FAILED):])
                    if failed[-1].isdigit():
                        counted["failed_photos"] = int(failed[-1])
                elif whole.startswith(bake_reading.SAID):
                    baked.append(whole)
                elif whole.startswith(bake_after_index.SAID):
                    wrote.append(whole)
                    for name, value in (bake_after_index.said_heard(whole) or {}).items():
                        counted["baked_" + name] = value
                clean = summarize_indexer_line(line)
                if clean:
                    match = re.search(r"(\d+)%", clean)
                    report(clean, int(match.group(1)) if match else None)
            proc.wait()
        return proc.returncode

    # The indexer stores the paths it walks as given, so it is handed the stored form; in a file, one or four hundred.
    try:
        with open(folders_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(paths.stored(each) + "\n" for each in folders))
        code = run(["index", FOLDERS_FILE, folders_file] + ([] if subfolders else ["--no-subfolders"]))
    finally:
        if hasattr(report, "stop_file"):
            report.stop_file = None
        for made in (stop_file, folders_file):
            try:
                os.remove(made)
            except OSError:
                pass
    result.details.update(counted)
    if code == EXIT_CANCELLED:
        result.skip(", ".join(folders), CANCELLED_WAITING)
        result.details.update(percent=0, cancelled=True, message=CANCELLED_WAITING)
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
    message = (" ".join(["Folder indexed. " + said[-1]] + baked[-1:] + wrote[-1:]) if said
               else "Folder indexed: no photo was added or read again.")
    if failed:
        message = "%s photo(s) failed and were not indexed (the indexer's log says why). %s" % (failed[-1], message)
    result.details.update(percent=100, message=message)
    return result

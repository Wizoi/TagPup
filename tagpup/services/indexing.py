"""Adding a folder's photos to a library: the CLI's `index`, which matches the faces an index made with the named ones
and writes nothing, with what the CLI prints turned into progress a person can read.

Both apps ran their own copy of these steps, until TagTuner's called TagPup's. When a
folder is indexed is tagpup.jobs.indexing's to decide.
"""
import os
import subprocess
import sys
import tempfile
import uuid

from tagpup.core import paths
from tagpup.core import processes
from tagpup.core import progress_line
from tagpup.core import runs
from tagpup.core import validation
from tagpup.core.result import Result
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

#: What the CLI's `index`, run by hand, begins a line of its own with after an index that recorded a photo: what
#: Re-examine would name among the faces it made, matched with the named ones and written nowhere (owner, DECISIONS
#: 2026-10-23; tagpup.services.faces.automatch_new), as new_faces_said says it, or that the matching failed. To the server
#: it is a `new_faces` fact (tagpup.core.progress_line), whose sentence ends the message.
NEW_FACES = "New faces: "
#: What the matching of an index's new faces says when it failed (matching_failed_fact): the photos are indexed.
MATCHING_FAILED = ("The photos are indexed, but matching their faces failed: %s: %s. Open Re-examine on their folder, "
                   "or run %s.")
#: The field of an index's status (tagpup.jobs.indexing, its Result's details here) naming the folder its closing sentence
#: offers Re-examine of: some of its new faces have a likely name, or their matching failed (offers_reexamine). Both pages
#: make their Re-examine of it from this field, never from the sentence's words; a batch's is its first folder, as its
#: status is named.
REEXAMINE = "reexamine"
#: The line the CLI prints by hand when photos failed in its per-photo loop (#1995); the server hears a `failed` fact,
#: and the message says how many, since a run whose every photo failed exits 0 having recorded nothing.
FAILED = "Photos that failed: "
#: What the CLI's `index` says as it begins matching the faces it made (before NEW_FACES): the faces phase of the live line.
MATCHING = "Matching the new faces with the named ones..."
#: The labels of the two bars the CLI's `index` draws by hand (tqdm `desc`), and the words of its phase facts.
READING_METADATA = "Reading metadata"
GENERATING_EMBEDDINGS = "Generating embeddings"
#: The words each phase of the CLI's `index` is said in (phase_fact).
_PHASE_LABEL = {"metadata": READING_METADATA, "embeddings": GENERATING_EMBEDDINGS, "faces": MATCHING}


#: Where each phase of an index starts on the one bar the person sees, and how much of it the phase takes: reading the
#: files' metadata, the embeddings and the faces share the first half, the bake into the files is the second (#2355).
_BAR = {"metadata": (0, 10), "embeddings": (10, 35), "faces": (45, 5), "bake": (50, 50)}


def phase_fact(phase, done=None, of=None):
    """The fact of how far the CLI's `index` has got in `phase` (metadata, embeddings, faces): `done` of `of`, and the
    percent, in the words of the bar a person sees by hand: `Generating embeddings: 100% (3/3)`."""
    label = _PHASE_LABEL[phase]
    if of is None:
        return progress_line.fact("phase", phase=phase, sentence=label)
    percent = done * 100 // of if of else 100
    return progress_line.fact("phase", phase=phase, sentence="%s: %d%% (%d/%d)" % (label, percent, done, of),
                              done=done, of=of, percent=percent)


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


def new_faces_fact(matched):
    """The `new_faces` fact (tagpup.core.progress_line) the CLI's `index` says of the faces it made, from
    tagpup.services.faces.automatch_new's Result: its sentence (new_faces_said) and how many of them have a likely name
    (`likely`), which Re-examine would name."""
    return progress_line.fact("new_faces", sentence=new_faces_said(matched), likely=matched.details["faces"])


def matching_failed_fact(error):
    """The `new_faces` fact of a matching of the new faces that raised `error`: the photos are indexed, and Re-examine or
    cluster-faces matches them (`matching_failed` 1)."""
    return progress_line.fact("new_faces", sentence=MATCHING_FAILED % (type(error).__name__, error, CLUSTER_AGAIN),
                              matching_failed=1)


def offers_reexamine(fact):
    """Does a `new_faces` fact offer Re-examine of the index's folder: some new face has a likely name, or the matching
    failed. Its sentence then says Re-examine (tests/test_new_faces_are_matched_after_an_index.py holds the two
    together)."""
    return fact.get("likely", 0) > 0 or fact.get("matching_failed", 0) > 0

#: Each phase of an index (job_runs.PHASES) in the words the Activity page shows it in (tagpup.services.activity.PHASE_SAID).
PHASE_SAID = {"metadata": "reading metadata", "embeddings": "making each photo's vector",
              "faces": "matching the new faces with the named ones", "bake": "writing the tags into the files"}

#: Longest person's line worth putting on a progress bar. Past this it is ellipsised in the page anyway, so a truncated
#: sentence is all anyone can read. A fact's sentence is shown whole.
_INDEXER_MAX_MESSAGE = 90


def _shortened(line):
    if len(line) > _INDEXER_MAX_MESSAGE:
        return line[:_INDEXER_MAX_MESSAGE - 1].rstrip() + "\u2026"
    return line


def index_folder(library, folder, code_folder, report=None, subfolders=True):
    """Add a folder's photos to the library: the CLI's `index`, which matches the faces an index made with the named
    ones and writes nothing (tagpup_cli.py; owner, DECISIONS 2026-10-23). Adding a folder, in either app, and sync's
    new files.

    The CLI runs in a process of its own, from `code_folder` -- the code this program
    runs, which the caller has from tagpup.config -- so the GPU work stays out of the
    server. It reads the library's settings itself (tagpup.runtime), and is asked for facts (tagpup.core.progress_line),
    which it says one a line: what it says of the faces it made (`new_faces`) ends the message, and after it what it read
    from the photos' bakes (`bake_read`) and what its bake wrote (`baked`).

    `report(message, percent)` hears progress as it comes, each fact's sentence whole and its percent: each phase
    (metadata, embeddings, the faces' matching, each folder of the bake as it begins and as each chunk of its files is
    written: bake_after_index.folder_told), and the line of a wait for the graphics card. Any other line is a person's
    (a log line, a traceback), shown cut at 90 characters; a fact cut short is passed over. The run under way on this
    thread, when one is recorded (job_runs.recording: the index queue's), is told each phase fact's phase and counts,
    never a path (job_runs.tell).

    details: `message`, what to tell the person; `percent`, where the bar stops; REEXAMINE, the first folder, when
    the last `new_faces` fact offers Re-examine (offers_reexamine);
    `cancelled`, true when the indexer was stopped while it waited for the graphics card; and, as counts, what the CLI
    said it did: `photos_read` (its embeddings fact's `of`), `baked_written`, `baked_current`, `baked_conflicts` (its
    `baked` fact's), `failed_photos` (its `failed` fact's).
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
    # Its facts, not the words and bars it prints for a person (tagpup.core.progress_line); a person's line it still
    # prints (rich's console) arrives with no colour, which is not wanted on a progress bar.
    progress_line.ask(env)
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

    said, offered, baked, wrote, failed = [], [], [], [], []
    # What the CLI said it did, as counts: the result's details, which the run's row keeps (tagpup.jobs.indexing).
    counted = {}

    def run(args):
        proc = processes.start(
            [sys.executable, "tagpup_cli.py"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1, cwd=code_folder,
        )
        furthest = [0]

        def one_bar(fact, percent):
            """`percent` of a phase as the one bar of the whole index: where its phase starts and how much of the bar it
            takes (_BAR), the bake into the files the second half, and never back (#2355)."""
            if percent is None:
                return None
            start, span = _BAR.get(fact.get("phase"), (0, 50))
            furthest[0] = max(furthest[0], start + percent * span // 100)
            return furthest[0]
        with proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                fact = progress_line.heard(line)
                if fact is None:
                    text = progress_line.plain(line)
                    if text:
                        report(_shortened(text), None)
                    continue
                kind, counts = fact["kind"], progress_line.counts(fact)
                if kind == "phase":
                    # By design, though the writer is this version's: a line must never end the job, and
                    # `job_runs.tell` raises ValueError on a phase it does not know.
                    if fact["phase"] in job_runs.PHASES:
                        job_runs.tell(fact["phase"], counts)
                    if fact["phase"] == "embeddings" and "of" in counts:
                        counted["photos_read"] = counts["of"]
                elif kind == "new_faces":
                    said.append(fact["sentence"])
                    offered.append(offers_reexamine(fact))
                elif kind == "failed":
                    failed.append(counts["photos"])
                    counted["failed_photos"] = counts["photos"]
                elif kind == "bake_read":
                    baked.append(fact["sentence"])
                elif kind == "baked":
                    wrote.append(fact["sentence"])
                    counted.update(("baked_" + name, value) for name, value in counts.items())
                if "sentence" in fact:
                    report(fact["sentence"], one_bar(fact, counts.get("percent")))
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
        result.details.update({"percent": 0, job_runs.FAILURE: job_runs.WAITED})
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
    if said and offered[-1]:
        result.details[REEXAMINE] = folders[0]
    return result

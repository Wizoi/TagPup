"""Adding a folder's photos to a library: the CLI's `index`, then, when asked,
`cluster-faces`, with what the CLI prints turned into progress a person can read.

Both apps ran their own copy of these steps, until TagTuner's called TagPup's. When a
folder is indexed is tagpup.jobs.indexing's to decide.
"""
import os
import re
import subprocess
import sys
from contextlib import nullcontext

from tagpup.core import paths
from tagpup.core import processes
from tagpup.core import validation
from tagpup.core.result import Result

#: Where faces are clustered when adding a folder did not: the runner's button, whose
#: label this is (tests/test_indexing_names_a_real_control.py holds the two together).
#: The messages named a Recluster button no page has (docs/findings.md, #22).
CLUSTERING_BUTTON = "Run Identity Resolution Clustering"
CLUSTER_ELSEWHERE = "%s in TagPup Runner" % CLUSTERING_BUTTON

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

    match = _INDEXER_TQDM.match(clean)
    if match:
        label, percent, done, total = match.groups()
        return f"{label.strip()}: {percent}% ({done}/{total})"

    if len(clean) > _INDEXER_MAX_MESSAGE:
        return clean[:_INDEXER_MAX_MESSAGE - 1].rstrip() + "\u2026"
    return clean


def index_folder(library, folder, code_folder, cluster=False, report=None, while_clustering=None):
    """Add a folder's photos to the library: the CLI's `index`, then, if `cluster`,
    `cluster-faces`. Adding a folder, in either app.

    The CLI runs in a process of its own, from `code_folder` -- the code this program
    runs, which the caller has from tagpup.config -- so the GPU work stays out of the
    server. Clustering runs only on request: it re-derives every face name in the
    library, not only this folder's.

    `report(message, percent)` hears progress as it comes. `while_clustering`, if given,
    is a context held while cluster-faces runs: TagTuner refuses assignments during it,
    since clustering rewrites the names an assignment would be setting.

    Both copies of this reported "identities resolved" when cluster-faces had failed:
    its exit code was never read. It is now, and a failure is the Result's error.

    details: `message`, what to tell the person; `percent`, where the bar stops.
    Refused, and nothing run, for a folder that is not named by its full path
    (tagpup.core.validation).
    """
    report = report or (lambda message=None, percent=None: None)
    result = Result(attempted=1)
    problem = validation.problem("folder", folder)
    if problem:
        result.refuse(problem)
        result.details["percent"] = 0
        return result
    env = os.environ.copy()
    env["TAGPUP_DB_PATH"] = library.path

    def run(args, scale):
        proc = processes.start(
            [sys.executable, "tagpup_cli.py"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1, cwd=code_folder,
        )
        with proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                clean = summarize_indexer_line(line)
                if clean:
                    match = re.search(r"(\d+)%", clean) if scale else None
                    report(clean, int(float(match.group(1)) * scale) if match else None)
        proc.wait()
        return proc.returncode

    # The indexer stores the paths it walks as given, so it is handed the stored form.
    code = run(["index", paths.stored(folder)], 0.9)
    if code != 0:
        result.fail(folder, "Indexing failed with exit code %s." % code)
        result.details["percent"] = 0
        return result

    if cluster:
        report("Resolving and matching face identities...", 95)
        with (while_clustering() if while_clustering else nullcontext()):
            code = run(["cluster-faces"], None)
        if code != 0:
            result.fail(folder, "Folder indexed, but resolving face identities failed "
                                "(exit code %s). To try again, use %s." % (code, CLUSTER_ELSEWHERE))
            result.details["percent"] = 100
            return result

    result.changed = 1
    result.details.update(percent=100, message=(
        "Folder indexed and face identities resolved." if cluster
        else "Folder indexed. Faces detected; to name them, use %s." % CLUSTER_ELSEWHERE))
    return result

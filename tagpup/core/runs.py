"""Which run a line of the logs belongs to: a recurring job's run, an index of folders, a
sync (the Activity page's "Logs for this run"; docs/ARCHITECTURE.md, phase 8.5).

A run is named by a tag -- `job:photo_index:12`, `sync:photo_index:20260926T101500`,
`index:photo_index:20260926T101500-3` -- which the code doing the work holds while it
runs:

    with runs.running(runs.job_tag(library.name, run_id)):
        job.call(...)

and every line logged meanwhile, on that thread, carries it (tagpup.logs puts the tags
of the running context into each line of a program's log file). Runs nest: a sync the
daily job runs logs both tags, so the job's lines include its sync's.

A process started for a run -- the CLI's `index`, started by the index queue -- is told
its tags in its environment (ENV), and every line it logs carries them; LOG_TO tells it
which kind of log to write in data/logs, one file for its run (tagpup.logs.run_log).

A tag names what the Activity page already shows of the run: the library's name, and the
job run's id, the sync's start or the index's start, so the page can ask for its lines
without the log naming anything else.
"""
import contextlib
import contextvars
import itertools
import os
import re
import time

#: The environment variable a child process is told its runs' tags in, comma-separated.
ENV = "TAGPUP_RUN_ID"

#: The environment variable a child process is told which log to write in: its kind
#: ("indexer"), the file being <kind>-<library>-<run>.log (tagpup.logs.run_log).
LOG_TO = "TAGPUP_LOG_TO"

#: What a tag may hold: a library's name holds nothing else (tagpup.core.validation).
_UNSAFE = re.compile(r"[^\w.-]")

#: A whole tag, as a log line or a request spells it.
TAG = re.compile(r"^(job|sync|index):[\w.-]+:[\w.-]+$")

_current = contextvars.ContextVar("tagpup_runs", default=())
_counter = itertools.count(1)


def _part(text):
    return _UNSAFE.sub("_", str(text if text not in (None, "") else "all"))


def tag(kind, library, ident):
    """`kind:library:ident`, each part only letters, digits, `_`, `.` and `-`."""
    return "%s:%s:%s" % (kind, _part(library), _part(ident))


def job_tag(library, run_id):
    """A recurring job's run, by its row in `job_runs`; `library` None for a job not run
    per library."""
    return tag("job", library, run_id)


def compact(stamp):
    """"2026-09-26 10:15:00" -> "20260926T101500": a time as a tag holds it."""
    return re.sub(r"[^0-9T]", "", str(stamp).replace(" ", "T"))


def sync_tag(library, started):
    """A sync of `library` that started at `started` (as `sync_runs` records it)."""
    return tag("sync", library, compact(started))


def index_tag(library, now=None):
    """A new run of the indexer for `library`: when it started, this process, and a number
    no other run of this process shares -- so no two processes' runs share a tag, nor the
    log file named for it."""
    return tag("index", library, "%s-%d-%d" % (time.strftime("%Y%m%dT%H%M%S", time.localtime(now)), os.getpid(),
                                               next(_counter)))


def is_tag(text):
    return isinstance(text, str) and bool(TAG.match(text))


def process_tags():
    """The tags this process was started for (ENV)."""
    return tuple(each for each in (os.environ.get(ENV) or "").split(",") if is_tag(each))


def current():
    """The tags of the runs under way on this thread: the process's, then each held."""
    return process_tags() + _current.get()


@contextlib.contextmanager
def running(run_tag):
    """Hold `run_tag` while the block runs: every line logged on this thread meanwhile
    carries it, beside any held already."""
    token = _current.set(_current.get() + (run_tag,))
    try:
        yield run_tag
    finally:
        _current.reset(token)


def child_environment(env, log_to=None):
    """`env` (a dict) told the runs under way here, and which log to write: for a process
    started for them."""
    found = current()
    if found:
        env[ENV] = ",".join(found)
    if log_to:
        env[LOG_TO] = log_to
    return env

"""A log file for each program, so what happened is still there after the window closes.

The apps logged to the console and nowhere else. It scrolled away, it went when the
window closed, and a question like "was that click slow on the server or in the
browser" had nothing to answer it afterwards. Each program now also writes
<data_dir>/logs/<program>.log, rotating at 5 MB and keeping five old files, and the
servers log every request slower than a second and every request that failed
(tagpup.web.app).

Each line carries the tags of the runs under way on its thread -- a recurring job's run,
a sync, an index (tagpup.core.runs) -- as `[run job:photo_index:12]` after the logger's
name, so the Activity page can show one run's lines. A process started for a run writes
its own file, <kind>-<library>.log (for_library): the indexer the index queue starts
writes indexer-<library>.log, one run at a time per library, so no two processes rotate
one file.
"""
import logging
import logging.handlers
import os
import re

from tagpup import config
from tagpup.core import runs

FORMAT = "%(asctime)s [%(levelname)s] %(threadName)s %(name)s%(run_tag)s - %(message)s"
MAX_BYTES = 5 * 1024 * 1024
KEEP = 5

#: What a program's log may be called: letters, digits, `_`, `.` and `-`.
_UNSAFE = re.compile(r"[^\w.-]")

#: Where the servers log slow and failed requests.
REQUESTS = "tagpup.requests"


def log_dir():
    return os.path.join(config.data_dir(), "logs")


class RunTag(logging.Filter):
    """Puts ` [run <tags>]` on a record -- the runs under way on its thread
    (tagpup.core.runs.current) -- or nothing, as `run_tag`, which FORMAT writes."""

    def filter(self, record):
        found = runs.current()
        record.run_tag = " [run %s]" % ",".join(found) if found else ""
        return True


def for_library(kind, library_name):
    """The program name of `kind`'s log for one library: `indexer-photo_index`."""
    return "%s-%s" % (_UNSAFE.sub("_", kind), _UNSAFE.sub("_", library_name or "none"))


def to_file(program, level=logging.INFO):
    """Also log to <data_dir>/logs/<program>.log, from this process. Returns the path.

    Call it from the process that does the work. Under the reloader that is the child,
    not the supervisor; two processes rotating one file fail on Windows. A second call
    for the same file adds nothing.
    """
    folder = log_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.abspath(os.path.join(folder, program + ".log"))
    root = logging.getLogger()
    for handler in root.handlers:
        # A FileHandler keeps os.path.abspath of the name it was given, which is `path`.
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == path:
            return path
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(RunTag())
    handler.setLevel(level)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return path

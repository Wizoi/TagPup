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


# ---- Reading them back, for the Activity page ---------------------------------------------
#
# A log is read from its end, never whole: the page polls it every few seconds, and a
# program's log may be 5 MB (MAX_BYTES) before it rotates. Each read looks at no more than
# READ_AT_MOST bytes, from the end or from where the page last read to; a page asking for
# older lines says where the last read began.

#: What a file in data/logs may be asked for by: a log or one of its rotated copies.
LOG_FILE = re.compile(r"^[\w.-]+\.log(?:\.\d{1,3})?$")

#: A line as FORMAT writes it: time, level, thread (which may hold spaces), logger, the
#: runs, message.
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) \[([A-Z]+)\] (.*?) (\S+?)(?: \[run ([^\]]+)\])? - (.*)$")

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

#: The most a read looks at, and the most the raw view sends.
READ_AT_MOST = 2 * 1024 * 1024
RAW_AT_MOST = 1024 * 1024

#: How much a read looks at first, from the end: doubled until it has the lines asked for.
FIRST_LOOK = 64 * 1024

#: What each program's log is, by its name: the page's tabs.
SOURCES = (
    (re.compile(r"^tagpup_web\.console$"), "Server console"),
    (re.compile(r"^tagpup_web$"), "Web server"),
    (re.compile(r"^supervisor$"), "Always on (supervisor)"),
    (re.compile(r"^indexer-(.+)$"), "Indexer ({0})"),
    (re.compile(r"^tagpup_mcp$"), "MCP server"),
    (re.compile(r"^runner$"), "Desktop runner"),
    (re.compile(r"^run_tests-.*$"), "Test run"),
)


def source_of(program):
    """What the log of `program` (its file name without .log) is, for a person."""
    for pattern, label in SOURCES:
        found = pattern.match(program)
        if found:
            return label.format(*found.groups())
    return program


def log_files():
    """The logs in data/logs, newest written first: {"name", "program", "source", "bytes",
    "modified" (seconds), "rotated": [{"name", "bytes"}]} -- each program's current file,
    with its rotated copies (<name>.1 ... <name>.KEEP) beside it."""
    folder = log_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    current, rotated = {}, {}
    for name in names:
        if not LOG_FILE.match(name):
            continue
        try:
            stat = os.stat(os.path.join(folder, name))
        except OSError:
            continue
        base, _dot, number = name.rpartition(".")
        if number.isdigit():
            rotated.setdefault(base, []).append((int(number), {"name": name, "bytes": stat.st_size}))
        else:
            program = name[:-len(".log")]
            current[name] = {"name": name, "program": program, "source": source_of(program),
                             "bytes": stat.st_size, "modified": stat.st_mtime, "rotated": []}
    for base, copies in rotated.items():
        if base in current:
            current[base]["rotated"] = [entry for _n, entry in sorted(copies, key=lambda each: each[0])]
    return sorted(current.values(), key=lambda entry: -entry["modified"])


def log_path(name):
    """The file in data/logs called `name` (a log, or a rotated copy of one), or None: a
    name that is not one, or reaches anywhere else, is None."""
    if not isinstance(name, str) or not LOG_FILE.match(name):
        return None
    path = os.path.join(log_dir(), name)
    return path if os.path.isfile(path) else None


def _record(line):
    """A line as {"time", "level", "thread", "logger", "runs", "message", "text"}, or None
    for a line FORMAT did not write (a traceback's, the console's)."""
    found = LINE.match(line)
    if not found:
        return None
    stamp, millis, level, thread, logger_name, run_tags, message = found.groups()
    return {"time": stamp, "ms": int(millis), "level": level, "thread": thread, "logger": logger_name,
            "runs": run_tags.split(",") if run_tags else [], "message": message, "text": line}


def _records(data, offset, at_start):
    """The records in `data`, bytes read from `offset`, oldest first, each with the offset
    it starts at; and the offset of the first whole record. A record is a line FORMAT
    wrote and the lines under it (a traceback); in a log with no such line (the console's),
    each line is one. Read from the middle of a file (not `at_start`), the part-line it
    starts with is dropped, and so are the lines before its first record, which belong to
    one before the read."""
    lines, position = [], offset
    for raw in data.split(b"\n"):
        lines.append((position, raw))
        position += len(raw) + 1
    if not at_start and lines:
        lines = lines[1:]
    if lines and lines[-1][1] == b"":
        lines = lines[:-1]   # the file's last newline
    decoded = [(at, raw.decode("utf-8", errors="replace").rstrip("\r")) for at, raw in lines]
    headed = [(at, _record(text), text) for at, text in decoded]
    formatted = any(record for _at, record, _t in headed)
    found, first = [], None
    for at, record, text in headed:
        if record is not None:
            record["offset"] = at
            found.append(record)
        elif formatted and found:
            found[-1]["text"] += "\n" + text
            found[-1]["message"] += "\n" + text
        elif formatted and not at_start:
            continue   # under a record that began before this read
        else:
            found.append({"time": None, "ms": 0, "level": None, "thread": None, "logger": None, "runs": [],
                          "message": text, "text": text, "offset": at})
        if first is None:
            first = at
    return found, (first if first is not None else offset + len(data))


def _wanted(record, level, text, run):
    if level is not None and record["level"] is not None and LEVELS.get(record["level"], 0) < level:
        return False
    if run and run not in record["runs"]:
        return False
    if text and text not in record["text"].lower():
        return False
    return True


def read(name, limit=200, level=None, text=None, run=None, before=None, after=None, at_most=READ_AT_MOST):
    """The newest `limit` records of the log `name` that are at `level` or above (a name,
    "WARNING"; a line the log's format did not write -- the console's -- always is), hold
    `text` (any case) and belong to the run `run` (a tag), newest first; never reading
    more than `at_most` bytes of it.

    `before`, an offset: the records before it (the page's "older", from the `start` the
    last read answered). `after`, an offset: only the records written since (the page's
    live follow, from the `end` the last read answered); a file smaller than `after` has
    rotated, and is read from its end again.

    {"name", "bytes", "records", "start" (where the oldest record looked at begins: the
    `before` of the next older read), "end" (the file's size when read: the next `after`),
    "looked_at" (bytes), "complete" (the read reached the file's start), "rotated"}.
    None for a name that is no log."""
    path = log_path(name)
    if path is None:
        return None
    wanted_level = LEVELS.get(str(level).upper()) if level else None
    needle = str(text).lower() if text else None
    limit = max(1, min(int(limit), 1000))
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        rotated = False
        if after is not None:
            after = int(after)
            if after > size:
                rotated, after = True, None
        if after is not None:
            low = max(after, size - at_most)
            handle.seek(low)
            data = handle.read(size - low)
            found, start = _records(data, low, low == after or low == 0)
            chosen = [each for each in found if _wanted(each, wanted_level, needle, run)][-limit:]
            return {"name": name, "bytes": size, "records": list(reversed(chosen)), "start": start, "end": size,
                    "looked_at": len(data), "complete": low == 0, "rotated": rotated}
        end = size if before is None else max(0, min(int(before), size))
        look = min(FIRST_LOOK, at_most)
        while True:
            low = max(0, end - look)
            handle.seek(low)
            data = handle.read(end - low)
            found, start = _records(data, low, low == 0)
            chosen = [each for each in found if _wanted(each, wanted_level, needle, run)]
            if len(chosen) >= limit or low == 0 or look >= at_most:
                break
            look = min(look * 2, at_most)
    chosen = chosen[-limit:]
    if len(chosen) == limit and chosen:
        # The next older read starts where the oldest record returned does.
        start = chosen[0]["offset"]
    return {"name": name, "bytes": size, "records": list(reversed(chosen)), "start": start, "end": size,
            "looked_at": end - low, "complete": start <= 0 or (low == 0 and len(chosen) < limit),
            "rotated": rotated}


def tail(name, at_most=RAW_AT_MOST):
    """The end of the log `name` as it is written, at most `at_most` bytes from a line's
    start, or None for a name that is no log: the page's "raw"."""
    path = log_path(name)
    if path is None:
        return None
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        low = max(0, size - at_most)
        handle.seek(low)
        data = handle.read(size - low)
    if low:
        cut = data.find(b"\n")
        data = data[cut + 1:] if cut >= 0 else b""
    return data

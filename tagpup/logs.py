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
a file of that run's own, <kind>-<library>-<run>.log (run_log): the indexer the index
queue starts writes indexer-<library>-<started>-<pid>-<n>.log. Two processes may index
one library at once -- the server's queue and a sync run by hand -- and one
file written and rotated by two fails on Windows; so each run has its own. Such a
process writes its log quietly (to_file's `quiet`): its stdout and stderr are the pipe
its parent turns into a progress bar, where logging's "--- Logging error ---" showed.

Two groups of file (group_of): a program's own log (SYSTEM: the web server, its console, the
MCP server, TagTuner), which rotates and never grows past its copies; and a run's (RUN: each
run of the indexer, each failed test run), which keep coming. The runs' logs are kept the
last KEEP_RUN_LOGS of each library and kind, removed as the server starts (retire_run_logs;
owner, DECISIONS 2026-10-28); a failed test run's are tools/run_tests.py's, which keeps ten.
"""
import logging
import logging.handlers
import os
import re
import time

from tagpup import config
from tagpup.core import runs

FORMAT = "%(asctime)s [%(levelname)s] %(threadName)s %(name)s%(run_tag)s - %(message)s"
MAX_BYTES = 5 * 1024 * 1024
KEEP = 5

#: What a program's log may be called: letters, digits, `_`, `.` and `-`.
_UNSAFE = re.compile(r"[^\w.-]")

#: How many runs' logs of one library and kind are kept (owner, DECISIONS 2026-10-28).
KEEP_RUN_LOGS = 5

#: The groups of file in data/logs (group_of): a program's own log, and a run's.
SYSTEM, RUN = "system", "run"

#: A run's own log, as run_log names it for a run of the indexer (tagpup.core.runs.index_tag):
#: <kind>-<library>-<started>-<pid>-<n>; and a failed test run's, tools/run_tests.py's.
RUN_LOG = re.compile(r"^(?P<kind>[A-Za-z_]+)-(?P<library>[\w.-]+)-(?P<ident>\d{8}T\d{6}-\d+-\d+)$")
TEST_RUN, TESTS = re.compile(r"^run_tests-\d{8}-\d{6}(?:-\d+)?$"), "run_tests"

#: The web server's log: its program's name, and the file (tagpup_web.py writes it; the
#: Activity page is told it, and never spells it).
SERVER_PROGRAM = "tagpup_web"
SERVER_LOG = SERVER_PROGRAM + ".log"

#: What a server an install started prints before its own log (a traceback that stops it starting): the launcher
#: writes it (tagpup.launcher.started_log) and SOURCES says it in words, both by this one name.
STARTED_PROGRAM = SERVER_PROGRAM + ".installed"
STARTED_LOG = STARTED_PROGRAM + ".log"

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


def run_log(kind, tags):
    """The file a run of `kind` writes: `indexer-photo_index-20260926T101500-4242-1.log`
    for the run `index:photo_index:20260926T101500-4242-1`. `tags` is a run's tag, or the
    tags a process was started for (tagpup.core.runs.current), of which the innermost --
    the last -- is its own run. The one place the name is made: the child writes it, and
    the Activity page is sent it."""
    found = [tags] if isinstance(tags, str) else list(tags or ())
    found = [each for each in found if runs.is_tag(each)]
    if not found:
        return "%s-%d.log" % (_UNSAFE.sub("_", kind), os.getpid())
    _kind, library, ident = found[-1].split(":", 2)
    return "%s-%s-%s.log" % (_UNSAFE.sub("_", kind), library, ident)


def group_of(program):
    """Which group the log of `program` (its file name without .log) is in, with its run's kind and library:
    (RUN, "indexer", "photo_index") for a run of the indexer, (RUN, TESTS, None) for a failed test run, (SYSTEM, None,
    None) for any program's own log."""
    found = RUN_LOG.match(program)
    if found:
        return RUN, found["kind"], found["library"]
    if TEST_RUN.match(program):
        return RUN, TESTS, None
    return SYSTEM, None, None


def _base(name):
    """The log a file in data/logs is, or a rotated copy of: `x.log` for `x.log.2`."""
    return name if name.endswith(".log") else name.rsplit(".", 1)[0]


def _run_families(folder, names, unread=()):
    """The runs' logs among `names` in `folder`, by library and kind -- the one ordering of "which runs are newer" that
    retire_run_logs removes by and why_missing explains by: {(kind, library lowercased): {base: [when it was last
    written, its files]}}, a run's log with its rotated copies one family, last written when its newest file was.
    A library in `unread` (lowercased) is left out; a file that cannot be looked at is passed over."""
    families = {}
    for name in names:
        if not LOG_FILE.match(name):
            continue
        base = _base(name)
        group, kind, library = group_of(base[:-len(".log")])
        if group != RUN or library is None or library.lower() in unread:
            continue
        try:
            modified = os.path.getmtime(os.path.join(folder, name))
        except OSError:
            continue
        family = families.setdefault((kind, library.lower()), {}).setdefault(base, [0.0, []])
        family[0] = max(family[0], modified)
        family[1].append(name)
    return families


def _newest_first(logs_of):
    """One library and kind's run logs (a value of _run_families), the last written first."""
    return sorted(logs_of.items(), key=lambda item: -item[1][0])


def retire_run_logs(spared=(), unread=(), keep=KEEP_RUN_LOGS):
    """Remove the runs' logs beyond the newest `keep` (by when each was last written: _run_families) of each library
    and kind, each with its rotated copies: the server's start does it (tagpup.runtime.retire_run_logs). Never a log in
    `spared` (the logs of runs a library's rows say are running), nor any of a library named in `unread` (one whose rows
    could not be read, so nothing says which of its runs are running); a test run's are tools/run_tests.py's. A file
    gone already -- another server starting at the same moment removed it -- or held open by its writer (Windows
    refuses to remove it) is passed over, left for the next start. Returns how many files this call removed."""
    folder = log_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    spared = set(spared)
    removed = 0
    for logs_of in _run_families(folder, names, {name.lower() for name in unread}).values():
        for base, (_modified, members) in _newest_first(logs_of)[keep:]:
            if base in spared:
                continue
            for name in members:
                try:
                    os.remove(os.path.join(folder, name))
                    removed += 1
                except OSError:
                    pass
    return removed


def _started(program):
    """When the run whose log is `program` (a file name without .log) started, from its name (tagpup.core.runs.index_tag
    writes it in local time), or None for a name that is not a run's of the indexer's shape."""
    found = RUN_LOG.match(program)
    try:
        return time.mktime(time.strptime(found["ident"][:15], "%Y%m%dT%H%M%S")) if found else None
    except (ValueError, OverflowError):
        return None


def why_missing(name):
    """Why there is no log called `name` in data/logs, as (a sentence the Activity page shows, whether it was removed):
    a run's log was removed (retire_run_logs) when its library and kind keep KEEP_RUN_LOGS others last written after
    the run started -- by the same ordering retire_run_logs removes by (_run_families), so a run that overlapped longer
    ones is said removed when it was; any other run's has not been written (its indexer has not started, or never did).
    A run that never wrote a log while five later ones did reads as removed: nothing on disk tells the two apart."""
    name = str(name)
    group, kind, library = group_of(_base(name)[:-len(".log")]) if LOG_FILE.match(name) else (SYSTEM, None, None)
    started = _started(_base(name)[:-len(".log")]) if group == RUN else None
    if group != RUN or library is None or started is None:
        return "There is no log called %s in data/logs" % name, False
    folder = log_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    logs_of = _run_families(folder, names).get((kind, library.lower()), {})
    newer = [base for base, (modified, _members) in _newest_first(logs_of) if base != _base(name) and modified > started]
    if len(newer) >= KEEP_RUN_LOGS:
        return ("The log of this run was removed: the last %d runs' logs of each library and kind are kept."
                % KEEP_RUN_LOGS), True
    return "This run has written no log of its own (yet).", False


class _QuietRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A log file whose write failures say nothing on stderr (to_file's `quiet`)."""

    def handleError(self, record):
        pass


def to_file(program, level=logging.INFO, quiet=False):
    """Also log to <data_dir>/logs/<program>.log, from this process. Returns the path.

    Call it from the process that does the work. Under the reloader that is the child,
    not the supervisor; two processes rotating one file fail on Windows. A second call
    for the same file adds nothing.
    """
    folder = log_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.abspath(os.path.join(folder, program if program.endswith(".log") else program + ".log"))
    root = logging.getLogger()
    for handler in root.handlers:
        # A FileHandler keeps os.path.abspath of the name it was given, which is `path`.
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == path:
            return path
    # `quiet`: a line that cannot be written is dropped, not reported on stderr -- which, in
    # a process started for a run, is its parent's progress bar.
    kind = _QuietRotatingFileHandler if quiet else logging.handlers.RotatingFileHandler
    handler = kind(path, maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8", delay=True)
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
    (re.compile("^%s$" % re.escape(STARTED_PROGRAM)), "Server start-up output (installed copy)"),
    (re.compile(r"^indexer-(.+)-(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)\d\d-\d+-\d+$"), "Indexer ({0}, {2}-{3} {4}:{5})"),
    (re.compile(r"^tagpup_mcp$"), "MCP server"),
    (re.compile(r"^runner$"), "Desktop runner"),
    (re.compile(r"^run_tests-.*$"), "Test run"),
)


#: Each kind of run (group_of) in words, for the Activity page's Kind filter: the listing sends them (kind_words), the
#: page holds no copy. The indexer's kind is its log's (tagpup.services.indexing.INDEXER_LOG).
KIND_WORDS = {"indexer": "Indexer runs", TESTS: "Test runs"}


def kind_words(kind):
    """The kind of run `kind` in words ("Indexer runs"); a kind with none is said as it is spelled."""
    return KIND_WORDS.get(kind, kind)


def source_of(program):
    """What the log of `program` (its file name without .log) is, for a person."""
    for pattern, label in SOURCES:
        found = pattern.match(program)
        if found:
            return label.format(*found.groups())
    return program


def log_files():
    """The logs in data/logs, newest written first: {"name", "program", "source", "group" (SYSTEM or RUN), "kind" and
    "library" (a run's, group_of; None for a program's own log and a test run's library), "bytes", "modified"
    (seconds), "rotated": [{"name", "bytes"}]} -- each program's current file, with its rotated copies (<name>.1 ...
    <name>.KEEP) beside it."""
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
            group, kind, library = group_of(program)
            current[name] = {"name": name, "program": program, "source": source_of(program), "group": group,
                             "kind": kind, "library": library, "bytes": stat.st_size, "modified": stat.st_mtime,
                             "rotated": []}
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

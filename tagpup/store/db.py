"""Every connection to a TagPup database is made here.

This program is a reader and a writer at the same time, constantly, and from many
threads. Both servers handle each request on its own thread; the tag suggester runs a
thread pool; indexing runs in a background thread beside all of it. Across the codebase
that is 71 places opening a connection and 72 statements writing through one.

Getting that right in 71 places independently is not something to attempt. The rules
live here instead:

**WAL.** Readers and one writer proceed together. In the default rollback-journal mode
a writer needs an exclusive lock on the whole file and any open reader denies it, which
is how clicking Suggest Tags produced a wall of "database is locked" against its own
server. `journal_mode` is a property of the file, so setting it once carries everywhere.

**A busy timeout**, so a connection waits its turn rather than failing the instant the
file is busy.

**One writer at a time within this process.** WAL permits many readers beside one
writer; it says nothing about two writers, and nearly every writer here is another
thread of this same program. Worse, the busy timeout does not help in that case: a
connection holding an open read transaction that then tries to write must upgrade its
lock, and SQLite refuses that immediately without ever calling the busy handler. So a
30-second timeout expires in no time at all. The lock is keyed by database file, so
writers to different databases never wait on each other.

**A retry** for the writer this process cannot see -- another process, or a checkpoint.

**The library's roots, held by the connection.** A path column holds a root's name and the path
under it once a library has been adopted by a root (tagpup.store.roots), and the store converts
at its boundary. The conversion a connection uses is kept on the connection (`roots_state`), so
an operation that holds a connection converts every path of it by one Roots -- never a lookup a
row -- and a write is refused, and run again, when the library's roots changed between the
moment its paths were converted and the moment it commits.

Never call `sqlite3.connect` directly; `tests/test_db_access.py` fails if you do.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from tagpup.core import paths, processes
from tagpup.core.library import Library

logger = logging.getLogger("tagpup_cli.db")

#: How long a connection waits for a busy database. Generous, because what it waits
#: for -- another part of this program finishing a write -- is short, and failing is
#: expensive.
BUSY_TIMEOUT_MS = 30000

_locks = {}
_locks_guard = threading.Lock()


def _key(target):
    """The database a connection target refers to, as a comparable key.

    Accepts a plain path or a `file:` URI, since read-only connections are opened as
    `file:...?mode=ro`. Two spellings of one file must map to one lock, or the lock
    protects nothing.
    """
    text = str(target)
    if text.startswith("file:"):
        text = text[len("file:"):].split("?", 1)[0]
    try:
        return paths.key(text)
    except Exception:
        return text


def readonly_uri(db_path):
    """The `file:` URI that opens a database read-only: connect(readonly_uri(p), uri=True).

    URI syntax wants forward slashes, which is the one place a path is spelled with
    them on purpose.
    """
    return "file:%s?mode=ro" % paths.stored(db_path).replace("\\", "/")  # not a path: URI syntax


class Connection(sqlite3.Connection):
    """The connection db.connect makes: SQLite's, with a place to keep the roots the store
    converts this connection's paths by (tagpup.store.roots.roots_for)."""

    #: What tagpup.store.roots keeps here, or None before the connection first needs it.
    roots_state = None

    #: Whether tagpup.store.derived has found the library's derived tables there (migration 19):
    #: remembered once true, so a write does not ask the schema each time.
    derived_ready = False


def _is_readonly(target, kwargs):
    return kwargs.get("uri") and "mode=ro" in str(target)


def lock_for(target):
    """The write lock for a database, shared by every connection to it."""
    key = _key(target)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            # Reentrant: a write path may call into another one.
            lock = threading.RLock()
            _locks[key] = lock
        return lock


def configure(conn, readonly=False):
    """Put a connection into the mode this program actually runs in."""
    try:
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    except Exception as e:
        # A read-only connection to a database another process has locked can refuse
        # even these. Worth knowing about, not worth failing over.
        logger.debug("Could not configure connection to %s: %s", _key(conn), e)
    return conn


def connect(target, *args, foreign_keys=False, **kwargs):
    """Open a connection, configured. Same signature as `sqlite3.connect`.

    `timeout` defaults to the busy timeout above rather than sqlite3's 5 seconds.
    `foreign_keys` turns on SQLite's enforcement for this connection, which PhotoIndex
    relies on: deleting a photo row takes its faces with it (ON DELETE CASCADE).
    """
    kwargs.setdefault("timeout", BUSY_TIMEOUT_MS / 1000.0)
    kwargs.setdefault("factory", Connection)
    conn = sqlite3.connect(target, *args, **kwargs)
    configure(conn, readonly=_is_readonly(target, kwargs))
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    return conn


def begin(conn, immediate=False):
    """Open a transaction on `conn` now, for a batch the caller commits or rolls back as
    one. IMMEDIATE takes the write lock at once rather than at the first write."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")


def retry_when_busy(operation, attempts=4, first_delay=0.25, label="database write"):
    """Run something that writes, waiting out a locked database rather than failing.

    Only lock errors are retried. Anything else is raised at once, because retrying a
    broken statement four times only delays the report.
    """
    delay = first_delay
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as e:
            message = str(e).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if attempt == attempts - 1:
                raise
            logger.info("%s: database busy, retrying in %.2fs", label, delay)
            time.sleep(delay)
            delay *= 2


#: How long a note that the library is busy for a reason stays believed: a process that crashed
#: holding it leaves the file behind.
BUSY_NOTE_SECONDS = 15 * 60


def _busy_file(target):
    return str(target) + ".busy"


def mark_busy(target, why):
    """Say, beside the library at `target`, why a write may have to wait: a maintenance step
    holds the write lock for a long time (a backup copy under a root's adoption). A write that
    then fails as locked says it (`write`), where it was a bare "database is locked". Cleared
    with clear_busy."""
    try:
        with open(_busy_file(target), "w", encoding="utf-8") as handle:
            json.dump({"why": why, "since": time.time(), "pid": os.getpid()}, handle)
    except OSError:
        pass


def clear_busy(target):
    try:
        os.remove(_busy_file(target))
    except OSError:
        pass


def busy_note(target):
    """Why the library at `target` is busy, if a maintenance step said so recently and the process
    that said it is still there, else None. A note whose process has gone -- killed during its
    copy -- is removed: its lock is gone with it, and any lock failure after it is an ordinary one."""
    try:
        with open(_busy_file(target), encoding="utf-8") as handle:
            found = json.load(handle)
        if time.time() - float(found["since"]) >= BUSY_NOTE_SECONDS:
            return None
        if not processes.is_alive(int(found["pid"])):
            clear_busy(target)
            return None
        return str(found["why"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write(target, operation, label="database write"):
    """Run a write with this process's other writes to the same database held back. A write
    that fails for the lock when a step said it holds it for a reason (mark_busy) says why."""
    with lock_for(target):
        try:
            return retry_when_busy(operation, label=label)
        except sqlite3.OperationalError as problem:
            note = busy_note(target) if "locked" in str(problem).lower() else None
            if note:
                raise sqlite3.OperationalError("%s: %s" % (problem, note)) from problem
            raise


def write_with_connection(target, operation, label="database write"):
    """Run a write on a connection of its own, with other writers held back.

    A connection has one transaction state, and this program shares connections
    across threads (`check_same_thread=False`). Two threads writing through one
    connection interleave into each other's implicit transaction, and the loser is
    told the database is locked -- immediately, without the busy timeout applying,
    because there is nothing to wait for.

    That is why recording faces, which has always opened its own connection, kept
    succeeding in the same instant the embedding cache on the shared connection
    failed. A writer gets a connection to itself.

    `operation` is called with the connection and its result returned; the commit,
    rollback and close are handled here. It may be called more than once, so it
    should not carry state between attempts: one is that the library's roots changed, by
    another process, between the moment the operation converted its paths and the moment it
    would commit (roots.unchanged), when it is run again with the roots as they now are.
    """
    def attempt():
        conn = connect(target)
        try:
            result = operation(conn)
            if conn.roots_state is not None:
                from tagpup.store import roots   # roots imports this module
                roots.unchanged(conn)
            conn.commit()
            return result
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    return write(target, attempt, label=label)


@contextmanager
def writing(target, label="database write"):
    """Hold the write lock for a block that writes.

    Use `write()` where the work fits in a callable -- it retries, and this cannot,
    since a block that has already half-run is not safe to run again.
    """
    with lock_for(target):
        yield


def copy_database(source_path, target, then=None):
    """Copy the database at `source_path` to `target`, a new file, in one step of the backup
    API from a read-only connection -- one read transaction, so the copy is the database as
    it stood when the step began, whoever writes meanwhile -- as a file of its own.

    The destination is put in rollback-journal mode before the copy, not in WAL as
    connect() leaves every connection: in WAL the backup wrote the whole database into the
    -wal and then checkpointed it into the file, twice the writing (photo_index: 12.2 s,
    not 6.1 s, and a 2.5 GB -wal beside it). The copied header says WAL, so it is put back
    in rollback-journal mode after, and nothing is left beside it. `then(connection)`, if
    given, runs on the destination before it is closed; it commits what it writes.
    """
    source = connect(readonly_uri(source_path), uri=True)
    try:
        destination = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000.0)
        try:
            destination.execute("PRAGMA journal_mode=DELETE")
            source.backup(destination)
            destination.execute("PRAGMA journal_mode=DELETE")
            if then is not None:
                then(destination)
        finally:
            destination.close()
    finally:
        source.close()


def backup(db_path, reason, into=None):
    """Copy a database before a bulk write, and return where the copy went.

    Through SQLite's backup API, from a read-only connection, so the copy is
    consistent even while an app has the database open (copy_database). Into backups/ beside the
    library unless `into` says otherwise, named for the database, the reason and the
    time: data/backups/photo_index.before-dedupe-faces-20260923_151200.db.

    Every script that writes in bulk calls this before it writes; three had their
    own copy of it and six had none. tests/test_bulk_scripts_back_up.py holds them
    to it.
    """
    import os

    into = into or Library(db_path).backups
    os.makedirs(into, exist_ok=True)
    target = os.path.join(into, "%s.before-%s-%s.db" % (
        os.path.splitext(os.path.basename(db_path))[0], reason,
        time.strftime("%Y%m%d_%H%M%S")))
    copy_database(db_path, target)
    prune_backups(into, os.path.splitext(os.path.basename(db_path))[0])
    return target


def space(db_path):
    """(bytes the file takes, bytes inside it that hold nothing) of the library at
    `db_path`. Rows deleted and columns dropped leave their pages free in the file until
    it is compacted: migration 3's crops alone left about 1 GB of photo_index's."""
    conn = connect(readonly_uri(db_path), uri=True)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        conn.close()
    return pages * page_size, free * page_size


def compact(db_path):
    """Rewrite the library at `db_path` without its free pages (VACUUM), under its write
    lock. It needs the file to itself for as long as it takes -- 41 s for photo_index --
    and fails with "database is locked" while an app has it open to write. Returns
    (bytes before, bytes after)."""
    before = space(db_path)[0]
    with lock_for(db_path):
        conn = connect(db_path, timeout=30.0)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
    return before, space(db_path)[0]


#: How many backups each library keeps; the oldest beyond it goes when a new one is made.
#: Nothing deleted a copy before, and backups/ once held 28 GB in 41 of them
#: (docs/findings.md, #7). Per library, so one library's never push out another's
#: *(owner, 2026-09-24)*.
KEEP_BACKUPS = 5

#: ... and none older than this many days: a one-off `before-*` copy of a bulk write is for the
#: mistake noticed within the month, and 18.7 GB of older ones sat in backups/ because only the
#: count limited them. The daily, weekly and monthly snapshots (tagpup.store.snapshots) are
#: kept by their own rule and are not these files. The newest copy is never taken by age: it
#: is the one just made.
BACKUP_DAYS = 30


def prune_backups(folder, library_name, keep=KEEP_BACKUPS, days=BACKUP_DAYS, now=None):
    """Delete all but the newest `keep` backups of `library_name` in `folder`, and any
    but the newest older than `days`, with the -wal and -shm files beside them. Newest by
    the time in the name. Returns the paths deleted."""
    import os
    import re

    stamped = re.compile(r"^%s\.before-.*-(\d{8}_\d{6})\.db$" % re.escape(library_name))
    copies = sorted((match.group(1), name) for name in os.listdir(folder)
                    for match in [stamped.match(name)] if match)
    cutoff = time.strftime("%Y%m%d_%H%M%S", time.localtime((time.time() if now is None else now) - days * 86400))
    last = len(copies) - 1
    doomed = [each for index, each in enumerate(copies)
              if not keep or index < len(copies) - keep or (each[0] < cutoff and index < last)]
    deleted = []
    for _stamp, name in doomed:
        for suffix in ("", "-wal", "-shm"):
            path = os.path.join(folder, name + suffix)
            if os.path.exists(path):
                os.remove(path)
                deleted.append(path)
    if deleted:
        logger.info("Deleted %d old backup file(s) of %s", len(deleted), library_name)
    return deleted

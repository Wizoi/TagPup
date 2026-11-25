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

**One connection for a bulk write's many**, on its thread (connections_held): a write of thousands of photo files
opened four connections a photo, 43% of its time; inside the scope its writes and reads each reuse one, every write still
its own transaction under the lock and the retry.

**The library's roots, held by the connection.** A path column holds a root's name and the path
under it once a library has been adopted by a root (tagpup.store.roots), and the store converts
at its boundary. The conversion a connection uses is kept on the connection (`roots_state`), so
an operation that holds a connection converts every path of it by one Roots -- never a lookup a
row -- and a write is refused, and run again, when the library's roots changed between the
moment its paths were converted and the moment it commits.

Never call `sqlite3.connect` directly; `tests/test_db_access.py` fails if you do.
"""
import contextlib
import logging
import os
import sqlite3
import threading
import time

from tagpup.core import paths
from tagpup.core.library import Library

logger = logging.getLogger("tagpup_cli.db")

#: How long a connection waits for a busy database. Generous, because what it waits
#: for -- another part of this program finishing a write -- is short, and failing is
#: expensive.
BUSY_TIMEOUT_MS = 30000

_locks = {}
_locks_guard = threading.Lock()


def key(target):
    """The database a connection target refers to, as a comparable key: the one the write lock, schema's record of the
    libraries found current and being migrated, and the journal key a library by.

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


def _is_readonly(target, kwargs):
    return kwargs.get("uri") and "mode=ro" in str(target)


def lock_for(target):
    """The write lock for a database, shared by every connection to it."""
    held = key(target)
    with _locks_guard:
        lock = _locks.get(held)
        if lock is None:
            # Reentrant: a write path may call into another one.
            lock = threading.RLock()
            _locks[held] = lock
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
        logger.debug("Could not configure connection to %s: %s", key(conn), e)
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
    one. IMMEDIATE takes the write lock at once rather than at the first write. The one
    place a transaction is begun (tests/test_db_access.py).

    It waits the busy timeout for another process and does not retry: a write run again on a
    busy database is write_with_connection's, with the begin inside its operation. A caller
    that holds a connection across several transactions -- the index's (roots.begin_write),
    the migration runner's, under the write lock for the whole run -- begins here, and a
    failure is raised to it: a migration left undone is run again by the next open."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")


def locked(error):
    """Is `error` SQLite saying the database is locked or busy: another writer held it longer than this one waited? Not
    "database table is locked" or "database schema is locked": a statement of this connection in the way of another, a
    code's defect that waiting does not settle."""
    if not isinstance(error, sqlite3.OperationalError):
        return False
    message = str(error).lower()
    return "database is locked" in message or "busy" in message


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
            if not locked(e):
                raise
            if attempt == attempts - 1:
                raise
            logger.info("%s: database busy, retrying in %.2fs", label, delay)
            time.sleep(delay)
            delay *= 2


def write(target, operation, label="database write"):
    """Run a write with this process's other writes to the same database held back."""
    with lock_for(target):
        return retry_when_busy(operation, label=label)


class _ThisThread(threading.local):
    """What connections_held holds on one thread: {key(target): _Held}. Named nowhere but here
    (tests/test_db_access.py): a connection is never lent to another thread."""

    def __init__(self):
        self.scopes = {}


_held_by_this_thread = _ThisThread()


class _Held:
    """The connections one connections_held scope lends: a read-write one to write_with_connection, a read-only one to
    read_with_connection, each opened at its first use and closed when the scope ends or a failure drops it."""

    def __init__(self, target):
        self.target, self.writer, self.reader = target, None, None

    def lend_writer(self):
        if self.writer is None:
            self.writer = connect(self.target)
        elif self.writer.in_transaction:
            # Every write commits, or rolls back and drops it (write_with_connection): one still in a transaction is
            # asked for by a write inside another's operation, and would commit the outer write's half.
            raise RuntimeError("the library's held connection was asked for while a write's transaction is open on it")
        return self.writer

    def lend_reader(self):
        if self.reader is None:
            self.reader = connect(readonly_uri(self.target), uri=True)
        return self.reader

    def drop(self, which):
        conn = getattr(self, which)
        setattr(self, which, None)
        if conn is not None:
            conn.close()

    def close(self):
        try:
            self.drop("writer")
        finally:
            self.drop("reader")


@contextlib.contextmanager
def connections_held(target):
    """On this thread only, until the block ends: write_with_connection(target, ...) writes on one read-write connection
    and read_with_connection(target, ...) reads on one read-only connection, rather than each opening and closing its own
    (a bulk write of photo files: docs/plans/bulk-write-connections.md). Each write is still its own transaction,
    committed where it was, under lock_for and retry_when_busy, and the lock is not held between two writes: in WAL a
    connection with no transaction holds up no other writer. Another thread, or process, opens its own as before.

    A block inside another for the same database (key) shares the outer's connections and closes nothing; the outermost
    closes both, also when it is left by a crash (a BaseException). write_if_free and connect are never served from it."""
    scopes = _held_by_this_thread.scopes
    name = key(target)
    if name in scopes:
        yield
        return
    held = scopes[name] = _Held(target)
    try:
        yield
    finally:
        del scopes[name]
        held.close()


def _rolled_back(conn):
    """Roll back what a failed write left on `conn`: True when that left it in no transaction."""
    try:
        conn.rollback()
        return not conn.in_transaction
    except Exception:
        return False


def _fit_after(error):
    """Is a held connection, rolled back, fit for the next write after a write on it raised `error`: an error of the
    operation's own (not SQLite's: a follower's), a lock another writer held (`locked`: run again on it), or a constraint
    the write broke (IntegrityError)? Anything else of SQLite's (a DatabaseError, a ProgrammingError, another
    OperationalError) or a crash (a BaseException) drops it, and the next write opens a fresh one."""
    if not isinstance(error, Exception):
        return False
    if not isinstance(error, sqlite3.Error):
        return True
    return locked(error) or isinstance(error, sqlite3.IntegrityError)


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

    Inside connections_held(target) on this thread the connection is the scope's: rolled back after a failed attempt, and
    kept for the next only when that left it in no transaction and the error leaves it fit (_fit_after).
    """
    held = _held_by_this_thread.scopes.get(key(target))

    def attempt():
        conn = held.lend_writer() if held is not None else connect(target)
        try:
            result = operation(conn)
            if conn.roots_state is not None:
                from tagpup.store import roots   # roots imports this module
                roots.unchanged(conn)
            conn.commit()
            return result
        except BaseException as error:
            if not _rolled_back(conn) or not _fit_after(error):
                if held is not None and held.writer is conn:
                    held.drop("writer")
            raise
        finally:
            if held is None:
                conn.close()

    return write(target, attempt, label=label)


def read_with_connection(target, operation):
    """`operation(cursor)`'s result, read from the database at `target` (a path) on a read-only connection: the one
    connections_held lends on this thread, else one of its own, closed after. The cursor is closed after the call, so no
    statement outlives it: an open one keeps its snapshot, and a read after it would not see a write committed since. A
    failure drops a held connection: the next read opens a fresh one."""
    held = _held_by_this_thread.scopes.get(key(target))
    conn = held.lend_reader() if held is not None else connect(readonly_uri(target), uri=True)
    try:
        cursor = conn.cursor()
        try:
            return operation(cursor)
        finally:
            cursor.close()
    except BaseException:
        if held is not None:
            held.drop("reader")
        raise
    finally:
        if held is None:
            conn.close()


class _Busy:
    def __repr__(self):
        return "db.BUSY"

    def __bool__(self):
        return False


#: What write_if_free returns for a write it did not make because the database was being written: false, like a write
#: that changed nothing.
BUSY = _Busy()


def write_if_free(target, operation, label="database write"):
    """Run a write now, on a connection of its own, or not at all: BUSY when this process is writing the database (its
    write lock is held) or another process is (SQLite says locked or busy), without waiting either out. For a write that
    is worth nothing late -- a run saying how far it got, which says it again a few seconds later -- and must never hold
    up the run while the library is written (a restore, a long migration, the index's batch). `operation` is called with
    the connection, converts no paths, and its result is returned; the commit, rollback and close are handled here."""
    lock = lock_for(target)
    if not lock.acquire(blocking=False):
        logger.debug("%s: skipped, the database is being written", label)
        return BUSY
    try:
        conn = connect(target, timeout=0)
        try:
            # configure() gave it the 30 s every other writer waits; this one does not wait.
            conn.execute("PRAGMA busy_timeout=0")
            result = operation(conn)
            conn.commit()
            return result
        except sqlite3.OperationalError as e:
            try:
                conn.rollback()
            except Exception:
                pass
            if not locked(e):
                raise
            logger.debug("%s: skipped, the database is being written by another process", label)
            return BUSY
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
    finally:
        lock.release()


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

def prune_backups(folder, library_name, keep=KEEP_BACKUPS):
    """Delete all but the newest `keep` backups of `library_name` in `folder`, with the -wal
    and -shm files beside them. Newest by the time in the name; a backup never expires by
    age (owner, 2026-10-15). Returns the paths deleted."""
    import re

    stamped = re.compile(r"^%s\.before-.*-(\d{8}_\d{6})\.db$" % re.escape(library_name))
    copies = sorted((match.group(1), name) for name in os.listdir(folder)
                    for match in [stamped.match(name)] if match)
    doomed = [each for index, each in enumerate(copies) if not keep or index < len(copies) - keep]
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

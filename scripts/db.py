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

Never call `sqlite3.connect` directly; `tests/test_db_access.py` fails if you do.
"""
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

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
        return os.path.normcase(os.path.abspath(text))
    except Exception:
        return text


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


def connect(target, *args, **kwargs):
    """Open a connection, configured. Same signature as `sqlite3.connect`.

    `timeout` defaults to the busy timeout above rather than sqlite3's 5 seconds.
    """
    kwargs.setdefault("timeout", BUSY_TIMEOUT_MS / 1000.0)
    conn = sqlite3.connect(target, *args, **kwargs)
    return configure(conn, readonly=_is_readonly(target, kwargs))


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


def write(target, operation, label="database write"):
    """Run a write with this process's other writes to the same database held back."""
    with lock_for(target):
        return retry_when_busy(operation, label=label)


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
    should not carry state between attempts.
    """
    def attempt():
        conn = connect(target)
        try:
            result = operation(conn)
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

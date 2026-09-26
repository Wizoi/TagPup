"""Snapshots of a library, as its backup: three dailies, one weekly and one monthly, in
data/backups/<library>/daily|weekly|monthly (docs/ARCHITECTURE.md, phase 8, "Snapshots
of each library").

A daily is taken when the newest is a day old; the weekly and the monthly are copied
from that same daily when they are 7 or 30 days old, so the library is read once, not
three times. Each is:

- copied through SQLite's backup API in one step, which reads the whole library inside
  one read transaction: in WAL any connection, of this process or another, may write
  meanwhile, and the copy is the library as it stood when the step began. Copied in
  several steps, a write by another connection between two of them would restart it.
  Nothing is held back: holding this process's writers (db.lock_for) only stalled every
  save in the web server for the whole copy;
- written under a temporary name (`.partial`), made a file of its own (no -wal beside
  it), its recurring jobs' runs still `running` marked `abandoned` -- the snapshots job's
  own is, as it copies -- checked with `PRAGMA quick_check`, and only then renamed into
  place;
- counted against its kind's number only after it is in place: an old one is removed
  once its replacement has passed, so a failed night never leaves fewer good copies.
  A `.partial` a crash left is deleted by the next snapshot.

Restoring one copies it back into the library through the same API -- into the file
every process has open, never a file swapped under them -- after a snapshot of the
library as it is (`before-restore`), so a restore can itself be undone.

The one-off copies before a bulk write stay db.backup's, in data/backups beside these.
"""
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass

from tagpup.core.library import Library
from tagpup.store import db, schema

logger = logging.getLogger(__name__)

DAY = 86400

#: What a restore needs free on the disk, in times the library: the library as it is,
#: snapshotted first; the snapshot copied back through the library's WAL; and the WAL
#: checkpointed into the file.
RESTORE_NEEDS = 3

#: What a daily, weekly or monthly snapshot is, how many of each are kept, and how old
#: the newest is before another is taken. `before-restore` is the library as a restore
#: found it, taken by the restore alone.
DAILY, WEEKLY, MONTHLY, BEFORE_RESTORE = "daily", "weekly", "monthly", "before-restore"
KINDS = (DAILY, WEEKLY, MONTHLY, BEFORE_RESTORE)
KEEP = {DAILY: 3, WEEKLY: 1, MONTHLY: 1, BEFORE_RESTORE: 2}
AGE = {DAILY: DAY, WEEKLY: 7 * DAY, MONTHLY: 30 * DAY}

#: How much younger than its age a snapshot may be and still be replaced: the recurring
#: runner looks now and then, not on the second, and a daily taken at 09:00 must be
#: replaced by the look at 08:58 the next day, not a day later.
SLACK = 3600

STAMP = "%Y%m%d_%H%M%S"
PARTIAL = ".partial"


class SnapshotFailed(RuntimeError):
    """A copy did not pass its check. Nothing it replaced was removed."""


@dataclass
class Snapshot:
    kind: str
    path: str
    #: When it was taken, seconds since the epoch, from its name.
    taken: float
    size: int

    @property
    def name(self):
        """How the tools name it: "daily/20260926_090000"."""
        return "%s/%s" % (self.kind, time.strftime(STAMP, time.localtime(self.taken)))


def folder(db_path):
    return Library(db_path).snapshots


def spelled(db_path):
    """The library's name as its file is spelled on disk: `--db harbour` names Harbour.db
    on Windows, and its snapshots are Harbour's. As given when the file is not found."""
    name = Library(db_path).name
    try:
        for file_name in os.listdir(os.path.dirname(os.path.abspath(db_path))):
            stem, extension = os.path.splitext(file_name)
            if extension.lower() == ".db" and stem.lower() == name.lower():
                return stem
    except OSError:
        pass
    return name


def _file_name(db_path, taken):
    return "%s-%s.db" % (spelled(db_path), time.strftime(STAMP, time.localtime(taken)))


def _pattern(db_path):
    """A snapshot's file name, the library's name in any case: one library by any spelling."""
    return re.compile(r"^%s-(\d{8}_\d{6})\.db$" % re.escape(Library(db_path).name), re.IGNORECASE)


def listed(db_path, kind=None):
    """The snapshots of the library at `db_path`, newest first: of `kind`, or of every
    kind in KINDS order."""
    pattern = _pattern(db_path)
    found = []
    for each in ([kind] if kind else KINDS):
        where = os.path.join(folder(db_path), each)
        if not os.path.isdir(where):
            continue
        mine = []
        for file_name in os.listdir(where):
            match = pattern.match(file_name)
            if match:
                path = os.path.join(where, file_name)
                mine.append(Snapshot(each, path, time.mktime(time.strptime(match.group(1), STAMP)),
                                     os.path.getsize(path)))
        found += sorted(mine, key=lambda s: s.taken, reverse=True)
    return found


def find(db_path, name):
    """The snapshot named `name` ("daily/20260926_090000"), or None."""
    for snapshot in listed(db_path):
        if snapshot.name == name:
            return snapshot
    return None


def disk(db_path):
    """The bytes every snapshot of the library takes, partial copies included."""
    total = 0
    top = folder(db_path)
    for each in KINDS:
        where = os.path.join(top, each)
        if os.path.isdir(where):
            total += sum(os.path.getsize(os.path.join(where, name)) for name in os.listdir(where))
    return total


def check(path):
    """`PRAGMA quick_check` of the copy at `path`; SnapshotFailed unless it says ok."""
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        found = [row[0] for row in conn.execute("PRAGMA quick_check").fetchall()]
    finally:
        conn.close()
    if found != ["ok"]:
        raise SnapshotFailed("%s failed its check: %s" % (os.path.basename(path), "; ".join(found[:5])))


def copy_library(db_path, target):
    """Copy the library at `db_path` to `target`, a new file, in one step of the backup
    API -- consistent, and no writer waits for it -- as a file of its own: rollback
    journal, nothing beside it, no run left `running` (db.copy_database)."""
    db.copy_database(db_path, target, then=_abandon_runs)


#: Why a run a snapshot or a restore found `running` is marked abandoned.
NOT_RUNNING = "running when its library was copied; not running in the copy"


def _abandon_runs(conn):
    """Mark every run `running` in the library on `conn` abandoned, and commit. A copy
    holds the runs that were under way as it was taken -- the snapshots job's own among
    them -- and a process still alive would hold the job "running" in the library the copy
    is restored into until that process ended (tagpup.store.job_runs.claim). Returns how
    many."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'job_runs'").fetchone():
        return 0
    marked = conn.execute("UPDATE job_runs SET outcome = 'abandoned', owner = NULL, note = ?,"
                          " finished = COALESCE(finished, started) WHERE outcome = 'running'",
                          (NOT_RUNNING,)).rowcount
    conn.commit()
    return marked


def _remove(path):
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def _place(partial, final):
    """Check the copy at `partial` and rename it to `final`; the partial is removed if
    it fails."""
    try:
        check(partial)
    except BaseException:
        _remove(partial)
        raise
    os.replace(partial, final)


def _clear_partials(db_path):
    """Delete what a snapshot stopped before its rename left: never a snapshot."""
    removed = 0
    for each in KINDS:
        where = os.path.join(folder(db_path), each)
        if not os.path.isdir(where):
            continue
        for name in os.listdir(where):
            if PARTIAL in name:
                os.remove(os.path.join(where, name))
                removed += 1
    return removed


def prune(db_path, kind):
    """Remove all but the newest KEEP[kind] of `kind`. Returns how many went."""
    removed = 0
    for old in listed(db_path, kind)[KEEP[kind]:]:
        _remove(old.path)
        removed += 1
    return removed


def _due(db_path, kind, now):
    newest = listed(db_path, kind)
    return not newest or now - newest[0].taken >= AGE[kind] - SLACK


def take_one(db_path, kind, now, source=None, pruned=True):
    """A snapshot of `kind` stamped `now`: copied from the library, or from `source`, a
    snapshot file already checked; checked, renamed into place, then, if `pruned`, the
    oldest of its kind beyond KEEP removed. Returns (the Snapshot, how many were removed)."""
    where = os.path.join(folder(db_path), kind)
    os.makedirs(where, exist_ok=True)
    final = os.path.join(where, _file_name(db_path, now))
    partial = final + PARTIAL
    _remove(partial)
    try:
        if source is None:
            copy_library(db_path, partial)
        else:
            shutil.copyfile(source, partial)
    except BaseException:
        _remove(partial)
        raise
    _place(partial, final)
    return Snapshot(kind, final, time.mktime(time.strptime(time.strftime(STAMP, time.localtime(now)), STAMP)),
                    os.path.getsize(final)), (prune(db_path, kind) if pruned else 0)


def take(db_path, now=None, force=False):
    """Take what is due of the library at `db_path`: a daily when the newest is a day old
    (or `force`), and from that copy a weekly and a monthly when theirs are 7 and 30 days
    old. Returns {"taken": [Snapshot], "removed": snapshots removed, "partials":
    partial copies of an earlier run deleted}."""
    now = time.time() if now is None else now
    partials = _clear_partials(db_path)
    taken, removed = [], 0
    if force or _due(db_path, DAILY, now):
        daily, gone = take_one(db_path, DAILY, now)
        taken.append(daily)
        removed += gone
        for kind in (WEEKLY, MONTHLY):
            if _due(db_path, kind, now):
                copy, gone = take_one(db_path, kind, now, source=daily.path)
                taken.append(copy)
                removed += gone
    return {"taken": taken, "removed": removed, "partials": partials}


def _changes(path):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'changes'").fetchone():
            return set()
        return set(conn.execute("SELECT id, created, operation FROM changes").fetchall())
    finally:
        conn.close()


def changes_since(db_path, snapshot_path):
    """The journal's changes the library at `db_path` holds and the snapshot does not:
    what restoring it would lose, [(id, created, operation)] oldest first. A change is
    known by its id and when it was made, so one a restore made again under an id the
    snapshot also holds is still told apart."""
    return sorted(_changes(db_path) - _changes(snapshot_path))


def schema_version(path):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return schema.version(conn)
    finally:
        conn.close()


def library_bytes(db_path):
    """The bytes the library at `db_path` takes, its -wal included."""
    return sum(os.path.getsize(db_path + suffix) for suffix in ("", "-wal") if os.path.exists(db_path + suffix))


def restore_needs(db_path):
    """(bytes a restore of the library at `db_path` needs free, bytes free on the disk
    its snapshots are kept on)."""
    where = folder(db_path) if os.path.isdir(folder(db_path)) else os.path.dirname(os.path.abspath(db_path))
    return RESTORE_NEEDS * library_bytes(db_path), shutil.disk_usage(where)[2]


def restore(db_path, snapshot_path):
    """Copy the snapshot at `snapshot_path` back into the library at `db_path`, through the
    backup API, into the file every process has open. The generations are moved past
    where they stood before, so a cache another process keyed by them is not taken for
    current (tagpup.store.generations); the WAL is checkpointed, without waiting for
    readers, so the file changes as every process sees it, and the library is brought to
    the current schema. The caller took the before-restore snapshot. Returns what the
    checkpoint did, {"busy", "log", "checkpointed"} (frames): one a reader held up is
    logged, and finished by the next checkpoint."""
    check(snapshot_path)
    with db.lock_for(db_path):
        source = db.connect(db.readonly_uri(snapshot_path), uri=True)
        destination = db.connect(db_path)
        try:
            before = dict(destination.execute("SELECT name, value FROM generations").fetchall()) \
                if _has_generations(destination) else {}
            source.backup(destination)
            # The snapshot's runs under way as it was taken are not under way now.
            _abandon_runs(destination)
            if before and _has_generations(destination):
                for name, value in before.items():
                    destination.execute("UPDATE generations SET value = ? WHERE name = ? AND value <= ?",
                                        (value + 1, name, value))
                destination.commit()
            # PASSIVE: TRUNCATE waited up to the busy timeout, 30 s, for any reader.
            busy, log, done = destination.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        finally:
            destination.close()
            source.close()
    checkpoint = {"busy": busy, "log": log, "checkpointed": done}
    if busy or log != done:
        logger.warning("%s: restored, but the checkpoint after it wrote %d of %d frame(s) into the file"
                       " (a reader held it up); the next checkpoint finishes it", db_path, done, log)
    schema.ensure(db_path)
    return checkpoint


def _has_generations(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'generations'").fetchone()

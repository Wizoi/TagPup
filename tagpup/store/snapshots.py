"""Snapshots of a library, as its backup: three dailies, one weekly and one monthly, in
data/backups/<library>/daily|weekly|monthly (docs/ARCHITECTURE.md, phase 8, "Snapshots
of each library").

A daily is taken when the newest is a day old; the weekly and the monthly are copied
from that same daily when they are 7 or 30 days old, so the library is read once, not
three times. Each is:

- copied with this process's writers held back (db.lock_for) through SQLite's backup
  API in one step, which reads the whole library inside one read transaction: in WAL
  another process may write meanwhile, and the copy is the library as it stood when the
  step began. Copied in several steps, a write by another connection between two of
  them would restart it;
- written under a temporary name (`.partial`), made a file of its own (no -wal beside
  it), checked with `PRAGMA quick_check`, and only then renamed into place;
- counted against its kind's number only after it is in place: an old one is removed
  once its replacement has passed, so a failed night never leaves fewer good copies.
  A `.partial` a crash left is deleted by the next snapshot.

Restoring one copies it back into the library through the same API -- into the file
every process has open, never a file swapped under them -- after a snapshot of the
library as it is (`before-restore`), so a restore can itself be undone.

The one-off copies before a bulk write stay db.backup's, in data/backups beside these.
"""
import os
import re
import shutil
import time
from dataclasses import dataclass

from tagpup.core.library import Library
from tagpup.store import db, schema

DAY = 86400

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


def _file_name(db_path, taken):
    return "%s-%s.db" % (Library(db_path).name, time.strftime(STAMP, time.localtime(taken)))


def _pattern(db_path):
    return re.compile(r"^%s-(\d{8}_\d{6})\.db$" % re.escape(Library(db_path).name))


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
    API with this process's writers held back, as a file of its own: rollback journal,
    nothing beside it."""
    with db.lock_for(db_path):
        source = db.connect(db.readonly_uri(db_path), uri=True)
        destination = db.connect(target)
        try:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode=DELETE")
        finally:
            destination.close()
            source.close()


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


def restore(db_path, snapshot_path):
    """Copy the snapshot at `snapshot_path` back into the library at `db_path`, through the
    backup API, into the file every process has open. The generations are moved past
    where they stood before, so a cache another process keyed by them is not taken for
    current (tagpup.store.generations); the WAL is checkpointed so every process sees the
    file has changed, and the library is brought to the current schema. The caller took
    the before-restore snapshot."""
    check(snapshot_path)
    with db.lock_for(db_path):
        source = db.connect(db.readonly_uri(snapshot_path), uri=True)
        destination = db.connect(db_path)
        try:
            before = dict(destination.execute("SELECT name, value FROM generations").fetchall()) \
                if _has_generations(destination) else {}
            source.backup(destination)
            if before and _has_generations(destination):
                for name, value in before.items():
                    destination.execute("UPDATE generations SET value = ? WHERE name = ? AND value <= ?",
                                        (value + 1, name, value))
                destination.commit()
            destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            destination.close()
            source.close()
    schema.ensure(db_path)


def _has_generations(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'generations'").fetchone()

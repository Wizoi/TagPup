"""A library's snapshots, as the recurring job, the CLI and the apps use them: taking what
is due, listing them, and restoring one (tagpup.store.snapshots; docs/ARCHITECTURE.md,
phase 8).

A restore is a dry run unless told to apply, as every bulk operation is: it says which of
the journal's changes the library holds that the snapshot does not -- what restoring it
would lose -- by id, operation and time, never a value. Applied, it first snapshots the
library as it is (`before-restore`), so the restore can itself be undone by restoring
that. Photo files written since the snapshot keep what was written: a restore puts back
the library, not the files, and sync (phase 8) brings the rows in step with them.
"""
import time

from tagpup.core.result import NotFound, Result
from tagpup.store import schema, snapshots


def take(library, now=None, force=False):
    """Take what is due of `library` (tagpup.store.snapshots.take). A Result: `changed` the
    snapshots written, details["taken"] their kinds, ["removed"] the old ones removed
    once their replacements passed, ["bytes"] what every snapshot of the library takes
    now, ["seconds"] how long it took."""
    started = time.perf_counter()
    result = Result()
    done = snapshots.take(library.path, now=now, force=force)
    result.attempted = result.changed = len(done["taken"])
    if not done["taken"]:
        result.skip("daily", "the newest daily is less than a day old")
    result.details = {"taken": [s.kind for s in done["taken"]], "removed": done["removed"],
                      "partials": done["partials"], "bytes": snapshots.disk(library.path),
                      "seconds": round(time.perf_counter() - started, 1)}
    return result


def _described(library, snapshot):
    return {"name": snapshot.name, "kind": snapshot.kind,
            "taken": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snapshot.taken)),
            "bytes": snapshot.size, "changes_since": len(snapshots.changes_since(library.path, snapshot.path))}


def listing(library):
    """`library`'s snapshots, newest first within each kind: name, kind, when taken, size,
    and how many of the journal's changes were made since (what restoring it would lose);
    and the bytes they take together."""
    found = snapshots.listed(library.path)
    return {"library": library.name, "snapshots": [_described(library, s) for s in found],
            "bytes": snapshots.disk(library.path)}


def restore(library, name, apply=False, now=None):
    """Restore `library` from its snapshot `name` ("daily/20260926_090000"): a dry run
    unless `apply`. details["lost"] lists the changes of the journal it would lose, by id,
    operation and time, and details["needs"] and ["free"] the disk it needs and has:
    refused, with nothing written, without it. Applied, the library as it stood is
    snapshotted first
    (details["before_restore"], its name, which restores it again); `changed` is 1 once
    the snapshot's copy is in the library. NotFound for a name the library has none of."""
    snapshot = snapshots.find(library.path, name)
    if snapshot is None:
        raise NotFound("The library %s has no snapshot %s." % (library.name, name))
    lost = snapshots.changes_since(library.path, snapshot.path)
    result = Result(attempted=1, details={
        "dry_run": not apply, "snapshot": snapshot.name, "bytes": snapshot.size,
        "lost": [{"id": cid, "created": created, "operation": operation} for cid, created, operation in lost]})
    if snapshots.schema_version(snapshot.path) > schema.LATEST:
        result.refuse("The snapshot %s was made by a newer version of TagPup; this one cannot open it." % name)
        return result
    needs, free = snapshots.restore_needs(library.path)
    result.details.update({"needs": needs, "free": free})
    if not apply:
        return result
    if free < needs:
        result.refuse("Nothing was restored: restoring needs about %d MB free on the disk -- the library as it"
                      " is, snapshotted first, and the copy back through its WAL -- and it has %d MB free."
                      % (needs // 1_000_000, free // 1_000_000))
        return result
    try:
        # Not pruned until the restore is done: the snapshot restored may be the
        # oldest before-restore, which the new one would push out.
        snapshots.clear_partials(library.path, snapshots.BEFORE_RESTORE)
        before, _removed = snapshots.take_one(library.path, snapshots.BEFORE_RESTORE,
                                              time.time() if now is None else now, pruned=False)
    except Exception as e:
        result.refuse("Nothing was restored: the library as it is could not be snapshotted first: %s" % e)
        return result
    result.details["before_restore"] = before.name
    result.details["checkpoint"] = snapshots.restore(library.path, snapshot.path)
    result.changed = 1
    snapshots.prune(library.path, snapshots.BEFORE_RESTORE)
    return result


def disk(library):
    """The bytes `library`'s snapshots take."""
    return snapshots.disk(library.path)

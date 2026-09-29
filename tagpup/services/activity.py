"""What the Activity page shows of each library, read and never written (docs/ARCHITECTURE.md,
phase 8.5): its syncs, its snapshots, and one timeline of what was done in it -- the
journal's changes, the recurring jobs' runs and the syncs -- newest first.

Counts, times and run tags (tagpup.core.runs), from the library's own records: `changes`
(tagpup.store.journal), `job_runs`, `sync_runs` and `damaged_files`. A job run's note -- why it failed --
is given as its `error`: the page that asks answers this PC alone (tagpup.web.
activity_routes), and says what went wrong, which a count cannot. A library behind this
version's schema reads as holding none of what it lacks; nothing is migrated by a look.
"""
import numbers
import time

from tagpup.core import runs
from tagpup.services import damaged_photos
from tagpup.store import damaged_files, db, job_runs, journal, snapshots, sync_runs

#: The most entries a timeline gathers from each record.
MOST = 500


def _look(library):
    return db.connect(db.readonly_uri(library.path), uri=True)


def _counts(found):
    """The numbers among `found`, a dict: what a record says it found or changed."""
    return {name: value for name, value in (found or {}).items()
            if isinstance(value, numbers.Number) and not isinstance(value, bool)}


def seconds_between(started, finished):
    """How long from `started` to `finished` (as the records spell a time), or None."""
    if not started or not finished:
        return None
    try:
        return max(0, int(time.mktime(time.strptime(finished, sync_runs.TIME))
                          - time.mktime(time.strptime(started, sync_runs.TIME))))
    except (TypeError, ValueError):
        return None


def _sync_entry(library, record):
    if record is None:
        return None
    return {"id": record["id"], "started": record["started"], "finished": record["finished"],
            "seconds": seconds_between(record["started"], record["finished"]), "whole": record["whole"],
            "in_step": record["in_step"], "found": _counts(record["found"]), "changed": _counts(record["changed"]),
            "change": record["change_id"], "run": runs.sync_tag(library.name, record["started"])}


def sync_state(library):
    """{"last_in_step", "last_whole", "last_folder", "review_folders"}: when the library was
    last in step with its folders, its newest sync of the whole library and of one folder,
    and how many folders under its roots wait to be reviewed, as its newest sync of the
    whole library counted them (None before any)."""
    conn = _look(library)
    try:
        last = sync_runs.last(conn)
        whole = sync_runs.newest(conn, whole=True)
        folder = sync_runs.newest(conn, whole=False)
    finally:
        conn.close()
    counted = whole or last["last_run"]
    review = counted["found"].get("review_folders", 0) if counted else None
    return {"last_in_step": last["last_in_step"], "last_whole": _sync_entry(library, whole),
            "last_folder": _sync_entry(library, folder), "review_folders": review}


def snapshot_list(library, now=None):
    """{"snapshots": [{"name", "kind", "taken", "age_seconds", "bytes"}], "bytes"}: the
    library's snapshots, newest first within each kind, and the disk they take together.
    Nothing is opened: the journal's changes since each (tagpup.services.snapshots.listing)
    are the restore's to count."""
    now = time.time() if now is None else now
    found = snapshots.listed(library.path)
    return {"snapshots": [{"name": each.name, "kind": each.kind,
                           "taken": time.strftime(sync_runs.TIME, time.localtime(each.taken)),
                           "age_seconds": max(0, int(now - each.taken)), "bytes": each.size} for each in found],
            "bytes": sum(each.size for each in found)}


def _plural(count, one, many):
    return "%d %s" % (count, one if count == 1 else many)


def found_damaged(records):
    """The timeline's entries for damaged photos found (store.damaged_files): one for each
    run of the indexer that found some -- or, found outside a run, each moment -- saying
    how many, as {"time", "what", "counts", "run"}. What the lists count: a photo replaced
    or changed since is no longer counted (damaged_photos.current)."""
    groups = {}
    for each in records:
        key = each.run or each.found
        group = groups.setdefault(key, {"time": each.found, "unreadable": 0, "incomplete": 0, "run": each.run})
        group["time"] = min(group["time"], each.found)
        group["incomplete" if each.kind == damaged_files.INCOMPLETE else "unreadable"] += 1
    found = []
    for group in groups.values():
        said = []
        if group["unreadable"]:
            said.append(_plural(group["unreadable"], "unreadable photo", "unreadable photos"))
        if group["incomplete"]:
            said.append(_plural(group["incomplete"], "possibly incomplete copy", "possibly incomplete copies"))
        found.append({"time": group["time"], "what": "found " + " and ".join(said), "run": group["run"],
                      "counts": {"unreadable": group["unreadable"], "incomplete": group["incomplete"]}})
    return found


def timeline(library, limit=50):
    """What was done in `library`, newest first, at most `limit` entries: each
    {"kind": "change" | "job" | "sync" | "found", "library", "time", "finished", "seconds",
    "what", "outcome", "counts", "id", "run", "error"}. A sync's change of the journal is its
    sync's entry, not one of its own; "found" is damaged photos found (found_damaged)."""
    limit = max(1, min(int(limit), MOST))
    entries = []
    conn = _look(library)
    try:
        syncs = sync_runs.recent(conn, limit)
    finally:
        conn.close()
    damaged = damaged_photos.current(library)
    for found in found_damaged(damaged):
        entries.append({"kind": "found", "library": library.name, "time": found["time"], "finished": None,
                        "seconds": None, "what": found["what"], "outcome": "needs attention",
                        "counts": found["counts"], "id": None, "run": found["run"], "error": None})
    sync_changes = set()
    for record in syncs:
        entry = _sync_entry(library, record)
        if record["change_id"]:
            sync_changes.add(record["change_id"])
        entries.append({"kind": "sync", "library": library.name, "time": record["started"],
                        "finished": record["finished"], "seconds": entry["seconds"],
                        "what": "sync of the whole library" if record["whole"] else "sync of one folder",
                        "outcome": "in step" if record["in_step"] else "not in step",
                        "counts": dict(entry["changed"], **{"found_" + k: v for k, v in entry["found"].items()
                                                            if k in ("new", "missing", "moved", "changed")}),
                        "id": record["id"], "run": entry["run"], "error": None})
    for run in job_runs.runs(library.path, None, limit):
        entries.append({"kind": "job", "library": library.name, "time": run.started, "finished": run.finished,
                        "seconds": seconds_between(run.started, run.finished), "what": run.job,
                        "outcome": run.outcome, "counts": _counts(run.changed), "id": run.id,
                        "run": runs.job_tag(run.library, run.id), "error": run.note})
    for change in journal.history(library.path, limit=limit):
        if change["id"] in sync_changes:
            continue
        rows = sum(count for actions in (change.get("rows") or {}).values() for count in actions.values())
        files = sum((change.get("files") or {}).values())
        counts = _counts(change.get("summary"))
        counts.update(rows=rows, files=files)
        entries.append({"kind": "change", "library": library.name, "time": change["created"],
                        "finished": change["applied"] or change["undone"], "seconds": None,
                        "what": change["operation"], "outcome": change["status"], "counts": counts,
                        "id": change["id"], "run": None, "error": None})
    entries.sort(key=lambda entry: entry["time"] or "", reverse=True)
    return entries[:limit]

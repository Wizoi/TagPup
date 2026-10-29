"""What the Activity page shows of each library, read and never written (docs/ARCHITECTURE.md,
phase 8.5): its syncs, its snapshots, the runs under way, and one timeline of what was done in it -- the
journal's changes, the recurring jobs' runs, the index's runs and the syncs -- newest first.

An index's run is its row of `job_runs` (job_runs.INDEX, written by the server's index queue, tagpup.jobs.indexing):
its tag, which its indexer's own log is named by, is `changed["run"]`; it is part of the run of its row's `parent` and
of each sync its `changed["syncs"]` names (a sync's own row is written only as it ends). Nothing here reads the queue.

Counts, times and run tags (tagpup.core.runs), from the library's own records: `changes`
(tagpup.store.journal) and `job_runs`, the syncs among them (tagpup.store.job_runs.record_sync). A job run's note -- why it failed --
is given as its `error`: the page that asks answers this PC alone (tagpup.web.
activity_routes), and says what went wrong, which a count cannot. A library behind this
version's schema reads as holding none of what it lacks; nothing is migrated by a look.

Needs you is each job whose last run that ended failed asking the owner to act (tagpup.services.job_runs.needs_you),
said with what its kind means (job_runs.FAILURES) and the next step for its job (job_runs.next_step), grouped by kind
across the libraries.
"""
import logging
import numbers
import time

from tagpup.core import runs
from tagpup.files import shares
from tagpup.services import indexing
from tagpup.services import job_runs as runs_service
from tagpup.services import sync
from tagpup.store import db, job_runs, journal, snapshots

logger = logging.getLogger(__name__)

#: The most entries a timeline gathers from each record.
MOST = 500

#: Each phase a run says it is in (job_runs.PHASES) in the words the page shows: a sync's (sync.PHASE_SAID, the words
#: its walk logs in) and an index's (indexing.PHASE_SAID). The page holds no copy.
PHASE_SAID = dict(sync.PHASE_SAID, **indexing.PHASE_SAID)


def _look(library):
    return db.connect(db.readonly_uri(library.path), uri=True)


def roots_there(roots):
    """[{"path", "there", "answering"}] of each root folder, asked as a sync asks it (shares.folder_state, bounded on
    a share): a root on a share that does not answer is not answering, never "not there" (#1356), and says since when
    its share has not ("away_since", shares.away_since); one on a local drive that is not connected is not there (#1387)."""
    listed = []
    for root in roots:
        state = shares.folder_state(root)
        listed.append({"path": root, "there": state == "there", "answering": state != "unanswered"})
        if state == "unanswered":
            listed[-1]["away_since"] = shares.away_since(shares.share_of(root))
    return listed


def _counts(found):
    """The numbers among `found`, a dict: what a record says it found or changed."""
    return {name: value for name, value in (found or {}).items()
            if isinstance(value, numbers.Number) and not isinstance(value, bool)}


def _what(run):
    """What a run of a job is called in the timeline: what it says it did (a text in its counts,
    "what"), else the job's name."""
    said = run.changed.get("what")
    return said if isinstance(said, str) and said else run.job


def _tag(run):
    """The tag the lines of `run` carry (tagpup.core.runs): an index's its own, held in its row; any other job's its row's."""
    if run.job == job_runs.INDEX:
        return run.changed.get("run")
    return runs.job_tag(run.library, run.id)


def _progress(run):
    """How far `run` said it got -- {"phase", "said": the phase in words (PHASE_SAID; None for one a newer version
    wrote), "counts", "progressed": when it said so} -- or None before it said."""
    if not run.progress:
        return None
    return dict(run.progress, said=PHASE_SAID.get(run.progress.get("phase")), progressed=run.heartbeat)


def index_runs_under(library, indexes=None):
    """{a run's tag: [the tags of the index runs part of it]}, newest first, from the library's index runs (job_runs.INDEX,
    the newest KEEP_RUNS; `indexes`, when the caller has read them): each under the job run its row names as `parent`
    and under each sync it names (`syncs`). What finds a run's lines in its indexes' own logs
    (tagpup.web.activity_routes)."""
    found = {}
    if indexes is None:
        indexes = job_runs.runs(library.path, job_runs.INDEX, job_runs.KEEP_RUNS)
    for run in indexes:
        under = [runs.job_tag(run.library, run.parent)] if run.parent is not None else []
        for tag in under + list(run.changed.get("syncs") or ()):
            found.setdefault(tag, []).append(_tag(run))
    return found


def under_way(library):
    """The runs of the library still `running`, oldest first, whichever process owns them -- an index the CLI runs among
    them: each {"job", "run_id", "started", "run", "parent", "progress"} (`run` its tag, `parent` the id of the run it is
    part of, `progress` _progress)."""
    return [{"job": run.job, "run_id": run.id, "started": run.started, "run": _tag(run), "parent": run.parent,
             "progress": _progress(run)} for run in job_runs.under_way(library.path)]


def _index_outcome(run):
    """What an index's run says it came to: one that ended `done` completed, or was cancelled (counted so); any other
    outcome (failed, abandoned, running) is its own."""
    if run.outcome == job_runs.DONE:
        return "cancelled" if run.changed.get("cancelled") else "completed"
    return run.outcome


def seconds_between(started, finished):
    """How long from `started` to `finished` (as the records spell a time), or None."""
    if not started or not finished:
        return None
    try:
        return max(0, int(job_runs.seconds(finished) - job_runs.seconds(started)))
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
        last = job_runs.last_sync(conn)
        whole = job_runs.newest_sync(conn, whole=True)
        folder = job_runs.newest_sync(conn, whole=False)
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
                           "taken": job_runs.stamp(each.taken),
                           "age_seconds": max(0, int(now - each.taken)), "bytes": each.size} for each in found],
            "bytes": sum(each.size for each in found)}


def timeline(library, limit=50):
    """What was done in `library`: timeline_and_children's entries."""
    return timeline_and_children(library, limit)[0]


def timeline_and_children(library, limit=50):
    """(the entries, index_runs_under) of `library`, from one read of its index runs. The entries: what was done in
    `library`, newest first, at most `limit` entries: each
    {"kind": "change" | "job" | "index" | "sync", "library", "time", "finished", "seconds",
    "what", "outcome", "counts", "id", "run", "error"}, a job's and an index's also its "parent" and "progress"
    (under_way), an index's also its "syncs" (the tags of the syncs it is part of: a folder sync's index, whose own
    row has no parent while the sync runs). A sync's change of the journal is its sync's entry, not one of its own. An index's run says how many
    folders it indexed, never which (its row holds no path); its error is its note only when it failed or was abandoned."""
    limit = max(1, min(int(limit), MOST))
    entries = []
    conn = _look(library)
    try:
        syncs = job_runs.recent_syncs(conn, limit)
    finally:
        conn.close()
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
    indexes = job_runs.runs(library.path, job_runs.INDEX, job_runs.KEEP_RUNS)
    for run in job_runs.runs(library.path, None, limit, exclude=job_runs.SYNC_JOBS + (job_runs.INDEX,)) + indexes:
        entries.append({"kind": "job", "library": library.name, "time": run.started, "finished": run.finished,
                        "seconds": seconds_between(run.started, run.finished), "what": _what(run),
                        "outcome": run.outcome, "counts": _counts(run.changed), "id": run.id,
                        "run": _tag(run), "error": run.note, "parent": run.parent, "progress": _progress(run)})
        if run.job == job_runs.INDEX:
            entries[-1].update(kind="index", what="index of %d folder(s)" % run.changed.get("folders", 0),
                               outcome=_index_outcome(run), syncs=list(run.changed.get("syncs") or ()),
                               error=run.note if run.outcome in (job_runs.FAILED, job_runs.ABANDONED) else None)
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
    return entries[:limit], index_runs_under(library, indexes)


def needs_you(libraries, run_now=()):
    """What the owner must act on in `libraries`, grouped by kind in the order of job_runs.FAILURES: [{"kind", "means",
    "items": [{"library", "job", "sentence", "count", "since", "last", "run", "run_now", "next_step"}]}], each item a job
    of a library whose failed runs since its last done one ask him to act (runs_service.needs_you, of the rows
    runs_service.runs_of reads): the newest's sentence (its note, which can name a folder: for the page that answers this
    PC alone) and tag, how many failed, when the first started and the newest finished, whether the page's Run now
    starts it (`run_now`, the jobs this process's recurring runner has) and the next step, naming how the job is run
    again (runs_service.next_step). A library that cannot be read is logged and left out; the others are said. A kind
    this version does not know reads as an error (runs_service.kind_of), so no row of a library leaves out the rest."""
    found = {}
    for library in libraries:
        try:
            for (job, name), (_last, ended) in sorted(job_runs.latest(library.path).items(),
                                                      key=lambda each: (each[0][0], each[0][1] or "")):
                if ended is None or ended.outcome != job_runs.FAILED:
                    continue
                failed = runs_service.needs_you(runs_service.runs_of(library, job, name))
                if failed:
                    kind, here = runs_service.kind_of(failed[0]), job in run_now
                    found.setdefault(kind, []).append({
                        "library": library.name, "job": job, "sentence": failed[0].note, "count": len(failed),
                        "since": failed[-1].started, "last": failed[0].finished, "run": _tag(failed[0]),
                        "run_now": here, "next_step": runs_service.next_step(kind, job, here)})
        except Exception as e:
            logger.warning("Could not read what needs you in %s: %s", library.name, e)
    return [{"kind": kind, "means": means, "items": found[kind]}
            for kind, (means, _next_step, _asks) in runs_service.FAILURES.items() if kind in found]

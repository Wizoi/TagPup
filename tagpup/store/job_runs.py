"""The runs of each recurring job, kept in the library they ran for: `job_runs`
(migration 13; docs/ARCHITECTURE.md, phase 8, "Recurring jobs").

Any TagPup process may run a recurring job -- the web server, the CLI, the MCP server --
so what is due, and who is running it now, are read from the library, not from memory.
A run is claimed by a row `running`, owned by the process as the file journal names one
("host:pid:start", tagpup.store.file_journal.owner), inside one IMMEDIATE transaction:
two processes claiming the same job for the same library at once are serialized by
SQLite's write lock, and the second finds the first's row. A row whose owner has ended
-- a crash, a process killed mid-run -- is marked `abandoned` (`abandon`, the one place) and the job claimed over
it, by the same test that settles a change of photo files a crash left
(file_journal.owner_alive); the server's start marks every such run of the library at once
(`sweep_abandoned`), so the Activity page does not show a run under way that is not.

What is due is the runner's to decide (tagpup.jobs.recurring); it is handed the last
run that ended and answers, inside the claim, so a process that lost the race to a run
that has just finished does not run the job again.

Times are the journal's: local time, "YYYY-MM-DD HH:MM:SS".
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from tagpup.core import dates
from tagpup.store import db, file_journal, schema

logger = logging.getLogger(__name__)

RUNNING, DONE, FAILED, ABANDONED = "running", "done", "failed", "abandoned"
OUTCOMES = (RUNNING, DONE, FAILED, ABANDONED)

#: The job a run of the indexer is recorded as (tagpup.jobs.indexing). Its row holds from the claim on, in `changed`,
#: the run's tag (`run`, tagpup.core.runs.index_tag), which the indexer's own log file is named by (tagpup.logs.run_log),
#: how many folders it indexes (`folders`) and the tags of the syncs it is part of (`syncs`, when any): what the Activity
#: page reads an index by (tagpup.services.activity), never a path.
INDEX = "index"

#: The jobs whose claim records a run and gates none: an index, which the graphics card's turns already take one at a time
#: (tagpup.core.gpu_turns) and the index queue one at a time in a process. Its claim excludes no other process's running
#: run of it -- the server's and a CLI's `sync --apply` index of one library are two rows, each its owner's -- and is made
#: now or within RECORD_WITHIN seconds, never waited out: a library written by another for longer leaves the run
#: unrecorded ("busy"), and it runs all the same. Every other job's claim is refused while a live process holds a run of it.
RECORDS_ONLY = (INDEX,)

#: The seconds a claim of a job of RECORDS_ONLY tries for while the library is being written.
RECORD_WITHIN = 2.0

#: The runs kept of each job for each library; older ones are deleted as a run finishes.
KEEP_RUNS = 50

_COLUMNS = "id, job, library, started, finished, outcome, changed, owner, note"


@dataclass
class Run:
    id: int
    job: str
    #: The library's name the run was for; None for a job not run per library.
    library: Optional[str]
    started: str
    finished: Optional[str]
    outcome: str
    #: What it changed, as counts: {"changed": 1, "attempted": 1, ...}.
    changed: Dict[str, Any] = field(default_factory=dict)
    owner: Optional[str] = None
    #: Why it failed or was abandoned; can name a path, so sent only to the Activity page, which answers this PC
    #: alone.
    note: Optional[str] = None
    #: The run that queued this one (migration 36), its id; None for one nothing queued. `tree`, `runs` and `under_way`
    #: read it and the two below from a library at 36 (_rows); the other readers, and a library at 35 -- which the MCP
    #: server and the Activity page's look read without migrating it -- leave them None.
    parent: Optional[int] = None
    #: When it last said how far it got, a run's time (`stamp`), and what it said: {"phase": .., "counts": {..}} (progress).
    heartbeat: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None

    @property
    def started_at(self):
        return seconds(self.started)


def stamp(at):
    """`at`, seconds since the epoch, as a run records it (dates.recorded)."""
    return dates.recorded(at)


def seconds(text):
    """A time a run recorded, as seconds since the epoch (dates.recorded_seconds)."""
    return dates.recorded_seconds(text)


def _run(found):
    run_id, job, library, started, finished, outcome, changed, owner, note = found
    return Run(run_id, job, library, started, finished, outcome, json.loads(changed or "{}"), owner, note)


def _prune(conn, job, library, keep):
    """Delete the runs of `job` for `library` but the newest `keep`."""
    conn.execute("DELETE FROM job_runs WHERE job = ? AND library IS ? AND id NOT IN"
                 " (SELECT id FROM job_runs WHERE job = ? AND library IS ? ORDER BY id DESC LIMIT ?)",
                 (job, library, job, library, keep))


def _running(conn, job, library):
    return conn.execute("SELECT id, owner FROM job_runs WHERE job = ? AND library IS ?"
                        " AND outcome = 'running' ORDER BY id", (job, library)).fetchall()


#: Why a run a claim found `running`, its process ended, is marked abandoned.
ENDED = "its process ended before it finished"

#: Why a run the server's start found `running`, its process ended, is marked abandoned: only what is known of every such
#: run -- an index's indexer, a process of its own, may have finished its work, or be finishing it, after the server that
#: started it stopped (#2195).
STOPPED = ("the process that started it stopped before it could record its end; its work may have finished or may"
           " still be finishing; what was written is kept")


def abandon(conn, note, at=None, run_ids=None):
    """Mark the runs `run_ids` of the library open on `conn` that are still `running` -- every one, when None --
    abandoned with `note`, finished at `at` (seconds since the epoch) or, when None, as they finished or started (a copy
    cannot tell when they ended), their owner forgotten. Only the columns a library at 35 has: a snapshot a restore puts
    back can be at 35 until it is brought up to date. The caller commits. Returns how many it marked."""
    if run_ids is not None:
        run_ids = list(run_ids)
        if not run_ids:
            return 0
    sql = ("UPDATE job_runs SET outcome = 'abandoned', owner = NULL, note = ?, finished = COALESCE(?, finished, started)"
           " WHERE outcome = 'running'")
    params = [note, stamp(at) if at is not None else None]
    if run_ids is not None:
        sql += " AND id IN (" + ",".join("?" * len(run_ids)) + ")"
        params += run_ids
    return conn.execute(sql, params).rowcount


def _owners_alive(rows):
    """{owner: whether its process lives} of the (id, owner) `rows`: asked before the write lock is taken -- it may read
    the process list, seconds on a busy machine, which every writer of the library waited for."""
    return {owner: file_journal.owner_alive(owner) for owner in {owner for _, owner in rows if owner}}


def _lives(owner, alive):
    """Does the run of `owner` belong to a live process, as `alive` (_owners_alive) found? A run with no owner does not;
    one whose owner was not asked -- claimed after the look -- does."""
    return bool(owner) and alive.get(owner, True)


def _last_ended(conn, job, library):
    found = conn.execute("SELECT " + _COLUMNS + " FROM job_runs WHERE job = ? AND library IS ?"
                         " AND outcome IN ('done', 'failed') ORDER BY id DESC LIMIT 1", (job, library)).fetchone()
    return _run(found) if found else None


class Claim:
    """What claim() found: `run_id` of the run begun, or `why` not -- "running" (a live
    process has it), "not due", "behind" (the library has not had this version's
    migrations), or "busy" (a job of RECORDS_ONLY, the library being written) -- and `abandoned`, the runs of ended
    processes it marked so."""

    def __init__(self, run_id=None, why=None, abandoned=0, holder=None):
        self.run_id, self.why, self.abandoned, self.holder = run_id, why, abandoned, holder

    def __bool__(self):
        return self.run_id is not None


def claim(db_path, job, library, now, due=None, parent=None, changed=None):
    """Begin a run of `job` for `library` (a library's name, or None for a job not run per
    library) at `now` (seconds since the epoch), owned by this process, under the run `parent`
    of the same library (its id) if it was queued by one, `changed` what it holds from the start
    (an index's tag, INDEX): unless a process
    still running holds one, or `due`, called with the last run that ended (Run or None),
    answers False -- a job of RECORDS_ONLY is begun beside a live process's, and not waited for. A run whose process has
    ended is marked `abandoned` first. A Claim.

    A library behind this version's schema is left alone ("behind"), not migrated: a job
    run by hand from the CLI is no reason to migrate one (docs/findings.md, #243). Its
    jobs run once an app has opened it. That is schema's question (every migration this version knows), not whether the
    library has the runs' columns of migration 36 (`_runs_nest`), which a library it lets by always has."""
    if schema.pending(db_path):
        return Claim(why="behind")
    # Whether each owner lives is asked before the write lock is taken -- it may start
    # tasklist, seconds on a busy machine, which every writer of the library waited for --
    # and the rows read again under it: a run that appeared meanwhile is its owner's.
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        alive = _owners_alive(_running(conn, job, library))
    finally:
        conn.close()

    def work(conn):
        db.begin(conn, immediate=True)
        running = _running(conn, job, library)
        abandoned = abandon(conn, ENDED, now, [run_id for run_id, owner in running if not _lives(owner, alive)])
        holder = next((owner for _, owner in running if _lives(owner, alive)), None)
        if holder and not records_only:
            return Claim(why="running", abandoned=abandoned, holder=holder)
        if due is not None and not due(_last_ended(conn, job, library)):
            return Claim(why="not due", abandoned=abandoned)
        run_id = conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner, parent, changed)"
                              " VALUES (?, ?, ?, 'running', ?, ?, ?)",
                              (job, library, stamp(now), file_journal.owner(), parent,
                               json.dumps(changed, sort_keys=True) if changed else None)).lastrowid
        return Claim(run_id=run_id, abandoned=abandoned)

    records_only = job in RECORDS_ONLY
    if not records_only:
        return db.write_with_connection(db_path, work, label="claim the job %s" % job)
    deadline = time.monotonic() + RECORD_WITHIN
    while True:
        made = db.write_if_free(db_path, work, label="claim the job %s" % job)
        if made is not db.BUSY:
            return made
        if time.monotonic() >= deadline:
            return Claim(why="busy")
        time.sleep(0.05)


def sweep_abandoned(db_path, now):
    """Mark abandoned, with STOPPED, every run of the library at `db_path` still `running` whose process has ended -- a
    server killed mid-run, a CLI run that died -- as the server starts. A run of a live process is kept, and one of another
    machine, which file_journal.owner_alive cannot tell. Whether each owner lives is asked outside the write lock, as
    claim asks it; a run claimed meanwhile is its owner's. Returns how many it marked; writes nothing when none runs."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        running = conn.execute("SELECT id, owner FROM job_runs WHERE outcome = 'running'").fetchall()
    finally:
        conn.close()
    if not running:
        return 0
    alive = _owners_alive(running)

    def work(conn):
        db.begin(conn, immediate=True)
        found = conn.execute("SELECT id, owner FROM job_runs WHERE outcome = 'running'").fetchall()
        return abandon(conn, STOPPED, now, [run_id for run_id, owner in found if not _lives(owner, alive)])

    return db.write_with_connection(db_path, work, label="sweep the abandoned runs")


def finish(db_path, run_id, outcome, now, changed=None, note=None):
    """End run `run_id`, this process's: `outcome` done or failed, `changed` its counts.
    False when it is no longer this process's to end -- taken over as abandoned, or the
    library restored from a snapshot without it. Older runs of the job beyond KEEP_RUNS
    are deleted."""
    if outcome not in (DONE, FAILED):
        raise ValueError("a run ends done or failed, not %r" % (outcome,))

    def work(conn):
        found = conn.execute("SELECT job, library FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        ended = conn.execute(
            "UPDATE job_runs SET outcome = ?, finished = ?, changed = ?, note = ?, owner = NULL"
            " WHERE id = ? AND outcome = 'running' AND owner = ?",
            (outcome, stamp(now), json.dumps(changed or {}, sort_keys=True), note, run_id,
             file_journal.owner())).rowcount == 1
        if found is not None:
            _prune(conn, found[0], found[1], KEEP_RUNS)
        return ended

    return db.write_with_connection(db_path, work, label="finish run %d" % run_id)


def discard(db_path, run_id):
    """Delete run `run_id`, this process's and still running, as if it had not been claimed: a claim that was made for work that
    was then refused (a resume that could not go on) must not use up one of the KEEP_RUNS rows a job's history is kept in. False
    when it was not this process's running run."""
    def work(conn):
        return conn.execute("DELETE FROM job_runs WHERE id = ? AND outcome = 'running' AND owner = ?",
                            (run_id, file_journal.owner())).rowcount == 1

    return db.write_with_connection(db_path, work, label="discard run %d" % run_id)


def counts_so_far(db_path, run_id, changed):
    """Record the counts of run `run_id` so far, while it runs: `changed` as the run's counts (what the Activity page lists
    of a run in progress). False when the run is no longer this process's and running -- taken over as abandoned, ended, or
    the library restored from a snapshot without it. One small write; a job calls it every few seconds, never per photo."""
    def work(conn):
        return conn.execute("UPDATE job_runs SET changed = ? WHERE id = ? AND outcome = 'running' AND owner = ?",
                            (json.dumps(changed or {}, sort_keys=True), run_id, file_journal.owner())).rowcount == 1

    return db.write_with_connection(db_path, work, label="counts so far of run %d" % run_id)


def progress(db_path, run_id, phase, counts, now, within=0.0):
    """Record how far run `run_id`, this process's and running, has got: `heartbeat` the time `now` (seconds since the
    epoch), `progress` {"phase": phase, "counts": counts}, as the caller checked them (tagpup.services.job_runs.progress).
    Made now or not at all (db.write_if_free): db.BUSY while the library is being written; `within` seconds, tried again
    for that long (as the claim of a job of RECORDS_ONLY is), for the write that must be made once. False when the run is no longer
    this process's and running -- abandoned, ended, gone with a restore -- or the library has not the columns of
    migration 36 (`_runs_nest`: a snapshot at 35 a restore has put back and not yet migrated). Never raises for either."""
    text = json.dumps({"phase": phase, "counts": counts}, sort_keys=True)

    def work(conn):
        if not _runs_nest(conn):
            return None
        return conn.execute("UPDATE job_runs SET heartbeat = ?, progress = ? WHERE id = ? AND outcome = 'running'"
                            " AND owner = ?", (stamp(now), text, run_id, file_journal.owner())).rowcount == 1

    deadline = time.monotonic() + within
    while True:
        written = db.write_if_free(db_path, work, label="progress of run %d" % run_id)
        if written is not db.BUSY or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    if written is None:
        logger.debug("progress of run %d: not written, the library is not yet at schema 36 (a restore under way)", run_id)
        return False
    if written is False:
        logger.debug("progress of run %d: not written, it is no longer this process's running run", run_id)
    return written


def get(db_path, run_id):
    """The run `run_id` of the library, or None. Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        found = conn.execute("SELECT " + _COLUMNS + " FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        return _run(found) if found else None
    finally:
        conn.close()


def latest(db_path):
    """{(job, library): (the latest run, the latest that ended)} of every job the library
    at `db_path` has run, each a Run or None. Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        found = {}
        for row in conn.execute("SELECT " + _COLUMNS + " FROM job_runs WHERE id IN"
                                " (SELECT MAX(id) FROM job_runs GROUP BY job, library)"):
            run = _run(row)
            found[(run.job, run.library)] = (run, None)
        for row in conn.execute("SELECT " + _COLUMNS + " FROM job_runs WHERE id IN"
                                " (SELECT MAX(id) FROM job_runs WHERE outcome IN ('done', 'failed')"
                                " GROUP BY job, library)"):
            run = _run(row)
            last = found.get((run.job, run.library), (run, None))[0]
            found[(run.job, run.library)] = (last, run)
        return found
    finally:
        conn.close()


# ---- The record of the syncs ---------------------------------------------------------------
#
# A sync of the library (tagpup.services.sync) is a finished run of the job "sync-whole" or, of one folder, "sync-folder": the
# record the pages' "last in step" and the Activity page read. Not the recurring job "sync", which is the daily catch-up claimed
# and ended like any job: a manual sync never makes the catch-up not due, but the watcher's sync of a whole library is a run
# of that job (recurring.sync_whole: one walk of a library at a time, #2102), and one that ends done does. A run's `changed` holds
# {"in_step": 0 or 1, "change": the journal's change, if it wrote one, "found_<what>": counts of what it found, <what>: counts of
# what it changed}; counts and ids, never a path or a name. Whether it was of the whole library is the job's name.
# Migration 32 folded the table `sync_runs` into these.

SYNC_WHOLE, SYNC_FOLDER = "sync-whole", "sync-folder"
SYNC_JOBS = (SYNC_WHOLE, SYNC_FOLDER)

#: How many runs of each are kept (a job's others keep KEEP_RUNS): the two are kept apart so that a folder's syncs, which the
#: watcher makes as folders settle, never push out the whole library's, and a sync's runs never push out another job's.
SYNC_KEEP = 200

_FOUND = "found_"


def record_sync(db_path, started, whole, in_step, found, changed, change_id=None):
    """Record a sync of the library at `db_path` that started at `started` (a run's time, `stamp`) and has just finished.
    `found` and `changed` are {what: count}. Returns the run's id. Older runs beyond SYNC_KEEP of the same job go."""
    schema.ensure(db_path)
    job = SYNC_WHOLE if whole else SYNC_FOLDER
    # The library's name, as tagpup.core.library.Library.name spells it.
    library = os.path.splitext(os.path.basename(db_path))[0]
    counts = {"in_step": 1 if in_step else 0}
    counts.update({_FOUND + what: n for what, n in found.items()})
    counts.update(changed)
    if change_id is not None:
        counts["change"] = change_id

    def store(conn):
        run_id = conn.execute(
            "INSERT INTO job_runs (job, library, started, finished, outcome, changed) VALUES (?, ?, ?, ?, 'done', ?)",
            (job, library, started, stamp(time.time()), json.dumps(counts, sort_keys=True))).lastrowid
        _prune(conn, job, library, SYNC_KEEP)
        return run_id

    return db.write_with_connection(db_path, store, label="record a sync")


def _sync(run):
    """A sync's run as the pages read it: {"id", "started", "finished", "whole", "in_step", "found", "changed", "change_id"}."""
    counts = dict(run.changed)
    in_step, change_id = counts.pop("in_step", 0), counts.pop("change", None)
    found = {what[len(_FOUND):]: n for what, n in counts.items() if what.startswith(_FOUND)}
    return {"id": run.id, "started": run.started, "finished": run.finished, "whole": run.job == SYNC_WHOLE,
            "in_step": bool(in_step), "found": found,
            "changed": {what: n for what, n in counts.items() if not what.startswith(_FOUND)}, "change_id": change_id}


def _sync_rows(conn, jobs, limit=1):
    marks = ",".join("?" * len(jobs))
    return conn.execute("SELECT " + _COLUMNS + " FROM job_runs WHERE job IN (" + marks + ") ORDER BY id DESC LIMIT ?",
                        tuple(jobs) + (max(0, int(limit)),))


def recent_syncs(conn, limit=50):
    """The newest `limit` syncs, newest first."""
    return [_sync(_run(row)) for row in _sync_rows(conn, SYNC_JOBS, limit)]


def newest_sync(conn, whole=None):
    """The newest sync of the whole library (`whole`), of one folder (not), or of either (None), or None."""
    jobs = SYNC_JOBS if whole is None else (SYNC_WHOLE,) if whole else (SYNC_FOLDER,)
    row = _sync_rows(conn, jobs).fetchone()
    return _sync(_run(row)) if row else None


def last_in_step(conn):
    """When the newest sync of the whole library that left it in step finished, or None."""
    for row in _sync_rows(conn, (SYNC_WHOLE,), SYNC_KEEP):
        found = _sync(_run(row))
        if found["in_step"]:
            return found["finished"]
    return None


def last_sync(conn):
    """{"last_run": the newest sync, or None; "last_in_step": when the newest sync of the whole library that left it in step
    finished, or None} of the library open on `conn`. Both None for a library without the runs: a look does not migrate."""
    return {"last_run": newest_sync(conn), "last_in_step": last_in_step(conn)}


def _rows(conn, where, params=(), with_=""):
    """The runs `where` (the SQL after FROM job_runs, under the WITH clause `with_`) selects on `conn`, each with its
    `parent`, `heartbeat` and `progress` (parsed) when the library has had migration 36 (`_runs_nest`); without them
    before it."""
    if not _runs_nest(conn):
        return [_run(row) for row in conn.execute(with_ + " SELECT " + _COLUMNS + " FROM job_runs " + where, params)]
    found = []
    for row in conn.execute(with_ + " SELECT " + _COLUMNS + ", parent, heartbeat, progress FROM job_runs " + where,
                            params):
        run = _run(row[:-3])
        run.parent, run.heartbeat = row[-3], row[-2]
        run.progress = json.loads(row[-1]) if row[-1] else None
        found.append(run)
    return found


def under_way(db_path):
    """Every run of the library still `running`, oldest first, whoever owns it, with its parent and progress (_rows):
    several of a job of RECORDS_ONLY can be. Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return _rows(conn, "WHERE outcome = 'running' ORDER BY id")
    finally:
        conn.close()


def runs(db_path, job=None, limit=20, exclude=()):
    """The library's runs, newest first, of `job` or of every job but those in `exclude`, with their parent and progress
    (_rows). Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if job is None:
            return _rows(conn, "WHERE job NOT IN (" + ",".join("?" * len(exclude)) + ") ORDER BY id DESC LIMIT ?",
                         tuple(exclude) + (limit,))
        return _rows(conn, "WHERE job = ? ORDER BY id DESC LIMIT ?", (job, limit))
    finally:
        conn.close()


#: A top run: one nothing queued, or whose parent is gone -- pruned, or not in a snapshot a restore put back.
_TOPS = ("SELECT id FROM job_runs AS r WHERE r.parent IS NULL"
         " OR NOT EXISTS (SELECT 1 FROM job_runs AS p WHERE p.id = r.parent) ORDER BY r.id DESC LIMIT ?")

#: The top runs and every run under them. A child is claimed after its parent, so its id is greater; asking for that
#: also ends the walk whatever the rows say.
_TREE = ("WITH RECURSIVE tops(id) AS (" + _TOPS + "),"
         " tree(id) AS (SELECT id FROM tops UNION ALL"
         " SELECT c.id FROM job_runs AS c JOIN tree ON c.parent = tree.id AND c.id > tree.id)")


def _runs_nest(conn):
    """Has the library open on `conn` had migration 36 (job_runs.parent, heartbeat, progress)? The one answer: the
    readers (`_rows`, `tree`) and the write of a run's progress ask it, for a library at 35 -- one the Activity page's
    look and the MCP server read without migrating it, a snapshot a restore has put back and not yet migrated."""
    return any(column[1] == "parent" for column in conn.execute("PRAGMA table_info(job_runs)"))


def tree(db_path, limit=20):
    """The newest `limit` top runs of the library -- those nothing queued, or whose parent is gone -- and every run under
    them, newest first, each with its `parent`, `heartbeat` and `progress` (parsed): the one reader of the columns of
    migration 36. A library not yet at 36 (one the Activity page's look reads without migrating it) has no parents: its
    newest `limit` runs, each a top run. Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        limit = max(0, int(limit))
        if not _runs_nest(conn):
            return _rows(conn, "ORDER BY id DESC LIMIT ?", (limit,))
        return _rows(conn, "WHERE id IN (SELECT id FROM tree) ORDER BY id DESC", (limit,), _TREE)
    finally:
        conn.close()

"""The runs of each recurring job, kept in the library they ran for: `job_runs`
(migration 13; docs/ARCHITECTURE.md, phase 8, "Recurring jobs").

Any TagPup process may run a recurring job -- the web server, the CLI, the MCP server --
so what is due, and who is running it now, are read from the library, not from memory.
A run is claimed by a row `running`, owned by the process as the file journal names one
("host:pid:start", tagpup.store.file_journal.owner), inside one IMMEDIATE transaction:
two processes claiming the same job for the same library at once are serialized by
SQLite's write lock, and the second finds the first's row. A row whose owner has ended
-- a crash, a process killed mid-run -- is marked `abandoned` and the job claimed over
it, by the same test that settles a change of photo files a crash left
(file_journal.owner_alive).

What is due is the runner's to decide (tagpup.jobs.recurring); it is handed the last
run that ended and answers, inside the claim, so a process that lost the race to a run
that has just finished does not run the job again.

Times are the journal's: local time, "YYYY-MM-DD HH:MM:SS".
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from tagpup.store import db, file_journal, schema

TIME = "%Y-%m-%d %H:%M:%S"

RUNNING, DONE, FAILED, ABANDONED = "running", "done", "failed", "abandoned"
OUTCOMES = (RUNNING, DONE, FAILED, ABANDONED)

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
    #: Why it failed or was abandoned; can name a path, so never sent to a page.
    note: Optional[str] = None

    @property
    def started_at(self):
        return seconds(self.started)


def stamp(at):
    """`at`, seconds since the epoch, as a run records it."""
    return time.strftime(TIME, time.localtime(at))


def seconds(text):
    """A time a run recorded, as seconds since the epoch."""
    return time.mktime(time.strptime(text, TIME))


def _run(found):
    run_id, job, library, started, finished, outcome, changed, owner, note = found
    return Run(run_id, job, library, started, finished, outcome, json.loads(changed or "{}"), owner, note)


def has_table(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'job_runs'").fetchone() is not None


def _running(conn, job, library):
    return conn.execute("SELECT id, owner FROM job_runs WHERE job = ? AND library IS ?"
                        " AND outcome = 'running' ORDER BY id", (job, library)).fetchall()


def _last_ended(conn, job, library):
    found = conn.execute("SELECT " + _COLUMNS + " FROM job_runs WHERE job = ? AND library IS ?"
                         " AND outcome IN ('done', 'failed') ORDER BY id DESC LIMIT 1", (job, library)).fetchone()
    return _run(found) if found else None


class Claim:
    """What claim() found: `run_id` of the run begun, or `why` not -- "running" (a live
    process has it), "not due", or "behind" (the library has not had this version's
    migrations) -- and `abandoned`, the runs of ended processes it marked so."""

    def __init__(self, run_id=None, why=None, abandoned=0, holder=None):
        self.run_id, self.why, self.abandoned, self.holder = run_id, why, abandoned, holder

    def __bool__(self):
        return self.run_id is not None


def claim(db_path, job, library, now, due=None):
    """Begin a run of `job` for `library` (a library's name, or None for a job not run per
    library) at `now` (seconds since the epoch), owned by this process: unless a process
    still running holds one, or `due`, called with the last run that ended (Run or None),
    answers False. A run whose process has ended is marked `abandoned` first. A Claim.

    A library behind this version's schema is left alone ("behind"), not migrated: a job
    run by hand from the CLI is no reason to migrate one (docs/findings.md, #243). Its
    jobs run once an app has opened it."""
    if schema.pending(db_path):
        return Claim(why="behind")
    # Whether each owner lives is asked before the write lock is taken -- it may start
    # tasklist, seconds on a busy machine, which every writer of the library waited for --
    # and the rows read again under it: a run that appeared meanwhile is its owner's.
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        owners = {owner for _, owner in _running(conn, job, library) if owner}
    finally:
        conn.close()
    alive = {owner: file_journal.owner_alive(owner) for owner in owners}

    def work(conn):
        db.begin(conn, immediate=True)
        abandoned = 0
        for run_id, owner in _running(conn, job, library):
            if owner and alive.get(owner, True):
                return Claim(why="running", holder=owner)
            conn.execute("UPDATE job_runs SET outcome = 'abandoned', finished = ?, note = ? WHERE id = ?",
                         (stamp(now), "its process ended before it finished", run_id))
            abandoned += 1
        if due is not None and not due(_last_ended(conn, job, library)):
            return Claim(why="not due", abandoned=abandoned)
        run_id = conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES (?, ?, ?, 'running', ?)",
                              (job, library, stamp(now), file_journal.owner())).lastrowid
        return Claim(run_id=run_id, abandoned=abandoned)

    return db.write_with_connection(db_path, work, label="claim the job %s" % job)


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
            conn.execute("DELETE FROM job_runs WHERE job = ? AND library IS ? AND id NOT IN"
                         " (SELECT id FROM job_runs WHERE job = ? AND library IS ? ORDER BY id DESC LIMIT ?)",
                         (found[0], found[1], found[0], found[1], KEEP_RUNS))
        return ended

    return db.write_with_connection(db_path, work, label="finish run %d" % run_id)


def latest(db_path):
    """{(job, library): (the latest run, the latest that ended)} of every job the library
    at `db_path` has run, each a Run or None. Reads only; {} for a library without the
    table (one a look has not migrated)."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not has_table(conn):
            return {}
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


def runs(db_path, job=None, limit=20):
    """The library's runs, newest first, of `job` or of every job. Reads only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not has_table(conn):
            return []
        if job is None:
            found = conn.execute("SELECT " + _COLUMNS + " FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            found = conn.execute("SELECT " + _COLUMNS + " FROM job_runs WHERE job = ? ORDER BY id DESC LIMIT ?",
                                 (job, limit))
        return [_run(row) for row in found.fetchall()]
    finally:
        conn.close()

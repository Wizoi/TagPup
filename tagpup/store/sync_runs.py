"""A library's record of its syncs: `sync_runs` (migration 13; docs/ARCHITECTURE.md,
phase 8).

Each applied sync is a row: when it started and finished, whether it looked at the whole
library or one folder, whether it left the library in step with its folders, and what it
found and changed, as counts -- never a path or a name. "Last in step", which the pages
show, is when the newest sync of the whole library that left it in step finished.

A record of runs, not a change of the library: it is not journaled, as `schema_version`
is not, and undoing a sync (tagpup.services.journal) leaves its record where it is.
"""
import json
import time

from tagpup.store import db, journal, schema

TABLE = "sync_runs"

#: The columns a record is read back as.
COLUMNS = ("id", "started", "finished", "whole", "in_step", "found", "changed", "change_id")


def now():
    """The time as a record holds it: the journal's format."""
    return time.strftime(journal.TIME)


def record(db_path, started, whole, in_step, found, changed, change_id=None):
    """Record a sync of the library at `db_path` that started at `started` (now()) and
    has just finished. `found` and `changed` are {what: count}. Returns the record's id."""
    schema.ensure(db_path)

    def store(conn):
        return conn.execute(
            "INSERT INTO sync_runs (started, finished, whole, in_step, found, changed, change_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (started, now(), 1 if whole else 0, 1 if in_step else 0, json.dumps(found, sort_keys=True),
             json.dumps(changed, sort_keys=True), change_id)).lastrowid

    return db.write_with_connection(db_path, store, label="record a sync")


def _as_dict(row):
    if row is None:
        return None
    found = dict(zip(COLUMNS, row))
    found["whole"], found["in_step"] = bool(found["whole"]), bool(found["in_step"])
    found["found"], found["changed"] = json.loads(found["found"]), json.loads(found["changed"])
    return found


def last(conn):
    """{"last_run": the newest record, or None; "last_in_step": when the newest sync of the
    whole library that left it in step finished, or None} of the library open on `conn`.
    Both None for a library from before migration 13: a look does not migrate."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is None:
        return {"last_run": None, "last_in_step": None}
    newest = conn.execute("SELECT " + ", ".join(COLUMNS) + " FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    in_step = conn.execute("SELECT finished FROM sync_runs WHERE whole = 1 AND in_step = 1"
                           " ORDER BY id DESC LIMIT 1").fetchone()
    return {"last_run": _as_dict(newest), "last_in_step": in_step[0] if in_step else None}

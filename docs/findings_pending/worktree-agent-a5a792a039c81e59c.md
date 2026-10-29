# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | low: an index's end (`IndexQueue._end`, through `job_runs.end` -> `store.job_runs.finish`, `db.write_with_connection`) still waits a foreign write out, up to four times 30 s, on the queue's worker after the run; the next queued index starts only then. The claim no longer waits (made now or within 2 s, job_runs.RECORD_WITHIN), and a failed end is now written at the next claim, so a `write_if_free`-or-later end would lose nothing: a candidate for the same rule. | open: Data |
| B-2 | 2026-10-29 | low: an end that could not be written (#2189) is now written at the queue's next claim of that library; until one comes the row stays `running` as this live process's, and a root move of the library is refused ("the index job is running") until another index of it runs or the server restarts (the sweep). No live instance: 0 index rows in the three libraries (all at schema 35, counted 2026-10-29); the user action is a library unwritable for over 2 minutes as an index ends. | open: Files |
| B-3 | 2026-10-29 | low: with an index's claim recording beside another process's (#2190), `store.job_runs.latest` (each job's newest row) no longer answers "is an index running": a newer row that ended hides an older one still running. Its readers now leave `index` out (Now) or read `under_way` (the root move); B2b-2, reading the index rows for Now, must read `under_way` or `tree`, not `latest`. | open: Files |

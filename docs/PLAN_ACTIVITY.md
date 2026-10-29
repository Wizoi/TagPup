# Plan: the Activity page and job logging

Written 2026-10-28 from an analysis by an Opus agent (read-only, code and live counts) and the owner's answers the same
day (DECISIONS 2026-10-28, ACTIVITY REWORK). Findings it answers: #1913 #1914 #1915 #1917 #2106; the watcher/bake loop
is #2167 (Files, separate). Verify a claim here against the trunk before building on it.

## What is wrong today

- The page (`web/activity/`, routes `tagpup/web/activity_routes.py`, `services/activity.py`) has seven peer sections in
  the order of the code. Only Now is live (3 s); the rest polls every 30 s. Run now reloads the jobs once (#1913).
- Two record stores that disagree: `job_runs` in each library (the recurring runner, location changes, naming faces,
  `record_sync` after a successful sync) and the index queue's in-memory `_history` (50, lost on restart, invisible to
  another process). A sync is `done` in `job_runs` while the index it queued runs on beneath it, with no parent link (#2106).
- The bake prints one line at the very end; each folder's write logs only "change N, index bake, wrote K file(s)". A 3 h
  bake showed nothing but the last indexer line. A sync (~12 min over the share) says nothing in between (#1915).
- A refused or failed sync records nothing; an index failure (WinError 206, 17:16) lives in memory only. The Error
  column is a raw `Type: text`.
- Logs: 48 flat files, level WARNING by default, the sync's own lines almost none (#1914). No action centre (#1917).

## Design (settled: a single owner of run records)

Every background run is one row of `job_runs`, written only through `tagpup/services/job_runs.py`, stored by
`tagpup/store/job_runs.py`, named by its `tagpup/core/runs.py` tag. The index queue stays the scheduler, no longer a record.
New columns (the migration): `parent` (the run that queued this one), `heartbeat` (last progress write), `progress`
(JSON counts only; `phase` a fixed vocabulary, never a path). The parent process writes the child's progress from the
indexer's stdout lines (the child cannot write the parent's row), at most every 10 s, never per photo. Stuck = running with
a heartbeat older than 5 minutes ("no progress for 12 min; the share may be away"). Folder paths go in the log and in the
live line, never as a per-folder table. One function maps an exception or refusal to a sentence plus the next step.
Abandoned runs are swept at server start. Page: Now (phase, N of M, elapsed, last progress, Stop after this folder),
Needs you, What happened (run trees), folded Schedule / Server / Logs; one `/api/activity/runs` replaces `/jobs`, `/sync`,
`/timeline`; fast polling while anything runs.

## Branches (each about 1,200 lines or fewer, one concern; build in order)

1. **B1 make a long job visible** (about 400, no migration; Files + integration review): the bake prints
   "Bake folder N of M, K written, C current, X conflicts" (one owner of the line's shape in `bake_after_index`, the
   parser beside it); `index_folder` turns it into `report(message, percent)`; the sync's walk logs every 50 folders or
   30 s and reports phase and counts through the watcher's status; `now.js` shows phase, N of M, elapsed, last progress;
   run logs default to INFO. Scenarios: watching a sync's new folders indexed and baked (stubbed indexer, 3 folders);
   a whole sync over a slow share says how far it got.
2. **B2 index runs are `job_runs` rows under their sync** (about 900, THE MIGRATION; Data + Files + integration): claim
   per run, parent link from the held tags, throttled progress, end with counts and a note, in-memory history deleted,
   abandoned sweep at start. Needs: no other migration in flight. Scenarios: restart during an index says abandoned and
   why; the sync shows done with its index running beneath it; an index that fails to start is shown failed with a sentence.
3. **B3 failures and refusals recorded and worded** (about 350; Files + integration): a failed or refused sync records a
   `failed` row; the sentence mapper; the Needs-you list.
4. **B4 the page** (about 1,000, mostly JS/CSS; integration): the layout above, `/api/activity/runs`, aria-live,
   narrow widths, text beside colour, one in-flight poll, one unreadable library does not blank the rest.
5. **B5 logs** (#1914, about 300): system logs apart from run logs; run logs kept at the last 5 per library and kind,
   deleted at start; `tagpup_web.log` rotated at about 5 MB; every run's "Lines of this run" has its lines.
6. **B6 the action centre** (#1917): its brief states the live counts.

Before or beside B1 (Files): #2167, the bake feeding the watcher (a whole sync of photo_index every ~12 minutes during
the bake; 200 no-op `sync-folder` rows in 30 minutes). Verify its counts first.

## Unverified (check before building)

Whether the 200-row folder-sync flood comes from `worth_recording` recording every sync while the newest record is "not
in step"; that the CLI jobs (embed, cluster, thumbnails, retire vectors) write no `job_runs` rows; whether
`tagpup_web.log` rotates today; whether the watcher half of #2106 is already fixed by #2102.

## Added 2026-10-29 (owner): a log that is clicked is never blank

The owner clicked a run's log and it was empty, while the job was running. Every job that runs writes lines as it goes
(B1 did it for the sync walk and the bake; B2 and B3 add the index and each failure), and the Logs view of a run says WHY when it
shows nothing: "no lines yet; this job writes one every 30 s" (a running job), "this run wrote no lines at this level
(INFO and up): show DEBUG" (a filter), or "the log of this run was removed (the last 5 are kept)" (retention). B5 owns the
view; B1's levels and B2's run records are what it stands on. Its scenario test: clicking the log of a run that has just
started shows that it started, never an empty box.

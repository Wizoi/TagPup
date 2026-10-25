# Findings

This file records every review finding, bug report and known problem: what was found, and where it stands. A finding that exists only in a conversation gets lost, and a decision that is not written down gets made again.

**This file holds the open findings.** A closed row (status starting `fixed`, `built`, `decided`, `closed`,
`accepted`, ...) is moved, byte for byte and with its number, to
`history/findings_closed_0001-0250.md` and so on ([index](history/README.md)) -- one file per 250 numbers.
A reference such as "#726" is found by its number there. The owner's decisions are collected in
[DECISIONS.md](DECISIONS.md); the rules that came out of findings are in [INVARIANTS.md](INVARIANTS.md).

- **Add a row when something is found**, before fixing it.
- **Severity** *(owner, 2026-10-09; CLAUDE.md, "Keeping the project small")*: start the Finding with `data:` (can lose or
  corrupt data), `wrong:` (a wrong result the owner sees) or `low:`. Only `data` and `wrong` block a merge; `low` rows are
  batched. Older rows carry none.
- **Status** is one of:
  - `open`
  - `fixed`: give the commit, by its hash or, for a row added in the same commit, its subject.
  - `not reproduced`: say what was tried.
  - `left as is`: say why.
  - `tabled`: say until when.
  - `planned`: give the phase in [ARCHITECTURE.md](ARCHITECTURE.md).
- **Decisions the owner makes** are marked *(owner, date)*. Don't reopen them without a new fact.
- **Never put real names here.** The library is photographs of real people.
- **Numbering is the main session's.** A worker does not number rows into this file (two branches that both do collide on
  merge). It writes `| ? |` rows with `tools/add_findings.py rows.md --branch-file docs/findings_pending/<branch>.md`
  (provisional `B-1`, `B-2`; never write a `B-n` into code or a commit message) and the main session, merging the branch,
  runs `tools/add_findings.py --take docs/findings_pending/<branch>.md`, which numbers the rows after the highest number
  in this file **and the archives**, and removes the file. `tools/add_findings.py --status N "fixed: abc"` sets a status
  (in the archive if the row is there); `--archive` moves the rows whose status closed.

Findings fixed before 2026-09-23 are in the git log (`git log --grep="^fix"`). This file starts with what was still open or decided on that day.

| # | Found | Finding | Status |
|---|---|---|---|
| 1855 | 2026-10-24 | low: `tools/retro_metrics.py` ties 21 worker runs (80.1M tokens, 38.6%) to no branch, so cost per branch and relaunches are not counted (retro 2026-10-24, E1). | planned: tie a run with no `worktreeBranch` to its worktree by the transcript's first `cwd` or the worktree folder named in the brief's tool calls |
| 1877 | 2026-10-24 | low: a photo whose vector the library kept from a run killed before its batch was saved (its row unread, the vector stamped as the file is) is read again without a decode when it holds no bake, so its row is recorded with no `photo_fingerprints` row; so is every row indexed before cut 1c (phase 6 cut 1c, `tagpup_cli.py index`'s `read`). Nothing reads the table yet. | open: the writer (cut 2) decodes a photo with no fingerprint row, as it must for the rows indexed before cut 1c; decide there |
| 1913 | 2026-10-25 | wrong: the Activity page's scheduled-jobs list does not update when a job is started, or when it ends, until the page is reloaded in full (owner, 2026-10-25); `web/activity/jobs.js`, `poll.js`; the page polls batches (1.5 s) but not the jobs it just started. | open |
| 1914 | 2026-10-25 | low: the Activity page's Logs section lists every file in `data/logs` (indexer-<library>-<stamp> runs, run_tests-<stamp>, the web, MCP and console logs) with no filter or grouping, so the owner cannot tell what a file is (owner, 2026-10-25; 48 files, 1.8 MB, counted today: 32 indexer runs, 10 test runs). Owner's design: split the system logs (web server, MCP, tagtuner, console: not job related, not historical) from the historical ones (indexer and job runs, which keep growing); keep only the last 3 to 5 of the historical ones; filter them by library and kind; each job run already has its own log link, but it shows nothing useful for a run that finished: a sync run ("sync of the whole library, in step, found missing 1, 0 s") links to the server log, `_logs_for` (`activity_routes.py`) gives a non-indexer run only `logs.SERVER_LOG`, and a sync writes no line of its own there (see the silent-sync row), so "Logs for this run" is empty (owner, 2026-10-25). | open |
| 1915 | 2026-10-25 | low: a sync of a whole library writes nothing to the log and records itself only when it finishes (`job_runs` shows `running` and no counts; photo_index over the share has been silent for 7+ minutes), so the owner cannot tell a slow sync from a stuck one. | open: the sync logs a line per folder batch (folder N of M, files seen) and the Activity page shows it while the job runs |
| 1916 | 2026-10-25 | low: `bake-photos` says only the kind of a refusal ("147 whose file could not be read"), never why; the first kr-track dry run reported 147 photos in two folders unreadable, the second run 0, and nothing in the log explained the first. | open: print the distinct reason sentences with their counts (no paths or names) |
| 1917 | 2026-10-25 | low: a warning the owner can do nothing about keeps coming back: a sync reports "missing" files (kr-track 1, photo_index 1 before the reroot; the row stays by design, "missing files are never removed") with no way to say what happened to the file or to clear the report, and nothing says what to do next (owner, 2026-10-25). Wanted: an action center on the Activity page, a summary with a light per area (needs action / fine) listing the things to do next, each with its count, what it means and the action that resolves it. Live counts today: missing rows 1 / 1 / 0 (photo_index / kr-track / renton_parkrun; a handful, so the first cut is the summary and the explanations, not a per-file workflow); photos in folders to review 9,274 (photo_index, one folder); old-detector faces to retire 1,211 / 329 (kr-track / renton_parkrun, before the retire run); photos never examined by the face model 10,408 / 12 / 18; photos the bake cannot yet write (the same counts); changed files after a reroot 74 on the share. | planned: one feature with the Activity page update (#1913-#1916); its brief states these live counts; the actions are the existing ones (restore the file, a journaled removal of a row if the owner decides the photo is gone, accept and silence with the reason kept) -- the owners plan which exist |

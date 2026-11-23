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
  - `watching`: nothing to do until it is reported again or reproduced *(owner, 2026-11-02)*. The row is moved by
    `tools/add_findings.py --watch N "why"` to [watching.md](watching.md): not counted open, not brought up in a report;
    **read watching.md when an issue is reported**, to see whether it is one of those. A reproduced one goes back here by hand.
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
| 3341 | 2026-11-22 | wrong: the merge-budget hook (.claude/hooks/merge-budget.mjs) was registered for the Bash tool only, so a `git merge` run through the PowerShell tool skipped it: f88904a2 (phone cut 3b) merged at 1,258 added lines with no `Over budget:` line, and the hook refuses that same merge when run through Bash (checked at its parent). Retro 2026-11-22 action 3. | fixed: 79c3e02a |
| 3342 | 2026-11-22 | low: a page test takes a write route's reply from a fixture the real route wrote (as `scan_replies.json` and `rename_replies.json` already do for scans and Smart Rename), not a reply spelled by hand; the other write replies (save, tag write, Delete, Convert, time shift) are still hand-spelled in the page tests and every `wrong` of the photos-written cuts agreed with them (#3326, #3330). live: 2 rows name hand-spelled test data (#3326, #3330); a guard in the style of test_fixtures_make_photo_rows_like_the_indexer fails on a reply no route makes. Retro 2026-11-22 action 2. | open |
| 3343 | 2026-11-23 | low: a frontend test fails when a page module writes a kept scan, `state.folderPhotos` or a view record after a write other than through `cache.js photosWritten` / `photosGone`; today the one owner (#3324) has no guard on the client side, and a module that patches a kept scan itself passes. live: 23 `wrong` rows (about) #3054..#3339 are page-side write coherence (hand count, retro 2026-11-23). Retro 2026-11-23 action 1. Fold #3342's fixtures into the same worker. | open |
| 3344 | 2026-11-23 | low: `tools/add_findings.py` third-fix check (#2924) also requires the row to say `guarded by <test>` naming the test that fails on a second reader or writer, and counts rows closed `fixed` on a branch before merge; it missed the rename map's escape until #3339, the chunk order chain (#3107..#3210) and cut 3a-2's rows (#3091..#3105). live: 3 chains of 3 or more fixes in the window were not stopped. Retro 2026-11-23 action 2. | open |
| 3345 | 2026-11-23 | low: the update hand-over hang's root cause: `IDLE_PATIENCE` ends a stuck server at a deadline, but no row names which background task's stop never returned (the 22-minute hang the owner hit, DECISIONS 2026-11-19; #3190, #3191, #3268, #3270, #3312). live: 1 owner-hit hang. Find the task, make its stop return, then see whether the deadline is still needed. Retro 2026-11-23 (owner: follow the recommendations). | open |

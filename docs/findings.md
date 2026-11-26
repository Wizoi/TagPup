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
- **Queued work** *(owner, 2026-11-24)*: a row whose Finding starts `work:` is work to do or in flight, kept here instead
  of a prose handoff. It never blocks a merge and `--open` does not list it; `tools/add_findings.py --work` does. Its status
  is `queued`, `claimed: <branch> <date>` (`--claim N`, refused when already claimed, closed or blocked; a claim over a day
  old whose branch has no commit in the last day is shown STALE and may be claimed again) or `blocked: #NNNN` /
  `blocked: owner decides <question>`; it closes (`fixed:`, `decided:` ...) and is archived like any row.
  `--note N "text"` appends a dated line to `work/N.md` (never rewritten); closing the row moves the notes to
  `history/work/N.md`. A branch's `B-n` row has no notes: `--take` it first.
  **Who does what**: the main session claims, on the trunk, before it launches a worker (`--claim N --branch <the
  worker's branch>`, committed); a claim made inside a worktree edits this table and conflicts on merge. A worker only
  notes (`--note N`), which writes a file of its own and merges cleanly, and records in a note what did not work and any
  follow-up. When it finishes, its last note is `metrics: agent=<type> model=<m> effort=<e> turns=<n> review-rounds=<n>`,
  so a phase end can tell whether a worker's size and effort paid off (a convention, not parsed). `--work` lists the rows
  that can be started first (`queued`, then `claimed`, then `blocked`; a row blocked on a closed `#NNNN` is shown
  UNBLOCKED and listed with the queued). A session starts with `--work`, not a handoff memory; a handoff is written only
  when a session must stop right now, and says so.
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
| 3376 | 2026-11-24 | work: retire ExifTool's READS of a JPEG's owned fields with a reader TagPup owns (owner, 2026-11-24: "strongly for retiring the tool where we can"). Today every JPEG field read is ExifTool (`field_values.read`, 15 call sites: the pre-write plan, read-back, undo checks, bake plan, the index's whole read); the own writer replaced only the write command. Sweep first (Files owner): which fields each site needs, owned vs not, the live counts, what a reader must handle; then a plan in docs/plans/ naming the ONE reader and what stays ExifTool (non-JPEG, fields TagPup does not own). Also speeds the suite (80% of ExifTool starts in the undo matrix are product reads). Plan: docs/plans/fast-trustworthy-test-suite.md (cut 2 findings, docs/work/3375.md notes 11-14). | claimed: own-metadata-l0a 2026-11-26 |
| 3407 | 2026-11-26 | wrong: TagPup's Library pane is never re-read when an index ends (`web/tagpup/navigator.js` `navigatorCountsChanged` is called only by writes, Refresh view and Manage folders' Remove; `web/tagpup/suggestions.js` index `completed` and `gear.js` `queued` do not call it), so a new library, or a folder just added, shows no folders until a save or Refresh view. Owner hit it 2026-11-26 (user-flow review, Integration owner, ranked suggestion 1). | fixed: 68bcfc59 |
| 3408 | 2026-11-26 | wrong: back in Organize after a library view, the folder's index is no longer followed (`web/tagpup/library-view.js` `quietTheFolder` clears `indexProgressTimer`; reopening restarts the Suggest poll but not the index's), and the list cached in the browser before the index (`web/tagpup/folder.js`, "(cached)") is shown, so faces and suggestions look absent. Owner hit it 2026-11-26 (user-flow review, ranked suggestion 2). | fixed: 68bcfc59 |
| 3409 | 2026-11-26 | low: a saved Suggest run never says the library lacks named faces or vectors (`tagpup/jobs/suggestions.py` saved run; the sentence exists in `tagpup/services/suggester.py` and only "Just look" runs show it), so a new library's Suggest just looks weak. user action: Suggest in a new library with no named faces. | open |
| 3410 | 2026-11-26 | low: a sync batch's end is read again once per folder of the batch: `tagpup/jobs/indexing.py` `active()` lists every folder of a `together` job as its own entry, they all leave the list in one tick, and `web/tagpup/suggestions.js` `heardIndexes` calls `indexFinished` -> `upper.indexEnded` once each, so a view is refreshed N times or the open folder rescanned N times. live: 2 (kr-track index runs with 2 folders). Fix: group the gone folders by their run before calling `indexFinished`. (review of cut A, docs/plans/working-on-a-new-folder.md) | open |
| 3411 | 2026-11-26 | low: the "kept scan from before an index" rule knows only the ends this page load saw (`web/tagpup/folder.js` `keptScanOutOfDate` reads the in-memory `state.indexFollow.ends`; the kept scan lives 30 minutes in localStorage, `web/tagpup/cache.js`), so after a reload, or an index run while TagPup was closed, the list kept before the index is shown "(cached)" without its faces. user action: an index ends, F5, reopen the folder within 30 minutes. Not a regression (the old code had it). (review of cut A) | open |
| 3412 | 2026-11-26 | low: an index whose status the server lost (a restart mid-index: statuses are process memory) is reported as a completed one, silently (`web/tagpup/suggestions.js` `heardIndexes`, `index-status` READY). live: 0 (only a repository-run app restarted by a saved .py; an install's drain waits for the queue). (review of cut A) | decided: no live instance (a repository-run app restarted mid-index only; an install's drain waits for the queue), not built |

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
| 3375 | 2026-11-24 | work: make the full test suite fast and trustworthy. Owner goals: full suite under 5 minutes (now about 5+ and the fast tier alone 165s); per-owner / per-area runs in under a minute, ideally seconds; no loss of coverage; fewer wasteful tests, more thorough where it matters; shift slow checks left (cheaper layer) rather than dropping them; research how production teams do it (parallelism, test selection, lighter browser for page tests). Step 1 is research and a plan (docs/plans/), not a build. | claimed: research-test-speed 2026-11-24 |
| 3376 | 2026-11-24 | work: retire ExifTool's READS of a JPEG's owned fields with a reader TagPup owns (owner, 2026-11-24: "strongly for retiring the tool where we can"). Today every JPEG field read is ExifTool (`field_values.read`, 15 call sites: the pre-write plan, read-back, undo checks, bake plan, the index's whole read); the own writer replaced only the write command. Sweep first (Files owner): which fields each site needs, owned vs not, the live counts, what a reader must handle; then a plan in docs/plans/ naming the ONE reader and what stays ExifTool (non-JPEG, fields TagPup does not own). Also speeds the suite (80% of ExifTool starts in the undo matrix are product reads). Plan: docs/plans/fast-trustworthy-test-suite.md (cut 2 findings, docs/work/3375.md notes 11-14). | blocked: owner decides after record/replay in tests is measured (#3375); may not be needed (owner, 2026-11-24) |
| 3377 | 2026-11-24 | low: `names.read_for_renaming` (tagpup/files/names.py:52) reads fields with its own `et.get_tags`, one command per photo, beside `field_values.read`, the one batch reader: a second reader. Fold it into `field_values.read`; the one-owner guard then pins it. user action: Smart Rename of a photo (sweep for #3376, Files owner, 2026-11-24) | open |
| 3378 | 2026-11-24 | low: the bare `CreateDate` / `DateTimeOriginal` a photo row keeps is whichever group ExifTool answered last (`metadata.structured`, `core/fields.py`): MakerNotes:CreateDate is in 121 of 3,000 sampled photo_index rows; `taken` differs only where EXIF:DateTimeOriginal is absent (5 of 3,000). Matters only if a reader that skips maker notes replaces ExifTool's. live: 121 of 3,000 (sample, 2026-11-24) | open |
| 3384 | 2026-11-24 | low: a held connection used more often than once a second never takes a moved root map: `tagpup/store/roots.py _held` reuses the map it holds while `now - state.checked < RECHECK_SECONDS`, then writes `State(version, rows, roots, now, file)` on EVERY use, so `checked` moves forward each time and the recheck (machine.roots_of) never comes while the connection is kept busy. RECHECK_SECONDS's comment ("How long a connection trusts the map it holds before asking again") and test_roots_location's "a moved map is found within a second" hold only for a connection that rests a second. Shown (#3375 cut 3A): test_a_running_server_reads_the_new_place_without_a_restart and test_the_generations_every_cache_of_paths_is_keyed_by_move_with_the_map, their fixed sleeps replaced by wait_until polling the held connection every 10 ms, both failed in 3 of 3 runs after RECHECK_SECONDS + 1.0 s (the map never moved); with the sleeps they pass. Fix (not made, the brief allowed no product change): keep `state.checked` when the held map is reused, stamp `now` only when it was read again. user action: move a root to another place (Roots page or the CLI) while a connection the server or a run holds is asked more than once a second; whether a server connection is held that busy is not counted here. | open |
| 3395 | 2026-11-24 | low: as built (#3375, 740ff9b5), undo's loop (`file_changes.undo`) and settle (`_settle_files`) run inside `db.connections_held`, so every write of the run reuses one held connection and `roots._held` re-stamps `checked` at each use (#3384): until #3384 is fixed a held connection gives undo and settle ONE root map per run, by #3384's bug and not by `_pinned` (#3393). Fixing #3384 alone flips both to a map that can change between two files of one run; change: the fix of #3384 first pins undo and settle (`_pinned`, the single owner of one map per run, or the reason they need none), with a test that a map moved mid-undo is not taken by that run. user action: Undo of a 1,638-file bulk change (~40 s) while machine_roots.json is edited | open |

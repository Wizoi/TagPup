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
| 3379 | 2026-11-24 | wrong: the fast tier fails on the trunk (ad51dd86): cut 3 part 2 (fb8fdcf8) made tests/test_batches.py's `wait_until` the one in tests/waiting.py (condition, timeout, interval, what), but its importers still pass what as the second positional argument, read as the timeout: tests/test_batch_strip_replies.py:103 and :145 (`wait_until(cond, "the second batch queues")`, TypeError float + str, failed again alone in run_tests --fast) and tests/test_tag_tree_edits_are_batches.py:108 and :149 (`B.wait_until(cond, "the rename waits")`). Fix: `what=` at the four calls; a guard could refuse a str as wait_until's second positional argument. Not fixed on the 3B branch: the files are not this worker's. | fixed: c033b851 (wrote 4 calls, 2 files, 33 tests OK) |
| 3380 | 2026-11-24 | wrong: the ExifTool replay keeper (branch worktree-agent-aaa77c536921ec661, tests/exiftool_replay.py `Keeper.execute`) keeps every answer that ran to completion, including a non-zero status; the key lacks whether another process holds the file, so one Windows flake (a scan, Explorer, a test's own handle: "Error renaming temporary file") is replayed as a permanent failure in every later replay run, and a hit whose `_placed` fails on a held file replaces a good entry with the failure. user action: run the replay suite with a file held open once | open |
| 3381 | 2026-11-24 | low: the seeded mint replaces `identity.mint_document_id` in every replay/record process, so test_photo_identity's TestMinting tests test the stand-in: change identity.py:38 to a constant and run_tests.py (replay or `--real-exiftool`) stays green, only plain `python -m unittest` catches it. user action: edit `identity.mint_document_id`, run the suite | open |
| 3382 | 2026-11-24 | low: `_placed` in tests/exiftool_replay.py leaves `<file>.replay-part` behind when `os.replace` fails (PermissionError on a held file); a test counting files in its folder sees it. user action: a replay hit on a file held open | open |
| 3383 | 2026-11-24 | low: `--real-exiftool` (record mode) still installs the keeper's seeded mint and lazy session start, so the release gate does not run the product's own mint or its `with`-time ExifTool start outside STAYS_REAL; document it or turn both off in record mode. user action: the release-gate run | open |
| 3384 | 2026-11-24 | low: a held connection used more often than once a second never takes a moved root map: `tagpup/store/roots.py _held` reuses the map it holds while `now - state.checked < RECHECK_SECONDS`, then writes `State(version, rows, roots, now, file)` on EVERY use, so `checked` moves forward each time and the recheck (machine.roots_of) never comes while the connection is kept busy. RECHECK_SECONDS's comment ("How long a connection trusts the map it holds before asking again") and test_roots_location's "a moved map is found within a second" hold only for a connection that rests a second. Shown (#3375 cut 3A): test_a_running_server_reads_the_new_place_without_a_restart and test_the_generations_every_cache_of_paths_is_keyed_by_move_with_the_map, their fixed sleeps replaced by wait_until polling the held connection every 10 ms, both failed in 3 of 3 runs after RECHECK_SECONDS + 1.0 s (the map never moved); with the sleeps they pass. Fix (not made, the brief allowed no product change): keep `state.checked` when the held map is reused, stamp `now` only when it was read again. user action: move a root to another place (Roots page or the CLI) while a connection the server or a run holds is asked more than once a second; whether a server connection is held that busy is not counted here. | open |
| 3385 | 2026-11-24 | low: tests/corpus.py `_make` builds an item under `<key>-part-<pid><ext>`, and ExifTool's write in that build (corpus.jpeg, noise_jpeg) names the part file; the test keeper (tests/exiftool_replay.py) keys a file by its name, so every corpus make was a keeper miss in every run: the 7 warm misses of test_file_journal, 3 of test_time_shift, 3 of test_a_photo_patched_in_place were all this (the input bytes were identical; the name differed). In a scratch copy with the part named `<key>-part<ext>` (the stripe/item lock already lets one maker at a time; `_discard` before the build still clears a killed maker's part) the warm runs hit 244 of 244, 56 of 56, 70 of 72 (the 2 left are a file the test makes unreadable, run real by design). With the corpus kept across runs (2f995279) the miss happens only when an item is made (once per machine per generation), so the fix is one line in the corpus owner's file. user action: any test run that makes a corpus item | open |
| 3386 | 2026-11-24 | low: the early return after a failed `_placed` in tests/exiftool_replay.py has no test that fails without it; a multi-file in-place write with the second file held open, then a succeeding real run, would keep the partial result. live: 0 (no product command rewrites in place; only test setUp does, with no held file seen) | decided: no live instance, not built |
| 3387 | 2026-11-24 | low: two superseded worktrees (agent-a10cb6e4cec0c1a1f, agent-ae12d9e6b4e5303bf) hold the old keeper that keeps failures, sharing the machine cache. live: 0 (953 of 953 entries status 0); user action: run_tests.py in either worktree | decided: no live instance, not built |

# Phase 9 retrospective

Written 2026-10-04, after 9d (editing across folders) merged and before 9e (search).
Every number below was counted from `git log`, `docs/findings.md` or a test run on
2026-10-04; nothing is recalled.

## What was done

Since 2026-10-02 the trunk gained 209 commits (96, 103 and 10 on the three days), 199
changed files outside docs, +30,929 / -1,220 lines, of which 80 test files and +15,658
lines are tests. Delivered, in merge order: roots and machines (stages 1-4), derived
tables (9a-1), thumbnail cache and library views (9a-2), the windowed grid (9b-1), Just
look edits, the library source on the grid (9b-2), the tag editor, analyse-only Suggest,
alphabetical tags, the navigator (9c), the file-access check, bulk edits as a job
(9d-1) and their page (9d-2).

The suite is now 3,641 Python tests (313 files) and 1,212 frontend tests; ruff is clean.

## Where the findings came from

Findings #421-#634 are 214 rows. 149 of them name a review. By area:

| area | findings |
|---|---|
| roots (stages 1-4) | 72 |
| Just look / analyse-only Suggest | 25 |
| 9d-1 bulk job | 24 |
| 9d-2 bulk page | 20 |
| 9a derived tables / views | 19 |
| 9b grid and library source | 12 |
| 9c navigator | 11 |
| file access check | 8 |
| tag editor + alphabetical | 10 |
| other | 13 |

Review rounds that produced findings: 9d-1, six (#577-582, #583-589, #595-598,
#610-612, #620-623, #629-634); 9d-2, three (#605-609, #613-619, #624-628; #599-601 were
the worker's own); file access, two (#590-594, #602-604). The roots work had several
rounds too; they were not counted per round.

## What worked

* **Reviews found what tests could not.** Every double-shift in 9d-1 was a combination
  of a crash, a failed write and a second process; the reviewers demonstrated each with
  the fake ExifTool in a temporary home, and each fix had a test that fails on the old
  code. None of these reached a library.
* **Measuring the user's click.** `scripts/measure_bulk_page.py` in a real browser: Select
  all of 68,472 took 5,858 ms on the old path and 57 ms by id; a range across unloaded
  cards 1,588 ms to 49 ms; Invert 6,316 ms to 63 ms. The same rule caught the grid and
  the navigator earlier.
* **Owner decisions asked before building** (the root-relative path, additive migration,
  the journal not to be gold-plated) saved rebuilds.
* **Fixtures from the real shape.** The file-access parser passed its tests and matched
  no rule on the machine, because the fixture was a shape the registry never holds
  (#590). The reviewer read the real registry. This is the "test data is made by the
  code that makes the real thing" rule again; it applies to reviewers' briefs too: tell
  them to read the real thing, read-only.

## What cost the most

1. **Six review rounds on one symptom.** Each of 9d-1's rounds found a narrower way a
   resumed time shift could shift a photo twice (a failed settle, a live owner, a
   restored snapshot, a pruned change, a concurrent undo). The fix that ended it was not
   a seventh guard: the journal's prune now never takes a resumable job's changes
   (`keep` is a required argument of the store's prune). CLAUDE.md already says the
   second fix for one symptom is a question about ownership; the review loop did not apply
   it to itself. **Change:** the second round's brief asks for the structural cause
   (CLAUDE.md, review-loop rule).
2. **Polish rounds with no file at risk.** 9d-2's rounds 2 and 3 were mostly what the
   owner is told after an odd sequence (a lost start answer, a 409 title). Worth fixing,
   not worth three reviewers. **Change:** rounds after the second are for findings that
   can change a file or lose data; wording is batched.
3. **Restarts.** Two restarts killed five background agents and their reviews had to be
   run again. Work in worktrees survived; running reviews did not. **Change:** commit
   before launching agents (CLAUDE.md).
4. **A stale number.** CLAUDE.md said the suite takes 45 seconds; it takes about 235
   seconds on this machine and 200-625 under load. Workers and reviewers each spent time
   judging whether a slow run was a hang. Corrected in CLAUDE.md.
5. **The supervisor timing tests** (`tests/test_supervisor.py`) failed under load in
   findings #476, #573 and #582 and in the final run reported by several workers this
   session, and each time someone had to establish that it passes alone. It is one test file that is
   not about the phase's work. **Proposed:** make it a task of its own, not a note.

## Optimizations worth doing, and what they would buy

Nothing here is a claim; each needs the baseline-first treatment before anyone says it
got faster.

* **The tally panel for a 68,000 selection: 731 ms** (250 ms wait, 224 ms request, about
  250 ms of rendering 913 chips). Fewer or lazy chips. (#601)
* **A 409 with a code.** The page tells "another job is running" from other refusals by
  matching "already running" in the sentence (#614). A `code` field on the server's 409
  would remove the string match.
* **The 20,000 limit** means a selection of 30,000 of 68,000 cannot be sent, only
  Select all minus some. Raise it or take a source-minus-excluded in both directions. (#600)
* **One journal change per chunk** (about 2,700 History entries for a 68,000-photo job).
  Accepted in 9d-1; group them if History gets slow.
* **The ~190 ms between a response and the browser painting it** is documented and
  unexplained.
* **Identity by id for faces and people** (the design is in ARCHITECTURE.md "Identity by
  id") is the biggest remaining model change; it needs the owner's go-ahead first.
* **16 photos are in photo_index twice** (a share-spelled row and a row at D:\Training).

## For 9e (search)

* A search is a new `kind` in `library-source.js` and `library_view.source_of`;
  `selectionRequest()` already names a source as `{kind, value, recursive}`, so Select
  all, ranges, Invert, the tally, every bulk edit and the strip should work unchanged.
  Test exactly that, in one browser run.
* Name how each side gets its data first: the FTS5 index is a derived table like the
  others, so it is rebuilt from the same rows and `record_tags_in_index()` must feed it.
* The bulk request resolves a source at start (#607): a search result that changes
  between the owner's count and the job's start shows as the "took more than picked"
  notice. Decide whether a search selection should be sent as ids.
* Measure with `scripts/sandbox.py` and a real browser; ask for the click's time.

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
| 3250 | 2026-11-19 | low: filterKey (navigator-model.js) keeps the case of a tag or person while memberKey (search-model.js) folds it, so a hand-edited address naming one person must and not in two cases reads as a two-row filter; live: 0 (case-variant tags: 0 in photo_index, 0 in kr-track; only a hand-edited address makes one), 1b chips use filterKey | decided: no live instance, not built |
| 3251 | 2026-11-19 | low: the search box's Clear clicked twice before the first Back has arrived calls history.go(-back) twice (search.js clearSearch reads searchBack from the place it is still on), so the page goes two views back past the one before the search; user action: two quick clicks on Clear (or Enter twice on an empty box). From the advanced filtering cut 1b brief | fixed: c9146d00 (library-view.js leaveSearch with the leaving guard; the V29 test of Clear clicked twice failed on 489691da: history.go [-1, -1] where [-1] was due) |
| 3252 | 2026-11-19 | low: a filter made by clicking rows in Filter mode is also put in the search box's Filters lists (search.js searchFollows fills All of and None of from the open search), so the same filter shows twice: as chips above the grid and as chips under Filters; user action: turn Filter on, click two rows, open Filters. Left open: whether the search box hides a filter or edits it is a design choice for the plan (docs/plans/advanced-filtering.md), not a quick fix | fixed: 5523f4d5 |
| 3253 | 2026-11-19 | low: filter-bar.js refused a mark once the filter held MAX_MEMBERS rows in all, stricter than the server, which caps each list of a search and each union in it (services.library_view search_of); user action: a filter of 1,000 rows marked not, then one more row marked any of. Fixed by library-source.js listOverCap, which the search box's own check now uses too; the V29 cap test failed on the code before the fix (one ids request where two were due) | fixed: 5e8a118c |
| 3254 | 2026-11-19 | low: Clear all (and a chip's last x) from a filter made over a words search goes history.go(-2), past the words search Back would return to: filter-bar.js pushes the first mark through library-view.js searchBackOf, whose +1 was written for the search box refining its own search; the filter drops the words, so it is no refinement; user action: words search, Filter on, click a row, Clear all (confirmed: searchBack 2, went [-2]) | fixed: f4875043 |
| 3255 | 2026-11-19 | low: state.nav.filter.notice (a refused mark) is painted on every tab's status line and survives Back and a new view until the next successful mark or toggle (filter-bar.js, navigator.js); live: 0 (needs more than 1,000 folders marked, or an address over 100,000 characters) | decided: no live instance, not built |
| 3256 | 2026-11-20 | data: a Delete planned by a process that died is deleted by a later settle (photos.py settle_delete): `claim` overwrites the dead owner, `release` (file_changes.py _put_aside, _settle_taken) sets owner NULL, and "planned with owner NULL" is read as a clean stop and deleted; the owner's rule is that a Delete after an unclean stop is never resumed; confirmed with a probe on branch one-write-path-1a; user action: server killed after a Delete is planned, then a hand-over or a refused settle turn, then any write; live: 0 | fixed: 025e95ed |
| 3257 | 2026-11-20 | data: a settle's redo of a Delete sends whatever file is at the path now (photos.py _by_look redo, writing() records stamp None), so a different photo put under that path while TagPup was down goes to the Recycle Bin and the old row's faces go; confirmed with a probe; user action: Delete killed after the Bin step, a different file placed at that path, next start; live: 0 | fixed: a4dd3f3d |
| 3258 | 2026-11-20 | low: a Delete the share did not answer for is told to the owner as failed and then carried out by the next settle with no sentence saying so (photos.py _by_look UNANSWERED); user action: Delete a photo on a share as it drops | fixed: 79e3ea34 |
| 3259 | 2026-11-20 | low: bulk_edit.py docstring still says a Delete is not journaled; user action: reading it; live: 1 docstring | fixed: 93728bff |
| 3260 | 2026-11-20 | low: the id a thumbnail goes by, read while the row is there, exists twice: thumbnails.ids_of photo_paths arm (no production caller) and photos.plan_deletes; user action: none, two spellings; live: 2 spellings | fixed: 703ef787 |
| 3261 | 2026-11-20 | low: docs/domains/files.md still lists only writes and renames as journaled; a Delete is now an INTERNAL change settled by settle_delete; live: 1 doc section | fixed: 964cc936 |
| 3262 | 2026-11-20 | low: delete() holds the lock of photo-file changes around recycle_bin.problem and the through-this-PC copy, so a big clip Delete from the panel makes every save of the process wait; decided by the plan (#3203); user action: Delete a large clip on a share while tagging goes on | decided: plan-decided (#3203); a bounded shares.there before problem is the cheap half if it ever bites |
| 3263 | 2026-11-20 | low: delete() does not begin with file_changes.settle as write_fields and rename do; live: 0 unfinished changes in any library; cut 2's one entry point is where every file change begins with a settle | decided: no live instance; built in cut 2 |
| 3264 | 2026-11-20 | low: delete_planned's own stamp look was a second stat of each file on a share in a bulk Delete (test_a_bulk_delete_looks_at_each_file_on_the_share_once failed on the branch); user action: a bulk Delete of photos on a share | fixed: a83026be |
| 3265 | 2026-11-20 | low: a Delete whose look at `writing` gave no stamp then failed at the Bin step on the share was told "deleted when the share answers" but a settle withdrew it and logged a cause that did not occur; user action: Delete a photo on a share as it drops within 30 s of a look that timed out | fixed: a83026be |
| 3266 | 2026-11-20 | data: a bulk Delete resumed at the next start (services/photos.py settle_delete, `_by_look(redo=True)`) forgets the row and faces of a `planned` photo whose file is not reachable (`stamp_of` None: folder renamed in Explorer, drive letter not connected, #1387); the file never reaches the Bin and the photo returns later as new with its hand-made names gone; user action: stop a bulk Delete with an update, rename the folder before the next start. A `planned` row was never begun, so gone is never the Delete's doing; gone means forget only for a `writing` row. Single place: what a Delete does with a file it finds gone | fixed: 91d32a00 |
| 3267 | 2026-11-20 | wrong: a bulk Delete with one unreadable (CANNOT_READ) file in the selection fails whole with KeyError (bulk_edit.py `_delete`, `looks[paths.key(path)]`; `file_stamps` answers PRESENT with stamp None); the route says 500 "The bulk edit stopped (KeyError)" and deletes nothing, where the trunk made it one error and deleted the rest; user action: Delete a selection holding a photo the account lost rights to | fixed: 91d32a00 |
| 3268 | 2026-11-20 | low: Cancel followed by an update's drain in the same chunk is read as the update's stop (`batches.closing()` at the chunk boundary; cancel and stop_all set the same event), so the photos the owner cancelled are left `planned` and deleted at the next start; user action: Cancel a bulk Delete during a long clip copy while the installer drains | fixed: 91d32a00 |
| 3269 | 2026-11-20 | low: the resume of a bulk Delete (settle_delete) holds the lock of changes of photo files for every photo left, with no chunk turns and no Cancel, and the owner's first save after the start waits for it; user action: a Delete of a few thousand photos stopped by an update | open |
| 3270 | 2026-11-20 | low: a claim race at hand-over: a save in the draining old server claims the released bulk Delete and returns at once, so the new server's start finds nothing to take and the resume waits for the owner's first save; live: 0 | decided: no live instance, not built |
| 3271 | 2026-11-20 | low: no test stops the resume of a bulk Delete (a Crash at "delete sent" during the settle after the update's stop) and settles again, though docs/domains/files.md and settle_delete say the rest is then withdrawn; user action: a kill during the first start after an update that stopped a bulk Delete | fixed: 91d32a00 |

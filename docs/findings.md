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
| 3304 | 2026-11-21 | low: the folder's Date Taken order after a Time Shift is spelled twice: `web/tagpup/rename.js` `inTakenOrder` puts the page's records back by their `taken` string, the undated after in the order they had, which copies `tagpup.services.photos.taken_order` (`dates.date_taken_sort_key`: the date, then "mtime_<file time>" for the undated). They agree for every reply a shift gives (a shift writes no undated photo, so their order is the server's already), but a change to the server's rule is not followed by the page. Single place: the reply names the folder's order (or the page asks the server for it) once cut 2c deletes `/api/folder/time-shift`'s `updated_photos`. live: 78 photos with no Date Taken (74 photo_index, 4 renton_parkrun) sorted by the copied undated arm; user action: a Time Shift in a folder | open |
| 3306 | 2026-11-21 | low: after a share stops answering part-way through a Time Shift, a photo whose write landed but whose read back did not answer keeps its pre-write record (old stamp, old Date Taken) in the server's kept scan of the folder (`tagpup/web/tagpup_routes.py` `FolderCache`, through `_told`'s `move_all` of `_shifted`): `tagpup.services.photos.shift_date_taken` now leaves it out of `records` (#3300's fix; before, an empty record took its place and showed "Unknown"). For a held folder the next open scans again (the library's scan stamp moved with the journaled write); for a folder the library does not hold, the kept scan is served until it is let go idle or Refresh is pressed, and a page that reads it there holds the old stamp (a save from it is refused as changed on disk, not written wrong). Its stamp could be taken from `written` (the write recorded it) without its date. user action: a share that stops answering after a shift's first photo lands, in a folder the library does not hold. | open |
| 3308 | 2026-11-21 | low: a folder scan is recursive, so a Time Shift of folder P also shifts the photos of P/A, but only the kept scan of the folder the shift was sent from is changed (rename.js, `changeCachedPhotos(folder, ...)`); a kept scan of P/A or of an ancestor saved within CACHE_TTL_MS shows the old dates and stamps and a save from it is refused 409 (not written wrong); pending.js tag writes have the same rule. Single place: cache.js, one "photos written" change applied to every kept scan holding them, for every writer. user action: a Time Shift in a folder whose subfolder was opened in this browser in the last half hour | open |
| 3319 | 2026-11-22 | low: with two TagPup tabs on overlapping folders, one tab saving its open scan writes over the other tab's patch of the kept scan; the next save from it is refused 409 (not written wrong); user action: two tabs in one library. Plan photos-written B-2 | open |
| 3320 | 2026-11-22 | low: Integration: after a Date Taken save (op `set`) or a Time Shift, the server's kept record of the photo takes its new `taken` (and a shift's read back its whole record) but a set leaves its `year` (page_record's `dates.photo_year`) as the old date's, so a kept scan served again carries the old year beside the new date. No TagPup page code reads a folder record's `year` (grep of web/tagpup and web/common, 2026-11-22: 0 reads of a record's year; navigator-model.js reads the navigator's own); user action: save a Date Taken in another year in a Just-look folder and reopen it within the server's keep. Found building photos-written cut 1; close decided unless a page starts reading it. | open |
| 3321 | 2026-11-22 | low: Integration: `_told`'s set arm patches the kept record's date-derived fields one at a time (`raw_metadata` dates by `record_written`, `taken` from the save's details) rather than deriving them by `page_record`'s rule from what was written, so each derived field is a separate line to remember (`year` already missed, the row above). From the review of photos-written cut 1; user action: save a Date Taken in a Just-look folder and reopen it within the server's keep. | open |
| 3322 | 2026-11-22 | low: Integration: a clip's Date Taken save answers `taken`/`order` as `fields.date_taken_value` spells the value sent, while a later scan of the clip gives `dates.date_taken` of what its file holds (its XMP date first, then its QuickTime one); the page's own saves send no zone, so the two agree for them, but the reply's `order` is not derived by the scan's rule for a clip. From the review of photos-written cut 1; user action: save a clip's Date Taken from the panel. | open |

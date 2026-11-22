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
| 3319 | 2026-11-22 | low: with two TagPup tabs on overlapping folders, one tab saving its open scan writes over the other tab's patch of the kept scan; the next save from it is refused 409 (not written wrong); user action: two tabs in one library. Plan photos-written B-2 | open |
| 3320 | 2026-11-22 | low: Integration: after a Date Taken save (op `set`) or a Time Shift, the server's kept record of the photo takes its new `taken` (and a shift's read back its whole record) but a set leaves its `year` (page_record's `dates.photo_year`) as the old date's, so a kept scan served again carries the old year beside the new date. No TagPup page code reads a folder record's `year` (grep of web/tagpup and web/common, 2026-11-22: 0 reads of a record's year; navigator-model.js reads the navigator's own); user action: save a Date Taken in another year in a Just-look folder and reopen it within the server's keep. Found building photos-written cut 1; close decided unless a page starts reading it. | open |
| 3321 | 2026-11-22 | low: Integration: `_told`'s set arm patches the kept record's date-derived fields one at a time (`raw_metadata` dates by `record_written`, `taken` from the save's details) rather than deriving them by `page_record`'s rule from what was written, so each derived field is a separate line to remember (`year` already missed, the row above). From the review of photos-written cut 1; user action: save a Date Taken in a Just-look folder and reopen it within the server's keep. | open |
| 3322 | 2026-11-22 | low: Integration: a clip's Date Taken save answers `taken`/`order` as `fields.date_taken_value` spells the value sent, while a later scan of the clip gives `dates.date_taken` of what its file holds (its XMP date first, then its QuickTime one); the page's own saves send no zone, so the two agree for them, but the reply's `order` is not derived by the scan's rule for a clip. From the review of photos-written cut 1; user action: save a clip's Date Taken from the panel. | open |
| 3326 | 2026-11-22 | low: test data, photos-written cut 2: `tests/frontend/scenario-a-write-reaches-every-scan-kept.test.mjs` and `shifting-a-folders-date-taken.test.mjs` spell a scan record's `order` by hand (`record.taken ?? mtime_...`, and `dated`), where the replies of a write are the real entry point's; no fixture of a scan's record (`page_record.json` holds none) carries the server's `order`, and making one means a new Python writer of a scan record, not a small change. user action: a page's scan reply gives a photo an `order` the test's spelling disagrees with (tagpup.core.dates taken_sort_key changes). | open |
| 3327 | 2026-11-22 photos-written cut 3 | low: a single save (the six `postPhotoMetadata` callers, rotate, Smart Rename of one) names no photos on its write-queue entry (`entry.photos`), so the page reloaded or the library switched while one is out forgets no kept scan holding it (`edits.js` pagehide, `write-queue.js photosOut`); opened again within 30 minutes the folder shows the record from before the save, and a save from it is refused 409 (not written wrong). As before this cut; cut 4 sets `entry.photos` in `queueWriteOf` (it knows the path). user action: reload the page while a title save is out | open |
| 3329 | 2026-11-22 | wrong: `web/tagpup/cache.js recordsWritten` re-keys a chained rename wrongly: with Smart Rename of a folder giving updated_paths {17->18, 18->19}, `now.has(from)` skips the second mapping, so a kept ancestor scan ends with one photo doubled and another gone until the scan expires or Refresh (the open folder is unaffected). live: 1 chained rename in 11 smart renames in photo_index (change 34, 2026-09-28); user action: Smart Rename a folder whose names renumber, with its parent kept | open |
| 3330 | 2026-11-22 | low: the folder Smart Rename's page tests (just-look-edits, suggestions-follow-a-saved-photo) use hand-spelled `updated_paths`/`updated_photos`, never a chain, and no kept ancestor scan is tested; no `rename_replies.json` from the real route exists. live: 11 smart renames, 1 chained | open |
| 3331 | 2026-11-22 | low: Convert (`clip-convert.js:202-207`) copies the record captured at the click over every record the page holds or keeps when the ticket ends, so tags added to the clip meanwhile (after the folder was reopened) are overwritten in the open folder and the kept scans. live: 15 convert changes; user action: Convert a clip, open another folder and come back, add a tag to the clip, wait for the end | open |
| 3332 | 2026-11-22 | low: a conversion in progress is not "out" for `photosOut` (the queue entry resolves when the ticket is taken), so the page going mid-conversion forgets no kept scan holding the clip; a save from it is refused as moved. live: 15 converts; user action: Convert, then reload before it ends | open |
| 3333 | 2026-11-22 | low: a folder Smart Rename (`rename.js:78`) and a single Delete (`photo.js:884`) are sent outside the write line, so `photosOut()` never names them: the page reloaded with one out keeps every scan holding the old names or the deleted photo (a save from it is refused, never written wrong). user action: Smart Rename a folder on a share and press F5 before the reply | open |
| 3334 | 2026-11-22 photos-written cut 4 docs | low: Smart Rename of a folder posts outside the write line (`api.fetch` in `rename.js`, not `queuePhotoWrite`), so the page going while it is out (`pagehide`) forgets no kept scan of the folder's photos: reopened within the half hour, a folder or ancestor scan shows the old names and stamps. user action: reload or choose another library during a folder Smart Rename, then open the folder (or one above it) again | open |
| 3335 | 2026-11-22 photos-written cut 4 docs | low: a Convert's ticket finishing after the page has gone (`clip-convert.js` polls `/api/clip/converted`; the poll dies with the page) leaves every kept scan holding the clip's old path and stamp, and nothing forgets them. user action: reload while a Convert runs, then open the clip's folder (or one above it) again within the half hour | open |

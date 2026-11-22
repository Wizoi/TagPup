# Photos written: a write's reply reaches every scan the browser and the server keep of its photos, in the server's order

Status: PLAN, not built. Findings #3304, #3306, #3308 (all `low`, open), and one new row (B-1 below, `wrong`).
Live count (read-only, 2026-11-21, `db.readonly_uri`): photos photo_index 60,270, kr-track 2,186, renton_parkrun 1,309.
Undated photos (`taken IS NULL`, the ones the copied order's undated arm sorts): 74 photo_index, 0 kr-track, 4 renton_parkrun
(78, #3304's count holds). Folders whose recursive scan holds a subfolder's photos (a proper ancestor of a photo folder):
60 photo_index (above 2,571 photo folders), 11 kr-track (above 32), 8 renton_parkrun (above 8); every photo sits under at
least one, so #3308 needs only the user action (open a subfolder, then its parent, within half an hour). Photos held by two
libraries' rows: 0 for every pair. A share stopping part-way (#3306) is a user action, never counted.
Domains: Integration (`web/tagpup/cache.js`, row 23; `tagpup/web/tagpup_routes.py` `FolderCache`, `_told`, `_envelope`, row 11),
Files (`web/tagpup/rename.js`, `edits.js`, `date-taken.js`, `web/common/paths.js`, `tagpup/core/dates.py`,
`tagpup/services/photos.py`), Views (`web/tagpup/pending.js`, `grid.js`, `suggestions.js`, `photo.js`); integration reviews the seams.

| Mechanism | The ONE place it lives | Owner | New or existing |
| --- | --- | --- | --- |
| A folder's order (Date Taken, the undated after by file time) | `tagpup/core/dates.py` `date_taken_sort_key`, split so its last line is `taken_sort_key(taken, mtime)`; `services.photos.taken_order` stays its caller | Files | existing rule, one function split out |
| The order key the page sorts by: `order` on each record a scan (or a folder rename) replies with, and on each photo a write's reply gives `taken` for (shift, set) | `tagpup_routes._sorted` (records leave the server with `order`; never stored in `FolderCache`) and `_envelope` (`written[path].order`) | Integration | new field |
| What the server keeps of a photo written: every map holding it (its folder's, an ancestor's) | `tagpup_routes._told`, through `FolderCache.entries_for` / `move_all` | Integration | existing; the shift arm gives a photo whose read back did not answer its stamp from `written` (#3306); the set arm gives the record its new `taken` (B-1) |
| What the browser keeps of a photo written: the open folder's records and every scan this library keeps that holds it (its own folder, every ancestor) | `web/tagpup/cache.js` `photosWritten(paths, take)`; `take` is the writer's change of one record, applied the same to each | Integration | new; replaces `changeCachedPhotos`, the open/left branches of `rename.js tookShift` and `pending.js takeReply` |
| A photo gone (deleted; the page left with its write out): every kept scan holding it forgets it | `cache.js` `photosGone(paths, { scans })` | Integration | new; replaces `forgetCachedPhoto`, `forgetCachedFolder(folderOf(...))` |
| Which kept scans hold a photo | `cache.js`, by a new `isUnder(path, folder)` in `web/common/paths.js` (the page's `paths.is_under`) | Files (paths), Integration | new helper beside `samePath` |
| Records put in the folder's order on the page | `cache.js` `inFolderOrder(records)`: a stable sort by `order`, no rule of its own | Integration | new; deletes `rename.js inTakenOrder` |
| A kept scan's shape | `cache.js` `CACHE_VERSION` 6 -> 7 (records carry `order`) | Integration | existing |
| The page's writes taken in the order sent | `edits.js queuePhotoWrite` (one line, `inTurn`) | Files | existing; Time Shift joins it |

Steady-state writer: every later write goes through `POST /api/photos/write` and its envelope; no command is run. One-offs: none.
No migration, nothing written to the live libraries.

## How it fails, and what settles it

- **A share stops part-way** (400, `written` names only what landed): each landed photo takes its stamp in every kept scan,
  browser and server; one whose read back did not answer keeps its Date Taken and `order` (#3300's rule) and takes the stamp
  `written` gives it, on the server as on the page (#3306). Nothing not in `written` changes.
- **The page closes or reloads with a write out**: `pagehide` forgets every kept scan holding a photo of the chunk out (an
  ancestor's too, not only its own folder's): reopened, it is scanned, never refused.
- **A kept scan that cannot be written back** (`localStorage` full): it is forgotten instead of left with the old stamps.
- **A subfolder kept while its ancestor is shifted, or the reverse**: both are scans holding the photo; both take it.
- **Two writes at once**: the server's `batch_exclusively` orders the files; the page takes replies in the order sent because
  every writer is in the one line (Time Shift joins it; a shift waits behind a tag drain, and the status line says so).
- **Two tabs**: each tab's in-memory records are its own; a tab saving its open scan over another's patch is refused at its next
  save (409, the stamp precondition, rows 11 and 23), never written wrong. Not built (B-2).
- **Two libraries holding one folder**: kept scans are per library (#1359); a write in one changes only its own. 0 photos are held
  by two libraries; for a folder looked at in two, the other's save is refused 409. Not built.
- **An update while working**: an old page ignores `order`; a new page drops version-6 scans (scanned once); the new server's
  `FolderCache` starts empty. No reply without `order` reaches a page that needs one: a tag write never sends it.
- **Rapid clicks**: shifts and saves queue in the one line; each shift is computed from the records at its click.
- **What the owner sees**: no extra request and no rescan; a write patches at most the kept scans above the photo (depth about
  10), each parsed once.

Decided, not built: a tag write sends no `order` and moves no photo in the grid; an undated photo (78) takes its file-time place
at the next scan, as today.

## Cuts (each about 1,200 lines or fewer, one concern, the trunk whole after each)

1. **Server (Integration; Files reviews `dates.py`), about 350 lines.** `dates.taken_sort_key`; `order` on `_sorted`'s records and
   on `_envelope`'s `written` where `taken` is given; `_told` shift arm stamps a written photo with no read-back record (#3306);
   `_told` set arm sets the kept record's `taken` (B-1, test fails on the old code first). The fixture writer in
   `tests/test_a_write_through_the_one_entry_point.py` regenerates `shift_replies.json` and the save and tag replies.
2. **The browser's kept scans and Time Shift (Integration; Files reviews `rename.js`, `paths.js`), about 500 lines.**
   `photosWritten`, `inFolderOrder`, `isUnder`, `CACHE_VERSION` 7; `tookShift` through `photosWritten` and the write line;
   delete `inTakenOrder` and the left-folder branch. Closes #3304, and #3308 for shifts.
3. **Tag writes (Views), about 300 lines.** `takeReply`'s `left` map and `pagehide` through `photosWritten` / `photosGone`;
   delete `changeCachedPhotos`, `forgetCachedFolder`. Closes #3308.
4. **Single saves and the other writers (Files; Views and Media review their call sites), about 400 lines.** The six
   `postPhotoMetadata` callers' `saveToLocalStorageCache()` become one `cache.js` call that takes the saved record into every
   kept scan (a caption rename re-keyed); the siblings found by the sweep -- rotate (`photo.js`), Smart Rename of one and of a
   folder (`rename-one.js`, `rename.js`), Convert (`clip-convert.js`), Delete (`forgetCachedPhoto`) -- the same. `bulk-run.js`
   keeps forgetting every scan (a library-wide write). Deletes `forgetCachedPhoto`.

Concepts: adds `order`, `photosWritten`, `photosGone`, `inFolderOrder`, `isUnder`; deletes `inTakenOrder`, `changeCachedPhotos`,
`forgetCachedPhoto`, `forgetCachedFolder`, the `left` map and two open/left branches.

## Scenario tests (page tests use the real entry point's replies)

- Integration, I9 lifted: a subfolder kept, its parent opened and a camera shifted; the subfolder reopened within the half hour
  shows the new dates, in the server's order, and a save from it is sent with the new stamp (and the reverse: parent kept, child
  shifted). Replaces `shifting-a-folders-date-taken.test.mjs`'s "a folder left before the shift's reply" test.
- Integration, I15: a share stopping part-way: the server's kept record of the photo whose read back did not answer has the
  reply's stamp (#3306); a Date Taken save in a folder the library does not hold, reopened from the server's scan, shows the new
  date (B-1); the envelope's `order` equals the scan's for every photo written.
- Views, V22 lifted: tags written from a parent reach the kept subfolder and grandparent; the page closed with a chunk out forgets
  every kept scan holding it.
- Files: a title save renaming the photo re-keys it in a kept ancestor scan; a Delete leaves no kept scan holding it.
- Integration: a kept scan that cannot be written back is forgotten.

## Question for the owner

1. A single Date Taken save: does the photo move to its new place in the open grid at once (as after Time Shift: one rule, a write
   that changes Date Taken re-sorts), or stay where it is until the folder is opened again (today)? The plan builds the first
   unless the owner says otherwise.

## New findings (rows for docs/findings_pending; the main session numbers them)

- B-1 `wrong:` a Date Taken save (op `set`) in a folder the library does not hold leaves the server's kept record's `taken` as it
  was (`tagpup_routes._told` set arm: `record_written` updates `raw_metadata`'s dates, nothing sets `record["taken"]`); the page,
  served that scan after its own half hour, shows the old date. user action: save a photo's Date Taken in a Just-look folder,
  open the folder again after 30 minutes and before the server lets the scan go idle.
- B-2 `low:` two tabs on overlapping folders: a tab saving its open scan writes its older records over the other tab's patch;
  the next save from it is refused 409, never written wrong. user action: two TagPup tabs in one library.

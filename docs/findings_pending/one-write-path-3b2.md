# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-21 | low: the folder's Date Taken order after a Time Shift is spelled twice: `web/tagpup/rename.js` `inTakenOrder` puts the page's records back by their `taken` string, the undated after in the order they had, which copies `tagpup.services.photos.taken_order` (`dates.date_taken_sort_key`: the date, then "mtime_<file time>" for the undated). They agree for every reply a shift gives (a shift writes no undated photo, so their order is the server's already), but a change to the server's rule is not followed by the page. Single place: the reply names the folder's order (or the page asks the server for it) once cut 2c deletes `/api/folder/time-shift`'s `updated_photos`. live: 78 photos with no Date Taken (74 photo_index, 4 renton_parkrun) sorted by the copied undated arm; user action: a Time Shift in a folder | open |
| B-2 | 2026-11-21 | low: a Time Shift over a folder holding photos with no Date Taken says "Time shift applied to 3 of 5 photo(s); 2 could not be written": a photo holding no date to move is skipped (`result.skipped`), not failed, but `attempted` counts it, as `/api/folder/time-shift` did (`updated_count` of `requested_count`). The page now adds the first skip's reason when no error says one (rename.js `tookShift`); whether skipped photos belong in "could not be written" at all is the owner's wording. live: 74 photos with no Date Taken in photo_index, 4 in renton_parkrun | open |

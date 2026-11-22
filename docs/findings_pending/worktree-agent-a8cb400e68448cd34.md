# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-22 | low: Integration: after a Date Taken save (op `set`) or a Time Shift, the server's kept record of the photo takes its new `taken` (and a shift's read back its whole record) but a set leaves its `year` (page_record's `dates.photo_year`) as the old date's, so a kept scan served again carries the old year beside the new date. No TagPup page code reads a folder record's `year` (grep of web/tagpup and web/common, 2026-11-22: 0 reads of a record's year; navigator-model.js reads the navigator's own); user action: save a Date Taken in another year in a Just-look folder and reopen it within the server's keep. Found building photos-written cut 1; close decided unless a page starts reading it. | open |
| B-2 | 2026-11-22 | low: Integration: `_told`'s set arm patches the kept record's date-derived fields one at a time (`raw_metadata` dates by `record_written`, `taken` from the save's details) rather than deriving them by `page_record`'s rule from what was written, so each derived field is a separate line to remember (`year` already missed, the row above). From the review of photos-written cut 1; user action: save a Date Taken in a Just-look folder and reopen it within the server's keep. | open |
| B-3 | 2026-11-22 | low: Integration: a clip's Date Taken save answers `taken`/`order` as `fields.date_taken_value` spells the value sent, while a later scan of the clip gives `dates.date_taken` of what its file holds (its XMP date first, then its QuickTime one); the page's own saves send no zone, so the two agree for them, but the reply's `order` is not derived by the scan's rule for a clip. From the review of photos-written cut 1; user action: save a clip's Date Taken from the panel. | open |

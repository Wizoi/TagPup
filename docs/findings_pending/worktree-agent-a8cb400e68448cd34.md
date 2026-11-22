# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-22 | low: Integration: after a Date Taken save (op `set`) or a Time Shift, the server's kept record of the photo takes its new `taken` (and a shift's read back its whole record) but a set leaves its `year` (page_record's `dates.photo_year`) as the old date's, so a kept scan served again carries the old year beside the new date. No TagPup page code reads a folder record's `year` (grep of web/tagpup and web/common, 2026-11-22: 0 reads of a record's year; navigator-model.js reads the navigator's own); user action: save a Date Taken in another year in a Just-look folder and reopen it within the server's keep. Found building photos-written cut 1; close decided unless a page starts reading it. | open |

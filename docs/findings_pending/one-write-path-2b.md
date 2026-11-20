# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-20 | low: a folder's Time Shift through the one entry point (`tagpup_routes._write`, `file_changes.batch_exclusively`) holds the lock of changes of photo files for its whole run, where a library view's bulk edit (no id named, `photo_writes.write` without the lock) lets a waiting save in between its chunks; live: 0 (no time-shift change ever journaled in the three libraries, counted read-only 2026-11-20; their largest folders hold 400, 151 and 202 photos) | decided: by design (plan one-write-path 2b): a shift of one folder is short, and the page's cut 3 sends it to /api/photos/write as a page's write |
| B-2 | 2026-11-20 | wrong: `/api/photos/write` op `shift`: a shift that a share stopped part-way answered `written` with the photos it never reached (`_told` took every record read back less the errors and skipped; `shift_date_taken` dropped write_fields' `written`); the scenario test failed on b2894d44 first | fixed: 58560e47 (`shift_date_taken` keeps `written`, `_told` reads it through `_stamps_after` as a tag write's; tests/test_a_write_through_the_one_entry_point.py) |

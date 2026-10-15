# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: tagpup_routes' own cap check (`_over_cap`, the 400 before the service) has no test of its own since test_bulk_cap went: file_changes.write_fields refuses over MAX_BATCH with the same sentence, covered by test_batches.Cap and test_bulk_edits.TheLimit. The route-level copy is a candidate for deletion (one place to own the cap), not a test to bring back. | open |
| B-2 | 2026-10-15 | low: a rename on a volume whose os.stat gives no st_ino is now refused by file_changes.rename with "this volume gives its files no file id, so a renamed file could not be told from a copy of it" (names.RenameFailed, so the Save-after-caption and Smart Rename replies already show it). Counted: 791 of 791 rename plans in the three live libraries carry an id. Not handled, no real instance: a plan stored without one (settling treats it as not found under either name, a conflict). The FAT32/exFAT case of #285 (an id a rename can move) is unchanged. | decided: owner, 2026-10-15 (DECISIONS, decision 1) |
| B-3 | 2026-10-15 | low: test_rename_keeps_the_index's Windows-only test (`d:\library/2020/2z6a5820.JPG` finds the row stored as `D:\Library\2020\2Z6A5820.jpg`) was deleted with the file: no rename scenario spells an old name two ways. The spelling is owned by paths.key/sql_equals and tested in test_paths*; a rename caller only passes stored paths. | left as is: covered by the paths guards |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-27 | low: History still counts a cache's rows a change recorded before a crop was a cache: journal.history's per-table counts and its keys (tagpup/store/journal.py, the two `FROM change_rows` reads in `history`) read every table_name, so an old face-pass change shows its face_crops rows in the dialog's "N row(s)" (web/common/history-dialog.js describeChange) and in the MCP `history` keys, while its undo (journal._load skips CACHES) neither asks for nor puts back one. Recorded crop rows, counted read-only 2026-10-27: photo_index 30,492 in 2,111 face-pass changes and 25,795 in 1 'retire faces' change, kr-track 1,647 + 1,211, renton_parkrun 316 + 329. Fix: leave CACHES out of both reads (`table_name NOT IN (...)`), or decide History says what was recorded. Found by the worker finishing the crop-is-a-cache branch. | open |

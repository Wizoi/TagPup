# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | low: every journaled file write's record (file_changes._record -> file_journal.follow_renamed) scanned the whole of change_files for a rename to follow: 51,451 rows in photo_index, 85 ms warm and up to 6 s cold per call (counted read-only this session), under the write lock, once per file of a bulk edit. The follow is now sought by the renames' changes (file_journal.RENAMES, the change_id index: a SEARCH in EXPLAIN QUERY PLAN on the live library, 0.02 ms), the same statement the mint's look uses; rename() refuses an operation not in RENAMES, so a new rename cannot go unfollowed. | fixed: the mint's carry commit (files: the indexer's identity mint carries a rename's fingerprint ...) |
| B-2 | 2026-10-29 | low: decided, the mint's carry (file_changes.OutsideWrites) leaves two cases as before, each a refused Undo, never a wrong write: a process killed between the mint's write and the transaction right after it (the rename keeps the old fingerprint), and a second library holding the same folder, whose own rename of the file is not told (the journal's own writes tell only the writing library too; no two libraries hold one folder with a rename today: the three libraries' rename changes are 1, 4 and 3, each its own folders, not checked for overlap this session). No branch written for either: no live instance. | decided: by design (the brief's forward-only fix) |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-26 | low: the settle of a rename cut off (`file_changes._finish` -> `_settle_renames`) held no root map: `_pending` read the change's files by the map then, and `_record_renames` moved their rows by the map at its write, so a map whose old place was dropped between the two left every row at its old name while the settle renamed the files (60 files renamed again, 0 rows moved, 60 != 0 in test_a_root_moved_during_a_long_undo_or_settle on the code before the fix). Fixed in this branch: the pin (tagpup.services.roots.pinned) moves from `_settle_files` up to `_finish`, around `_pending` and both settles; the test failed on the old code first. user action: a Smart Rename cut off (a crash, the app closed), then machine_roots.json edited by hand to drop the old place while the next start settles it (TagTuner's Roots keeps the old place, where it does not happen) | open |
| B-2 | 2026-11-26 | low: decided in this branch: with `_pending` inside `_finish`'s pin, a root this machine does not place, met by a settle of a change cut off, is caught at the pin as `Unplaced` and logged as a line naming machine_roots.json, the change released for the next settle; before, `_pending` raised RootsError and `_settle_taken` logged it with a traceback and released it. The same outcome (left, released, settled once the map places the root), said in words; no new branch (the except was already there). user action: open a library with a change left half done while machine_roots.json lacks one of its roots | open |

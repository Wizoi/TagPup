# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-24 | low: as built (#3375, 740ff9b5), undo's loop (`file_changes.undo`) and settle (`_settle_files`) run inside `db.connections_held`, so every write of the run reuses one held connection and `roots._held` re-stamps `checked` at each use (#3384): until #3384 is fixed a held connection gives undo and settle ONE root map per run, by #3384's bug and not by `_pinned` (#3393). Fixing #3384 alone flips both to a map that can change between two files of one run; change: the fix of #3384 first pins undo and settle (`_pinned`, the single owner of one map per run, or the reason they need none), with a test that a map moved mid-undo is not taken by that run. user action: Undo of a 1,638-file bulk change (~40 s) while machine_roots.json is edited | open |

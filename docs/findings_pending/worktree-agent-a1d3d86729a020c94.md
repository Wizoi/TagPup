# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-22 | wrong: a rename of a photo (a save, Smart Rename of one or of a folder, a renaming Convert) moved the open folder's suggestions and the kept scans' records to the new names, but never the suggestions of another kept scan holding it (the parent's: a parent's Suggest holds its subfolders' photos): opened again within 30 minutes the renamed photo showed none, and after a chained Smart Rename (#3336's) the photo at an old name of another was shown that other's suggestions, an accept writing them into its file. Fixed in this branch: `cache.js photosWritten(paths, take, renames)` moves each kept scan's suggestions through `paths.js renamedBy`; the chained and swap page tests assert the kept parent's, failing on the old code (they failed there on the open folder's first). user action: Suggest in a parent folder, open a subfolder, Smart Rename (or rename by caption) a photo there, open the parent again within 30 minutes | fixed: this branch |

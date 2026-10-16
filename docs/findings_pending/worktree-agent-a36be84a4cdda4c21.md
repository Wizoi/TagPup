# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: decision made in #1443's fix, not copied: `sync.folder_state` is kept, not removed: its one caller left is the folder watcher (`tagpup/jobs/watching.py`), and the jobs layer may not import `tagpup.files` (`tests/test_layers.py`), so it is the watcher's door to `shares.folder_state`; `include()` now asks `shares.folder_state` itself. Removing it needs the layer rule changed or the watcher's look moved into a service. | open |
| B-2 | 2026-10-15 | low: decision made in #1387's fix: "unanswered" is asked only of a path `shares.on_a_network_drive` calls a network path (UNC, or a drive letter GetDriveType says is remote). A mapped network drive Windows no longer reports as remote (the mapping removed, or GetVolumePathName failing on it, which `recycle_bin.on_a_network_drive` answers False) is taken as a local drive not connected: its root gone and its rows missing (reported, never removed). Not handled: 0 of 62,327 photos in the three libraries are on a network path (counted 2026-10-15, read-only). | open |
| B-3 | 2026-10-15 | low: `MetadataExtractor._stamp` still stamps (0.0, 0) when the stat before the read fails (the file gone between the walk and the read); with #1441's guard gone nothing refuses it. Not handled: 0 of 62,327 rows are stamped 0.0 / 0 and 0 have no stamp (counted 2026-10-15, read-only). | open |

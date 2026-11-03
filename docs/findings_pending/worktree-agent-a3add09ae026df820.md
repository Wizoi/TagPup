# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | low: decided: migration 37 adds a column to `photos`, a table the journal keys, but its `touches` names only `change_files`, so a change of rows made at 36 stays undoable (20 stood in the three libraries, counted read-only 2026-11-02; naming `photos` would refuse every one, `journal.schema_gap_blocker` blocking per gap, not per table). Sound because every photo row at 36 is a photo (0 of 62,897 paths has a video's extension, counted 2026-11-02), so a row an undo puts back without the column takes the default. The charter's by-design rule (a check of an additive step's touches when it claims the undo exemption) is met by `TheOwnersLibraryAt36MeetsMigration37`, whose deletion made at 36 is undone exactly after 37. Naming `photos` instead is a one-line change before the install, not after. | data | open |

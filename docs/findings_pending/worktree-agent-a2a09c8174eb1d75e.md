# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: #2401's sentence is ExifTool's answer to a READ, not a write: probed this run on this PC's ExifTool, a photo held open with no sharing (lock_owners.opened, NO_SHARING) answers a write (`-o` beside it) `Error: Error opening file - <file>` and a read (get_metadata) `Error: File not found - <file>`, for a 62-, a non-ASCII and a 303-character path alike; the same three written and read free all succeed (so an over-long path is not a source of either sentence here). So the planned fix (field_values.write turning "File not found" into an OSError of winerror 32) never fires. A fix is two places: the write's "Error opening file" (not in lock_owners.HELD_WORDS, so explain names no holder), and a journaled write's re-read of a held photo (field_values.read / read_one, whose Unreadable reaches file_changes' error without lock_owners.explain). Not built (more than a bounded fix; which owner rewords a read's failure is a design choice). | open |

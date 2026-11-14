# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | wrong (if built as the plan words it): the plan's "row.before holds each box's old digits per kind in box order" cannot hold with the journal's reads as they are: every before, conflict check, verify and read back of a clip (`file_changes._holds_already`, `_held_or_conflict`, `_verified`, `_read_back`) reads through `field_values.read`, ExifTool with -G -n, which answers ONE value per header kind. Measured on a two-track clip whose second tkhd and mdhd were moved +7 s and +9 s: TrackCreateDate answered the first tkhd, MediaCreateDate the last mdhd; -a with -G collapses them too. An undo writing per-box values would read back one value, fail `_verified` and be put back. Either the three header fields of a clip are read per box by TagPup's own walk wherever they are `wanted` (not `also`, so raw_metadata stays ExifTool's), or the patch reports old digits into row.before and same_read compares by membership. The main session chooses. Confirmed. | open |

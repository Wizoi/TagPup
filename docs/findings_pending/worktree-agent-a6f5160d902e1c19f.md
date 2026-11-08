# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-07 | low: M7 flip cut 2: a record's `writes` (field_values.writes) names no rename or delete, though the server refuses both of a clip (leave_out_clips, recycle_bin.CLIP_NOT_DELETED); the panel holds Smart Rename one and Delete with the turn (`data-writes="rotate"` in web/tagpup/index.html), a decision made rather than copied. Correct for every record today (a photo names rotate and may be renamed and deleted; a clip names neither); it parts if A5 makes a clip's rename or delete allowed without its turn. Then `writes` should name them (Files owner). | open |

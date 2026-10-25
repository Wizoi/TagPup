# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | low: `bake-photos`'s dry run counts a photo ExifTool did not answer for in its deadline (field_values.NotAnswered: a share gone away mid-run, after which every photo not reached is NotAnswered) as "not read", apart from "could not be read", and does not plan it: planned, `baking.plan_photo` would call it UNREADABLE (NotAnswered is an Unreadable), and a share that dropped would read as hundreds of broken files. | decided: a decision of this branch (`tagpup/services/baking_report.py` `_plan`); cut 3's write meets the same NotAnswered through write_fields, which already leaves such photos unread. |
| B-2 | 2026-10-25 | low: the dry run plans each photo with `baking.plan_photo`, which asks the library four questions a photo (its row, its vector, its faces, its detector's look) and stats the file twice (`shown_shape`, `embeddings.stamp_of`): 59,519 photos of photo_index is about 240,000 indexed lookups and 120,000 stats over the share, untimed. | decided: measured by the main session's read-only live run before cut 3 batches anything; a batch per folder (one query a question) is the change if the run is slow, in `baking.plan_photo`'s owner. |

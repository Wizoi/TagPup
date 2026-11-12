# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | data: a photo or clip on a LOCAL drive is sent to that drive's own Recycle Bin weighed only against what the Bin keeps per file (`recycle_bin.keeps_up_to`: MaxCapacity, NukeOnDelete), never against how full it is: a nearly full Bin takes it, and Windows makes room by deleting its oldest items for good, saying nothing. A file from a share is weighed (`copies_not_kept`, with BIN_MARGIN). Not fixed on the bin-full branch (one concern). The fix: weigh a local file's own Bin the same way (`copies_not_kept` on its volume, the bulk Delete's sum by drive) and ask through the same `for_good` path. | open |
| B-2 | 2026-11-12 | low: `bulk_edit._delete`'s docstring and SPEC_TAGPUP_GUI.md (`/api/photo/delete`) said the bulk Delete checks this PC's Recycle Bin "each chunk of 25" before copying; `_cannot_keep` runs once per request, before the first copy. Both now say once per request, and that each file's own delete weighs its copy again. | fixed: the bin-full branch |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: the panel's first delete over a full Recycle Bin asks Windows for the Bin's size twice, back to back. `recycle_bin._one_not_kept` (tagpup/files/recycle_bin.py:664-667) calls `copies_not_kept(size, size, folder)` and, when that answers BIN_FULL, `copies_not_kept(..., fresh=True)`; when the Bin was not asked in this process within BIN_FRESH (the first delete after a start, or after ten minutes), the first call already asked Windows (`_bin_used`: no entry in `_bin_seen`), so the second query repeats one made an instant before. Scenario: the owner opens TagPup, presses Delete on a photo of D: whose Bin holds a few thousand items and is nearly full: SHQueryRecycleBinW runs twice (11.8 s each at 3,353 items, the module's own measurement) before "The Recycle Bin is full: delete permanently instead?" appears, about 24 s where 12 s would do. Counted in a test home (the branch's BinFull setup, `_bin_size` counted): the first `/api/photo/delete` over a full Bin makes 2 queries for a local file and 2 for a share file; the OK resend makes 1. Both Bins are empty today, so not felt now. Fix: `_bin_used` says whether it just asked, or `_one_not_kept` skips the second ask when the first was fresh. | open |

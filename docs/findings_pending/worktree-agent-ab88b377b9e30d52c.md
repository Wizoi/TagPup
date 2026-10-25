# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-24 | low: an index whose every photo is baked under the library's keys still takes its turn on the graphics card at the start (`Runtime.gpu_turn` in `tagpup_cli.py index`), so it waits behind a Suggest run holding the card though it will ask nothing of it (phase 6 cut 1c; `tests/test_an_index_reads_a_bake.py` asks no model of such a folder). | decided: by design: the turn is the existing gate; skipping it for an all-baked run waits for the no-card machine work, cut 4 (main session, 2026-10-24) |
| B-2 | 2026-10-24 | low: a photo whose vector the library kept from a run killed before its batch was saved (its row unread, the vector stamped as the file is) is read again without a decode when it holds no bake, so its row is recorded with no `photo_fingerprints` row; so is every row indexed before cut 1c (phase 6 cut 1c, `tagpup_cli.py index`'s `read`). Nothing reads the table yet. | open: the writer (cut 2) decodes a photo with no fingerprint row, as it must for the rows indexed before cut 1c; decide there |

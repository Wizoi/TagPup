# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | low: a rotation in the app between a bake's plan and that file's write (#1925) also leaves a stale `photo_fingerprints` row: rotation.follower forgets it in the rotate's done transaction, then baking.follower puts the bake's fingerprint of the old upright decode in the bake's done transaction (baking.py:122-134, rotation.py:81). Only the dry run's sample reads the table today (baking_report.py:176), and the next index of the photo puts the new one (search.py:348); found reading the code for cut 3b, not yet tested. | open: assert it in the #1925 scenario test of cut 3b, or have baking.follower put the row only while the photo's vector stamp still describes the file |

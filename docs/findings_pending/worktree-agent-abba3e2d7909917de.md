# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-17 | low: after the collapse of #1545 a single-face `KnownFaces.likeness` (cluster-faces' rounds ask it once a face and a person, 620,624 times on photo_index by #1547) goes through `likeness_table` and costs about 3 us more a call (0.77 s to 0.83 s for 20,000 calls, 150 people of 1 to 400 faces, 512-d, timed on this machine twice); a page's column (`likeness_many`, 29 x 3,000 faces) is 10% faster and the table (20,000 faces x 150 people) 9% faster, from a row's maxima being two additions and one reduceat. On photo_index's count that is about 2 s more in a 76 s loop. The cost is the call's frames, not a per-face loop; the rounds asking a photo's faces of the table at once would remove it, which changes the rounds' caller and is not this collapse (People owner's choice). | open |
| B-2 | 2026-10-17 | low: cluster-faces' plan (the names `resolve` would save, stopped before the write) on one seeded library of 995 faces left one face unnamed in one run and named it in 44 other runs, on the code before this branch (the trunk at 69fc08d8); not reproduced under PYTHONHASHSEED 0 to 30 with fresh temporary folders. Something in resolve is not deterministic at a tie or a threshold edge; unpinned. | not reproduced: 44 runs, hash seeds 0 to 30, same plan each time |

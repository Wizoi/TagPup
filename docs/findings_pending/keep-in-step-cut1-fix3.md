# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-27 keep-in-step cut 1 fix 3 | low: the index's no-model check (tagpup_cli._kept_with_no_model) stops at the first stale photo whose decode shows a different picture; that photo, and the up to three read ahead of it, are decoded again by the index's read (the decodes before it are reused). Holding the mismatching photo's decoded picture from the check to its turn in the index would hold it outside the read-ahead's bound; the double decode is one photo (and the window) in a run that builds the models anyway | decided: one decode again per model run, against a picture held outside the bound |
| B-2 | 2026-10-27 keep-in-step cut 1 fix 3 | low: an index that recorded photos but made no face (the all-kept run: a watcher's restamp) still calls _match_new_faces, which builds Re-examine's named matrix (identify.decided_faces) to match no face, on photo_index seconds of processor work. Not changed here: skipping it (`upto > after`) drops the `New faces:` line the server's queue ends its message with, a change of what the owner sees, the main session's to choose | open |

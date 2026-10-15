# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: docs/SPEC_TAGPUP_CLI.md said the apps' "Name faces from tags" button runs `identities.resolve`; nothing but the `cluster-faces` command (the indexer's child process) and tests calls it (grep, 2026-10-15). The sentence is gone with the command's public section. | fixed: the cluster-faces hand-run surface is deleted |
| B-2 | 2026-10-15 | low: a name that no person was called when clustering read the faces is no longer given a node by the write (set_names used to make one by name): the face is left and counted. 0 of 82,831 listed people in the three libraries have no id, so the arm had no instance; if a run meets one the face stays unnamed until the next. | decided: not handled, no instance |
| B-3 | 2026-10-15 | low: store_faces.set_names now skips a face whose person was merged away or deleted while the run ran (it used to abort the whole save, or, resolving by name, give the survivor's or a new person's name); the faces are counted as left. | fixed: with #1271 |

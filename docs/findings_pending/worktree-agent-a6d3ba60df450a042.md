# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: docs/SPEC_TAGPUP_CLI.md said the apps' "Name faces from tags" button runs `identities.resolve`; nothing but the `cluster-faces` command (the indexer's child process) and tests calls it (grep, 2026-10-15). The sentence is gone with the command's public section. | fixed: the cluster-faces hand-run surface is deleted |
| B-2 | 2026-10-15 | low: a name that no person was called when clustering read the faces is no longer given a node by the write (set_names used to make one by name): the face is left and counted. 0 of 82,831 listed people in the three libraries have no id, so the arm had no instance; if a run meets one the face stays unnamed until the next. | decided: not handled, no instance |
| B-3 | 2026-10-15 | low: store_faces.set_names now skips a face whose person was merged away or deleted while the run ran (it used to abort the whole save, or, resolving by name, give the survivor's or a new person's name); the faces are counted as left. | fixed: with #1271 |
| B-4 | 2026-10-15 | low: "one person once per photo" has one enforcement now, the SQL of store_faces.set_names (what the clustering writer does at the moment of the write). face_tags.guards is not a second copy of it: it snapshots the photo's other faces' persons at plan time so faces_from_tags' write refuses if they changed (an expect-guard on a journaled change), and face_tags.plan decides which faces are proposed. Making _edits use the SQL rule would drop the journal's expect-guard (journal.update records before/after) and change what an undo restores, so it is left; if a third writer needs the rule, lift the NOT EXISTS clause into a store_faces fragment beside ASSIGN. | decided: left as is |

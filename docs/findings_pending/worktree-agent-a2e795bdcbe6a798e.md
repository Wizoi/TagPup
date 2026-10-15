# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: #1357 decision: cluster-faces' summary (`identities.resolve`'s return, the CLI's table) is keyed by the person's tag path, not the leaf: two people called alike are two rows (the column reads "Person (tag)"); a majority-vote tie now goes to the lowest node id, where it went to the first name in sort order. | decided |
| B-2 | 2026-10-15 | low: #1357 decision: `store_faces.set_names` takes a person's id or None only and raises TypeError for a name or a tag path before it writes (its one caller, clustering, sends ids); the name-to-node arm it had (`ensure_node` for a name with no node) had no caller but two tests, which now pass ids. Guard for the People charter's "a write path resolves by id": `test_two_cousins_called_alike.TwoSamsAreGuessedApart` (the charter's guard cell still says "none"). | decided |

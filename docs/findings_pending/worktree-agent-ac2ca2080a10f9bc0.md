# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-17 | wrong: cluster-faces' cluster vote names a face that clustered ALONE (a cluster of one: DBSCAN min_samples=1) in a photo of two or more faces with the photo's one keyword person: its "majority" is its own photo's keywords, so the name rests on which face happened to group alone, with no likeness and no margin asked (the 2b margin gates likeness names only). Counted on a sandbox copy, run to "saving" (2026-10-17): photo_index holds 2,389 such names, 865 of them more like another named person than the one given (margin below 0, everyone named, never a face of its own photo) and 692 under 0.70 like their person; kr-track 53 and renton_parkrun 7, none failing. It names new photos so from now on. Found measuring phase 2c. Which faces get names is the owner's. | open |
| B-2 | 2026-10-17 | low: the comment above cluster-faces' DBSCAN call (tagpup/services/identities.py, "A cosine similarity cutoff of 0.85 ... eps ≈ 0.547") gives a cut and a radius the call does not use: it groups at clustering.GROUPING, 0.885 (0.48 as a distance). Found reading phase 2c. | open |
| B-3 | 2026-10-17 | low: docs/AI_PIPELINE.md phase 2c reads "79% of faces are noise at today's cut": cluster-faces' DBSCAN runs at min_samples=1, so no face is noise; 79.3% is kr-track's share of faces in a cluster of one (photo_index 67.1%, renton_parkrun 76.7%, counted on copies 2026-10-17), and a larger min_samples makes more noise, not less (photo_index 67.1% at 2 to 80.2% at 20). | open |

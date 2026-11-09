# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: People: a third "face in the background" rule, `clustering.background_faces` (under a tenth of the photo's largest face's area AND under `BACKGROUND_AREA`, 2,000 px), asked by Suggest (`services/suggester.py`, no name offered for it) and by cluster-faces (`services/identities.py`, the face keeps no name); it is a speck beside a subject, relative to the photo's other faces, and disagrees with the 2% of the shorter side the photo is shown by (DECISIONS 2026-11-02). Counted read-only 2026-11-08 on stored boxes: it leaves out 7,248 faces of photo_index (12 named), 166 of kr-track, 142 of renton_parkrun; its 2,000 px cap keeps in 14,174, 884 and 61 faces under a tenth of their photo's largest. Like the job's `BACKGROUND_AREA`, cluster-faces works on stored rows and the library stores no picture size, so it cannot ask the share either: one answer with #2738's. Found by the sweep of the background rule's definitions. | open |

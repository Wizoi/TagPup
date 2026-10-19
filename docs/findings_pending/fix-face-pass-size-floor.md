# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-18 | low: a photo whose only detections are under NEW_FACE_FLOOR and take no old face holds no vector, so every run asks the model about it again and counts its small detections again (as for a photo with no face found); a marker would be schema work (DECISIONS 2026-10-15). Count of such photos in the live libraries unknown until the pass runs. | by design, if the owner accepts: same as the none-found photos |

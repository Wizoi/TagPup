# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | low: docs/PHASE6_DESIGN.md says a bake of "over 35 faces" is Extended XMP, but at the size the planner counts (3,072 + 1,730 a face, `services/baking.py` PHOTO_BYTES/FACE_BYTES, within 10% of the texts' length in `tests/test_a_bake_is_planned_from_the_library.py`) 36 faces are 65,352 bytes and fit the 65,536 of bake.MAX_VALUE: 37 or more are Extended. The live "more than 35 faces" counts (293 / 48 / 3) are an upper bound of the Extended photos. | decided: the code decides (`ToBake.extended`, bytes > MAX_VALUE); cut 3 measures the written file and corrects the constants if ExifTool's packet differs |
| B-2 | 2026-10-25 | low: the planner bakes a photo's excluded faces with the rest (an exclusion is the library's, never baked, bake.py's docstring), and reads "looked, none found" from `faces_detected.detected` (any row under the detector) with no face rows, not from a `found = 0` row: a found>0 row with no face rows would plan FacesFound 0. 0 such rows in the three libraries (counted 2026-10-25, read-only; `faces_detected.forget_faceless` removes them). | decided: no branch for a case with no instance; an excluded face is a face the detector found, and the bake holds every face found |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | low: tagpup/store/face_tags.py's module text and Plan docstring stated FaceNet's 0.70/0.80 as the gate and the like_them bands, though the code reads the library's offer and naming cuts (clustering.cuts_of the library's face model, AdaFace's on a new library); reworded to the library's cuts, the #833 measurement kept as FaceNet's of its time. Text only. | fixed (pending) |
| B-2 | 2026-10-25 | low: tests/test_new_faces_are_matched_after_an_index.py's stand-in detector filed its new faces under FaceNet's key while the library (and its seeded earlier faces) is on scrfd-adaface, so the library read no vector of a new face: the "None of the 2 new faces" tests passed with nothing compared, and the two "1 of the 2" tests failed. The detector now takes the new model's key (stand_in_models.NewFaces); all 10 pass, the strangers' tests now compared for real. The "RuntimeError: out of memory" in its log is the test's own mock of _closest_named (the matching-fails scenario), not a real allocation. | fixed (pending) |

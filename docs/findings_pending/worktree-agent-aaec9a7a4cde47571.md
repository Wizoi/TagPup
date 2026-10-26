# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | wrong: importing the new face model imports the old one's package: `tagpup/ml/scrfd_adaface.py` takes `SMALLEST_FACE` and `NotDetected` from `tagpup/ml/faces.py`, which imports `facenet_pytorch` at module level, so facenet-pytorch cannot be dropped and a machine without it cannot run the new model (docs/OLD_MODEL_VESTIGES.md row 5). | open |
| B-2 | 2026-10-25 | low: kr-track holds 175 and renton_parkrun 139 `faces_detected` rows under MTCNN's detector name (photo_index 0), which no library on the new model reads; deleting them writes the live libraries (owner's call). Counted read-only 2026-10-25. | open |
| B-3 | 2026-10-25 | wrong: the new-library stamp of #1974 cannot change `faces.model`'s default alone: the three live switches inserted that row (changes 3244, 342, 1221), so undone, the library reads the default; a library holding settings and no faces.model row must read FaceNet (`BEFORE_FACE_MODELS`), apart from the stamp's default, or an undo would leave the library on the new model while the journal says it was undone. 46 test files fail with the defaults changed (they seed FaceNet vectors through tests/face_rows.py). | open |

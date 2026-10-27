# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-27 | wrong: TagPup's photo panel drew each face's box in the stored pixels over the upright picture /api/photo-file serves, so for a photo of EXIF Orientation 2 to 8 the boxes sat beside the faces and the panel said "stored turned, it may not sit on the face" (owner, 2026-10-27: faces of turned photos are now detected on the shown picture, boxes kept stored). /api/photo-faces now answers `shown_box` and `shown_size` (tagpup.core.orientation.to_upright from photos.box_shape's `turn`, the same one look) and the page draws those; the note goes. The scenario test (tests/test_organize_faces.py ABoxSitsOnItsFaceInATurnedPhoto, all 8 Orientations) and the two page tests failed on the old code first. | fixed: 2cc5f139 |
| B-2 | 2026-10-27 | low: services/photos.box_shape keeps its `turned` key beside the new `turn` only for its two other readers, services/face_pass.py (which another branch is removing it from) and scripts/sandbox.py (`turned` per sampled photo); once both branches are merged, sandbox reads `turn != 1` and the `turned` key is deleted, one statement of the turn. | open |

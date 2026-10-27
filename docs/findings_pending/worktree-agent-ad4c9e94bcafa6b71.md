# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-27 | low: a rotation of a JPEG committed while the face pass is between reading that photo and writing it is not caught: face_pass._as_read compares the row's path, the model's vectors and the faces' boxes, none of which a JPEG turn changes (boxes stay in stored pixels), so the pass then writes a faceless `faces_detected` row for the old picture, or new faces whose crops were cut upright by the old Orientation, after the rotation dropped both. Produced by the owner rotating the very photo a CLI pass is detecting (a window of one photo's detection); 0 rotate changes in photo_index's journal today. Fix would be in face_pass.py (People's file, not this branch's): _as_read also compares the photo row's mtime/size read before the picture. Found by the worker fixing the rotation's crops and record. | open |
| B-2 | 2026-10-27 | low: an undo of a face deletion recorded before a rotation puts back the face's crop as recorded, cut upright by the old Orientation (journal records face_crops with the face): delete a face, rotate its photo, undo the deletion. A TIFF did the same before this branch. No live instance (0 rotate changes in photo_index's journal); the next crop request serves the stale one until the photo is turned again. Found by the worker fixing the rotation's crops and record. | open |

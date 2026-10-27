# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-27 | low: a face crop cached before detection ran on the picture as shown stays as it was cut, sideways for a photo turned by its Orientation, until it is cut again (images.face_crop_of now cuts upright; nothing recuts a cached crop). Live count of such crops not taken. | open |
| B-2 | 2026-10-27 | low: a turned photo the new model already recorded under scrfd-adaface on its stored (sideways) pixels -- a face found there, or 'examined, none found' -- is done by the pass and the index and never looked at again as shown; the detector's name is unchanged ({"model": "scrfd-adaface"}, no stamp, no migration). The count in full is the owner's. | open |
| B-3 | 2026-10-27 | low: the old MTCNN FaceModel (tagpup/ml/faces.py:232) still hands images.face_crop_of a decode as stored, which the cutter now reads as upright: its crop of a photo turned by its Orientation would be cut from the wrong place. No library is on mtcnn-facenet (3 of 3 on scrfd-adaface, counted 2026-10-27); cut B2 deletes the model. | open |

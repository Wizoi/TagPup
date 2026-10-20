# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-20 | wrong (once a library is switched): the grouping radius (clustering.GROUPING 0.885: cluster-faces' DBSCAN and Identify's grid, tagpup.ml.grouping) is one value for every face model, read on FaceNet's vectors (kr-track, 2026-09-24); AdaFace's likenesses of one person sit far lower (the gate's 0.99 likeness cut 0.520 against FaceNet's 0.795), so under scrfd-adaface grouping joins far fewer faces: fewer names by a cluster's vote and smaller Identify groups, never a wrong one. FaceNet's number kept until a reading on the gate's faces (a measurement worker is reading it, 2026-10-20). | People | open |
| B-2 | 2026-10-20 | low: scrfd-adaface's offer cuts were FaceNet's scaled by its naming cuts' ratio (0.455, 0.1025), not read off the gate; at matched precision FaceNet's 0.70 sits between the gate's open-set 0.98 and 0.95 readings. Fixed: the owner chose the pooled open-set reading at 0.98, likeness 0.310 and margin 0.090 (clustering.ADAFACE_CUTS; tests/test_rules_have_one_owner.py). | People | fixed |
| B-3 | 2026-10-20 | low: a decision's cuts are read with faces.model_key(conn) on the reader's own connection, while the named matrix it compares with (identify.named_faces, cached by the faces fingerprint, which holds the model) may have been read a moment before: a switch committed between the two reads compares one model's matrix by the other's cuts for that one request; the next ask rebuilds the matrix (the fingerprint moved). Carrying the model with clustering.FaceRows would close it. | People | open |

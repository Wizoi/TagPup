# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-09 | low: Data: no test undid a 'take the names off faces' (unname bulk, faces/unmatch-bulk) change, though tests/undo_kinds.py gave `AS_FACE_BATCH` to the four face_batch kinds citing tests/test_bulk_faces_and_tags.py. Now `TakingNamesOffMany.test_the_undo_of_taking_the_names_off_puts_back_the_names_the_deciders_and_the_tags` names three faces (a guess and a choice among them), takes the names off, undoes from History and asserts names, deciders and tags; with face_batch.Follower's backward walk made a no-op in a copy of the trunk it fails on the names; the reason in `AS_FACE_BATCH` now cites the unname and re-examine undos. Closes #2793. | fixed: the unname-bulk undo is a scenario in TakingNamesOffMany, cited in AS_FACE_BATCH; it fails when the undo restores nothing (wrote 0 rows: tests only) |

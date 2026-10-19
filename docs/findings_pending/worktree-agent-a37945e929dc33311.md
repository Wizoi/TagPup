# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-18 | low: the merge 3 brief asked that a folder's undo be refused once the owner names a MATCHED face afterwards. The pass's only row for a matched face is its `face_embeddings` row; naming changes the `faces` row, which the change does not hold, so `journal._not_as_left` and `_newer_overlapping` see nothing and the undo goes through: the vector goes, the name stays (harmless; nothing the name rests on is taken). A NEW face named since does refuse it. Built and tested as that (`tests/test_the_face_pass.py`, `test_an_undo_is_refused_once_a_new_face_is_named_and_a_matched_face_named_since_keeps_its_name`); making it refuse would need the change to hold the face row, which the brief rules out. The main session decides: keep (by design) or refuse. | open |
| B-2 | 2026-10-18 | low: `journal.record_rows` adds rows to a change but leaves its summary's `rows` counts at what `record` was handed (none, for tagpup.store.face_batches), so History's row counts for such a change are stale. The face pass uses the new `journal.record_inserted`, which adds the rows and their counts to the summary in the same transaction; face_batches still uses `record_rows`. Not changed here (not in this slice's files). | open |
| B-3 | 2026-10-18 | low: a face pass change's operation is "face pass (<key>) <folder>" with the folder as this machine spells it (native), not in a root's row form, so the same folder on a rooted library run from another machine is a different operation name and `Folder.held` would not find the earlier change. One machine runs the pass today; decide whether the name takes the row form when the CLI (slice B) is built. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-11 | low: with `remove` and `index-faces` deleted, three helpers have only tests as callers: `store/photos.remove` (tests/test_derived_follow_writes.py, test_photo_index_keeps_only_vectors.py, test_derived_writers.py), `store/faces.photos_with_faces` (tests/test_roots_store.py) and `services/faces.replace_detected` (tests/test_every_face_has_a_photo_row.py, test_faces_pending_is_cleared.py). Left: removing them means rewriting those tests to another seeding route; a follow-up deletion. | open |
| B-2 | 2026-10-11 | low: the CLI leaves no record of its own runs (only `index` writes a log, `indexer-<library>-...`; the journal does not say whether a change came from the CLI or a page), so the live use of `compact`, `sync`, `faces-from-tags`, `tags-from-faces`, `tree-fill`, `settings`, `thumbs`, `jobs`, `snapshots`, `history`, `undo` and `roots check` is unknown, and only `remove` and `index-faces` could be deleted on a zero-use count (the owner's decision). The overlap review is a table in the branch report; no other command was deleted. | open |

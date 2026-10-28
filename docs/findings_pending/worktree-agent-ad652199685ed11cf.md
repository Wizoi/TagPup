# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 cut B build 2 commit 3 | low: with identify.NO_VECTOR, the person grid's no_vector and cluster-faces' without_vector deleted, the readers that still keep a face with no vector under the library's model have no live instance either (0 of 197,285 faces lack one, counted read-only 2026-10-28; store.faces.insert files the vector with the face, journaled together): store.faces' VECTOR left join beside WITH_VECTOR / HAS_VECTOR and the "or None" of in_photo, in_photo_with_names, person_page and embedding_row; the suggester's `emb = None if emb_bytes is None` (suggester.py, on_file); baking's FACES_WITHOUT_VECTORS, whose sentence names "a re-index gives a face it holds already no vector" as its cause (no writer found that does: the only delete of face_embeddings is the faces-delete trigger). Not in this commit's brief (baking and the store join are other owners' files); one concept (a face always holds its model's vector) could collapse the three joins into one. | open |
| B-2 | 2026-10-28 cut B build 2 commit 3 | low: decided in this commit: identify._embedding_of no longer refuses a face with no vector (NO_VECTOR deleted); such a face, which nothing makes, would now fail face-matches and New Person with a TypeError (a 500), loudly, rather than with a sentence, by CLAUDE.md "A special case needs a real instance". identify.person_faces likewise scores every row it reads. | open |

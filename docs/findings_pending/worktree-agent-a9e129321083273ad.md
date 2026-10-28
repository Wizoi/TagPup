# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 face-vector guard | low: tests/test_identify_queries.py's `unreadable_embedding_face` files a face whose vector is three bytes, "real bytes but not a vector", to show the unmatched-faces queue still opens. No writer files such a vector (store.faces.insert takes the face model's float32 output), so the test pins a defensive path with no live instance (CLAUDE.md, 'A special case needs a real instance'); the queue's tolerance of it, and the test, are candidates for deletion by the people owner. Left as it is by the guard branch, which refuses only an empty vector at the fixture. | open |
| B-2 | 2026-10-28 face-vector guard | low: decided by the guard branch: a face a test makes with no vector named is given its own (tests/face_rows.vector_of: 512 float32 values of length one from numpy's default_rng seeded with the face's id), not one shared constant row, so two such faces are as unalike as two strangers (cosine near 0) and no cluster or automatch test sees them as one person; an explicit vectorless face is not offered, since nothing makes one (#2112, #2122). Tests that give 4- or 8-value vectors for their geometry keep them. | decided: no instance of a vectorless face to keep |

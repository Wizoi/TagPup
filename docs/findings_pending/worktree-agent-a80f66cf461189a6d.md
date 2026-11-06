# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-06 | low: retiring a photo model's vectors (`services.retire_photo_vectors`) counts only `embeddings` in its plan and its chunks' bytes (`embeddings.key_totals`; `_write`'s `done.bytes` from `next_under`'s lengths), while the trigger `embedding_frames_go_with_their_vector` (migration 38) also deletes the clip's frames 1 to 4 under that key: the MB it reports freed would be short by the frames' bytes. Not one line: `done.bytes` comes from `next_under`, which reads `embeddings` alone. 0 frames are held in the three live libraries today (all at 37, no `embedding_frames` table; counted read-only 2026-11-06), so no report is wrong yet; it becomes so when the indexer writes frames (M1). | open |
| B-2 | 2026-11-06 | low: the journal follows the cascade `(embeddings, embedding_frames)` by `photo_id` alone (a cascade is followed by the parent's first key column, `journal.CASCADES`), so a journaled delete of a single vector would record every model's frames of that photo, not the vector's model's only. No writer journals a vector delete except with its photo (`embeddings.forget`, a retire's `delete_between` and a replacement by `put`/`put_by_id` write unjournaled), so no branch is written for it (CLAUDE.md, "A special case needs a real instance"); the photo's own cascade `(photos, embedding_frames)` records them all, as it should, and an undo puts back each frame once (tested in test_embeddings_follow_their_photo, AClipsFrames). | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 | low: tests/test_folder_watcher.py (the #2174 fold, a short walk says no progress line) was vacuous: `watcher._run(library, Dunes)` was refused before any walk (the library held nothing under Dunes), so `assertNotIn("folder(s) and", ...)` passed on a refusal and `syncing is None` on `_run`'s finally; the closing line ("looked at 1 folder(s) and 1 photo file(s)") also contains that substring, so on a real walk it would fail. Found in review. | fixed: 131fed68 |
| B-2 | 2026-10-30 | low: tagpup_cli `_resolve_directories` exits 1 with the share-away sentence for a share away at the canonicalised first place, `_folders_now` (the click callback) exits 2 with the same sentence: one refusal, two codes. Found in review. | fixed: ccaa94c6 |
| B-3 | 2026-10-30 | low: tests/test_sync.py (#2229) queued a fake with changed=0 and every folder `invalid`, a shape jobs/indexing.py `_enqueue` never makes (it refuses when none is valid). The real one is mixed: one folder queued, one invalid. Found in review. | fixed: 88665ba8 |
| B-4 | 2026-10-30 | low: a share that drops mid-queue in a mixed batch (some folders queued, the share's folders invalid) ends the daily sync `ok`: SHARE_AWAY is set only when no folder was valid and the share is away, so the hourly retry (#2103) does not fire; the files are found new again at the next daily sync and the sync's `in_step` stays False. Found in review. | decided: self-heals at the next daily sync; no instance counted |
| B-5 | 2026-10-30 | low: #2143's deletion of the unreadable-vector test left nothing guarding that the identify queue (store.faces.identify_candidates) reads no vector (a join of face_embeddings read 380 MB of BLOB on photo_index to answer a question about integers). Found in review. | fixed: 819a936e |

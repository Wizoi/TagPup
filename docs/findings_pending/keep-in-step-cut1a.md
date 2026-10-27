# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-27 keep-in-step cut 1a | wrong: a file written outside after its vector was made was re-read by sync (its row stamped as the file) and its vector left with the old stamp; the indexer then called it current and skipped it, and bake-photos said CHANGED "index it again", which never settled it (74 such in photo_index, every one with no fingerprint row; 0 in kr-track and renton_parkrun; counted 2026-10-27). Fixed: sync counts it `to_check` and queues its folder; search.current asks photo_fingerprints.same_picture; plan_photo says NO_VECTOR without a matching fingerprint row. Tests failed on the old code first (test_sync APictureToCheck, test_index_startup_reads, test_a_bake_is_planned_from_the_library). | fixed in branch keep-in-step-cut1a |
| B-2 | 2026-10-27 keep-in-step cut 1a | low: 1a alone sends the 74 (and any later metadata write outside) to the index, which computes their vectors again on the GPU: the keep-and-restamp of a matching fingerprint is cut 1b's indexer loop. Merge 1a with 1b, or before any run of sync --apply on photo_index. | open: cut 1b |
| B-3 | 2026-10-27 keep-in-step cut 1a | low: faces_detected.unexamined and search.current's `unexamined` have no caller in 1a (the indexer passes nothing); cut 1b's indexer loop is the caller (223 photos of photo_index, counted 2026-10-27). | open: cut 1b |
| B-4 | 2026-10-27 keep-in-step cut 1a | low: decided by the worker: a picture to check in a folder sync holds back (a new file there may be a copy of a missing photo) is counted `to_check` and not queued until the ambiguity is settled, as the folder's new files are; counts `new_folders` stays the folders of new files, reveal/work `new_folders` adds the to-check folders. | decided: worker, for review |

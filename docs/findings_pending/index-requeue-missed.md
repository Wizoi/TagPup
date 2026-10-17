# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | wrong: `IndexQueue.cancel` (everything or by name) never clears `_missed` (#1565's branch), so a folder the owner cancelled while it was being indexed and asked for again is indexed once more when the running one completes (about 200 s of clustering refusing face writes); SPEC_TAGTUNER.md's index-cancel says cancel drops what has not started. And `_queue_what_was_missed` pops the entries and starts them in two lock holds, so a cancel between them misses the entry. Integration owner's review of #1565. | fixed: 864112ba; `test_a_folder_cancelled_while_it_was_asked_for_again_runs_once` failed on the old code |
| B-2 | 2026-10-16 | low: the re-run of a missed folder loses its `parents`: `start` reads `runs.held()` on the worker thread, where nothing is held, so the re-queued job has parents [] and the Activity page shows an index nobody asked for; its status says only "Waiting to be indexed..." with no reason. Integration owner's review of #1565. | fixed: 864112ba; `test_the_rerun_is_part_of_the_sync_that_asked_and_says_why` failed on the old code |
| B-3 | 2026-10-16 | low: `tests/test_jobs_indexing.py`'s WRITE regex (the waiting list and the worker are written only under the lock) covers `_pending` and `_runner` only, not the new `_missed`. Integration owner's review of #1565. | fixed: 864112ba |
| B-4 | 2026-10-16 | low: docs/SPEC_TAGTUNER.md (index-start) and docs/SPEC_TAGPUP_GUI.md (index-start) say a folder already running is only reported; since #1565 it is reported and run once more when the run completes. Integration owner's review of #1565. | fixed: bdcadcc3 |
| B-5 | 2026-10-16 | low: a sync's `together` batch of N folders, with a whole-library sync asking for them again mid-run, re-runs all N once (a scan each; the stamp rule skips the unchanged files). Integration owner's review of #1565. | decided: by design: one scan per folder, once, and not a loop (the re-run is repeated only when something asks again during it); splitting the batch by which folder received files would need the sync to say which, which nothing records. Written in docs/domains/files.md, "By design". |

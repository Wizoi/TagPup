# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | low: a whole sync the folder watcher runs shows twice in the Activity page's What happened: its run of the `sync` job (`job_runs`, recurring.sync_whole) and its `sync-whole` record (`job_runs.record_sync`), with nothing linking the two, so the timeline cannot pair them (main session's decision Q3 of B4a: not fixed in B4a). | open |
| B-2 | 2026-10-29 | low: the replies test of the Activity page (tests/test_activity_page_replies.py, commit 88676cc9) ended its index runs without the `run`/`folders` the queue keeps from the claim (an ended index named no log) and "failed" the stuck index with `job_runs.failure(sentence=...)`, which returns None, so it ended done; fixed on the branch (the replies now say the index failed, in Needs you with no Run it now). The fixture also changed every run (the journal's `dates.recorded()` reads `time.localtime()`, not `time.time`; the run tags' process-wide count; `logs.run_log`'s pid); now pinned and made twice in two homes. | fixed: on the branch |

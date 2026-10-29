# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | low: one sync has two tags in two sections: `/api/activity/sync`'s `last_whole.run` is `runs.sync_tag` (the record's), What happened's paired entry is `runs.job_tag` (the sync job's run), and `activity.index_runs_under` keys the whole sync's index under the job tag only, so the Sync section's "Logs for this run" lacks the indexer's log that What happened's shows. Pre-existing, left by the integration owner's review of the What-happened tree branch. | open |
| B-2 | 2026-10-29 | low: the id of the journal change a run applied (`changed["change"]`, from a Result's `details["change"]`: the sync, maintenance, naming faces) was said on the Activity page as a count ("change 2") in the Jobs section and in a job entry of What happened, a sibling of the sync record's id; `job_runs.shown_counts` leaves out both links (`job_runs.LINKS`). The Jobs scenario test failed on the old code with `'record': 2` and `'change': 2` among the counts. | fixed: this branch, "activity: a run's counts are job_runs.shown_counts, never the link to its record or change" |
| B-3 | 2026-10-29 | low: `tests/test_activity_routes.py` `TheWatcherSays.test_its_roots_when_it_last_heard_and_its_last_sync` raced once in nine related files run together (`found["syncing"]` still the second folder's sync): the fake sync appends to `synced` inside the sync, before the watcher clears `syncing`, and the test reads `status()` as soon as it sees the append; passed 3 of 3 alone. A flaky test, not a product fault; it waits on the append, not on the sync's end. | open |

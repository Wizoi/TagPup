# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | wrong: a sync whose walk saw the new files' folders but whose index queue then refused them (the share stopped answering between the walk and `IndexQueue.start`: refused with the share's sentence and `SHARE_AWAY`) drops the refusal: `services/sync._sync` reads only `outcome.changed` and `outcome.skipped`, so the run ends `done` (recorded not in step, `queued_folders` 0) and nothing says the share went away; only an exception from `queue` is a failure. | fixed: a refused queue is a failure of the sync carrying `SHARE_AWAY` (`tests/test_a_failed_run_says_what_to_do.py`, failed on the old code: the run ended done) |
| B-2 | 2026-10-29 | low: two rules of "a job is failing": `recurring.overview`'s `failing` counts an `abandoned` newest run as failing, while what is due (`job_runs.latest`'s "latest that ended", `_last_ended`) reads only `done` and `failed`; the Needs-you list B3 adds must take one of them, and the other then is a rule written twice. | fixed: `failing` and Needs you read one rule, `job_runs.needs_you` (abandoned neither adds nor settles) |

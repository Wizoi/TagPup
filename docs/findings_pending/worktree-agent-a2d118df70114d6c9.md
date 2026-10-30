# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | low: a sync run by the CLI writes its start and end lines (`services/sync._said`) to no file in `data/logs` (`tagpup_cli.py` logs to a file only when `TAGPUP_LOG_TO` is set), so the Activity page's "Logs for this run" for a CLI sync stays empty. | open: Integration |
| B-2 | 2026-10-29 | low: #2181's server cause is partly answered: a sync run by the server now writes its own begin line and end line under its run tag (`_said`), so a running sync's log is no longer empty from its first moment. What remains of #2181: the owner's job and the run that showed an empty log were not reproduced, a run whose lines are not tagged is not known to exist, and the page's reason line for a log with nothing to show is B5's page half. | open: Integration (B5b) |
| B-3 | 2026-10-29 | low: two retention owners in `data/logs`: `tagpup.logs.retire_run_logs` keeps the last 5 run logs of each library and kind (the indexer's, at the server's start), and `tools/run_tests.py` keeps its own last ten test-run logs; they keep different things (a library's runs; a developer's failed test runs), so one owner for both would join two rules that share nothing. | decided: by design, two owners; `retire_run_logs` leaves `run_tests-*` logs to `tools/run_tests.py` (`logs.group_of` names them kind `run_tests`) |

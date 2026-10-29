# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | low: the naming job's plan ended its run through `self.run_id`, which a Yes reassigns to the apply's run in `confirm`, after the plan set `asking` and before its end was written: a Yes in that window ended the apply's run as the plan and left the plan's `running` (`tagpup/jobs/naming_faces.py`, `run_plan`). The window is a lock release to the next statement, under a human click after the page has polled `asking`; no live instance, no test (it needs a thread paused between two statements). | fixed: Name faces: each phase is a recorded run (the plan's end goes to the run the plan's thread holds) |
| B-2 | 2026-10-29 | low: the naming job's give-back ended a run it could not discard (failed, "not begun"); `job_runs.discard` refuses only a row that is no longer this process's `running` row, which nothing of this process may end either, so the end could never be written: deleted with no live instance (CLAUDE.md, "A special case needs a real instance"). | decided: Name faces: each phase is a recorded run (a give-back that cannot discard is logged) |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 | low: Integration: `launcher.make_way` ends a server idle but not letting go (IDLE_PATIENCE, owner 2026-11-19) on a `GET /api/server` reading requests 0 and busy []; a server on the fixed drain has taken work again by then (a drain past its deadline resumes), so a page's write that arrives between that status read and `end()` is cut. A server on the code before stays closed while it hangs and refuses writes, so it is not exposed. user action: a save in a page in the instant an install ends an old server whose background stop hung (1 hang in about 10 installs this month). | open |
| B-2 | 2026-11-19 | low: Integration: decided while building the bounded drain: `runtime.Background.stop` now runs every task's stop at once, each on its own daemon thread joined to the one deadline (was: one after another, the later ones given what was left, #721's test rewritten to say so), and `lifecycle._drain_until` counts a task Background.stop names (stop not returned, or returned False) as `<name> (still stopping)` when it is not already busy; before, a stop returning False with the task not busy let the drain answer drained. Parallel so a hung stop does not starve the others' timeouts; counted so a drain never answers drained beside a stop still running. user action: an install while a folder watch is on a share that stopped answering. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 | low: `polled()` in tests/frontend/activity-page.test.mjs waits without a deadline (a `while` on node's own timer), so a regression that stops a route being asked hangs the whole `node --test` run instead of failing it (`--test-timeout` fails the test but the loop keeps the process alive); the B4a 2d tests use a bounded `settled()` beside it. | open: make `polled` use `settled` the next time the file is touched |

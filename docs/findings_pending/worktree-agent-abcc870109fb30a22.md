# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-24 | low: tests/frontend/progress-polling.test.mjs's six "stops polling" checks waited 200-300 ms after the last poll, under the pollers' 1 s (index) and 1.5 s (Suggest) intervals (web/tagpup/suggestions.js), so no poll could land in the wait: all 11 tests passed with every clearInterval in suggestions.js removed (a page whose pollers never stop). On the test's clock they now wait TWO_POLLS (3.2 s, no real time); with the pollers never stopping 6 fail, on the real page all pass. user action: none, a test gap (cut 7 of #3375 found it while moving the file onto the test's clock) | fixed: 98ee5c7d |

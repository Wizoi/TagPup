# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-16 | low: the page harness (tests/frontend/harness.mjs `codeOf`) empties quoted strings but not template literals, so a word in a template of a page module that another module declares at top level (`check`, declared by web/common/validate.js) fails the load with "uses check ... without importing it" though a browser runs it; the folder Refresh's sentences hit it ("no full check was started: ${why}") and were written as joined quoted strings instead. user action: a page module's template literal holding such a word. | open |
| B-2 | 2026-11-16 | low: the folder Refresh's sentence for a share that did not answer (`counts.folders_not_looked_at > 0`, missing 0, whole "not needed") is not among the plan's sentences; without one the page said "This folder is in step." of a folder it could not look at. Worded once in `folderRefreshSaid`: "This folder could not be looked at (a share that did not answer, or a folder that would not list); nothing was changed." user action: Refresh on a folder of a share that is away (plan, How it fails). | decided: said, page test "a share that did not answer is not 'in step'" (fails on the old code with every V21 test) |

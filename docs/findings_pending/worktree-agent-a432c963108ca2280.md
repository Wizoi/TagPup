# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 | low: an app made without a runtime keeps two arms no server reaches (P4 step 1): `Lifecycle(runtime=None)` reads no index queue (`_queues` None, `under_way(queues=None)`), and `activity_routes._models` answers None; `tagpup_web` always builds one Runtime, so the only instance is a test app, while `/api/activity/now` and the routes that queue an index now need `RUNTIME` (a test app that asks them is given a real `Runtime()`). Either give every app a runtime (`create_app` builds one when none is given) and delete both arms, or record the test-only arm as by design. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 | low: an app made without a runtime keeps two arms no server reaches (P4 step 1): `Lifecycle(runtime=None)` reads no index queue (`_queues` None, `under_way(queues=None)`), and `activity_routes._models` answers None; `tagpup_web` always builds one Runtime, so the only instance is a test app, while `/api/activity/now` and the routes that queue an index now need `RUNTIME` (a test app that asks them is given a real `Runtime()`). Either give every app a runtime (`create_app` builds one when none is given) and delete both arms, or record the test-only arm as by design. | open |
| B-2 | 2026-10-30 | low: the real page server (contract row 22) answers 500 to TagTuner's indexing poll (`GET /<library>/api/folder/index-active`, tagpup/web/tuner_routes.py) and to `/api/activity/now`: tests/page_server.py builds both apps and their Lifecycle with no Runtime and the routes dereference `state.runtime()`; the page tolerates a failed poll, so tests/test_pages_against_the_real_server.py still passed while the server logged 'a request failed' tracebacks: a guard degraded silently. | open |
| B-3 | 2026-10-30 | low: charter row 15's claim 'two runtimes in one process share none' (.claude/agents/tagpup-owner-integration.md) names no guard, and `ASKED_THROUGH_THE_RUNTIME` in tests/test_background_work_is_waited_for.py checks only that `under_way` has a parameter named `queues`, not that it reads it. | open |

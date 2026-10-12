# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-11 | low: against the real server, naming a face from a suggestion in TagPup's Organize view sends `POST /api/taxonomy/create` for the person's path before the tag save, though the tree holds the node (`resolveTagOrPerson` posts it for every typed or suggested path with a `/`). The server answers a no-op, so nothing is wrong, but it is one request and one tree reload (`loadTaxonomy`) per naming; the hand-written fake in organize-faces.test.mjs answered people as bare names and never sent it. The real test now expects it. | open |
| B-2 | 2026-10-11 | low: decision, not copied: a page test against the real server gets one library per test from `/__seed/<name>` and a folder of its own beside it, since two libraries of one home holding one folder shared the page's folder cache and made the second test wait forever for faces that never came. A test needing two libraries on one folder is the scenario of tests/test_scenario_two_libraries_share_a_folder.py, not this mechanism. | decided: built in the page-server commit |
| B-3 | 2026-10-11 | low: the 503-while-migrating answer (`X-TagPup-Updating`) is carried by the bridge (status and headers of a reply are kept) but no real-server page test makes a library migrate under an open page; `tests/frontend/scenario-write-through-a-server-update.test.mjs` still answers 503 from a fake. Row 21 keeps that guard. | left as is: a migrating library needs a second schema version to seed, which the freeze does not allow a test to invent |
| B-4 | 2026-10-11 | low: the real-server page tests are not reached by `tools/run_tests.py` unless through `tests/test_pages_against_the_real_server.py` (scenario tier, about 30 s, needs node on the PATH and fails without it); the frontend suite proper is still `node --test tests/frontend/*.test.mjs`, where the real-server describes skip themselves. `tools/affected_tests.py` does not map a changed page module to that Python file. | open |

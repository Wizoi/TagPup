# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: Apps-A regrouping: the inventory called tests/test_activity_routes.py `ThisPcOnly` a duplicate of test_web_app's another-host tests. It is not: it guards `security.from_this_pc(request.remote_addr)` in activity_routes (the peer address), web_app's tests guard `security.refusal` (Host and Origin headers). Breaking security.py's `abort` leaves ThisPcOnly passing; breaking `from_this_pc` leaves web_app's passing. Kept, nothing deleted. | open |
| B-2 | 2026-10-15 | low: Apps-A regrouping: AReplyThatIsNotHttp (test_install_hands_over) was not folded into Choices: Choices.setUp patches `launcher.ask` and `launcher.drain`, the two functions that test runs for real against a patched `urlopen`, so a fold needs `mock.patch.stopall` or a second setUp and saves about 8 lines. Left as its own class. | open |
| B-3 | 2026-10-15 | low: Apps-A regrouping: the "two duplicate tests in test_library_view_routes' successor" could not be identified (test_library_view_routes.py is gone; Views-A lifted it into test_library_views_scenarios.py, tests/test_thumbnail_routes.py kept). Not touched. | open |
| B-4 | 2026-10-15 | low: Apps-A regrouping: test_the_reaper_is_quiet took 3.0 s in tests/.durations.json and 0.33 s alone; its only wait is one child process import (`processes.run(..., timeout=60)`), no sleep. The 18.6 s seen under load is process start-up on a busy machine, not a wait to bound; it now sits in test_tests_have_homes_of_their_own with its 60 s timeout. test_run_tests_keeps_failures, test_add_findings and test_patch_tool share no harness (three different tools, each with its own fixtures) and were kept apart. | open |
| B-5 | 2026-10-15 | low: Apps-A regrouping: dropping the `Updating` raise in web/libraries.py by `pass` (not `return library`) makes test_server_migrates_at_start::ALibraryBeingMigrated take about 4 minutes before it fails (24 parked requests, each `join(10)`); `ask_all` could join against one deadline. Not changed (a failing run is the only slow one). | open |

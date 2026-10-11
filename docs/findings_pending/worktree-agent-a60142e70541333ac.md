# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-11 | low: the supervisor's token path outlives it: `Lifecycle(token=)`, `Lifecycle.allows`, the `X-TagPup-Supervisor` header, the `supervised` field of `/api/server`, `/api/activity/server` and the launcher records, and the `ALWAYS_ON` kind in `launcher.kind_of`/`make_way` are all fed by nothing now (tagpup_web passes no token). Left because the brief says not to touch the drain or the hand-over; the tests of the drain (test_server_drain, test_launcher_hands_over) still exercise them. A later deletion can take them and the spec lines at once. | open |
| B-2 | 2026-10-11 | low: findings 352 and 353 are about the deleted supervisor's loop and `start_in_background`; nothing to fix now. | decided: close with the supervisor's deletion |
| B-3 | 2026-10-11 | low: `%LOCALAPPDATA%\TagPup\TagPup Background.pyw` still exists on this machine and imports the deleted `tagpup.supervisor`; nothing runs it (no Startup shortcut, no scheduled task, no launcher names it). The next `install_app.py --apply` retires it (RETIRED_LAUNCHERS, only a file bearing the installer's line). Until then running it would fail with an import error. | open |
| B-4 | 2026-10-11 | low: CLAUDE.md and `.claude/agents/*.md` still say "the supervisor" ("anything touching sync, the journal, the supervisor or writes"); not edited by this branch. | open |
| B-5 | 2026-10-11 | low: the Activity page's tab is still titled "Always on" (web/activity/index.html, SPEC bullets "Always on"); it now shows the server's version, start time, background tasks and models only. A rename to "Server" is a one-line change left for the next Activity edit. | open |

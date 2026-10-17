# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-17 | low: #1543 changed what a journaled write (tag, rename, rotate, delete) does with photos on a share whose server answers WinError 53 at once -- each left out with file_changes.AWAY_WHY after one stat for the whole selection, no ExifTool read, no row touched, no journal row -- and no scenario test holds it (the asleep-share write test covers a share that hangs, not one that says not found at once). Files owner's review of #1543. | fixed: 7d8a4c68 (fails on the trunk's shares.py: 4 stats, each photo read) |
| B-2 | 2026-10-17 | low: ownership: "a share that says not found at once is away" is learned only in `shares.stamp_of` (its own arm marking `_away`); `folder_state` answers "unanswered" on the same evidence without marking the share, so sync's per-folder looks each ask the server again. The one place that learns it is `_share_answers`: answering False marks the share away for AWAY_SECONDS, both lookers share it. Files owner's review of #1543. | fixed: d5284716 (its scenario test fails on the commit before) |
| B-3 | 2026-10-17 | low: `shares.Walk.stamp_of` names an away server by `photo_path.split("\\")[2]`, a path parsed by hand for display beside `share_of`, which owns what a share is. Files owner's review of #1543. | fixed: d3c30232 (the walk test fails on the commit before) |
| B-4 | 2026-10-17 | low: `_share_answers` kept an away entry still inside its 30 s (shares.py ~136), an arm no look reaches (bounded answers from the memo; only a join-path or race look could, sliding the window by milliseconds) and no test reached; the test docstring claimed it was covered. Quick review of the #1543 fix round. | fixed: the arm is gone (it always marks), the docstring says where the window is held |

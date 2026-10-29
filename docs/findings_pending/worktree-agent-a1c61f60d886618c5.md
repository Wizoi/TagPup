# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: a row the second walk kept (`sync._gone_again`: its file moved during the first walk, found and paired in the second) is reported missing and not moved in this run, and the run can record "in step" (`in_step` does not count missing rows); its move is applied by the next sync (the watcher's sync of the folder it landed in pairs it, `_missing_elsewhere`, or the next whole sync). Decision made, not copied: the second walk only vetoes a removal, it adds no edits (no second plan); no new count. | open: Files |

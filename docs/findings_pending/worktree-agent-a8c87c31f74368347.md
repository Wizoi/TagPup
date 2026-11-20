# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 | low: the search box's Clear clicked twice before the first Back has arrived calls history.go(-back) twice (search.js clearSearch reads searchBack from the place it is still on), so the page goes two views back past the one before the search; user action: two quick clicks on Clear (or Enter twice on an empty box). From the advanced filtering cut 1b brief | open |

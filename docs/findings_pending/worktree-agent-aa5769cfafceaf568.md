# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: the Windows-Tags move skipped whole a clip naming a branch of the tag tree (#2817's skip); owner 2026-11-12: a group tag is legitimate on a photo (#986), the leaf rule is about people. A branch now moves like any tag; the non-person-under-People skip stays. The tests that changed failed on the old code first. Reverses #2817's skip. | fixed: 35e9d711 |

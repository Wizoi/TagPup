# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: `.claude/hooks/plan-first.mjs` names an owner by the first word of each part of a `Domain:` value (split on `and`, commas), so `Domain: Files / Media` or `Files & Media` counts one owner. Not matched: no brief of that shape is known; the split would add `/` and `&`. Found while building #2927-#2929 (tests added first, failing on the old hook). | open |

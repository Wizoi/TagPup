# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-16 | low: Tag writes: one file named twice in one bulk-tags `changes` body under two spellings (a drive letter in two cases, or / for \) was keyed by the string as sent in `_tag_changes`, but by `paths.key` in `tagging._change_each`, which kept the last entry: the first entry's tags were lost without a word and the reply reported both written. live: 0 (user action: none through the real writers; the page sends one entry per file). | fixed: 3597bdf1 (tests/test_a_chunked_tag_write_is_one_line_of_history.py; fails on the old code first) |
| B-2 | 2026-11-16 | low: Tag writes: a `changes` entry whose `add` and `remove` overlap. live: 0 (user action: none; the page sends one entry per file). | decided: no live instance, the page sends one entry per file; remove wins when add and remove overlap (the same rule as the old body) |

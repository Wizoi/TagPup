# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: the merge-budget guard's "one-off" is a decision, not copied: a deleted one-off is a `scripts/` program with a `__main__` block whose text holds `--apply` (a measuring script goes freely), and a script-only service is a new `tagpup/` module that, at the branch tip, only `scripts/` name as a word (tests aside), not transitively (`tagpup/files/clip_convert.py`, named only by the Convert service, is not flagged itself; the program beside it refuses that merge). The `<name>: dry run 0 left` line is the merger's statement of a count, not a run the hook makes. Replayed on the 224 trunk merges since 2026-10-23: 4 judged differently (273b7e7a, e67dbf9f, c65440c9, fe91c9a2), all the retro's two shapes. | decided: the hook reads git, not the libraries; a count it cannot make is stated in the message as the retro's action asks |

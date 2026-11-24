# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-24 | wrong: the fast tier fails on the trunk (ad51dd86): cut 3 part 2 (fb8fdcf8) made tests/test_batches.py's `wait_until` the one in tests/waiting.py (condition, timeout, interval, what), but its importers still pass what as the second positional argument, read as the timeout: tests/test_batch_strip_replies.py:103 and :145 (`wait_until(cond, "the second batch queues")`, TypeError float + str, failed again alone in run_tests --fast) and tests/test_tag_tree_edits_are_batches.py:108 and :149 (`B.wait_until(cond, "the rename waits")`). Fix: `what=` at the four calls; a guard could refuse a str as wait_until's second positional argument. Not fixed on the 3B branch: the files are not this worker's. | open |

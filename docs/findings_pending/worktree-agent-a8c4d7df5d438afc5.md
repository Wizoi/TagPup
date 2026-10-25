# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | wrong: `baking.plan_photo` read a stat failure (`embeddings.stamp_of` None: a file deleted outside the app, a drive or share away) as CHANGED, advising "index it again" (false for a share that is away), and the stat was unbounded. | fixed: baking: a file that cannot be reached plans unreadable, not changed; the stamp is shares.stamp_of's bounded look (test failed on the old planner: CHANGED) |
| B-2 | 2026-10-25 | low: `plan_photo` took the fingerprint, the costliest read (a full upright decode), as an eager value, so the caller decoded before the library's checks. | fixed: baking: the fingerprint is a function called after the library's checks, like held and shape |
| B-3 | 2026-10-25 | low: the lazy-open test (`test_a_photo_the_library_refuses_is_not_opened`) did not fail on a planner whose caller decoded first. | fixed: baking: the fingerprint is a function called after the library's checks, like held and shape (the test now passes a fingerprint callable that fails the test when called, for each refused and unreachable photo; failed when the helper was shaped as the old caller) |

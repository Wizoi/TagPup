# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 share fix part 4b | low: the brief had `Watcher._note_whole` keep the earliest wall time when a whole sync is wanted twice before it runs; a run that started between the two wants would then cover the second, which it did not see (an overflow or a folder back during the daily walk). Built as the later of the two instead (an extra walk at worst, never a missed change). | decided: `watching.Watcher._note_whole`, max of the two |
| B-2 | 2026-10-28 share fix part 4b | wrong: with a whole sync waiting wanted since an earlier time (a folder back from a share found not answering at that time), a photo copied in before it ran was cleared with the folders in `_due` and the whole sync told "not due" by the catch-up that covered the return: the photo was synced only by the daily job. Not on the trunk: a hole in this branch's own design, found before commit. | fixed: `Watcher.notice` raises a waiting whole sync's `noted` to the notification's time; test_one_walk_of_a_library_at_a_time's third scenario fails without it |
| B-3 | 2026-10-28 share fix part 5 | low: `web/activity/sync.js` says "share not answering" without a time when `away_since` is null: the share answered between the root's look and `shares.away_since` (another look heard it wake, `shares._answered`), the one interleaving that leaves it null. | decided: the badge says it without a time |

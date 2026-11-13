# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: Files: `clip_convert.removed` swallowed a "not found" from its remove of a conversion's unfinished copy, so a copy on a share that fell asleep was said removed (None), never TEMP_LEFT; a sibling of #2781 that moving the ask into `shares.bounded` could not reach, since the call never raised. | fixed: commit "shares: a look's not-found asks its share in bounded itself, for every caller" -- the remove raises, and a not-found the share answers for is the copy gone; tests/test_a_share_asleep.py AShareThatFallsAsleepMidLook test_a_converted_clips_unfinished_copy_on_a_share_asleep_is_not_said_removed failed on the old code (TEMP_LEFT != None) |
| B-2 | 2026-11-12 | low: Files: `roots_sample.reach`'s look (`_probe`) answers a share root that is not there by `os.path.isdir` ("anchor", no_drive) and never raises "not found", so the ask now in `shares.bounded` (#2781) does not reach it: a probe that timed out and then ended "anchor" (a server asleep) still takes the share as back. Not changed here: making it ask turns a share's "no_drive" ("Not changed. ... a share that is away") into "away" on the Roots page, a message the owner sees. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | low: the poster brief said a clip on a share that does not answer is served LAST_KNOWN "as for photos"; a photo there is answered Unavailable (503, try again) after one bounded look (shares.reachable_stamp), and LAST_KNOWN only when its file is not there. A clip now does what a photo does: 503 on a share asleep, no ffmpeg started, LAST_KNOWN once its file is gone (tests/test_a_clip_shows_its_poster.py) | decided: the photo's rule, one statement for both kinds (reachable_stamp) |
| B-2 | 2026-11-05 | low: a poster past thumbs.POSTER_SECONDS (15 s) is remembered as UNREADABLE by the clip's stamp for the server process, as a clip ffmpeg cannot read is, so a share slow for a moment leaves that clip a placeholder until the file changes or the server restarts; not remembering it would start a 15 s ffmpeg run for each card of a clip that is always too slow. `thumbs warm` (its own process, the CLI) makes it at a later run | decided: remembered; warm is the retry |
| B-3 | 2026-11-05 | low: the thumbnail's stripe lock (64 stripes by id) is held while a poster is made, up to POSTER_SECONDS plus the wait for one of the 3 ffmpeg turns, so a photo whose id shares the stripe waits behind a slow clip; the same holds for a slow decode of a photo on a share today. Not measured on the live library | open |

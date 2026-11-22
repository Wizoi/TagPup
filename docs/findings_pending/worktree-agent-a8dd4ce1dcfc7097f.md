# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-22 photos-written cut 3 | low: a single save (the six `postPhotoMetadata` callers, rotate, Smart Rename of one) names no photos on its write-queue entry (`entry.photos`), so the page reloaded or the library switched while one is out forgets no kept scan holding it (`edits.js` pagehide, `write-queue.js photosOut`); opened again within 30 minutes the folder shows the record from before the save, and a save from it is refused 409 (not written wrong). As before this cut; cut 4 sets `entry.photos` in `queueWriteOf` (it knows the path). user action: reload the page while a title save is out | open |
| B-2 | 2026-11-22 photos-written cut 3 | low: `.claude/agents/tagpup-owner-integration.md` contract row 23 still says a write reaching every kept scan is "a Time Shift today, the other writers by the plan photos-written"; tag writes reach them now, and the page going with a write out forgets them (`photosGone`). The worker may not edit the charter. user action: an owner review reads row 23 | open |

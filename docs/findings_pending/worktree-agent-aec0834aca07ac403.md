# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-22 | low: the page reloaded, or the library switched (a page load), while a Time Shift is out: its reply is never taken, so the kept scans holding the shifted photos (the folder's, those above it, a subfolder's) keep the old dates and stamps for up to 30 minutes; opened again they show the old dates, and a save from one is refused 409 (not written wrong). Tag writes forget such scans on `pagehide` (plan photos-written, cut 3); a shift does not. user action: switch library during a Time Shift. | open |
| B-2 | 2026-11-22 | low: decision, photos-written cut 2: a library view open when a Time Shift's reply comes takes it (each photo of it the view lists takes its stamp and new date, in place, not re-sorted: a view's order is its query's), where before a view opened after the click was not touched and kept the old stamps a save would send. One rule for every record of a photo the page holds (`web/tagpup/cache.js` `photosWritten`). user action: click Apply in Time Shift, then open a library view listing the photos before the reply. | decided: the view's records are the same photos; a stamp left old there is a save refused 409 |
| B-3 | 2026-11-22 | low: decision, photos-written cut 2: a Time Shift a share stopped part-way through is a failed entry of the page's write queue with no Retry (`rename.js` `applyTimeShift` sets `entry.retry = null`): a Retry would send the whole shift again and move the photos that landed twice. user action: a share stops answering during a Time Shift. | decided: the status says how many were not written; the owner shifts those again by hand |

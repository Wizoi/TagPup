# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-23 | wrong: on the phone's Photo screen a facts read that failed (`state.facts.failed`) is never repaired by the Edit sheet's successful read of the same photo: the sheet's `readRecord` sets only its own copy of the record, and `photoWritten` drops the written fields when the facts hold no record, so "Could not read the title and tags" stays under a sheet that says "Saved.", the title empty and no chips; a successful vocabulary re-read by the sheet does not redraw the facts' failed-vocabulary sentence either. One holder of the photo's record (the facts), fed by whichever read lands. user action: a read that fails once (the PC busy), then Edit and Save | open |
| B-2 | 2026-11-23 | low: the phone's facts read keeps a reply that lands after Back (`leavePhoto`), and a facts read that is out when `photoWritten` runs can land after it with the older record and undo the written fields on screen. user action: Back within the facts read's flight, then the same card again; or a save while the facts read is still out | open |
| B-3 | 2026-11-23 | low: the phone's closed facts sheet (`max-height: var(--facts-peek)`, 40dvh, overflow hidden) cuts its Edit button off on a crowded photo on a small phone. live: 24 photos with 4 or more people and 4 or more keyword chips; user action: such a photo on a 360x640 phone | open |
| B-4 | 2026-11-23 | low: a photo deleted on the PC and then tapped on a stale phone grid reads two sentences, one wrong: "This photo is no longer in the library." and "Could not read the title and tags". user action: delete a photo on the PC, tap its card on a phone grid not yet refreshed | open |
| B-5 | 2026-11-23 | low: the 12-hour clock wording is written twice in `web/tagpup/format.js` (`takenSaid` and `whenSaid`). live: 60,196 dated photos (as briefed) | open |
| B-6 | 2026-11-23 | low: the phone's facts say a photo with no date one way and the PC's another ("No Date Taken" in `web/tagpup/mobile-photo.js` against `takenSaid`'s "Unknown" in `web/tagpup/format.js`); which wording the owner wants is the owner's to choose, left as is. user action: open a photo with no Date Taken on the phone and on the PC | open |

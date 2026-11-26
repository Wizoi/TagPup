# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-26 | wrong: a folder queued while the Library pane's Folders tab is already read and marks nothing does not show in it until the tab is next read (an index's end, a write, Refresh view): nothing tells the navigator a folder was queued (`web/tagpup/gear.js` Manage folders `queued` and `web/tagpup/membership.js` Add call only `checkIndexingStatus`; `web/tagpup/suggestions.js` `heardIndexes` hears TagTuner's and the watcher's queues too). The tab reads itself again while it marks a folder (navigator.js `followQueued`), so only the first is missed. Fix in the one place that hears every queue: `heardIndexes` calls `upper.navigatorCountsChanged()` when an answer lists a folder the last answer did not. user action: add one folder from Manage folders with the Folders tab already read. (cut F, worker; suggestions.js was another worker's) | open |
| B-2 | 2026-11-26 | low: the phone's Browse lists a folder the library's index queue holds with its count 0 and no mark (`web/tagpup/mobile-browse.js` draws a row's count, not the `hint` where navigator-model.js puts the queued / indexing word), its cover none until a photo is indexed. user action: add a folder and open the phone's Browse while it indexes. (cut F, worker) | open |
| B-3 | 2026-11-26 | low: a click on a queued folder opens its library view as any folder's does, which says the library holds no photo in it until its index reaches them; the brief's "Organize reads it from disk" would give a click on a folder with no photo another meaning (asked of the owner in cut F's report, not built). user action: add a folder and click it in the Folders tab before it is indexed. (cut F, worker) | open |

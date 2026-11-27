# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-26 | low: decision in the second round of cut D (docs/plans/working-on-a-new-folder.md, #3423): the placing of an index's Re-examine in its sentence (on the word Re-examine, which every sentence offering it holds; the arm placing it after a sentence without the word had no instance and was removed) is one rule for both pages, so it went into `web/common/dom.js` as a generic `withAction(text, word, makeAction)` when `web/common/reexamine-offer.js` was deleted; dom.js is the integration owner's file, which its owner should confirm. user action: an index whose new faces have a likely name, or whose face matching failed. | open |
| B-2 | 2026-11-26 | low: decision in the second round of cut D (#3422): TagTuner's `state.lastFinishedFolder` went with the fix. It kept followQueue from following a folder index-active listed after index-status called it finished, a disagreement `tagpup/jobs/indexing.py` no longer allows (both read the one status dict a run keeps, which only moves forward; the folder's next dict is a new run's); with it, followQueue took the rerun for the finished run. Should the two ever disagree again, the page polls that folder every 400 ms plus one status read until they agree, not a tight spin. user action: files arrive in a folder while it is indexed. | open |

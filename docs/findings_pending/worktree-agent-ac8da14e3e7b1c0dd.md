# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 | low: Tag writes (cut 3b): the open photo's panel stays editable while its own removals (a pill, a face's person; pending.js `panel` ops) wait or are out; only a change it does not draw (a bulk click, an Undo) makes it read-only (pendingOf leaves the panel's own removals out). The plan's read-only rule read literally would hold the panel after every pill click. user action: pills of the open photo clicked quickly, one after another. | decided: owner's V20 request of 2026-11-15 (pill removals queued like faces, the panel kept live); kept in cut 3b |
| B-2 | 2026-11-19 | low: Tag writes (cut 3b): not built from docs/plans/one-queue-for-tag-changes.md: the tag part of edits.js `writeDetailEdits` (a tag or person typed in the open photo's panel) still writes through /api/photo/save-metadata with the photo's whole tag list and its `base`, not through pending.js; a save and a pill removal of one photo are two kinds of write in the one queue, kept apart only by the queue's order (V20 "a removal and a save around it"). user action: a tag typed and saved on the open photo while one of its pills' removals waits. | open |
| B-3 | 2026-11-19 | low: Tag writes (cut 3b): a pill's removal that fails while another removal of the same photo waits says "Not taken off X: why" in the status line, and the next write's "Writing tags..." then "Ready" say over it at once; the alert and the failed row in the list of writes still say it (tags-off.js kept the line until the last removal was answered; V20 no longer asserts the line). user action: two pills taken off quickly on a share that refuses the first write. | open |

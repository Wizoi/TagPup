# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-17 | low: a folder's Delete (Organize, cut 2b) ends with the status line saying "Ready": the follow's scan (web/tagpup/folder.js scanFolder, its `statusText.textContent = 'Ready'`) writes over the end sentence or the error `bulk-run.js runBulk`/`failed` put there, so "Deleted 3 photos to the Recycle Bin; 0 errors." or a 500's sentence is said only in the batch strip (which keeps it, Dismiss to close); found writing I13 (scenario-deleting-the-selected-photos-of-a-folder.test.mjs asserts the strip, not the status line). user action: select photos in a folder in Organize and click Delete. | open |

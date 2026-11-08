# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-07 | low: /api/library/selection/tally's "total: photos selected" still counts clips as photos (a separate count, out of #2569's scope, which gave the library view's counts by kind). | open |
| B-2 | 2026-11-07 | low: the clip statement of a library view (`SELECT p.id FROM <from_> WHERE (<where>) AND p.kind = 'video' ORDER BY p.id`) is a SCAN of photos for the whole-library view, as no index holds `kind`: counted by the reviewer in-process on the live photo_index, warm: whole library 32-46 -> 123-140 ms (the clip statement alone 85-99 ms, SCAN p); top folder with subfolders about 196 -> 321 ms; a 14,838-photo keyword 92 -> 148 ms; a 14,838-photo person 33 -> 82 ms; leaf folder, month, year unchanged within noise. A partial index `ON photos(id) WHERE kind = 'video'` rides in the next migration (39), not its own (owner decision 2026-11-07: merge now). A browser timing is to be taken before anyone claims the click got slower. The plan test accepts the scan by name. | open |
| B-3 | 2026-11-07 | low: the selection sentences still count clips as photos: web/tagpup/bulk-run.js ('TagPup took N photos but M were selected'), the Delete question and 'Deleted N photos' in bulk-words.js, index.html 'No photos selected', and /api/library/selection/tally total (B-1's words folded in). One owner: the page's selection count by kind is `selected ∩ lib.videos` (rest-source: lib.videos less the excluded), spelled by folder.js kindsSaid; no new server read. | open |

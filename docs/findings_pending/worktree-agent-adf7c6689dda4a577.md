# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-16 | wrong: web/tagpup/pending.js `takeChunk` dropped ops its record already satisfied (an add the photo's record holds, a removal of a tag it lacks), trusting a record that may be older than the file, though the server already skips a file holding everything (tagging.py bulk-tags). user action: another program removes a tag from a file after the folder is read, then Add to all selected sends nothing for it and the photo goes on missing the tag the owner asked for (the same for a removal of a tag another program added). Kept the drop? No: a stale record can only make the page skip a write the file needs; sending everything costs a request the server answers by skipping the files that hold it (a mostly-tagged 5,000-photo select-all is 200 requests of 25 files, no file written). The drop is removed; an add then a removal of one tag is now sent as the removal (the last click wins). Test: scenario-tagging-many-photos-at-once, "a record older than its file", failed with the drop. | fixed: pending.js: every pended op is sent whatever the page's record holds (cut 3a-2 follow-up) |

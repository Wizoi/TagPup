# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-16 | wrong: Views: a failed removal put back a tag the file no longer holds, said "Not taken off", and the next save wrote the tag back (`web/tagpup/tags-off.js`). The click takes the tag off the page's copy; a write queued before the click (add, title save) builds its list from that copy and writes the removal itself; the removal's own later write then fails and every tag of `batch.taken` not on the copy was put back without asking the file. user action: take A off, add a tag (queued), take B off (queued), the share fails the third write. | fixed: e642500c; wrote 0 (a page fix, no bulk write); only the tags `baseOf(photo).tags` still holds come back, the status says "Not saved" when none does; V20 case "a removal that fails after an earlier write already wrote it" fails on the old code (the pill came back) |
| B-2 | 2026-11-16 | low: Views: a Retry of a failed removal runs outside `state.removals.open` (`web/tagpup/tags-off.js`, writeRemoval's Retry), so its success sets 'Ready' while another removal is still out. user action: Retry a failed removal while another pill's write is queued. | open |
| B-3 | 2026-11-16 | low: Views: the click changes `photo.tags` before the write and any other write's `saveToLocalStorageCache` stores that record; a tab closed with a removal still queued reopens that folder from the cache (30 min) without the tag and the next save writes it off. user action: close the tab while a removal is queued behind a slow write. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: `store.embeddings.stamp_of`, an unbounded stat of a photo, keeps one caller, `services/faces.py`'s record of a detection, which another worker had open (#2111); the guard's `NOT_MOVED_YET` still holds it (1). The change is one line, `shares.read_stamp(photo_path)`, then `stamp_of` and `NOT_MOVED_YET` with its test are deleted. | open: Files |
| B-2 | 2026-10-28 | low: the brief said to replace `embeddings.stamp_of` with `shares.known_stamp`. The indexer's and Suggest's stamps (`search.keep`, `prepared`, `opened`) went to a new unbounded `shares.read_stamp` instead: INVARIANTS' by-design row says the indexer's per-photo reads, the stamp included, have no deadline, and a bounded stamp loses the vector of a photo on a share that takes more than 1 s to answer one stat (no stamp, no vector kept that run). It lives in shares, so the one subsystem still owns the look. | decided: the indexer's reads are unbounded by design; read_stamp says why in its docstring |
| B-3 | 2026-10-28 | low: docs/domains/files.md's by-design row #1384 says each listing is unbounded ("bounding each listing costs a thread per folder"); since #2104 `shares.listing` waits LISTING_WAIT for each listing on a share. The row is stale. | open: Files |
| B-4 | 2026-10-28 | low: `roots_ingress.guard` refused a folder spelled at an old place with the sentence "not at the first place" when `isdir` was false, which on a share that does not answer said it was gone. It now refuses only a folder `folder_state` finds gone; an unanswered one goes on to the route, which refuses it as not a folder (scan, add) or answers could_not_check (membership). | decided: a share away is not a place gone (#1356) |
| B-5 | 2026-10-28 | low: the routes' folder checks are `folder_state == "there"`, so a folder on a share that does not answer in 1 s is refused 400 "not a valid directory" (scan, add, suggest, auto-apply, time shift, rename; the Tuner's subfolders "Not a folder"), as the old isdir refused it after Windows's wait. The words do not say the share is away. | open: Files |

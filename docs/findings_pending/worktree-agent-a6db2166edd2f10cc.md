# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 Batch C | low: an index's end the library is being written for past RECORD_WITHIN (#2200) is written on a thread of its own (`RunEnd`, not a daemon) that waits the writer out as every end used to (db.write_with_connection), so the queue's worker never waits; a CLI run ending in that window waits for the thread at exit, as its worker waited before, and a server killed in it leaves the row to the start's sweep (STOPPED), as before. The end is also kept for the next claim, so whichever comes first writes it, once. | decided: the thread is what keeps the deferred row from reading stuck on the Activity page (STUCK_AFTER) when no next index comes; the next claim alone would leave it `running` until then |
| B-2 | 2026-10-30 Batch C | low: `thumbnails.sweep` has no automatic caller: it runs only inside `thumbs warm --apply` (thumbnails.warm). A sync's removal keeps the thumbnail entries of the rows it removes, where Remove Folder and Delete forget theirs (#2160). Live: 2 rows removed by syncs in the kept sync records of the three libraries (1, 1, 0). | decided: by design: a sync's removal is a journaled, undoable change (#2151, DECISIONS 2026-10-30), and its undo brings back a row whose file is gone, which thumbnails.serve answers from the kept entry (LAST_KNOWN); nothing serves an entry while its row is gone, and a forgotten one would leave the undone photo with no picture. Remove Folder and Delete are not undone, so theirs go |

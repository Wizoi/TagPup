# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | wrong: the catch-up sync 3 s after the server starts is answered "away" from the watcher's 1 s look (`shares.py` memo, 30 s): `sync._scan` asks `folder_state(root, FOLDER_WAIT=10)` but `bounded` answers at once while the memo stands, so the 10 s wait is never used and every library on the share is recorded not in step (photo_index run 92: 59,524 rows not looked at; kr-track 83; renton_parkrun 67; 2026-10-28 08:33:18). The share answered 30 s later. Live: 2026-10-28 08:33 and 2026-10-27 16:55 (Files owner, read-only). | open: shares.py owns "back" as well as "away" (`on_back`, `wait_back`); `sync._scan` waits for it; the watcher resumes on it |
| B-2 | 2026-10-28 | wrong: the watcher's whole sync and the recurring sync of photo_index run at once (`watching.py:369`, `recurring.py:292`): runs 94 and 95, 2026-10-28 08:34-08:48, two walks of 69,700 files over the share; renton_parkrun's watcher sync waited 15 min behind them and the owner ran it by hand; on 2026-10-27 16:55-18:09 neither finished. | open: one claim for two walkers (the watcher's whole sync claims `sync` in job_runs, or the recurring runner refuses while the watcher syncs that library) -- the owner's decision on which |
| B-3 | 2026-10-28 | low: a sync that could not look at its roots ends `done`, so the recurring runner's RETRY_AFTER does not apply and the next attempt is 23 h later (`recurring.py:166`). | open: Integration |
| B-4 | 2026-10-28 | low: `images.photo_entries` lists a folder with `os.scandir` over a share with no bound (`images.py:244`); a drop mid-walk holds the watcher thread for Windows's timeout (possible mid-walk hang on 2026-10-27 16:55-18:09). | open: the listing goes through the share owner (`files/shares.py`) with every other look at a photo path |
| B-5 | 2026-10-28 | low: after the index's bake, each folder's watcher sync found 1 row changed and read it again (2026-10-28 08:49:33-08:50:44; the shape of #360). | open: confirm against the bake's own record of what it wrote |

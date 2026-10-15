# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: (left of #1338 after #1356's fix) sync now asks each root and each missing row's folder through `shares.folder_state` (bounded), and a listing that fails or drops part-way is "not looked at"; but the walk itself (`images.photo_entries`' `os.scandir`) of a share that answered at its root and then hangs mid-walk is not bounded and holds the sync's thread until Windows gives up (then it is "not looked at"). Bounding each listing costs a thread per folder; no hang mid-walk has been seen. | open |
| B-2 | 2026-10-15 | wrong: the Activity page's Sync card asks each root `os.path.isdir` bare (`tagpup/web/activity_routes.py`, `entry["roots"]`): a root on a share asleep is shown "missing" (and a hanging share holds the request's thread), while the sync beside it now says "1 folder not looked at (share not answering)". One live root is spelled as a share (1,309 rows, counted 2026-10-15). Fix: `shares.folder_state` and a "not answering" badge. Not in this branch's files. | open |
| B-3 | 2026-10-15 | low: `tagpup/services/inspect.py` (the MCP tool for rows whose file is gone) reports `folder_gone` with a bare `os.path.isdir`: a share asleep reads as its folders gone. `shares.folder_state` answers it. | open |
| B-4 | 2026-10-15 | low: decision made in #1356's fix, not copied: `shares.folder_state` calls a folder "gone" only when the drive or share it is on answers (the rule `stamp_of` already had), so a root on a drive letter that is not connected (an unplugged USB drive) is now "not looked at" and leaves the library not in step, where it was a root gone with its rows missing. A root renamed or deleted on a drive that is there is still gone. | open |

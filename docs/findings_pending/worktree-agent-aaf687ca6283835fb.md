# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-22 photos-written cut 4 docs | low: Smart Rename of a folder posts outside the write line (`api.fetch` in `rename.js`, not `queuePhotoWrite`), so the page going while it is out (`pagehide`) forgets no kept scan of the folder's photos: reopened within the half hour, a folder or ancestor scan shows the old names and stamps. user action: reload or choose another library during a folder Smart Rename, then open the folder (or one above it) again | open |
| B-2 | 2026-11-22 photos-written cut 4 docs | low: a Convert's ticket finishing after the page has gone (`clip-convert.js` polls `/api/clip/converted`; the poll dies with the page) leaves every kept scan holding the clip's old path and stamp, and nothing forgets them. user action: reload while a Convert runs, then open the clip's folder (or one above it) again within the half hour | open |

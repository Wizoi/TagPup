# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | low: a folder's page record takes `media` from the path's extension (images.kind_of), not from the row's kind or the reader's meta["kind"] as the plan said: the index gives a row its kind by the same function (photos.kind, metadata._structure), so the three are one rule; a record built without a row (a folder the library does not hold) has it too. Chosen by the worker of video merge B2 | decided: one rule, images.kind_of; services.photos.page_record |
| B-2 | 2026-11-05 | low: a folder's clip the index records after the folder was opened stays a placeholder in that browser until the folder is scanned again (Refresh, or an open after the browser's half-hour copy, web/tagpup/cache.js): the server's kept scan is read again when the photos counter moves (scan_stamp), the browser's copy is not asked. The same holds of a photo the index adds meanwhile, as it always has | decided: the browser's half-hour copy is the folder cache's design; the spec's folder-scan row says so |
| B-3 | 2026-11-05 | low: Smart Rename's answer re-reads the folder's photos (read_folder, photos only) and keeps the folder's clips from the server's kept scan as they were (tagpup_routes._with_clips); when that scan was already let go (the photos counter moved between the open and the rename), the answer lists no clip and the page shows none until Refresh. Not built further: no instance counted, and a rename never changes a clip, so Refresh restores them | decided: Refresh restores them; no instance of the let-go case |

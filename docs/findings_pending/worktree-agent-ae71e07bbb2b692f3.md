# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: `tagpup_cli._resolve_directories` asks a bare `os.path.isdir` of a folder typed in an old place's spelling and canonicalised to the first place: on a share that does not answer it waits Windows's wait, then refuses with the old-place sentence ("does not exist at its current place") instead of the share's (photos.folder_refusal). Only a typed old-place spelling reaches it; not changed by the #2129 branch (one concern). | open: Files |
| B-2 | 2026-10-28 | low: the index queue (`tagpup.jobs.indexing._enqueue`) keeps its own "No valid folder path: a, b, c" for a request whose folders are all not there (it names up to three), while every single-folder site says "Path is not a valid directory: <path>"; an away share in the request gets the share's sentence and 503 (#2129). Decided in the #2129 branch: the queue's sentence lists several folders, the single-folder one names one. | decided: the queue's refusal is of a list, not one folder |

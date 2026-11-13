# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: the Media domain's first page (docs/domains/media.md) states conditions 3 (no hidden coupling: a clip's patch, settle and read-back held together by test) and 5 (Convert's old-format arm, 0 clips of .wmv/.mpg/.avi left in any library, counted 2026-11-13) as not yet stated: the owner's first pass decides each, and counts the clips holding an XMP packet and those a browser cannot play (a TODO on the page). | open |
| B-2 | 2026-11-13 | low: decided in the carve, not copied from the brief: contract row 69's caller became M (the viewer is `web/tagpup/media.js`, now Media's), the Views charter's `/api/video-file` Scope entry kept but marked as Media's scenario C8, and `tests/test_container_layout.py` went to Media though `tagpup/files/containers.py` stays Files' (the brief's choice). | open |
| B-3 | 2026-11-13 | low: `data/` holds four library files, not the three the charters and domain pages name: a fourth of 2,065 photos and 5 clips beside kr-track (2,181 and 5), photo_index (59,319 and 2,910) and renton_parkrun (1,309 and 0), counted read-only 2026-11-13; photo_index's clips were 2,179 on 2026-11-08, so the Files and Views pages' 2026-11-08 counts are stale. | open |

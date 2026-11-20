# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-20 | low: `/api/photo/save-metadata` still takes a body with neither `stamp` nor `base` as no check (`tagpup_routes._set_fields`, NO_BASE), an arm no page uses: `web/common/photo-write.js` postPhotoMetadata always sends both keys; the new `/api/photos/write` `set` refuses it 400 (one-write-path 2a), and the arm goes when save-metadata is deleted in cut 2c; user action: none (only a hand-made request) | decided: the arm is deleted with save-metadata in cut 2c (plan one-write-path), nothing a page sends reaches it |
| B-2 | 2026-11-20 | low: `/api/photos/write` `tags`: a share that stops answering part-way (`file_changes._share_stopped`) ends the whole change 400 with `written`, where the spec and I15 said the photo is its error and the others written (true only for a share away at plan time); user action: a NAS share sleeping between a chunk's reads and its writes | fixed: e7ee98e8 (the spec and I15 now say it, as bulk-edit's contract) |
| B-3 | 2026-11-20 | low: the new route had its own `except Exception -> 500` arm, a copy of the app handler (#3199); user action: none | fixed: e7ee98e8 |
| B-4 | 2026-11-20 | low: contract table had no row for `photo_writes._moved` reading `selection.paths_of`; user action: any page write with an id | fixed: e7ee98e8 (row 35) |
| B-5 | 2026-11-20 | low: a row deleted through TagPup refuses a chunk whole (409) while a file deleted in Explorer is skipped and the rest written; cut 3 must re-read on 409; user action: delete a photo on the phone while Organize holds a queued tag for it | fixed: e7ee98e8 (plan cut 3 line, I15 wording) |

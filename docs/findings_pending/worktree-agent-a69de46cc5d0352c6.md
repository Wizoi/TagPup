# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-16 | wrong: `/api/photo/open` answers 400 "Not a photo file" for a photo on a share asleep: `desktop.is_openable` (tagpup/web/desktop.py) looks at the file by `photo_actions.file_stamp` and reads None as no file, a second place for #3068's symptom (the one place is `responses.file_refused`). user action: open a photo in its program while the share sleeps. Fixed with a test that fails on the old code first. | open |
| B-2 | 2026-11-16 | low: docs/SPEC_TAGPUP_GUI.md lists `/api/photo/delete`, `/rotate`, `/open-explorer`, `/save-metadata` and `/open` with 400 for a missing path and never the 503 with `shares.SHARE_AWAY_SENTENCE` that the folder paragraph documents (#3068). user action: read the spec for a photo write while the share sleeps. | open |
| B-3 | 2026-11-16 | low: `tagpup.services.photos.page_copy` and `face_crop` (photos.py, `shares.there(photo_path)`) raise NotFound for a photo on a share asleep as for a gone file; the routes answer 404, so the page shows a broken image (not "not found" text) for a photo that is there. user action: open a photo's page image or a face crop (when no stored crop) while the share sleeps. Not fixed here: a new place for the share-away refusal, asked of the main session. | open |

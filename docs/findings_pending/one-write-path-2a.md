# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-20 | low: `/api/photo/save-metadata` still takes a body with neither `stamp` nor `base` as no check (`tagpup_routes._set_fields`, NO_BASE), an arm no page uses: `web/common/photo-write.js` postPhotoMetadata always sends both keys; the new `/api/photos/write` `set` refuses it 400 (one-write-path 2a), and the arm goes when save-metadata is deleted in cut 2c; user action: none (only a hand-made request) | open |

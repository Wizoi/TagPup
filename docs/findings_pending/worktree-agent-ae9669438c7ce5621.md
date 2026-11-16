# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-15 | low: Views: the save reply never says which faces the one-face rule named (`tagpup/store/photos.py:570` drops `face_tags.name_photos`' return; `tagpup_routes._faces_unnamed` sends only `unnamed_faces` #908 and `renamed_faces`), so the page infers it from the tag change, in one place (`photo.js` `facesFollowTags`: a tag that may name somebody added while a face is nameless and no named face is them, or a named face's person taken off). user action: Add person on a photo with one nameless face. | open |

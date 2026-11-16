# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-15 | low: Views: the save reply never says which faces the one-face rule named (`tagpup/store/photos.py:570` drops `face_tags.name_photos`' return; `tagpup_routes._faces_unnamed` sends only `unnamed_faces` #908 and `renamed_faces`), so the page infers it from the tag change, in one place (`photo.js` `facesFollowTags`: a tag that may name somebody added while a face is nameless and no named face is them, or a named face's person taken off). user action: Add person on a photo with one nameless face. | fixed: 1bb44300 (the page reads the faces again from the save's reply: named_faces 1b063c91, facesFollowReply 1bb44300; a reply field, no bulk write); wrote 0 |
| B-2 | 2026-11-15 | low: Views: a read of the open photo's faces that is out when a naming starts is dropped (`photo.js` `renderPhotoFacesHere` bumps `facesRequestToken` so the older answer cannot overwrite the face shown named) and nothing reads them again when the naming ends (`faceBoxes.owed` is set only by a reply that lands during the naming), so a face the server named or unnamed by the save that asked for that read stays drawn as it was until the photo is opened again. user action: a person put on the photo whose reply lists a face, then within the read's 0.3 s a face's card or box clicked to name another face. | open |

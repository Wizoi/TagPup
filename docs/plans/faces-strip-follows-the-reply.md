# The faces strip is read again when the save's reply lists a face: the page keeps no rule of its own
Live count: photo_index 25,024 photos hold a face still to be named (62,198 photos), so today every tag write on them reads the strip again (counted read-only by the worker, 2026-11-15); the one-face rule (#788) names a face on a save, and its return is dropped at `tagpup/store/photos.py:570`.
Domains: Views (the page), People (`face_tags`, what names a face), Data (`photos.record`), Files (the save's Result and route); integration reviews the seams

| Mechanism | The ONE place it lives | Its owner | New or existing |
|---|---|---|---|
| Which faces a save named (the one-face rule) | `face_tags.name_photos`' return, carried by `photos.record` into the save's Result `details["named_faces"]`, spoken by `_faces_unnamed` beside `unnamed_faces` and `renamed_faces` | People (value), Data (carry), Files (route) | new field, existing reply |
| When the strip is read again | the page reads it again iff a save's reply lists a face (`named_faces`, `unnamed_faces` or `renamed_faces`): one function `facesFollowReply(path, reply)` in `web/tagpup/photo.js` | Views | replaces `facesFollowTags`, `mayNameAFace`, `readWith` |
| Keep the strip while the same photo is read again, swap in one step | `renderPhotoFaces` / `drawPhotoFaces` of the branch | Views | existing (branch 615322bb) |

Steady-state writer: every save of a photo's tags, through the one writer, says which faces it named or unnamed; no command run.
One-offs: none.
Cuts: one worker on branch worktree-agent-ae9669438c7ce5621 (already holds the keep-the-strip drawing, commit 615322bb): add `named_faces` to the reply; replace `facesFollowTags`' tag comparison with the reply (every caller runs after a reply: edits.js `redrawIfShowing`, `refreshAfterDetailSave`, undo.js, face-boxes.js `notBusy`, `removePhotoTags`); delete `mayNameAFace`, `readWith`, `nameless`, `isFaceOf`; fix "Back to grid, the same photo again" (`openFolderView` calls `clearPhotoFaces()`); V19 gains "a face named by its card, its person then taken off" and "a person added to a photo with one nameless face" through a fake reply, run against the old code first; V19 is People's, Views co-signs. Under 600 lines; a larger piece is cut.

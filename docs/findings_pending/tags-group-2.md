# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-23 | low: a tag change's reply (POST /api/photos/write, op `tags`) never carries `renamed_faces`: `face_people.follow_change` sets `renamed` only for History's Undo (`undone=True`, `journal._unname_what_was_taken_off`), and the tag write calls it without (`tagging.py`, `follow_change(library, change, moved=moved)`), so the page's arm for it in `web/tagpup/photo.js facesFollowReply` (and its comment, "names it again when they come back"), the `renamed_faces` arm of `tagpup_routes._faces_unnamed` and of `tagging.py` (`if done.details.get("renamed")`) have no instance on that route; the page test that answered an undo with a hand-spelled `{renamed_faces, success}` (scenario-tagging-keeps-the-faces-in-place) now answers it with the real `unnamed_faces` reply. Page code left in place, as briefed. live: 0 (no route the page reads makes the field) | decided: no live instance, not built |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: Smart Rename (A5 cut A): a clip of a held folder whose writer refuses the patch of its PreservedFileName (its boxes not patchable -- over 4 GB with no XMP, a tail after its last box -- or open in another program) stopped the rename of its WHOLE selection, as a photo whose name cannot be kept does (#523), though a clip left out only keeps its name. Files owner's census of such held clips, 2026-11-08 (not recounted by this worker). | fixed: a009f5e8 (`write_fields` tells a file its writer refused apart, `file_changes.WRITER_REFUSED`; `services.photos._refused_clips` leaves such a clip out, its number unused, its sentence the reply's error, the others renamed); `tests/test_a_clip_renamed.py` `test_a_clip_the_patch_cannot_place_keeps_its_name_and_the_photo_beside_it_is_renamed` failed on the old code first. |
| B-2 | 2026-11-08 | low: `"name"` added to `fields.PHOTO_WRITES` (A5 cut A) though a photo's rename was never refused by `writes`: so the page's Smart Rename button wakes from a record's `writes` alone, a photo's and a clip's by one rule, with no kind test in `rename.js`. | decided: by design; `writes` is the one statement of what a save may change of a file (`field_values.writes`), and a photo's name is one of them. |

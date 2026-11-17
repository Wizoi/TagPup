# Own writer flip (4d): a save patches the photo's own metadata in place, and ExifTool rewrites only what it cannot patch
Live count: photo_index 57,221 JPEGs; the 4d gate, `metadata-check --sample 3000` on trunk 7fc24ef6 (2026-11-16): 166 differences, none the own writer's (40 ExifTool-side, 120 MPF offsets by design, 6 Canon rows that are ExifTool's rewrite); 24 of the 10,515 JPEGs of 2005-2008 hold an XMP packet in IFD0 (0x02BC), which `segment_edits.new_header` refuses and ExifTool rewrites (#3108).
Domains: Files and sync (the writer), Data and journal (undo's values); integration reviews the seams

| Mechanism | The ONE place it lives (module, function) | Its owner | New or existing |
| write a photo's fields: patch in place, else ExifTool's rewrite | `tagpup/files/field_values.py` `write` (its caller `file_changes.write_fields`) | Files | existing: the own writer becomes the only patch writer |
| which segments a patch edits, and when it refuses | `tagpup/files/segment_edits.py` `new_header` | Files | existing |
| the patch itself (temp copy, verify, move) | `tagpup/files/in_place.py` `patch_jpeg` | Files | existing: `fitted`/`_fitted` deleted |
| ExifTool's header-copy patch | `field_values._exiftool_header` | Files | deleted |
| undo's re-write of a field's values | `file_journal` undo -> `file_changes.write_fields` | Data | existing: tested through the flip, unchanged |
| the shadow comparison | `tagpup/services/metadata_check.py` (`metadata-check`) | Files | existing: kept the first weeks (owner, 2026-11-05) |

Steady-state writer: a photo saved afterwards (a tag, a caption, Date Taken, a turn, an undo, a bulk edit) goes through `file_changes.write_fields` -> `field_values.write`: the own writer patches in place when the change fits and the file is its to patch; otherwise ExifTool's rewrite. `photos.follow_fields` records what the file holds in the same transaction. No command is run.
One-offs: none. No migration, no stamp, nothing automatic on the libraries; the flip changes how the next save writes.
Cuts: one merge (about 600-900 lines counting deletions): `PATCH_IN_PLACE` and `OWN_WRITER` become one; `_exiftool_header`, `in_place.fitted`, `_fitted` and what only they used deleted; the pinned test of a tag add flips; scenario tests for undo, extended XMP and the bulk paths, which the gate did not cover. `metadata-check` and `field_values.write`'s `own_writer` argument stay for the first weeks of shadow mode.
Fails how: a write interrupted part way (the original stays whole); two saves of one photo at once (the per-file lock); a file changed on disk since the scan (the record precondition); a share that does not answer; a photo with zero EXIF slack, MPF, a maker note another program moved, an IPTC block the own editor cannot parse (it falls to ExifTool's rewrite), a second XMP packet in IFD0; rapid saves, a bulk tag add over hundreds of photos, undo straight after a save.

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-27 | low: the T1 brief's byte-order recount cannot be made: `tagpup/services/segment_census.py` (scripts/census_segments.py) counts no EXIF byte order (its `_shape` reads `layout.order` and counts nothing of it), and no row records one (raw_metadata holding an `ExifByteOrder` key: 0 of 60,355 / 2,186 / 1,309 / 1,284, counted 2026-11-27, read-only), so `tagged_ii` cites a user action (a photo of a camera writing Intel order) where the brief wanted a count. A count of `EXIF order II` / `MM` in `_shape` would give it; Files' file, not T1a's (T1a branch). user action: a brief or recipe citing the libraries' byte orders | open |
| B-2 | 2026-11-27 | low: the T1 brief's `tagged_ii` recipe ("`tagged`, `-ExifByteOrder=II`") makes `tagged`'s bytes again: ExifTool 13.59 takes ExifByteOrder only in the write that makes the EXIF, never over an EXIF held. `corpus.tagged_ii` gives it to the camera photo's first write instead; tests/test_a_photo_made_once_is_copied_for_each_test.py's "no two recipes make one file" failed on the brief's recipe (tagged_ii's digest was tagged's). user action: a recipe reordering a photo's EXIF | fixed: 90af5789 |

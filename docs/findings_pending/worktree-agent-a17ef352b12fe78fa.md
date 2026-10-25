# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-24 | low: the census brief names `XMP-MP:RegionInfoMP`, a struct, which ExifTool 13.59 answers only under `-struct` and answers nothing without it (measured on a hand-made file holding a Microsoft Photo region): asked, it would count every file as holding no MP region. The census asks the struct's flattened fields `XMP-MP:RegionRectangle` and `RegionPersonDisplayName` instead, and MWG regions by `RegionType` or `RegionAreaX` (`tests/test_xmp_census.py`, the real-ExifTool class). | decided: flattened fields, as measured; the read side (cut 1c) asks the same way or loads `-struct` |
| B-2 | 2026-10-24 | low: ExifTool says a JPEG holds two XMP blocks ("Duplicate XMP at JPEG-APP1-XMP") only under `-validate` (measured), so `field_values.read` takes `params` and the census passes `-validate`; with several warnings ExifTool's JSON gives one `Warning`, so a file whose first warning is another one is counted under `other_warnings`, not `duplicate_xmp`: the two counts together bound the duplicates. | decided: counted as said; the census prints both counts |
| B-3 | 2026-10-24 | low: the census's sample is systematic over the photos in path order (every total/sample-th photo), so each folder gives its share by its size and a folder smaller than total/sample photos may give none (photo_index: about one photo in 60). Deterministic, so a second run reads the same files; the XMP size counted is the standard block's only, never an Extended XMP's. | decided: proportional by folder, deterministic; owner may ask for one-per-folder first |

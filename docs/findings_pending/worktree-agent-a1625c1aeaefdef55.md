# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-03 | low: #2026 again, met by the IPTC block editor: `fields.as_written` cuts an IPTC keyword or title at 64 characters, `fields.as_read` at 64 bytes in the block's character set, so in UTF-8 a value of 33 to 64 characters holding letters outside ASCII is written whole (`iptc_block` and the ExifTool write alike send `as_written`'s value) but predicted read back cut (`same_read` cuts both sides alike, so no comparison differs). Probed this session on a made-up JPEG: ExifTool without `-m` cuts at 64 bytes, as `as_read` does (40 e-acutes keep 32); with `-m`, which every TagPup write passes, it cuts nothing (80 bytes kept; 70 ASCII letters kept 70). So `as_read` matches an ExifTool run TagPup no longer makes, and the file holds `as_written`'s cut. #2026's reopening condition, a UTF-8 IPTC file in a library, is met (kr-track's blocks carry 1:90 UTF-8), but no value is cut differently: of the distinct keywords in photos.tags none is over 64 characters, and none of 64 or fewer is over 64 bytes in UTF-8 (0 of 827 / 113 / 249, photo_index / kr-track / renton_parkrun, read-only). | decided: by design, #2026's rule holds: no value of the three libraries is cut differently (0 counted 2026-11-03); reopen with one |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-27 | wrong: `-m` on a write (#2009) also stops ExifTool cutting an IPTC value at IPTC's length (IPTC:Keywords at 64 bytes, IPTC_LIMITS), so a long keyword went into IPTC whole while the journal's reading of a file (fields.as_read) still predicted the cut: three tests of tests/test_a_file_holds_what_exiftool_kept.py failed on the -m change, and a settling after a crash would have called such a file a conflict. Live count of tags over 64 bytes: 0 in photo_index, kr-track and renton_parkrun (counted read-only 2026-10-27); a user action makes one (a tag path over 64 bytes). Decided by the worker, not the brief: the write cuts each IPTC value to IPTC_LIMITS characters first (fields.as_written, the one table), so files hold what they held before. | fixed: on this branch (worker's decision; the Files owner reviews it) |
| B-2 | 2026-10-27 | low: fields.as_written cuts an IPTC value by characters, which is ExifTool's byte cut in its default Latin charset; in a file whose IPTC CodedCharacterSet is UTF-8 a long keyword with letters outside ASCII is now written longer than 64 bytes (ExifTool used to cut it at 64 UTF-8 bytes), and still reads as the same (fields.same_read tries both charsets, each side cut alike). Live count of tags over 64 bytes: 0 in the three libraries (2026-10-27); UTF-8 IPTC files not counted. | open: by design unless the Files owner says otherwise |

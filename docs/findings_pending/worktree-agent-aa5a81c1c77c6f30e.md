# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-03 | low: segment_edits maps EXIF:SubSecTime, SubSecTimeOriginal and SubSecTimeDigitized (the brief's list), but no real write reaches a patch with them: only a Date Taken given a fraction names them (`fields.date_taken_fields`), and that write's EXIF:DateTimeOriginal then holds the fraction too ("2019:06:15 10:30:00.123"), which exif_block refuses, so the write goes to the rewrite; a time shift names no SubSec (`dates.SHIFTED_FIELDS`). Left mapped: taking them out gives the same outcome (no patch) for every write today. Decide whether the map keeps them or a Date Taken writer strips the fraction off the EXIF date (as EXIF spells it, with the fraction in SubSec). | open |
| B-2 | 2026-11-03 | low: decided in segment_edits. A rotation of a photo whose EXIF block holds no Orientation is not a patch because exif_block refuses the new IFD0 entry (data follows the table in every absent case of its census, 433 / 397 / 400), not by a rule of segment_edits; a block with 12 zero bytes after its IFD0 table would take the new entry in place, as ExifTool adds the tag. The brief asked "a rotate with no Orientation tag => None": the test builds the block as the census found them and the outcome is None. | open |

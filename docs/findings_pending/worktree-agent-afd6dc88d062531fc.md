# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: note: a read-only or locked photo whose write fails leaves `<photo>_exiftool_tmp` beside it, and the next write of it fails "Temporary file already exists". Pre-existing on the trunk, left as is: the cleanup this branch first made is removed, because after a failure that deleted the original the temporary file is the photo's only copy (#1410); the error now says what is in the way and what to do (#1411). | noted: #1410, #1411 |
| B-2 | 2026-10-15 | low: two writes of a photo file do not go through `field_values.write`, the one field writer: the loose rotate (`file_only.rotate` -> `metadata.rotate_image_file`) and the indexer's identity mint (`identity.ensure_document_id`). Each builds its own ExifTool command, so a rule of the field write (how a clear is spelled, what a failure leaves) is kept in three places. Which single place should own a write of a photo's fields. | open |
| B-3 | 2026-10-15 | low: an ExifTool refusal is said as `<file>` (exiftool_session hides the path: a file's name can hold a caption, and so a name), so a Smart Rename of several photos one of which ExifTool cannot read answers "ExifTool: Error: Entire file is binary zeros - <file>" without saying which photo (`names.read_for_renaming` raises for the whole set); the log names it. Nothing renamed, so no data at risk; the owner has to find the photo. | open |

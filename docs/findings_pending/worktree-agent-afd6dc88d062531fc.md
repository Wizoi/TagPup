# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: after the whole-file scan went (#1160), a held photo that is empty or ends in zero bytes is still refused when rotated alone (`services/photos.py` rotate_photos: `len(held) == 1 and file_only.refuse_unwritable(result, held)`, the end-of-file check), while the same photo in a rotate of two, or in any tag write, is written. Which way the owner's decision goes for a file that ends in zeros (the 2026-10-15 test included a JPEG with zeros in the middle, which ExifTool wrote) is the main session's to settle; nothing was changed here. | open |
| B-2 | 2026-10-15 | low: `file_only.unwritable` (#523, the end-of-file check, not `-validate`) still refuses a save, rename, time shift or rotate of, and skips in a bulk tag write, a photo of a folder the library does not hold that is empty or ends in zeros. It is outside DECISIONS 2026-10-15 as briefed (only the whole-file scan) and kept; if the decision's reasoning (ExifTool writes the tag and leaves the picture) is meant for these too, the check, DAMAGED_PHOTOS, SKIPPED_DAMAGED, `libraries.with_skipped` and the pages' "skipped: damaged" go with it. | open |
| B-3 | 2026-10-15 | low: decided here: the replies of Time Shift and Smart Rename drop `skipped_damaged` (their only source was the scan's skip; a photo of a folder not held is refused whole by both, never skipped) and the page drops its wording for them. `/api/library/bulk-edit`'s `skipped_damaged` is kept, as its write goes through `tagging.change_tags`, whose half for photos of folders not held directly still skips through `file_only.leave_out_unwritable`; how many live rows sit in such a folder was not counted. | open |

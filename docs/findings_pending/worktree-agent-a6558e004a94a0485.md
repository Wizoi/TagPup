# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-09 | low: Files: a real .wmv or .avi may carry its date in the ASF header (`ASF:CreationDate`) or a RIFF `IDIT`/`ICRD` chunk, which the clip date rule (`dates.CLIP_XMP_DATE_FIELDS`, then `dates.CLIP_DATE_FIELDS`) never reads, so an older clip now indexed (DECISIONS 2026-11-09) shows undated though its file names a date; the test clips are made with no date (`-map_metadata -1`), the live count not taken (the share was offline this task). | decided: undated stays undated; the owner is going through all the videos to verify dates and tags himself (DECISIONS 2026-11-09 item 6) |

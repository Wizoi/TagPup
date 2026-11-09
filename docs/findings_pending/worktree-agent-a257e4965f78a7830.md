# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-09 | low: Files: clip_patch refuses (the rewrite's, nothing written) a clear of a clip's Windows Tags when its Xtra holds no WM/Category text (an empty Xtra: 10 live clips per cut A's count; one holding other Windows entries alone), although the move asks a clear only of a clip whose Category ExifTool read: a journaled move plans from what it reads, and a clip with no Category read holds what it is to hold already, so the writer is never asked (tests/test_a_clips_windows_tags_freed.py: no Xtra and an empty Xtra write nothing, no change). The refusal is reached only by a Category ExifTool read from bytes clip_patch.windows_tags does not read as one. | decided: by design; the refusal is the guard that the four bytes freed are the Xtra whose Tags were moved, and a clip whose Xtra holds no Tags has nothing to move |

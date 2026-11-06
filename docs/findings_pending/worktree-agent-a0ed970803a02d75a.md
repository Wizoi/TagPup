# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-06 | wrong: a hard kill of the caption sweep's --apply (its console window closed, not Ctrl+C) leaves its one change holding up to about 43,700 files `planned` (photo_index, counted 2026-11-06), and whatever opens the library next settles them all forward (file_changes.settle_once through runtime.library_settings, the server's start among them) with no chunk, no Cancel and no progress: hours on the share before that library answers. Ctrl+C is safe (the planned files are withdrawn, #1493). The same holds for any uncapped write_fields (the tag tree's rewrites). Not fixed on the sweep's branch: it is file_changes' settle (withdraw a batch's planned files at settle, or settle in chunks). The re-run after a kill is correct (test_a_caption_repeating_the_title_is_cleared); only its time is the finding. | open |

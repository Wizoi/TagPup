# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-07 | low: a title cleared in the panel of a clip whose QuickTime Title holds one (2,017 photo_index clips hold a title there alone, 1 there and in XMP:Title alike, counted 2026-11-07 read-only) clears XMP:Title only, and the QuickTime Title is the clip's title again: by the rule (DECISIONS 2026-11-07, item 3: XMP:Title, else the QuickTime Title; a save never writes the QuickTime Title), so a clip's QuickTime title cannot be cleared from TagPup. Not refused, as a tag kept in QuickTime is (segment_edits.KEPT_IN_QUICKTIME): the save says it saved, and the page shows the QuickTime title back (tests/test_a_clip_titled_in_quicktime.py asserts it). Proposed closed decided: the rule's consequence; a refusal would be a new rule for the owner. | open |
| B-2 | 2026-11-07 | low: a save of a photo whose file held the save already followed its row with every field it read (file_changes._follow, store.photos.follow_fields), and a group read empty took the bare name out of the row whatever group it held: the photo's empty QuickTime Subject (fields.CLIP_KEYWORDS, read on every keyword save) took out the bare Subject the scan stores, so the row no longer said what a scan reads; with the QuickTime Title read by the save (#2594) the bare Title went the same way. The bare name is now taken out only when it was that group's copy; tests/test_tag_a_photo_read_it_back_and_undo.py's new assertion failed on the old code. | fixed |

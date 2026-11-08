# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-07 | low: `field_values.CLIP_PATCH_IN_PLACE` is True since the 20-copy trial (DECISIONS 2026-11-07, item 4); its off arm (`field_values.writes` answering () for a clip, every clip write refused CLIP_NOT_EDITED) has no live instance after the flip. Kept as the owner's off switch, by the brief; to be removed when the owner says (M7 flip, cut 1) | open |
| B-2 | 2026-11-07 | low: a page record now carries `writes` (field_values.writes), but a folder scan the server or the browser kept from before the install carries none, and one kept from before the owner turned writing clips off still says a clip's ["tags", "title"]; the save is refused by the server either way (CLIP_NOT_EDITED), so nothing is written wrongly. For the cut 2 brief: the page reads a missing `writes` as the record's media used to mean, never as "anything may change" (M7 flip, cut 1) | open |
| B-3 | 2026-11-07 | low: decision (M7 flip, cut 1, #2556): with segment_edits.new_packet no longer answering a Refusal (the QuickTime rule moved to the plan, file_changes.KEPT_IN_QUICKTIME), `in_place.patch_clip`'s arm raising a Refusal as Refused had no producer and was deleted; `leave_out_clips` admits a clip only to a write whose read names fields.CLIP_KEYWORDS, so a future writer that reads no QuickTime keywords is refused CLIP_NOT_EDITED rather than taking a clip's last keyword off (mutation check: with the plan's check disabled the new panel test fails) | decided: M7 flip cut 1 |

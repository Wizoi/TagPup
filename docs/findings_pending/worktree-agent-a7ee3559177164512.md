# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | wrong: with `field_values.CLIP_PATCH_IN_PLACE` on, a bulk tag add or a Save of one of the 14 live .mp4 clips whose keywords are only in QuickTime:Subject plans from `tagging.KEYWORD_READ`/`_tags_held` (IPTC and XMP only), so the clip's XMP gets the new tag alone and its QuickTime keywords stop being read (the index reads XMP first, `vocabulary.KEYWORD_COPIES`): the owner sees the old tags disappear. Only removing the last keyword is refused (`segment_edits.KEPT_IN_QUICKTIME`). Behind the flag (off); to settle before it is switched on and before M7 B3 merges: a clip's keyword plan starts from its QuickTime keywords too. | open |
| B-2 | 2026-11-05 | low: decision (main session, M7 B3.2): Just look always refuses a clip, flag on or off (`file_only.write_fields` passes no `read` to `file_changes.leave_out_clips`), because with no journal a clip patch cut short by a crash could not be settled. | decided: main session, M7 B3.2; DECISIONS 2026-11-05 M7 row |
| B-3 | 2026-11-05 | low: decision (main session, M7 B3.2): a clip the writer refuses at write time (open in another program, `images.CLIP_NOT_PATCHED`, `segment_edits.KEPT_IN_QUICKTIME`; a `core.result.Refused`) is that clip's error and does not stop a `stop_at_first_error` run (Add to all selected), as a clip `leave_out_clips` refuses does not; any other write error still stops it. | decided: main session, M7 B3.2 (a0c88ddc) |

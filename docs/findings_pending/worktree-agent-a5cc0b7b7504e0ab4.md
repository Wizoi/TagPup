# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: an undo of a clip's first XMP write leaves the XMP box that write appended (its date emptied): the clip's own bytes are as before, the file is not byte for byte. 627 of the 669 photo_index clips `scripts/date_undated_clips.py` lists hold no XMP field in their row (counted read-only 2026-11-13), so undoing its change leaves about that many emptied boxes; a panel save's undo does the same today (`tests/test_dating_clips_by_their_names.py` asserts the prefix only). For the Media owner: by design, or should an undo cut the box it appended? | open |
| B-2 | 2026-11-13 | low: the 11 photo_index clips dated only in their folder stay undated (`taken` None, owner 2026-11-13), but each row's `year` comes from the folder's name (`dates.photo_year` falls back to `year_in_name`, which walks the folders), so they show a year while undated: 11 of 11, counted read-only 2026-11-13. A rule older than this branch, for every photo; is the folder's year still wanted for a clip the owner chose not to date by its folder? | open |
| B-3 | 2026-11-13 | low: `scripts/date_undated_clips.py` copies the Ctrl+C handling (`Progress`, `stopped_said`) and `library_at` from `scripts/clear_duplicate_captions.py` (a third copy after the deleted `move_windows_tags.py`); scripts may not import one another (tests/test_layers.py). Decided here, not copied from the brief: kept as a copy because both scripts are one-offs to be deleted; a fourth would want a shared `scripts/` helper. | open |

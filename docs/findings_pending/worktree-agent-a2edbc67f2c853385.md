# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | low: a grown patch cut by a power loss (not a process kill) can leave the file at its new size with the appended run as zeros (the file system extends the file before the data reaches the disk); `in_place.held` answers NEITHER, so the settle makes it a conflict, never overwritten, and the owner settles it by hand. Not handled by a branch: no live instance (0 patch records in the three libraries, counted read-only 2026-11-05) and a byte that is neither old nor new is by the record's rule another writer's. Test: `test_a_patch_that_grows_a_file_is_settled_by_its_bytes` ("zeros where the box was to be"). | decided: by design, a conflict is the safe answer for a byte the patch neither found nor wrote (DECISIONS 2026-11-05, record v2) |
| B-2 | 2026-11-05 | low: a patch record an earlier TagPup saved (v1, no `size_after`) met by this one: `in_place.held` answers NEITHER for any record whose `v` is not `VERSION`, and `_by_bytes`' conflict note says its patch was saved by an earlier TagPup. Live instance: 0 (counted read-only 2026-11-05), but the installed app writes in place today (`field_values.PATCH_IN_PLACE = True`), so a crash before the next install makes one; without the line `_held` raised KeyError out of the settle (test `test_a_record_an_earlier_tagpup_saved_is_a_conflict_never_overwritten`, failing on the old code with KeyError 'size_after'). | fixed: in this branch |

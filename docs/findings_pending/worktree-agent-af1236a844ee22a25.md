# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-17 | low: tests (4d of docs/PLAN_OWN_METADATA.md, merge efe92d1f): five scenario- and slow-tier files still expected the deleted ExifTool header-copy patch, which wrote every copy of a keyword, title or caption (test_a_photos_title_and_caption, test_taxonomy_lifecycle, test_file_journal APatchInPlace, test_a_crash_part_way_is_settled_at_the_next_start AnInPlacePatchCutShort, test_a_caption_repeating_the_title_is_cleared); the 4d worker ran --fast only. Each checked against the own writer's contract (DECISIONS 2026-11-03, fields.XMP_ONLY, files.md 'Known, by this design'): stale, not a regression; no writer changed. Three more files ran fewer tests on purpose (4d deleted 4 of test_a_photo_patched_in_place and 1 of test_the_block_editors_have_one_caller; the revert a78aca23 took 2 of test_a_clip_is_indexed_as_a_video): `run_tests.py --accept-fewer` records them. user action: the full suite run on the trunk after the 4d merge. | fixed: 6b13c7a9 |

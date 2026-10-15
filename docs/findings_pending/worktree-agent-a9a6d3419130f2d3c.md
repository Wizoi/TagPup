# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: scenario regrouping merge 0 (placement only): a scenario file may now name more than one id, and its owner is the owner of its first id (tests/owners.py `_build`, which overwrote the owner and ids per id; tests/test_guard_tests_have_one_owner.py asserted every id belonged to the file's owner, so test_roots_scenarios could not be D7 and I6). Decision made, not copied from a spec. | open |
| B-2 | 2026-10-15 | low: scenario ids D3, D4, D6, F7 already existed in tests/owners.py with no scenario file; merge 0 reworded D3, D4, D6, F7 to the owners' sentences and added D8, F9-F11, P6, P7, V6-V9, I9. D6, D8, F7, F10, F11 (F11 only through test_index_progress_end_to_end), P6, P7, V7, V8, V9, I9 have no scenario file yet: the files that would run them (test_settings, test_migrations, test_unreachable_is_not_missing, test_photo_identity, test_selection_*, test_suggestions_follow_their_photo, ...) are kind unit/migration today and become scenario files in the owners' own regroup merges. | open |
| B-3 | 2026-10-15 | low: kinds chosen by the charter Scope where the lists were silent: test_journal D2+D3, test_maintenance D2, test_photo_people_follow_their_sources D5 (not P2), test_reexamine_folder P3, test_a_person_count_reads_only_their_photos P4 (it moved to the scenario tier), test_refresh_rows F6, test_rows_only_in_folders_added F2, test_index_progress_end_to_end F6+F11, test_library_being_migrated moved from kind migration to scenario I4. | open |

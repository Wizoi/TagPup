"""Which tier each test file belongs to. tools/run_tests.py reads this.

    fast      the default tier of the loop: everything not named below. Core, store and
              service tests on a temporary library, plan tests, source guards, the fake
              ExifTool, Flask's test client.
    scenario  a whole job from start to end: crash and resume, two writers at once,
              the file journal against real files, the folder watcher, real ExifTool
              contract tests. Each is worth running at a merge; in an edit loop it is
              most of the time (about 60% of the suite's seconds in about 10% of its files).
    slow      real processes and real time: the hand-over of one server version to another,
              the installer, and a 68,000-photo library built from nothing.

`tools/run_tests.py` runs all three unless told otherwise: the whole suite is still what
a commit and a merge run. `--fast` leaves the other two out for the edit loop, `--no-slow`
leaves out the slow one. A file is in exactly one tier, and
tests/test_tiers_name_real_files.py fails a name that is not a test file, so a rename
cannot quietly move a scenario into the fast tier.

Which file goes where is by what it does, not by its time alone (the times, from
tests/.durations.json on 2026-10-09, only say where to look). A new file is fast until
it is added here.
"""

SLOW = frozenset({
    "test_roots_at_scale",
    "test_supervisor_hand_over",
    "test_launcher_hands_over",
    "test_install_app",
    "test_install_hands_over",
})

SCENARIO = frozenset({
    # Bulk edits: a job over many photos, its crashes, its resume, real ExifTool.
    "test_bulk_edits",
    "test_bulk_edits_resume_records",
    "test_bulk_edits_progress_record",
    "test_bulk_edits_crashes",
    "test_bulk_edits_real_exiftool",
    "test_bulk_writes_start_from_the_file",
    # The file journal and the writes it covers.
    "test_file_journal",
    "test_stale_record_precondition",
    "test_derived_follow_writes",
    "test_just_look_edits",
    "test_just_look_delete_and_rename_rules",
    "test_just_look_bin_and_long_names",
    # Writes that stop part-way or meet a damaged photo, with a real ExifTool.
    "test_partial_writes_leave_rows_unread",
    "test_no_writes_to_damaged_photos",
    "test_damaged_photos_reindexed",
    "test_damaged_photos_are_remembered",
    "test_rotate_keeps_the_photo",
    "test_taxonomy_lifecycle",
    # Faces and tags kept in step across files, jobs and the CLI's journaled apply.
    "test_bulk_faces_and_tags",
    "test_naming_faces_job",
    "test_faces_and_tags_in_step",
    "test_faces_from_tags_in_a_folder",
    "test_face_people_follow_file_changes",
    # Two real processes on one library, and folders renamed or marked on disk.
    "test_people_by_id_at_once",
    "test_migration_28_and_the_newer_library",
    "test_folder_ids",
    "test_folder_ids_scenarios",
    "test_reread_fields",
    # Roots: the folders a library holds, through a whole scenario.
    "test_roots_scenarios",
    "test_roots_adoption",
    "test_roots_location",
    "test_roots_ingress",
    "test_roots_store",
    # Real time and real processes, short of a hand-over.
    "test_folder_watcher",
    "test_supervisor",
    "test_mcp_server",
    "test_tagpup_server_api",
    # Real ExifTool against a table of cases.
    "test_person_filing_table",
    # Suggest and the analyse-only run, start to end.
    "test_analyse_only_looks_are_kept",
    "test_analyse_only_suggest",
})

TIERS = ("fast", "scenario", "slow")


def tier_of(module):
    """'slow', 'scenario' or 'fast' for a test module's name (test_x)."""
    if module in SLOW:
        return "slow"
    if module in SCENARIO:
        return "scenario"
    return "fast"

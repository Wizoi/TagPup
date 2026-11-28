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
    page      the page tests, tests/frontend/*.test.mjs, each file under `node --test` (jsdom):
              every one of them, by its name, and nothing else.

`tools/run_tests.py` runs all four unless told otherwise: the whole suite is still what
a commit and a merge run. `--fast` leaves out scenario, slow and page for the edit loop, `--no-slow`
leaves out the slow one. A file is in exactly one tier, and
tests/test_the_test_map.py fails a name that is not a test file, so a rename
cannot quietly move a scenario into the fast tier.

Which file goes where is by what it does, not by its time alone (the times, from
tests/.durations.json on 2026-10-09, only say where to look). A new file is fast until
it is added here.
"""

SLOW = frozenset({
    "test_roots_at_scale",
    "test_launcher_hands_over",
    "test_install_app",
    "test_install_hands_over",
})

SCENARIO = frozenset({
    # Bulk edits: a job over many photos, its crashes, its resume, real ExifTool.
    "test_bulk_edits",
    "test_bulk_edits_stalled_exiftool",
    "test_bulk_edits_real_exiftool",
    "test_bulk_writes_start_from_the_file",
    "test_a_write_through_the_one_entry_point",
    "test_a_bake_is_planned_from_the_library",
    "test_a_caption_repeating_the_title_is_cleared",
    "test_a_long_change_cut_off_is_settled_in_chunks",
    # The file journal and the writes it covers.
    "test_file_journal",
    "test_a_photo_patched_in_place",
    "test_a_photo_written_by_its_own_editors",
    "test_the_two_writers_compared_on_copies",
    "test_a_clip_patched_in_place",
    "test_a_clip_tagged_through_the_journal",
    "test_a_clip_titled_in_quicktime",
    "test_a_clip_tag_undone",
    "test_a_tag_tree_edit_of_a_tag_a_clip_holds",
    "test_a_clip_patch_cut_short",
    "test_a_clip_dated_through_the_journal",
    "test_a_clip_dated_by_its_name",
    "test_a_clip_renamed",
    "test_a_clip_deleted",
    "test_a_clip_converted",
    "test_an_adobe_clip_packet_edited_and_read_back",
    "test_time_shift",
    "test_a_crash_part_way_is_settled_at_the_next_start",
    "test_a_file_holds_what_exiftool_kept",
    "test_stale_record_precondition",
    "test_a_photos_title_and_caption",
    "test_derived_follow_writes",
    "test_just_look_edits",
    "test_just_look_delete_and_rename_rules",
    "test_just_look_bin_and_long_names",
    # Writes that stop part-way, or an index that meets a damaged photo, with a real ExifTool.
    "test_partial_writes_leave_rows_unread",
    "test_no_writes_to_damaged_photos",
    "test_an_index_reads_the_next_photos_ahead",
    "test_a_clip_is_indexed_as_a_video",
    "test_a_clip_shows_its_poster",
    "test_an_index_reads_a_bake",
    "test_the_index_writes_the_bake",
    "test_photos_with_no_identity_are_given_one",
    "test_a_machine_without_a_card",
    "test_rotate_keeps_the_photo",
    "test_rotate_is_journaled",
    "test_rotate_crash_settles_with_the_boxes",
    "test_taxonomy_lifecycle",
    "test_tag_tree_edits_are_batches",
    # Faces and tags kept in step across files, jobs and the CLI's journaled apply.
    "test_bulk_faces_and_tags",
    "test_naming_faces_job",
    "test_faces_and_tags_in_step",
    "test_faces_from_tags_in_a_folder",
    "test_face_people_follow_file_changes",
    # Two real processes on one library, and folders renamed or marked on disk.
    "test_people_by_id_at_once",
    "test_two_cousins_called_alike",
    # Undo of every change kind after every later kind, real writers and real ExifTool (#1805).
    "test_undo_after_every_later_kind",
    "test_a_library_indexes_and_suggests_with_its_face_model",
    "test_a_face_in_the_background_is_one_answer",
    "test_a_library_from_a_newer_tagpup",
    # Roots: the folders a library holds, through a whole scenario.
    "test_roots_scenarios",
    # Pages run against the real apps (node + jsdom, tests/page_server.py), not a hand-written fake.
    "test_pages_against_the_real_server",
    "test_roots_location",
    "test_a_root_moved_during_a_long_undo_or_settle",
    "test_roots_ingress",
    "test_roots_store",
    # Real time and real processes, short of a hand-over.
    "test_folder_watcher",
    "test_mcp_server",
    "test_one_library_three_programs",
    # Real ExifTool against a table of cases.
    "test_person_filing_table",
    # Suggest and the analyse-only run, start to end.
    "test_analyse_only_looks_are_kept",
    "test_analyse_only_suggest",
    "test_suggest_then_apply_all",
    # The real CLI's index in a process, read as the server reads it.
    "test_index_progress_end_to_end",
    # The server's real --check in a process, and an install, on a machine without ffmpeg.
    "test_ffmpeg_is_a_prerequisite",
})

TIERS = ("fast", "scenario", "slow", "page")


def tier_of(module):
    """'slow', 'scenario' or 'fast' for a test module's name (test_x), 'page' for a page test's (x.test.mjs)."""
    if module.endswith(".test.mjs"):
        return "page"
    if module in SLOW:
        return "slow"
    if module in SCENARIO:
        return "scenario"
    return "fast"

"""Which domain owner each test file belongs to, and which scenario a scenario file runs. Read by
tools/run_tests.py (--owner, --scenarios, the per-owner line), tests/test_guard_tests_have_one_owner.py and
tests/test_owner_charters_name_real_paths.py; written like tests/tiers.py, by hand, in the commit that adds a test file.

    OWNERS     the five domain owners (docs/OWNERS.md): their charters are .claude/agents/tagpup-owner-<data, people,
               files, views-ai, integration>.md; `apps` is Integration and apps.
    SCENARIOS  {id: (owner, one sentence)}: a flow a person lives through. The letter of an id is its owner's (D data,
               P people, F files, V views, I apps). A charter lists its ids; a scenario file names the ones it runs,
               one or more (its owner is the owner of its first; test_roots_scenarios is D7 and I6).
    FILES      {test file: (owner, kind, scenario ids)} for every tests/test_*.py (the module name) and every
               tests/frontend/*.test.mjs (the file name).
    kind       scenario (runs a flow, names its ids), unit (one module's behaviour), migration (a schema step), guard (a
               rule about the source text), unmapped (legacy: no import or name placed it; apps owns it until it is
               placed; the count may only go down, tests/test_guard_tests_have_one_owner.py).

A file got its owner from the module of tagpup/ (or the page module of web/) it imports most, through the Scope in the
owner charter that lists it; a file naming no scope module was placed by its name or by hand. A new test file is added
to _FILES below under its owner; the guard names a file that is not.
"""
OWNERS = ("data", "people", "files", "views", "apps")
KINDS = ("scenario", "unit", "migration", "guard", "unmapped")
#: Finding numbers a test file once cited and may stop citing: only a number listed here may leave tests/findings_cited.txt's set.
RETIRED = frozenset({
    745,   # the supervisor's hung-path finding: the supervisor is deleted (DECISIONS 2026-10-11); its test went with it
})

SCENARIOS = {
    "D1": ("data", "A library one migration behind, or newer than the code, is served or refused at start without losing a row."),
    "D2": ("data", "A change is one journaled step and Undo puts every row of it back."),
    "D3": ("data", "A crash between writing and done, or a restart part-way through a write, is finished at the next start."),
    "D4": ("data", "A snapshot restore puts the library back as it was, the monthly from before roots included, under a running index."),
    "D5": ("data", "Every writer rebuilds the derived rows in its own transaction."),
    "D6": ("data", "A library's settings are its own: stamped, changed, undone, and read by the next run."),
    "D7": ("data", "The folders a library holds (roots) are stored, verified and followed through the same flows with and without them."),
    "D8": ("data", "A kept old snapshot migrates to the latest: interrupted, two openers, a failing check, a backup, an older app, and it is undoable."),
    "P1": ("people", "A person is the id of a tag-tree node: two processes naming, renaming or merging at once leave one answer."),
    "P2": ("people", "Names, tags and faces stay in step across photo files, jobs and the CLI's journaled apply."),
    "P3": ("people", "Identify faces: a cluster is named, ignored or split, and the page shows what the server says."),
    "P4": ("people", "Name faces from tags runs as a resumable job: a restart in the middle settles, a second click does not repeat."),
    "P5": ("people", "A tree edit (new, rename, merge, move) keeps every photo, face and filing in step."),
    "P6": ("people", "Two cousins called alike under two groups are named by id; a typed bare name that fits both is refused, naming both paths."),
    "P7": ("people", "Identify Faces: ignore, assign, restore and undo, against the real server."),
    "F1": ("files", "A photo write is journaled, one at a time, read back; a click three times writes once."),
    "F2": ("files", "Nothing is written into a photo cut short or into a folder the library does not hold."),
    "F3": ("files", "A bulk edit over many photos: dry run first, a crash or a stalled ExifTool part-way, then resume."),
    "F4": ("files", "A folder renamed or a photo moved is followed by sync and the watcher; sync never removes and never writes a file."),
    "F5": ("files", "Two libraries holding one folder stay each their own."),
    "F6": ("files", "A row never claims more than was read: an index or a partial write stamps only what described the file."),
    "F7": ("files", "A share asleep is not missing."),
    "F8": ("files", "Just look edits a file only, and delete, rename and the recycle bin keep their rules."),
    "F9": ("files", "A run holds one map of the roots; a root moved meanwhile stops a write with a sentence."),
    "F10": ("files", "One photo whatever its path's spelling."),
    "F11": ("files", "Add a folder: it is queued, indexed by the CLI with progress, and a second run reads nothing."),
    "V1": ("views", "A view lists each photo once, in the owner's order, with its count."),
    "V2": ("views", "A typed search ANDs its words and ranks nothing."),
    "V3": ("views", "Suggest, and the analyse-only run, from start to end."),
    "V4": ("views", "One process at a time has a model on the graphics card; the waiter is told who holds it."),
    "V5": ("views", "The navigator's counts are the photos under a node, each once."),
    "V6": ("views", "The server goes idle: models, vectors and caches are let go, and the next ask loads them again."),
    "V7": ("views", "Select all minus the excluded: the tally, the folders and a bulk edit read the same photos under the caps."),
    "V8": ("views", "A write elsewhere makes the open view and the cached scan read again."),
    "V9": ("views", "Vectors and suggestions follow their photo through rename, settle, rotate, delete and undo."),
    "I1": ("apps", "Switching library in the middle of a folder shows no card of the first."),
    "I2": ("apps", "A write through a server update is waited out and not repeated."),
    "I3": ("apps", "An update by a launch and by an install hands one server over to the next without killing its work."),
    "I4": ("apps", "A library one migration behind is served at start, and a request for one being migrated gets 503 at once."),
    "I5": ("apps", "A page loaded against the real server, not a fake one."),
    "I6": ("apps", "The same flows with and without roots, end to end."),
    "I7": ("apps", "The CLI, the MCP server and the always-on server act on one library at once."),
    "I8": ("apps", "A page save from a stale record is refused, not overwritten."),
    "I9": ("apps", "A page's saved scan of an older shape is read again, not trusted."),
}

#: scenario id -> its scenario files; a file listed under more than one runs more than one scenario (the first is its owner's).
_SCENARIO_FILES = {
    "D1": "test_migration_28_and_the_newer_library test_newer_library_guard_in_a_long_lived_process",
    "D2": "test_journal test_maintenance",
    "D3": "test_journal test_a_crash_part_way_is_settled_at_the_next_start",
    "D4": "test_snapshots",
    "D5": "test_derived_follow_writes test_photo_people_follow_their_sources",
    "D6": "test_a_librarys_settings_are_its_own",
    "D7": (
        "test_library_roots test_roots_at_scale test_roots_ingress test_roots_location test_roots_scenarios "
        "test_roots_store"
    ),
    "F1": (
        "test_a_file_holds_what_exiftool_kept test_file_changes_one_at_a_time test_file_journal "
        "test_rotate_crash_settles_with_the_boxes test_rotate_is_journaled test_rotate_keeps_the_photo"
    ),
    "F2": "test_no_writes_to_damaged_photos test_rows_only_in_folders_added test_writes_refuse_cut_short_photos",
    "F3": (
        "test_batches test_bulk_edits test_bulk_edits_real_exiftool test_bulk_edits_stalled_exiftool "
        "test_bulk_writes_start_from_the_file"
    ),
    "F4": "test_folder_watcher test_sync",
    "F5": "test_scenario_two_libraries_share_a_folder",
    "F6": "test_index_progress_end_to_end test_partial_writes_leave_rows_unread test_refresh_rows",
    "F8": "test_just_look_bin_and_long_names test_just_look_delete_and_rename_rules test_just_look_edits",
    "F9": "test_roots_old_place test_roots_pinned_runs",
    "F11": "test_index_progress_end_to_end",
    "I1": "scenario-switching-library-mid-folder",
    "I2": "scenario-write-through-a-server-update test_server_drain",
    "I3": "test_install_app test_install_hands_over test_launcher_hands_over",
    "I4": "test_library_being_migrated test_server_migrates_at_start",
    "I5": "test_pages_against_the_real_server test_tagpup_server_api",
    "I6": "test_roots_scenarios",
    "I7": "test_mcp_server",
    "I8": "test_stale_record_precondition",
    "P1": "test_people_by_id_at_once",
    "P2": (
        "test_bulk_faces_and_tags test_clustering_meets_naming test_face_people_follow_file_changes "
        "test_faces_and_tags_in_step test_faces_from_tags_in_a_folder test_person_filing_table "
        "test_tags_from_faces_in_a_folder"
    ),
    "P3": "test_reexamine_folder",
    "P4": "test_a_person_count_reads_only_their_photos test_name_faces_routes test_naming_faces_job",
    "P5": "test_tag_tree_edits_are_batches test_taxonomy_lifecycle",
    "V1": "test_library_views_scenarios",
    "V2": "test_library_search",
    "V3": "test_analyse_only_looks_are_kept test_analyse_only_suggest",
    "V4": "test_one_gpu_at_a_time test_one_gpu_jobs",
    "V5": "test_selection_scenarios",
    "V6": "test_models_released_when_idle",
    "V7": "test_selection_scenarios",
    "V8": "test_library_views_scenarios",
}

#: owner -> kind -> the unit, migration and guard files, a .mjs without its ".test.mjs".
_FILES = {
    "data": {
        "unit": (
            "tag-rules "
            "test_backups_are_kept_to_five test_bulk_scripts_back_up "
            "test_compact_gives_back_free_space test_derived_batches test_derived_tables test_doctor "
            "test_every_face_has_a_photo_row test_face_save_retries_a_lock "
            "test_faces_detected_record "
            "test_folder_membership test_journal_undo_refusals "
            "test_mcp_photo_against_file test_path_locker "
            "test_roots_unplaced test_roots_verify test_search_words "
            "test_services_refuse_what_the_rules_refuse "
            "test_sync_records test_the_tree_is_the_librarys test_unused_tables test_unused_tables_31 "
            "test_unused_tables_32 test_validation test_view_indexes"
        ),
        "migration": "test_a_library_that_cannot_be_migrated_is_still_served test_migrations test_schema",
        "guard": "test_db_access test_derived_writers test_journal_keys_and_cascades test_photo_rows test_sandbox_roots",
    },
    "people": {
        "unit": (
            "face-strip grid-build-progress identify-faces identify-faces-cluster-buttons identify-faces-counts "
            "identify-faces-in-place-removal library-sort-and-organize-header name-faces organize-faces person-directory "
            "person-faces person-filing-table person-names person-tag-form review-dialog review-from-activity "
            "show-matched-toggle tag-editor-in-place tag-order tag-view tag-vocabulary tagpup-people-by-id "
            "tagpup-people-lists-by-id tags-shown-alphabetical test_a_face_is_named_by_a_person_not_a_path "
            "test_a_library_keeps_a_face_root test_a_listed_person_of_a_gone_node test_an_ambiguous_person_gets_no_path "
            "test_answers_carry_their_person test_automatch_references test_automatch_writes_once test_cluster_candidates "
            "test_clustering_evidence test_dedupe_faces_keeps_decisions test_every_tag_has_an_id "
            "test_excluded_faces_stay_unnamed test_face_click_reads_named_faces_once test_face_clustering_rules "
            "test_face_exclusion test_face_names_survive_keyword_writes test_face_roots_are_the_trees "
            "test_faces_from_tags test_folder_scope_roots test_group_tag_is_not_a_person "
            "test_grouping_keeps_names_the_tags_confirm test_hidden_people_still_exist "
            "test_identify_cache_keeps_others_writes test_identify_cache_sees_every_change test_identify_faces_hot_paths "
            "test_index_resolves_people test_journal_through_the_services test_name_faces_routes_in_a_folder "
            "test_naming_faces_in_a_folder test_new_person_reads_the_pool_once test_organize_faces test_people_by_id_tree "
            "test_people_by_id_writers test_people_cache_sees_other_processes test_people_representative_faces "
            "test_people_resolved_on_write test_people_vocabulary test_person_face_samples test_person_ids "
            "test_person_rename_reaches_the_files test_person_tag_duplication test_queued_clustering_is_guarded "
            "test_reindex_keeps_face_decisions test_server_lists_are_alphabetical test_service_faces test_service_tags "
            "test_shared_taxonomy_routes test_stability test_store_people test_store_taxonomy_add_path test_tag_order "
            "test_tag_sort_key_edges test_tag_view_api test_tags_are_checked_where_they_are_set test_tags_from_faces "
            "test_taking_a_person_off_unnames_the_face test_taxonomy_edits_report_what_they_wrote test_top_level_person "
            "test_trace_file_per_library test_tree_fill test_tuner_photo_copy_upright test_tuner_reads "
            "test_tuner_server_api test_two_people_one_leaf_on_a_photo test_vocabulary tuner-assign-label-tells-the-truth "
            "tuner-face-zoom tuner-hidden-people-exist tuner-lists-alphabetical tuner-new-person-group "
            "tuner-people-by-face tuner-people-by-id tuner-reexamine-folder tuner-refresh-keeps-the-grid-loading "
            "tuner-select-all-is-linear tuner-starts-last tuner-suggestions-belong-to-their-grid "
            "tuner-undo-puts-faces-back"
        ),
        "guard": (
            "common-modules test_face_values_have_one_owner test_face_writes_go_through_the_assignment "
            "test_no_person_row_without_an_id test_person_ids_single_owner test_write_routes_are_classified"
        ),
    },
    "files": {
        "unit": (
            "bulk-edit bulk-writes-queue just-look-edits path-helpers photo-write-queue rename-failed retried-save "
            "rotate-refreshes-thumbnails smart-rename-count "
            "test_a_case_only_rename_settles "
            "test_a_cut_keyword_is_not_planned_again "
            "test_a_file_undo_waits_for_newer_changes "
            "test_a_partial_bulk_write_says_what_it_wrote test_a_row_describes_its_file_one_way "
            "test_a_save_is_one_session test_a_saved_row_is_the_indexers_shape "
            "test_apply_all_stopped_half_way_says_what_it_wrote test_batches_go_through_the_model test_bulk_cap "
            "test_bulk_delete test_bulk_tags_missing test_bulk_writes_update_the_index "
            "test_cli_path_spelling test_dates test_delete_through_this_pc test_derived_file_changes "
            "test_exiftool_encoding test_exiftool_says_why test_exiftool_session test_functional_cli "
            "test_index_adds_the_folder_it_is_given test_index_queue_active_is_one_read test_index_queue_is_locked "
            "test_index_reports_failed_clustering test_index_startup_reads test_jobs_indexing "
            "test_keyword_fields test_lock_owners test_membership_is_bounded "
            "test_metadata_extraction test_minor_errors_in_a_journaled_write test_one_answer_to_the_librarys_folders "
            "test_path_spelling_in_faces_and_records test_paths "
            "test_paths_roots test_photo_identity test_photo_meta test_refresh_rows_guards "
            "test_relink_checks_the_destination test_relink_stays_in_its_folder test_remove_folder_lists_the_library "
            "test_rename_keeps_the_index test_rename_settle_knows_a_copy test_renaming "
            "test_save_photo_rename_is_journaled test_save_reply_says_rename_failed test_service_change_tags "
            "test_service_delete test_service_indexing test_service_page_images test_service_replace_tag "
            "test_service_save_photo test_service_shift_date_taken test_service_smart_rename "
            "test_settling_carries_vectors test_single_save_and_smart_rename_are_journaled "
            "test_smart_rename_stays_put test_sync_counts_a_size_change test_tag_write_follows_the_keyword_rules "
            "test_tag_write_is_journaled test_tag_writes_keep_the_index_true test_tagpup_server_finds_native_rows "
            "test_thumbnails test_time_shift_records_every_date test_time_shift_tells_the_index "
            "test_tree_grows_only_from_folders_added test_unreachable_is_not_missing test_what_counts_as_a_photo "
            "test_when_a_photo_was_taken_is_kept test_writes_only_in_folders_held tuner-picker-and-queue "
            "tuner-remove-folder unsaved-edits write-queue-status"
        ),
    },
    "views": {
        "unit": (
            "details-panel grid-keys grid-window library-header-strip-toolbar library-moves library-photo-back "
            "library-selection library-selection-jumps library-tally library-view looking-suggestions navigator "
            "navigator-multi photo-camera photo-position photo-suggestions-panel search-page selection "
            "selection-follows-the-photo selection-panel selection-suggestions stale-cards suggest-cancel tagging-flow "
            "test_a_search_keeps_no_thread_buffers "
            "test_auto_apply_writes_what_it_offers test_camera_words test_caption_order test_chip_keys_follow_the_server "
            "test_clip_sees_photos_upright test_embeddings_follow_their_photo "
            "test_every_library_keeps_its_saved_suggestions "
            "test_index_reload_reuses_its_connection test_jobs_suggestions "
            "test_library_union_and_order test_library_view test_library_view_plans test_models_in_use_survive_an_unload "
            "test_one_value_for_a_suggested_tag test_page_caches_released_when_idle "
            "test_person_tag_edges test_photo_index_keeps_only_vectors test_rename_moves_suggestions_and_embeddings "
            "test_saved_suggestions_do_not_cancel_a_run test_selection test_selection_tally test_settings "
            "test_suggest_index_stays_current test_suggest_reads_people_once test_suggest_respects_face_decisions "
            "test_suggest_status_poll_is_a_snapshot test_suggester_scoring test_suggestions_follow_their_photo "
            "test_suggestions_keep_hierarchy test_suggestions_pipeline test_tag_usage_from_photo_tags "
            "test_the_runtime_reaches_suggest test_thumbs_cli test_vector_index test_vectors_and_pool_released_when_idle "
            "test_thumbnail_routes test_web_beside_its_requests test_zero_shot_skips_people vgrid"
        ),
        "guard": "test_gpu_single_owner test_no_test_loads_a_model",
    },
    "apps": {
        "unit": (
            "activity-page add-folder-dialog apply-all-failure-undo banner-cost batch-strip database-routing "
            "delete-while-folder-changes dialog-frames dialogs-keep-the-keys dom-output folder-keeps-history-position "
            "folder-opening folder-picker gear history-dialog image-zoom images-after-an-update library-memory "
            "library-picker open-photo page-state progress-polling roots-dialog server-replaced server-update "
            "settings-dialog stale-record test_a_mistyped_library_is_not_created test_a_request_is_decoded_once "
            "test_activity_routes test_add_findings test_an_empty_folder_is_scanned_again test_background_tasks "
            "test_background_work_is_waited_for test_batch_strip_replies test_browser_opens_once test_config "
            "test_dark_selects test_database_context_isolation test_folder_scan_follows_the_library test_gzip_json "
            "test_heavy_imports_stay_lazy test_history_routes test_indexing_names_a_real_control "
            "test_inspect_reads_only_the_photos_asked_about test_inspect_uses_the_stores_mtime_rule "
            "test_launcher_refuses_a_library_it_cannot_make test_library "
            "test_library_registry_race test_local_server_socket test_log_runs test_logs "
            "test_logs_are_bounded test_mcp_config_resolves test_mcp_write_tools test_multiple_databases "
            "test_only_the_server_runs_jobs test_patch_tool test_photo_open test_recurring_jobs test_result "
            "test_roots_ingress_keys_cover_the_routes test_roots_map test_roots_open_check test_roots_routes "
            "test_run_tests_keeps_failures test_server_startup test_serving_photos test_suggest_thread_keeps_its_library "
            "test_test_processes_are_ended test_test_servers_run_no_jobs test_the_reaper_is_quiet test_unload_models_now "
            "test_web_app undo-from-reply validation"
        ),
        "guard": (
            "dom-contract no-heredoc-escapes page-modules page-shape test_config_single_owner test_database_axis_guard "
            "test_guard_tests_have_one_owner test_layers test_lint test_owner_charters_name_real_paths "
            "test_page_module_names test_processes_single_owner test_rules_have_one_owner test_sandbox_has_all_the_code "
            "test_sandboxes_are_homes_of_their_own test_spec_accuracy test_sync_entry_points "
            "test_tests_have_homes_of_their_own test_tiers_name_real_files test_verify_workflow_is_sandboxed"
        ),
    },
}

def _build():
    files = {}

    def put(names, owner, kind, ids=()):
        for name in names.split():
            name = name if name.startswith("test_") else name + ".test.mjs"
            if name in files and kind == "scenario":   # a second scenario of one file: the owner stays its first scenario's
                owner, ids = files[name][0], files[name][2] + ids
            files[name] = (owner, kind, ids)
    for owner, kinds in _FILES.items():
        for kind, names in kinds.items():
            put(names, owner, kind)
    for sid, names in _SCENARIO_FILES.items():
        put(names, SCENARIOS[sid][0], "scenario", (sid,))
    return files


FILES = _build()


def files_of(owner, kind=None):
    """The Python test modules (test_x) of an owner, or of one kind of it, sorted."""
    return sorted(n for n, (o, k, _ids) in FILES.items() if o == owner and n.startswith("test_") and kind in (None, k))


def page_files_of(owner):
    """The .test.mjs files of an owner, sorted."""
    return sorted(n for n, (o, _k, _ids) in FILES.items() if o == owner and n.endswith(".test.mjs"))

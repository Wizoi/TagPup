"""Which domain owner each test file belongs to, and which scenario a scenario file runs. Read by
tools/run_tests.py (--owner, --scenarios, the per-owner line), tests/test_the_test_map.py; written like tests/tiers.py, by hand, in the commit that adds a test file.

    OWNERS     the five domain owners (docs/OWNERS.md): their charters are .claude/agents/tagpup-owner-<data, people,
               files, views-ai, integration>.md; `apps` is Integration and apps.
    SCENARIOS  {id: (owner, one sentence)}: a flow a person lives through. The letter of an id is its owner's (D data,
               P people, F files, V views, I apps). A charter lists its ids; a scenario file names the ones it runs,
               one or more (its owner is the owner of its first; test_roots_scenarios is D7 and I6).
    FILES      {test file: (owner, kind, scenario ids)} for every tests/test_*.py (the module name) and every
               tests/frontend/*.test.mjs (the file name).
    kind       scenario (runs a flow, names its ids), unit (one module's behaviour), migration (a schema step), guard (a
               rule about the source text), unmapped (legacy: no import or name placed it; apps owns it until it is
               placed; the count may only go down, tests/test_the_test_map.py).

A file got its owner from the module of tagpup/ (or the page module of web/) it imports most, through the Scope in the
owner charter that lists it; a file naming no scope module was placed by its name or by hand. A new test file is added
to _FILES below under its owner; the guard names a file that is not.
"""
OWNERS = ("data", "people", "files", "views", "apps")
KINDS = ("scenario", "unit", "migration", "guard", "unmapped")
#: Finding numbers a test file once cited and may stop citing: only a number listed here may leave tests/findings_cited.txt's set.
RETIRED = frozenset({
    745,   # the supervisor's hung-path finding: the supervisor is deleted (DECISIONS 2026-10-11); its test went with it
    80,    # migration below the baseline deleted (migration 4's face counter); its test went with it (owner, 2026-10-15)
    81,    # migration below the baseline deleted (migration 4's photo id counter); its test went with it
    82,    # migration below the baseline deleted (migration 4's stored form of a never-indexed photo); its test went with it
    83,    # migration below the baseline deleted (migration 4 with foreign keys on); its test went with it
    96,    # migration below the baseline deleted (migration 7's saved suggestions under two folders); its test went with it
    753,   # migration below the baseline deleted (migration 24's word index being made, answered 503); its test went with it
    250,   # refresh_rows' planner reading a row never read: the planner is deleted (DECISIONS 2026-10-28); its test went with it
})

SCENARIOS = {
    "D1": ("data", "A library one migration behind, or newer than the code, is served or refused at start without losing a row."),
    "D2": ("data", "A change is one journaled step and Undo puts every row of it back."),
    "D3": ("data", "A crash between writing and done, or a restart part-way through a write, is finished at the next start."),
    "D4": ("data", "A snapshot restore puts the library back as it was, a monthly from before the library had roots included, under a running index; one below the baseline is refused."),
    "D5": ("data", "Every writer rebuilds the derived rows in its own transaction."),
    "D6": ("data", "A library's settings are its own: stamped, changed, undone, and read by the next run."),
    "D7": ("data", "The folders a library holds (roots) are stored, verified and followed through the same flows with and without them."),
    "D9": ("data", "A run nests under the run of its library that queued it and says how far it got, at most every 10 s, skipped while the library is written, never a path; one gone or another process's is left alone."),
    "D8": ("data", "A library kept at the baseline meets an additive and a destructive step: interrupted, two openers, a failing check, a backup, an older app, undoable; one below the baseline is refused untouched; the owner's library at 32 meets the real 33, at 33 the real 34, at 34 the real 35, at 35 the real 36, and at 36 the real 37."),
    "P1": ("people", "A person is the id of a tag-tree node: two processes naming, renaming or merging at once leave one answer."),
    "P2": ("people", "Names, tags and faces stay in step across photo files, jobs and the CLI's journaled apply."),
    "P3": ("people", "Identify faces: a cluster is named, ignored or split, and the page shows what the server says."),
    "P4": ("people", "Name faces from tags runs as a resumable job: a restart in the middle settles, a second click does not repeat."),
    "P5": ("people", "A tree edit (new, rename, merge, move) keeps every photo, face and filing in step."),
    "P6": ("people", "Two cousins called alike under two groups are named by id; a typed bare name that fits both is refused, naming both paths."),
    "P7": ("people", "Identify Faces: ignore, assign, restore and undo, against the real server."),
    "P10": ("people", "A library on its face model: a new photo is indexed and Suggested by it, the face under the floor not made, and Name faces from tags reads its vectors by its own cuts."),
    "P11": ("people", "The same face gets the same nearest person in Suggest, Identify, the strip, the boxes and Name faces from tags, a person who changed over the years by the faces of the face's own years, an undated face by every year."),
    "F1": ("files", "A photo write is journaled, one at a time, read back; a click three times writes once."),
    "F2": ("files", "The indexer writes nothing into a photo that does not decode; nothing is written into a folder the library does not hold."),
    "F3": ("files", "A bulk edit over many photos: dry run first, a crash or a stalled ExifTool part-way, then resume."),
    "F4": ("files", "A folder renamed or a photo moved is followed by sync and the watcher; sync never writes a file, and removes only a row it looked for and did not find, its root confirmed there."),
    "F5": ("files", "Two libraries holding one folder stay each their own."),
    "F6": ("files", "A row never claims more than was read: an index or a partial write stamps only what described the file."),
    "F7": ("files", "A share asleep is not missing."),
    "F8": ("files", "Just look edits a file only, and delete, rename and the recycle bin keep their rules."),
    "F9": ("files", "A run holds one map of the roots; a root moved meanwhile stops a write with a sentence."),
    "F10": ("files", "One photo whatever its path's spelling."),
    "F11": ("files", "Add a folder: it is queued, indexed by the CLI with progress, its photos' bakes read from their files under the library's keys, followed from every entry point, when it recorded a photo, by a matching of the faces it made that writes nothing, and a second run reads nothing."),
    "F12": ("files", "A clip in a folder is indexed as a row of kind video, dated by its XMP dates first, then its QuickTime ones, with no vector; the owed scan never queues it again, the folder view and a time shift leave it out, and the photos beside it are indexed as before; sync follows a clip moved or renamed, reads again one whose copy went on, a clip cut short is a row never decoded, and two libraries holding one folder of clips keep their own rows; a clip's tags and title are written in place into its XMP, a cleared clip title frees its QuickTime title and its undo gives it back, its Date Taken is written into its XMP dates (xmp:CreateDate only where it holds one), its QuickTime dates left byte for byte, undone from History, refused outside the library and settled when cut short, and the clips stored before that rule are dated again from their rows (a dry run that writes nothing, one change, a clip saved meanwhile skipped); a turn naming a clip leaves its file as it was with a sentence, the photos beside it written; a clip of up to 2 GiB is deleted as a photo is (from a share through this PC, its copy whole, its vector, frames and poster with it, the lock let go of after each clip of a bulk delete), a larger one, on any drive, deleted for good only once the owner confirmed the warning naming it, never copied and counted in no copy (unconfirmed, refused untouched with its size; a question that did not name it never deletes it for good; one held open is left with its row), the photos and smaller clips beside it to the Bin; a file its own drive's Recycle Bin would not keep is deleted for good only when the owner confirmed a warning naming it (the delete-check counts and names it, its token covers the warning, an unconfirmed start refused with nothing touched) and the reply says for good, never the Recycle Bin; a clip of a folder the library holds is Smart Renamed (a day's selection, its button, a Save after its title) with the photos, the name it had kept first in its XMP, undone from History, a clip the patch cannot place refused before anything is renamed, and a clip of a folder it does not hold keeps its name; a clip's tag save undone puts its XMP back, and while writing clips is off its undo is refused whole, the change still applied; a clip's write cut short is settled by its bytes at the next start (written again, cut back, recorded, or a conflict), two writes of it run one after the other, and the player streams it at its new size; a tag-tree rename, merge or retire rewrites a clip that holds the tag, and one it could not rewrite (writing clips off, the last keyword of a clip kept in QuickTime) is named with why and the tree keeps the node."),
    "V1": ("views", "A view lists each photo once, in the owner's order, with its count; a clip by its XMP date first, then its QuickTime one, a date of zeros no date."),
    "V2": ("views", "A typed search ANDs its words and ranks nothing."),
    "V3": ("views", "Suggest, and the analyse-only run, from start to end."),
    "V4": ("views", "One process at a time has a model on the graphics card; the waiter is told who holds it."),
    "V5": ("views", "The navigator's counts are the photos under a node, each once."),
    "V6": ("views", "The server goes idle: models, vectors and caches are let go, and the next ask loads them again."),
    "V7": ("views", "Select all minus the excluded: the tally, the folders and a bulk edit read the same photos under the caps."),
    "V8": ("views", "A write elsewhere makes the open view and the cached scan read again."),
    "V9": ("views", "Vectors and suggestions follow their photo through rename, settle, rotate, delete and undo."),
    "V11": ("views", "A library made on the new face model is refused the old one, nothing written, nor in a dry run."),
    "V12": ("views", "A library switches to SigLIP 2 SO400M once the new model's vectors are made for its photos, refused with the counts before, and its undo held to the same share; each library then searches by its own model's vectors, a run on the model it began with."),
    "V13": ("views", "The new photo model's vectors are made for a library beside its own (embed-photos): a dry run that writes nothing, a run stopped and run again that makes only the rest, a batch one write, a photo removed meanwhile given no row back, a photo edited meanwhile stamped as it was opened, a file that does not decode tried again, a drive not connected stopping the run with a sentence, two libraries holding one folder each by its own run, the turn on the card taken before the list is read, a running server giving its kept turn to the run and its next Suggest on the switched model; then the switch from the command line (set-photo-model), refused before, passes as one change of the journal, and its undo puts the old model back."),
    "V14": ("views", "After the switch, the old photo model's vectors and Suggest's words under it are retired (retire-photo-vectors): a dry run that writes nothing, the own key's vectors and words kept byte for byte, one change a key whose undo is refused and the undo of the switch refused with the switch's sentence, a run stopped (or an app started) and run again as the same change, a switch back beside the run ending it, a photo removed meanwhile, and a library whose own model does not hold its photos refused."),
    "V15": ("views", "A video played in the browser: a range answered with its bytes and capped, one that cannot be answered refused with the size, sought back and forth quickly, closed half way without holding the file, renamed while it plays ending every stream short, a share that stops answering ending the stream within its wait and the next ask answered 503 at once, an id a video in one library and a photo in another; the cards and the record say which are videos."),
    "V16": ("views", "A clip in the grid shows its poster frame, its middle frame, kept as a photo's thumbnail is: made once (two cards at once one ffmpeg run, a folder scrolled fast at most three at once), found again by its stamp, made again once renamed or changed, its last poster shown once its file is gone; a clip cut short or a machine with no ffmpeg answered the placeholder and nothing kept, a share asleep one bounded look and no ffmpeg; `thumbs warm` making the posters ahead."),
    "V17": ("views", "The owner measures Suggest's neighbour cut for clips on a copy of a library, nothing written to it and no model loaded: a clip matched on its best of five frames against its frame 0 alone, the share of the neighbour slots clips take and the tags offered at each cut, held out by photo and by folder, ids and counts only."),
    "I1": ("apps", "Switching library in the middle of a folder shows no card of the first."),
    "I2": ("apps", "A write through a server update is waited out and not repeated."),
    "I3": ("apps", "An update by a launch and by an install hands one server over to the next without killing its work."),
    "I4": ("apps", "A library one migration behind is served at start, and a request for one being migrated gets 503 at once."),
    "I5": ("apps", "A page loaded against the real server, not a fake one."),
    "I6": ("apps", "The same flows with and without roots, end to end."),
    "I7": ("apps", "The CLI, the MCP server and the server act on one library at once."),
    "I8": ("apps", "A page save from a stale record is refused, not overwritten."),
    "I9": ("apps", "A page's saved scan of an older shape is read again, not trusted."),
    "I10": ("apps", "A background run that failed overnight says, from its row, what happened and what to do next; TagPup's own work in the way never asks."),
    "I11": ("apps", "On a machine without ffmpeg (or with one that does not run) the server and the CLI's index runs start nothing and say what to install; an install says so and leaves the old server running."),
}

#: scenario id -> its scenario files; a file listed under more than one runs more than one scenario (the first is its owner's).
_SCENARIO_FILES = {
    "D1": "test_a_library_from_a_newer_tagpup test_newer_library_guard_in_a_long_lived_process",
    "D2": "test_journal test_maintenance test_undo_after_every_later_kind",
    # test_a_photo_written_by_its_own_editors runs F1 too (the own writer's saves); its crash settles are D3's.
    "D3": (
        "test_journal test_a_crash_part_way_is_settled_at_the_next_start test_a_photo_written_by_its_own_editors "
        "test_a_patch_that_grows_a_file_is_settled_by_its_bytes test_a_long_change_cut_off_is_settled_in_chunks"
    ),
    "D4": "test_snapshots",
    "D5": "test_derived_follow_writes test_photo_people_follow_their_sources",
    "D6": "test_a_librarys_settings_are_its_own",
    "D8": "test_migrations",
    # test_an_index_is_one_run_under_its_sync is Data's file (D9, the row); its index-queue scenarios (F11) are reviewed by
    # the Files owner with the integration owner.
    "D9": "test_runs_nest_and_say_how_far_they_got test_an_index_is_one_run_under_its_sync",
    "D7": (
        "test_library_roots test_roots_at_scale test_roots_ingress test_roots_location test_roots_scenarios "
        "test_roots_store"
    ),
    "F1": (
        "test_a_clip_patched_in_place test_an_adobe_clip_packet_edited_and_read_back "
        "test_a_file_holds_what_exiftool_kept test_a_photo_patched_in_place "
        "test_a_rename_stopped_part_way test_smart_rename_one_photo smart-rename-one-photo "
        "test_file_changes_one_at_a_time test_file_journal test_rotate_crash_settles_with_the_boxes test_rotate_is_journaled test_rotate_keeps_the_photo "
        "test_tag_a_photo_read_it_back_and_undo test_time_shift test_xmp_packet_edits_what_it_owns "
        "test_iptc_block_edits_what_it_owns test_exif_block_edits_what_it_owns test_segment_edits_choose_what_to_write "
        "test_a_photo_written_by_its_own_editors test_the_two_writers_compared_on_copies "
        "test_a_photos_title_and_caption"
    ),
    "F2": "test_no_writes_to_damaged_photos test_rows_only_in_folders_added test_an_index_reads_the_next_photos_ahead",
    "F3": (
        "test_batches test_bulk_edits test_bulk_edits_real_exiftool test_bulk_edits_stalled_exiftool "
        "test_bulk_writes_start_from_the_file test_a_bake_is_planned_from_the_library "
        "test_an_index_reads_a_bake "
        "test_the_index_writes_the_bake test_a_caption_repeating_the_title_is_cleared "
        "test_a_long_change_cut_off_is_settled_in_chunks"
    ),
    "F4": "test_folder_watcher test_one_walk_of_a_library_at_a_time test_sync",
    "F5": "test_scenario_two_libraries_share_a_folder",
    "F6": "test_index_progress_end_to_end test_partial_writes_leave_rows_unread",
    "F7": "test_a_share_asleep test_a_share_that_drops_mid_walk test_a_share_that_wakes",
    "F8": (
        "test_bulk_delete test_delete_a_photo test_just_look_bin_and_long_names test_just_look_delete_and_rename_rules "
        "test_just_look_edits"
    ),
    "F9": "test_roots_old_place test_roots_pinned_runs",
    "F10": "test_one_photo_whatever_its_spelling",
    "F11": (
        "test_an_index_is_one_run_under_its_sync test_index_progress_end_to_end test_new_faces_are_matched_after_an_index test_an_index_reads_the_next_photos_ahead "
        "test_an_index_reads_a_bake test_a_machine_without_a_card"
    ),
    "F12": (
        "test_a_clip_is_indexed_as_a_video test_a_clip_is_not_turned test_a_clip_tagged_through_the_journal "
        "test_a_clip_tag_undone test_a_clip_patch_cut_short test_a_tag_tree_edit_of_a_tag_a_clip_holds "
        "test_a_clip_titled_in_quicktime test_a_clip_renamed test_a_clip_deleted test_a_clip_dated_through_the_journal "
        "test_a_clip_dated_again_from_its_row"
    ),
    "I1": "scenario-switching-library-mid-folder",
    "I2": "scenario-write-through-a-server-update test_server_drain",
    "I3": "test_install_app test_install_hands_over test_launcher_hands_over",
    "I4": "test_server_migrates_at_start",
    "I5": "test_pages_against_the_real_server",
    "I6": "test_roots_scenarios",
    "I7": "test_mcp_server test_one_library_three_programs",
    "I8": "test_stale_record_precondition",
    "I9": "scenario-a-scan-kept-by-an-older-page",
    "I10": "test_a_failed_run_says_what_to_do",
    "I11": "test_ffmpeg_is_a_prerequisite",
    "P1": "test_people_by_id_at_once",
    "P2": (
        "test_a_cropped_picture_gets_new_faces test_bulk_faces_and_tags test_cluster_faces_on_a_crowded_library test_clustering_meets_naming "
        "test_face_people_follow_file_changes "
        "test_faces_and_tags_in_step test_faces_from_tags_in_a_folder test_person_filing_table "
        "test_tags_from_faces_in_a_folder"
    ),
    "P3": "test_reexamine_folder",
    "P4": "test_a_person_count_reads_only_their_photos test_name_faces_routes test_naming_faces_job",
    "P5": "test_tag_tree_edits_are_batches test_taxonomy_lifecycle",
    "P6": "test_two_cousins_called_alike",
    "P7": "test_identify_ignore_assign_restore_undo",
    "P10": "test_a_library_indexes_and_suggests_with_its_face_model",
    "P11": "test_a_face_gets_one_nearest_person_everywhere",
    "V1": "test_library_views_scenarios",
    "V2": "test_library_search",
    # test_a_clip_is_matched_on_its_best_frame: Suggest's neighbours, a clip among them at its best frame (M2).
    "V3": "test_analyse_only_looks_are_kept test_analyse_only_suggest test_suggest_then_apply_all "
          "test_a_clip_is_matched_on_its_best_frame",
    "V4": "test_one_gpu_at_a_time test_one_gpu_jobs",
    "V5": "test_selection_scenarios",
    "V6": "test_models_released_when_idle",
    "V7": "test_selection_scenarios",
    "V8": "test_library_views_scenarios",
    "V9": "test_suggest_then_apply_all",
    "V11": "test_switching_a_librarys_face_model",
    "V12": "test_switching_a_librarys_photo_model",
    "V13": "test_making_a_new_photo_models_vectors",
    "V14": "test_retiring_the_old_photo_models_vectors",
    "V15": "test_playing_a_video",
    "V16": "test_a_clip_shows_its_poster",
    "V17": "test_measuring_the_neighbour_cut_for_clips",
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
            "test_roots_sample test_roots_unplaced test_search_words "
            "test_services_refuse_what_the_rules_refuse "
            "test_sync_records test_the_tree_is_the_librarys test_validation test_view_indexes"
        ),
        "migration": "test_a_library_that_cannot_be_migrated_is_still_served test_schema",
        "guard": (
            "test_db_access test_derived_writers test_every_change_kind_has_an_undo_check test_journal_keys_and_cascades "
            "test_photo_rows test_sandbox_roots"
        ),
    },
    "people": {
        "unit": (
            "face-strip grid-build-progress identify-faces identify-faces-cluster-buttons identify-faces-counts "
            "identify-faces-in-place-removal library-sort-and-organize-header name-faces organize-faces person-directory "
            "person-faces person-filing-table person-names person-tag-form review-dialog review-from-activity "
            "show-matched-toggle tag-editor-in-place tag-order tag-view tag-vocabulary tagpup-people-by-id "
            "tagpup-people-lists-by-id tags-shown-alphabetical "
            "test_answers_carry_their_person test_automatch_references test_automatch_writes_once test_cluster_candidates "
            "test_clustering_evidence test_every_tag_has_an_id "
            "test_excluded_faces_stay_unnamed test_face_click_reads_named_faces_once test_face_clustering_rules "
            "test_face_exclusion test_face_roots_are_the_trees "
            "test_faces_from_tags test_folder_scope_roots test_group_tag_is_not_a_person "
            "test_clustering_keeps_a_name_the_tags_confirm "
            "test_identify_cache_keeps_others_writes test_identify_cache_sees_every_change test_identify_queries "
            "test_index_resolves_people test_journal_through_the_services "
            "test_new_person_reads_the_pool_once test_organize_faces test_people_by_id_tree "
            "test_people_representative_faces "
            "test_person_face_samples test_person_ids "
            "test_person_rename_reaches_the_files "
            "test_reindex_keeps_face_decisions test_server_lists_are_alphabetical test_service_faces test_service_tags "
            "test_shared_taxonomy_routes test_store_taxonomy_add_path test_tag_order "
            "test_tag_sort_key_edges test_tag_view_api test_tags_are_checked_where_they_are_set test_tags_from_faces "
            "test_taxonomy_edits_report_what_they_wrote "
            "test_trace_file_per_library test_tuner_photo_copy_upright test_tuner_reads "
            "test_tuner_server_api test_vocabulary tuner-assign-label-tells-the-truth "
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
            "bulk-edit bulk-writes-queue just-look-edits path-helpers photo-write-queue picture-check-says-so rename-failed retried-save "
            "rotate-refreshes-thumbnails smart-rename-count "
            "test_a_bake_is_used_only_whole test_a_cut_keyword_is_not_planned_again test_a_face_box_turned_upright_and_back "
            "test_a_photo_is_known_by_its_picture "
            "test_a_row_describes_its_file_one_way "
            "test_batches_go_through_the_model "
            "test_census_of_jpeg_segments "
            "test_container_layout "
            "test_dates test_derived_file_changes "
            "test_exiftool_encoding test_exiftool_says_why test_exiftool_session "
            "test_frames_of_a_video "
            "test_index_adds_the_folder_it_is_given "
            "test_index_startup_reads test_jobs_indexing "
            "test_keyword_fields test_lock_owners "
            "test_metadata_extraction test_minor_errors_in_a_journaled_write test_one_answer_to_the_librarys_folders "
            "test_paths "
            "test_paths_roots test_photo_identity test_photo_meta "
            "test_relink_checks_the_destination test_remove_folder_lists_the_library "
            "test_renaming "
            "test_service_change_tags "
            "test_service_indexing test_service_page_images test_service_replace_tag "
            "test_service_smart_rename "
            "test_settling_carries_vectors "
            "test_tag_writes_keep_the_index_true "
            "test_thumbnails "
            "test_what_counts_as_a_photo "
            "test_when_a_photo_was_taken_is_kept tuner-picker-and-queue "
            "tuner-remove-folder unsaved-edits write-queue-status"
        ),
        "guard": "test_photo_looks_have_one_owner test_the_block_editors_have_one_caller",
    },
    "views": {
        "unit": (
            "details-panel faces-say-they-are-loading folder-view-clips grid-keys grid-window library-header-strip-toolbar library-moves library-photo-back "
            "library-selection library-selection-jumps library-tally library-view looking-suggestions navigator "
            "navigator-multi photo-camera photo-details-layout photo-position photo-suggestions-panel search-page selection "
            "selection-follows-the-photo selection-panel selection-suggestions selection-suggests-who-the-faces-look-like stale-cards suggest-cancel suggestions-follow-a-saved-photo tagging-flow "
            "test_a_search_keeps_no_thread_buffers "
            "test_camera_words test_caption_order test_chip_keys_follow_the_server test_view_rules_follow_the_server "
            "test_clip_sees_photos_upright test_embeddings_follow_their_photo "
            "test_every_library_keeps_its_saved_suggestions "
            "test_index_reload_reuses_its_connection test_jobs_suggestions "
            "test_library_union_and_order test_library_view test_library_view_plans test_models_in_use_survive_an_unload "
            "test_one_value_for_a_suggested_tag "
            "test_person_tag_edges test_photo_index_keeps_only_vectors test_pipeline_measurements "
            "test_selection test_selection_tally "
            "test_suggest_index_stays_current test_suggest_reads_people_once test_suggest_respects_face_decisions "
            "test_suggest_status_poll_is_a_snapshot test_suggest_status_replies test_suggester_scoring "
            "test_suggestions_keep_hierarchy test_tag_usage_from_photo_tags "
            "test_scrfd_adaface_faces "
            "test_the_runtime_keeps_models_by_settings test_the_runtime_reaches_suggest test_thumbs_cli test_vector_index "
            "test_thumbnail_routes test_zero_shot_skips_people vgrid video-viewer"
        ),
        "guard": "test_gpu_single_owner test_no_test_loads_a_model",
    },
    "apps": {
        "unit": (
            "activity-page add-folder-dialog apply-all-failure-undo banner-cost batch-strip database-routing "
            "delete-while-folder-changes dialog-frames dialogs-keep-the-keys dom-output video-keys folder-keeps-history-position "
            "folder-opening folder-picker gear history-dialog image-zoom images-after-an-update library-memory "
            "library-picker open-photo page-state progress-polling roots-dialog server-replaced server-update "
            "settings-dialog stale-record test_a_request_is_decoded_once "
            "test_activity_page_replies test_activity_routes test_add_findings test_retro_metrics test_stale_findings test_an_empty_folder_is_scanned_again test_background_tasks "
            "test_background_work_is_waited_for test_batch_strip_replies test_browser_opens_once test_config "
            "test_dark_selects test_database_context_isolation test_folder_scan_follows_the_library test_gzip_json "
            "test_heavy_imports_stay_lazy test_history_routes test_indexing_names_a_real_control "
            "test_inspect_reads_only_the_photos_asked_about test_inspect_uses_the_stores_mtime_rule "
            "test_library "
            "test_library_registry_race test_local_server_socket test_log_runs test_logs "
            "test_mcp_config_resolves "
            "test_patch_tool test_photo_open test_recurring_jobs test_result "
            "test_roots_ingress_keys_cover_the_routes test_roots_map "
            "test_run_tests_keeps_failures test_server_startup test_serving_photos test_suggest_thread_keeps_its_library test_tagpup_routes "
            "test_web_app undo-from-reply validation"
        ),
        "guard": (
            "context-budget dom-contract merge-budget no-heredoc-escapes page-modules page-shape test_config_single_owner test_database_axis_guard test_fixtures_make_photo_rows_like_the_indexer "
            "test_layers test_lint "
            "test_page_module_names test_processes_single_owner test_rules_have_one_owner test_sandbox_has_all_the_code "
            "test_spec_accuracy test_sync_entry_points "
            "test_tests_have_homes_of_their_own test_the_test_map test_verify_workflow_is_sandboxed"
        ),
    },
}

def _build():
    files = {}

    def put(names, owner, kind, ids=()):
        for name in names.split():
            name = name if name.startswith("test_") else name + ".test.mjs"
            # A second scenario of one file: the owner stays its first scenario's. The name's own, never carried onto
            # the names after it in the string.
            first, before = (files[name][0], files[name][2]) if name in files and kind == "scenario" else (owner, ())
            files[name] = (first, kind, before + ids)
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

# Data and journal: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-data.md` (its Scope, its contract, its guards, the scenario list D1 to
D9). This page states what the domain is today, each of the seven conditions of health (docs/OWNERS.md) with its evidence
and date, and what is so by design. A condition not met is the domain's queue.

## What the domain is (the table and the unfinished work counted read-only 2026-11-08)

What is in the library file, and whether it can be put back: how a file is opened and written (`store/db.py`), the schema
and its one baseline at 32, the journal of row changes and its undo, the journal of photo-file changes, the derived tables
and the word index, snapshots and backups, the doctor's checks, a library's settings as stored, and roots as stored data. It
does not decide what a tag, person or face means (People), how a photo file is read or written (Files), or what a page
shows (Views, Integration).

| | photo_index | kr-track | renton_parkrun |
|---|---|---|---|
| schema | 38 | 38 | 38 |
| photos (of them clips) / faces | 61,797 (2,179) / 183,669 | 2,070 (5) / 12,664 | 1,309 (0) / 3,315 |
| a clip's frames 1 to 4 (`embedding_frames`), photo-model keys | 8,708, 1 | 20, 1 | 0, 1 |
| changes / change_rows / change_files | 6,340 / 1,500,284 / 95,369 | 1,948 / 88,883 / 4,696 | 1,247 / 24,032 / 4,319 |
| changes made below 32 (row undo refused) | 715 | 316 | 1,210 |
| roots / settings / job_runs | 1 / 11 / 438 | 1 / 10 / 245 | 1 / 10 / 176 |

Unfinished work (2026-11-08): 0 `derived_pending`, 0 files `writing`, 0 patch records; 139 `conflict` files, all in
photo_index (every other file `done`), none with a note the doctor counts (`file_journal.NOTES`) (the doctor's last full
report: 0 broken rules on all three, 2026-10-29). Snapshots are 3 daily, 1 weekly and 1 monthly per library (counted
2026-11-08 under `data/backups`); a copy of photo_index is 4.03 to 4.13 GB (the monthly, taken 2026-10-16,
2.62 GB), so its five snapshots hold about 19 GB beside the `before-*` copies; the copies below schema 32 are listed "too
old to restore". Migration 33 (each face's vector to `face_embeddings`, keyed by the face model; the drops of
`faces_pending` and `idx_faces_name`; DECISIONS 2026-10-18, "MIGRATION 33 IS PULLED FORWARD") has run on all three
(schema 33, counted 2026-10-18), and 34 to 38 after it: 37 (`photos.kind`, 'photo' in every row there was, and
`change_files.patch` and `patch_before`; DECISIONS 2026-11-02, "VIDEO: THE OWNERS' PLANS AND THE OWNER'S ANSWERS", 4) and
38 (`embedding_frames`; DECISIONS 2026-11-06, "MIGRATION 38") are additive. All three are at 38 (counted read-only
2026-11-09). Migration 39 (`photos.width` and `height`, NULL until read; `idx_photo_people_name` dropped; `idx_photos_clips` made; DECISIONS 2026-11-09, "MIGRATION 39") is IN FLIGHT: to be installed and run on the three libraries before another migration starts. What an install needs (disk, one app first, no CLI index meanwhile): DATABASE.md,
section 10.

## The contract other domains rely on

| Who calls | Contract | Guard |
|---|---|---|
| every domain | `db.connect / write / write_with_connection / lock_for / readonly_uri`: WAL, a 30 s busy timeout, one writer per file, retry; one `db.key` for a library file; no `sqlite3.connect`, no SQL outside `tagpup/store`; `db.write_if_free` for a write worth nothing late (a run's progress): made now or not at all, `db.BUSY` while the file is being written | `test_db_access`, `test_layers`, `test_runs_nest_and_say_how_far_they_got` |
| Integration (server, CLI, MCP, launcher) | `schema.ensure` migrates or raises; a newer or below-baseline library is refused, also in a long-lived process; `reading_newer` / `newer_problem` never write; 503 while migrating | `test_a_library_from_a_newer_tagpup`, `test_newer_library_guard_in_a_long_lived_process`, `test_server_migrates_at_start`, `test_migrations` |
| People, Files (sync, refresh_rows, relink, maintenance), Views (a retiring of vectors) | `journal.apply / rehearse / undo / record / record_faces / add_counts / kept / kept_named / old_values / recorded_values`, `db.begin`: one transaction, applied and undone only where rows are as read (a retiring of vectors' change is added to a piece at a time, `add_counts` while `kept`; the deleted face pass's changes, added to one photo at a time, stay in the journals and are undone as recorded, made in the tests by `face_rows.as_a_face_pass_gave`) (an insert's undo: the exception for the change's own rows is the module docstring's, "undo"); refusals name keys; one `schema.row_key` spelling for a row's key; `journal.KEYS` is the one statement of the tables a change may name (`faces_detected` among them, keyed by its photo's id with no foreign key: `journal.UNLINKED`; journaled only by the deleted `delete-old-detector-rows`, whose change in each live journal is a kind in `services.journal.RETIRED`, its undo refused); `journal.CACHES` the tables none records (`face_crops`: the face pass recorded no crop, and `journal.NOT_CACHED` leaves a crop row a change recorded before out of `_load` and of History's counts and keys; DECISIONS 2026-10-27) | `test_journal`, `test_maintenance`, `test_sync` (the dry run, then `--apply`), `test_journal_keys_and_cascades`, `test_journal_through_the_services` (the journal's list and undo for changes of the deleted maintenance services' shape, at the current schema), `test_undo_after_every_later_kind` |
| Files (`file_changes`), Integration (batches) | `file_journal`: planned, `writing`, `done` with its row; a crash settled at the next start by what the file holds; a change of an `INTERNAL` operation (the index's bake, a Delete) is erased on a forward finish -- but for its files that ended a conflict noted one of `file_journal.NOTES`, kept with the change, `applied` (owner, 2026-11-03; any other conflict erased with it) -- listed by no history and no batch list, and met by no undo check but `newer_overlapping` (an undo of photo files) while a file of it is `writing` | `test_file_journal`, `test_a_crash_part_way_is_settled_at_the_next_start` |
| Files (indexer), People (faces, tree) | `derived.*` and `people.rebuild` in the writer's transaction; `doctor --rebuild-derived` rebuilds the same | `test_derived_writers`, `test_derived_follow_writes`, `test_photo_people_follow_their_sources` |
| every writer of photos; Views reads | `search_index.refresh` from `derived`; a deleted photo's words by trigger | `test_derived_writers`, `test_search_words` |
| every cache | `generations` counters moved by triggers from any process | `test_schema`, `test_doctor` |
| Files (`file_changes`: a write refused twice for its picture) | `file_journal.NOTES`: a conflict noted `PICTURE_CHANGED` is counted by the doctor, with the patch records kept outside `writing`, from one scan (`noted`, cached by `checks` while nothing is written) | `test_doctor`, `test_a_crash_part_way_is_settled_at_the_next_start` |
| Integration (CLI, scheduler) | `snapshots.take / listed / restore` (3/1/1; a restore snapshots first; below 32 refused) | `test_snapshots` |
| Integration (`runtime.library_settings`) | `services.settings.of / change / stamp`: changes journaled, a locked group acknowledged, no `config.ini` | `test_a_librarys_settings_are_its_own`, `test_config_single_owner` |
| `services.settings` -> Files (`sync.review`) | a change of roots or ignored folders asks sync which folders to review | `tests/test_library_roots.py` (`FoldersNotAddedAreExcluded`: settings asking sync nothing fails it) |
| Files, Views, Integration | `store.roots`: a row's path form only at the store boundary | `test_roots_store`, `test_roots_scenarios` |
| People | `removals`: a person taken off a photo blocks naming that face as them | `test_faces_and_tags_in_step`, `test_two_cousins_called_alike` |
| Files, Views | `faces_detected`, `added_folders`, `job_runs` | `test_faces_detected_record`, `test_rows_only_in_folders_added`, `test_sync_records`, `test_an_index_is_one_run_under_its_sync` |
| Files, Integration (the runs of B2) | `services.job_runs.progress(library, run_id, counts, phase)`: `phase` one of `PHASES` (else ValueError), counts names and numbers only, at most every 10 s a run unless the phase changes, skipped while the library is written; False, never raising, for a run gone or a library a restore left at 35 | `test_runs_nest_and_say_how_far_they_got` (D9) |
| Views (the Activity page, B4), Integration | `services.job_runs.tree(library, limit)`: the one reader of `parent`, `heartbeat`, `progress`; the newest `limit` top runs (none queued them, or their parent is gone) and every run under them, newest first; a library at 35 reads as its newest runs, each a top run | `test_runs_nest_and_say_how_far_they_got` (D9) |
| Integration (the server's start) | `job_runs.abandon`, the one place a run is marked abandoned (a claim, a snapshot or restore, the start); `services.job_runs.sweep_abandoned` after each library is brought up to date: a run of a process that has ended is abandoned, a live one's and another machine's kept, a failure logged | `test_runs_nest_and_say_how_far_they_got` (D9) |
| People (the index), Views (Suggest), Files (the bake) | `faces_detected`: the index and Suggest write it only through `faces_detected.record` (`services.faces._record_detection`), under the name `services.faces.detector_of` gives; Suggest and the bake's plan read it under that name through `detected`, the owed walk of a sync and an index through `unexamined` (`photo_fingerprints.owed`); a change deleting its rows records them (`journal.KEYS`; only the deleted `delete-old-detector-rows` did, a retired kind whose undo is refused); a rotation forgets a photo's row (`faces_detected.forget`, unjournaled, as its vectors and crops, `services.rotation`; a crop is a cache, `journal.CACHES`, which no change records) | `test_faces_detected_record`, `test_a_bake_is_planned_from_the_library`, `test_journal_undo_refusals` |
| Files (`services.rotation`; the bake and the read side, phase 6) | `photo_fingerprints` (migration 34): get and put by photo id, `forget` by path; a row whose photo is gone reads as none; not journaled, not derived; a rotation forgets it beside the vectors | `test_migrations` (`TheOwnersLibraryAt33MeetsMigration34`), `test_rotate_is_journaled` |
| People (`store.faces`) | `store.faces.model_key` reads `store.settings.face_model` on the reader's own connection: a face's vector (`face_embeddings`, keyed by face and model) is read under the key its caller gives the reader, which a decision reads once from the library's `faces.model` on its own connection | `test_a_library_indexes_and_suggests_with_its_face_model` (P10: the index, Suggest and Name faces from tags read under the library's key), `test_journal_keys_and_cascades` |

## The seven conditions (2026-11-08; first stated 2026-10-15, DECISIONS 2026-10-15 and 2026-10-16)

1. **No reach-ins.** Met. No module outside the domain calls a private name of a Data module (`db.key`, `schema.row_key`,
   `schema.version`, `schema.pending_on`, `schema.forget` are the doors); the contract rows above are the Integration
   table. A transaction is begun only by `db.begin` (`tests/test_db_access.py`, #2750).
2. **No rule written twice.** Met. The stored-value comparison and the column list (`schema.same_stored`, `table_columns`),
   the pending list, the schema version, the row-key text and the library key each have one owner; the rooted-row scan
   is one checker (`roots_check.broken` over `store.roots.PATH_COLUMNS`), read once per connection. Each derived table
   (`derived.ALL`) has one doctor rule (`checks.OF_DERIVED`), held to it by `tests/test_doctor.py`.
3. **No hidden coupling.** Met. The doctor's cache depends on its own stamp (writes and `data_version`) and is not stored
   inside a transaction (a rollback case is a test).
4. **No concept that could collapse.** Met. The data-changing migration kind, `rebuild_table`, `roots_verify` under its old
   name, `inspection.schema_version` and `libraries._key` are gone; the two one-off re-derives (`rederive tags and
   captions`, `rederive clip dates`) are retired names in `journal.RETIRED` (3 applied changes in photo_index, none in
   the others, counted 2026-11-08).
5. **No branch without an instance.** Met. About 30 version probes and legacy arms went with the baseline at 32 (DECISIONS
   2026-10-15); what the baseline still makes and nothing reads is by design (below). Each `journal.RETIRED` kind cites
   its live count in its comment.
6. **Scenario tests, owned and mapped.** Met. Every file is owned in `tests/owners.py` (`tests/test_the_test_map.py`); the
   domain holds 22 scenario, 22 unit, 6 guard and 2 migration files (counted 2026-11-08). D2's undo is the undo matrix
   (`test_undo_after_every_later_kind`: every ordered pair of the change kinds a library holds, made by their real
   writers); D8 meets the owner's library at each of 32 to 37 with the real next migration (`tests/test_migrations.py`).
7. **No open `data` or `wrong` finding.** Met: none in the domain. Open `low` (2026-11-08): the doctor's cost on
   photo_index, a full check not finished in 13 minutes in a review run (#2753).

## By design (each row closed `decided:`)

- **#464, #1382**: no runner check of a migration's declared `touches`, nor of the AUTOINCREMENT counters. The rule instead:
  `touches` is the tables whose rows a step may move (`schema.Migration`); a migration branch carries, in its own branch, a
  check of an additive step's `touches` if it claims the undo exemption (names no table the journal keys), and a check
  that no `sqlite_sequence` counter goes down if it rebuilds an AUTOINCREMENT table.
- **#1375**: a library whose making failed holds only `schema_version`. The three libraries each have their 35 tables (34 without
  `sqlite_sequence`, 2 virtual; counted 2026-11-08 at schema 38), and
  every entry that makes a library runs `ensure`, which makes it whole.
- **#1088, #1277**: `faces_pending` and `idx_faces_name` go with migration 33. The `pruned` value of the `changes.status`
  CHECK stays: dropping it rebuilds the journal's `changes` table for nothing (DECISIONS 2026-10-18, "AFTER MIGRATION 33",
  to be confirmed by the owner).
- **Migration 33's one long transaction** (about 30 s on photo_index, at the edge of the busy timeout): another process
  that opens the library meanwhile can fail once with "database is locked", having written nothing. The runner's behaviour
  since the first migration; the install note says to start one app first (DATABASE.md, section 10).
- **Backups** do not expire by age; the 3/1/1 rotation and the newest five `before-*` copies are the only rule.
- **A migration that rewrites values** is destructive: a full backup first and no undo (DECISIONS 2026-10-15).

## History

One line per item; the reasoning is in DECISIONS.md.

- 2026-10-02 to 10-11: settings journaled, roots adoption and repair-address deleted (DECISIONS 2026-10-11).
- 2026-10-15: the baseline moved to 32 and the probes, the data-changing migration kind and `rebuild_table` went (DECISIONS
  2026-10-15, 2026-10-16); one comparison, one column list, one pending list, one row key, one rooted-rows checker.
- 2026-10-18: migration 33, the face vectors keyed by model (DECISIONS 2026-10-18).
- 2026-10-28: the old models and their change kinds retired into `journal.RETIRED` (DECISIONS 2026-10-28).
- 2026-11-02 to 11-08: migrations 37 and 38 for clips (DECISIONS 2026-11-02, 2026-11-06); the one-off re-derives retired.

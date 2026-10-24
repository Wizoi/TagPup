# Data and journal: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-data.md` (its Scope, its contract, its guards, the scenario list D1 to
D8). This page states what the domain is today, each of the seven conditions of health (docs/OWNERS.md) with its evidence
and date, and what is so by design. A condition not met is the domain's queue.

## What the domain is (counted 2026-10-15, read-only)

What is in the library file, and whether it can be put back: how a file is opened and written (`store/db.py`), the schema
and its one baseline at 32, the journal of row changes and its undo, the journal of photo-file changes, the derived tables
and the word index, snapshots and backups, the doctor's checks, a library's settings as stored, and roots as stored data. It
does not decide what a tag, person or face means (People), how a photo file is read or written (Files), or what a page
shows (Views, Integration).

| | photo_index | kr-track | renton_parkrun |
|---|---|---|---|
| schema | 33 | 33 | 33 |
| photos / faces | 59,519 / 187,784 | 1,499 / 9,850 | 1,309 / 3,328 |
| changes / change_rows / change_files | 742 / 8,939 / 77 | 318 / 1,204 / 1,190 | 1,210 / 1,470 / 2,940 |
| changes made below 32 (row undo refused) | 715 | 316 | 1,210 |
| roots / settings / job_runs | 1 / 13 / 42 | 1 / 12 / 35 | 1 / 12 / 35 |

Unfinished work: 0 `derived_pending`, 0 files `writing`, 1 `conflict` file (a changed file, as designed). The doctor reports
0 broken rules on all three. Snapshots are 3 daily, 1 weekly and 1 monthly per library; the copies below schema 32 are
listed "too old to restore". Migration 33 (each face's vector to `face_embeddings`, keyed by the face model; the drops of
`faces_pending` and `idx_faces_name`; DECISIONS 2026-10-18, "MIGRATION 33 IS PULLED FORWARD") has run on all three
(schema 33, counted 2026-10-18): no migration is in flight, and the slot is free. What an install needs (disk, one app first, no CLI index meanwhile): DATABASE.md, section 10.

## The contract other domains rely on

| Who calls | Contract | Guard |
|---|---|---|
| every domain | `db.connect / write / write_with_connection / lock_for / readonly_uri`: WAL, a 30 s busy timeout, one writer per file, retry; one `db.key` for a library file; no `sqlite3.connect`, no SQL outside `tagpup/store` | `test_db_access`, `test_layers` |
| Integration (server, CLI, MCP, launcher) | `schema.ensure` migrates or raises; a newer or below-baseline library is refused, also in a long-lived process; `reading_newer` / `newer_problem` never write; 503 while migrating | `test_a_library_from_a_newer_tagpup`, `test_newer_library_guard_in_a_long_lived_process`, `test_server_migrates_at_start`, `test_migrations` |
| People, Files (sync, refresh_rows, relink, maintenance), People (the face pass) | `journal.apply / rehearse / undo / record / record_faces / record_inserted / record_removing / add_counts / kept / newest_kept / kept_named / old_values / recorded_values`, `db.begin`: one transaction, applied and undone only where rows are as read (the face pass's change is added to in one transaction a photo, `record_inserted` while `kept`; retiring the old detector's faces, one transaction a chunk, `record_removing` while `newest_kept`) (an insert's undo: the exception for the change's own rows is the module docstring's, "undo"); refusals name keys; one `schema.row_key` spelling for a row's key | `test_journal`, `test_maintenance`, `test_journal_keys_and_cascades`, `test_journal_through_the_services`, `test_undo_after_every_later_kind` |
| Files (`file_changes`), Integration (batches) | `file_journal`: planned, `writing`, `done` with its row; a crash settled at the next start by what the file holds | `test_file_journal`, `test_a_crash_part_way_is_settled_at_the_next_start` |
| Files (indexer), People (faces, tree) | `derived.*` and `people.rebuild` in the writer's transaction; `doctor --rebuild-derived` rebuilds the same | `test_derived_writers`, `test_derived_follow_writes`, `test_photo_people_follow_their_sources` |
| every writer of photos; Views reads | `search_index.refresh` from `derived`; a deleted photo's words by trigger | `test_derived_writers`, `test_search_words` |
| every cache | `generations` counters moved by triggers from any process | `test_schema`, `test_doctor` |
| Integration (CLI, scheduler) | `snapshots.take / listed / restore` (3/1/1; a restore snapshots first; below 32 refused) | `test_snapshots` |
| Integration (`runtime.library_settings`) | `services.settings.of / change / stamp`: changes journaled, a locked group acknowledged, no `config.ini` | `test_a_librarys_settings_are_its_own`, `test_config_single_owner` |
| `services.settings` -> Files (`sync.review`) | a change of roots or ignored folders asks sync which folders to review | `tests/test_library_roots.py` (`FoldersNotAddedAreExcluded`: settings asking sync nothing fails it) |
| Files, Views, Integration | `store.roots`: a row's path form only at the store boundary | `test_roots_store`, `test_roots_scenarios` |
| People | `removals`: a person taken off a photo blocks naming that face as them | `test_faces_and_tags_in_step`, `test_two_cousins_called_alike` |
| Files, Views | `faces_detected`, `added_folders`, `job_runs` | `test_faces_detected_record`, `test_rows_only_in_folders_added`, `test_sync_records` |
| People (`store.faces`) | `store.faces.model_key` reads `store.settings.face_model` on the reader's own connection: a face's vector (`face_embeddings`, keyed by face and model) is read under the key its caller gives the reader, which a decision reads once from the library's `faces.model` on its own connection | `test_switching_a_librarys_face_model` (V11 (c)), `test_journal_keys_and_cascades` |

## The seven conditions (2026-10-15)

1. **No reach-ins.** Met. No module outside the domain calls a private name of a Data module (swept after the pass: `db.key`
   is public, `schema.row_key`, `schema.version`, `schema.pending_on`, `schema.forget` are the doors); the contract rows
   above are in the Integration table.
2. **No rule written twice.** Met. The stored-value comparison and the column list (`schema.same_stored`, `table_columns`),
   the pending list, the schema version, the row-key text and the library key each have one owner; the rooted-row scan
   is one checker (`roots_check.broken` over `store.roots.PATH_COLUMNS`), read once per connection.
3. **No hidden coupling.** Met. The doctor's cache depends on its own stamp (writes and `data_version`) and is not stored
   inside a transaction (a rollback case is a test).
4. **No concept that could collapse.** Met. The data-changing migration kind, `rebuild_table`, `roots_verify` under its old
   name, `inspection.schema_version` and `libraries._key` are gone.
5. **No branch without an instance.** Met. About 30 version probes and legacy arms went with the baseline at 32; what the
   baseline still makes and nothing reads is by design (below).
6. **Scenario tests, owned and mapped.** Met. Every file is owned in `tests/owners.py`; the four per-service failure tests
   became one scaffold test with mutation proofs for all three arms of `maintenance.run`; the bar is that no unit test
   restates a scenario, and the rest hold a specific rule. D2's undo is the undo matrix
   (`test_undo_after_every_later_kind`, 2026-10-24: every ordered pair of the change kinds a library holds, made by their
   real writers); the per-pair refusal tests it lifted were deleted (#1805).
7. **No open `data` or `wrong` finding.** Met: none in the domain.

## By design (each row closed `decided:`)

- **#464, #1382**: no runner check of a migration's declared `touches`, nor of the AUTOINCREMENT counters. Both guard only a
  migration above 32; the one there is, 33, is destructive (it claims no undo exemption) and rebuilds no AUTOINCREMENT
  table (`DROP COLUMN` rewrites `faces` in place; `sqlite_sequence.faces` equals `MAX(faces.id)` in the three libraries (302,954, 10,241 and 3,329, counted 2026-10-18, read-only, at 32)). The rule instead: a migration branch carries, in its own branch, a check of
  an additive step's `touches` if it claims the undo exemption, and a check that no `sqlite_sequence` counter goes down if it
  rebuilds an AUTOINCREMENT table.
- **#1375**: a library whose making failed holds only `schema_version`. The three libraries each have their 33 tables, and
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

- 2026-10-02: settings journaled; the journal not gold-plated during the roots work.
- 2026-10-11: roots adoption and repair-address deleted; the `before-*` copies of versions 2 to 18 deleted.
- 2026-10-15: the baseline moved to 32; migrations 1 to 32, their probes and tests deleted; a library or snapshot below 32 is
  refused untouched.
- 2026-10-15: the data-changing migration kind and `rebuild_table` deleted; `schema.forget` replaced 20 reaches into a
  private; snapshots read their version through `schema.version_of`; one rooted-rows checker.
- 2026-10-15: `faces_pending`'s code deleted; the doctor asks shares for missing files as sync does.
- 2026-10-15: one comparison, one column list, one pending list, one row key; the doctor reads rooted rows once per
  connection; `db.key` made public; `roots_verify` became `roots_sample`.
- 2026-10-15: the one face with a name and no id was cleared; a name alone is nobody and the doctor counts such a row with
  "faces whose name is not their person's".

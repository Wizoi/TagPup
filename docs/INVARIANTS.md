# Invariants

The rules whose violation loses data or misleads the owner. Each has an owner (the one place that implements
it), a one-line reason (what shipped before it was a rule), and the test or guard that enforces it. **Review
against this page.** A new rule goes here with its guard; a rule with no guard says so. The map is
[ARCHITECTURE.md](ARCHITECTURE.md); the owner's decisions are in [DECISIONS.md](DECISIONS.md); the longer
reasoning is in [DEVELOPMENT.md](DEVELOPMENT.md) and [history/](history/README.md).

The library is photographs of real people, many of them minors: **no real name in any tracked file** (tests,
fixtures, comments, commit messages, docs) -- use a fictional name of the same shape. *Guard: none; a
review and the scrub of 2026-10-09.*

## One owner for each concern

| Rule | Owner | Why | Guard |
|---|---|---|---|
| Never `sqlite3.connect`; the database only through `db` (journal mode, busy timeout, the per-file write lock, retry) | `tagpup/store/db.py` | 71 places opened a connection, each deciding for itself; "database is locked" against its own server | `tests/test_db_access.py` |
| SQL only inside `tagpup.store` | `tagpup/store/` | SQL outside the store is not caught by any other guard | `tests/test_sql_single_owner.py` |
| Never call `subprocess`; start, run, kill and `is_alive` through `processes` (hidden console) | `tagpup/core/processes.py` | a terminal window on the desktop for every test process | `tests/test_processes_single_owner.py` |
| Never construct `ExifToolHelper`/`ExifTool`; use `ExifToolSession` (drains stdout and stderr, a deadline per command) | `tagpup/files/exiftool_session.py` | two scripts sat at 0% CPU for two days on a full stderr pipe | `tests/test_exiftool_single_owner.py` |
| Never load a model on the GPU yourself; one process at a time has a model on the card, in turn, through `Runtime.begin` / `gpu_turn` | `tagpup/ml/gpu.py`, `tagpup/runtime.py` | the server's Suggest and its indexer both loaded ViT-H-14 onto one 10 GB card and sat at 0 of 165 for 48 minutes (#750) | `tests/test_gpu_single_owner.py` |
| A model is built once, by the runtime, from the library's own settings | `tagpup/runtime.py` | models built where used meant two settings in one process and 26 readers of `config.ini` | `tests/test_models_single_owner.py`; `tests/test_no_test_loads_a_model.py` |
| Never spell or compare a photo path by hand: `stored()` (written, walked, sent), `key()` (compared in memory), `sql_equals()`/`sql_under()` (SQL); the page's `pathKey`/`samePath` | `tagpup/core/paths.py`, `web/common/paths.js` | `D:\x` to `D:/x` made tag writes, renames and deletes match no row for three months while reporting success | `tests/test_paths_single_owner.py`, `tests/frontend/path-helpers.test.mjs` |
| Never convert between a person's name and their tag by hand: `leafOf`, `rootOf`, `samePerson`, `photoAlreadyHas`; in Python `vocabulary` and `taxonomy.find_person_path()` | `tagpup/core/vocabulary.py`, `web/common/vocabulary.js` | identity is a leaf, the tag a path; hand splits disagreed in 54 places | `tests/test_vocabulary.py`, `tests/frontend/tag-vocabulary.test.mjs` |
| A library's settings live in the library, read through `services.settings`, changed only by a journaled change; nothing reads `config.ini` at all (an old file is ignored, left on disk); a library holding none is stamped with the defaults | `tagpup/services/settings.py`, `tagpup/config.py` | 26 places read `config.ini` and disagreed on four things | `tests/test_config_single_owner.py` |
| What may be set has one rule per kind of input; services refuse on it, pages check from the rules the server publishes | `tagpup/core/validation.py`, `web/common/validate.js` | a tag holding `|` was written into a photo file | `tests/test_validation.py`, `tests/test_tags_are_checked_where_they_are_set.py`, `tests/test_services_refuse_what_the_rules_refuse.py` |
| Derived tables (`photo_tags`, `folders`, `photo_folder`, `photo_meta`, the search words) are written only by their rebuild functions, in the writing transaction | `tagpup/store/derived.py`, `tagpup/store/search_index.py` | a writer that forgot left the views describing what a photo used to hold | `tests/test_derived_writers.py`, `tests/test_derived_follow_writes.py` |
| A person is the id of a tag-tree node; a person comes in through `person_ids.resolve`; a name with no id is linked only by the writer that knew the id, by `link_added` (a name a person was ADDED under) or by the owner (`people link-name`) | `tagpup/store/person_ids.py` | a name matched by chance says nothing about who a row meant | `tests/test_person_ids.py`, `tests/test_tree_edit_links_only_what_it_adds.py`, `tests/test_people_link_name.py` |
| Who a face is, and whether its name looks right, is decided in `clustering` | `tagpup/core/clustering.py` | three different centroids flagged half of every correctly named face | `tests/test_face_values_have_one_owner.py` |
| The tree says which roots hold faces; the buckets (Unknown Faces, Ungrouped, Excluded) are named once | `tagpup/core/vocabulary.py`, `tagpup/store/taxonomy.py` | five lists of root names disagreed; a person called "Excluded" opened the excluded faces | `tests/test_face_roots_are_the_trees.py`, `tests/test_bucket_names_have_one_owner.py`, `tests/test_rules_have_one_owner.py` |
| A library's tag tree is in the library | `tagpup/store/taxonomy.py` | a JSON file named by pattern saved a test tree into the real library | `tests/test_the_tree_is_the_librarys.py` |
| A page's request goes through `api.js`; elements are built from text by `dom.js`, never a value into `innerHTML` | `web/common/api.js`, `web/common/dom.js` | a bare `fetch` reaches no library; markup built from text is not an injection | `tests/frontend/dom-output.test.mjs`, `tests/frontend/page-modules.test.mjs` |
| Imports go down the layers; `scripts/` holds programs, nothing imports from it | `tests/shipped_sources.py` | two servers each carried their own copy of the library | `tests/test_layers.py`, `tests/test_scripts_are_entry_points.py` |

## Writes

| Rule | Owner | Why | Guard |
|---|---|---|---|
| Every bulk edit, maintenance operation and data-changing migration is one journaled change: dry run first, `--apply` to write, undoable where the rows are still what it left | `tagpup/store/journal.py`, `tagpup/services/maintenance.py` | six scripts wrote without a backup; a full copy of 1.4 GB per run | `tests/test_journal*.py`, `tests/test_bulk_scripts_back_up.py`, `tests/test_journaled_write_is_one_command.py` |
| Journal keys are never reused; a cascade into a journaled table is recorded or forbidden | `tagpup/store/journal.py` (`KEYS`) | an undo could put a row back on a key a newer row had taken | `tests/test_journal_keys_and_cascades.py` |
| A photo file is written only through the journaled path (the file marked `writing`, then `done` with its row); a file changed outside is a conflict and never overwritten | `tagpup/services/file_changes.py`, `tagpup/files/field_values.py` | a crash between a file and its row left them disagreeing | `tests/test_file_journal.py` |
| A write reports what it changed, not what it attempted (`changed` counts a file whose read-back differs) | the service that writes | a backfill read 60 photos, reported 60 done, and wrote nothing | `tests/test_changed_counts_what_changed.py`, `tests/test_relink_reports_rows_changed.py` |
| Check the destination before moving data into it | `tagpup/services/relink_photos.py`, `folder_moves.py` | re-pointing rows at paths that already had rows created 233 duplicate faces | `tests/test_relink_checks_the_destination.py` |
| A bulk write tells the index what it wrote (`record_tags_in_index`); a row keeps the shape the indexer records | `tagpup/store/photos.py` | the bulk paths left rows describing what photos used to hold | `tests/test_tag_writes_keep_the_index_true.py`, `tests/test_a_saved_row_is_the_indexers_shape.py` |
| A row nobody read from its file is never stamped as read | `tagpup/store/photos.py` | a caption-only write stamped a path-only row, and the scan trusted it for ever | `tests/test_partial_writes_leave_rows_unread.py`, `tests/test_a_stamped_empty_row_is_never_read.py` |
| Nothing is written into a photo that does not decode, or may be an incomplete copy | `tagpup/services/damaged_photos.py` | an XMP id was written into 13 damaged files | `tests/test_no_writes_to_damaged_photos.py`, `tests/test_damaged_photo_writes_never_fail_open.py` |
| A drive or share that does not answer is not "missing" | `tagpup/files/shares.py` | a sleeping NAS made every photo on it read-only in the page | `tests/test_unreachable_is_not_missing.py` |
| A face is never recorded for a photo with no row; rows are made only in folders the library holds or was asked to add | `tagpup/store/photos.py` (`ensure_row`) | 859 faces on 95 photos had no row | `tests/test_every_face_has_a_photo_row.py`, `tests/test_writes_only_in_folders_held.py`, `tests/test_rows_only_in_folders_added.py` |
| A look (the CLI's read commands, the MCP inspections, the doctor) never migrates or stamps a library | `tagpup/runtime.py` (`peek_settings`) | a look at a library one migration behind applied it | `tests/test_a_look_does_not_migrate.py` |
| Only the web server runs the recurring jobs (the CLI lists and runs one by hand; the MCP server runs none) | `tagpup/jobs/recurring.py` | the owner's decision (#334): one runner, so no two processes run one job | `tests/test_only_the_server_runs_jobs.py` |
| A migration runs in one transaction with its checks; an additive one changes no row that was there; a destructive one takes a backup; one migration is in flight at a time (CLAUDE.md) | `tagpup/store/schema.py` | a migration runs on the owner's only copy of the data | `tests/test_migrations.py`, `tests/test_schema.py` |
| An update never ends a server doing work; the hand-over waits for it | `tagpup/launcher.py`, `tagpup/web/lifecycle.py` | an install must not need Task Manager, and a save under way must not be cut | `tests/test_launcher_hands_over.py` |
| The server answers this PC only (loopback) | `tagpup/web/security.py` | the library is photographs of real people | `tests/test_local_server_socket.py` |

## Standing decisions of the owner (see [DECISIONS.md](DECISIONS.md))

| Rule | Owner | Guard |
|---|---|---|
| **A person is a leaf tag.** A branch is a group, never a person; a group tag is never put on a photo as a person; a tag used on photos gets no children | `person_ids.resolve`, `tags` service | `tests/test_group_tag_is_not_a_person.py` |
| **A name with no person tag is not converted automatically**: it is listed for the owner (names to review), who decides each | `tagpup/services/name_review.py` | `tests/test_name_review.py` |
| **Ghosts are only reported.** A gone folder with no rows under it is counted (named with `--reveal`); nothing follows it | `tagpup/services/folder_moves.py` | `tests/test_relink_folders.py` |
| **Nothing converts a library unasked.** Adopting a root, marking folders (`.tagpup`) and stamping a library id are explicit commands, a dry run first, and never run by indexing, sync or the watcher | `tagpup/services/roots.py`, `folder_ids.py` | `tests/test_roots_adoption.py`, `tests/test_folder_ids.py` |
| **A marker is exact**: a folder carrying its own id follows any rename; `relink-folders` does not mark a folder it followed | `tagpup/services/folder_ids.py` | `tests/test_folder_ids_scenarios.py` |
| **Date Taken, not file mtime**, orders and dates photos (mtime changes from metadata writes are accepted) | `tagpup/core/dates.py` | none specific |
| **Truncated JPEGs are refused**, not read (#415, 2026-10-08) | `tagpup/files/images.py`, `tagpup/services/damaged_photos.py` | `tests/test_no_writes_to_damaged_photos.py` |

## How a test and a change must be

- A test that makes a library runs in a home of its own (`tests/own_home.py`, `tests/web_client.py`); seed rows
  as the indexer stores them (`tests/photo_rows.py`), never through the helper under test; web tests use
  Flask's test client. *Guard: `tests/test_tests_have_homes_of_their_own.py`.*
- SQL on this library is not SQL on a fixture (225,000 faces, most carrying a 6 KB crop): no `LIKE` for
  equality, no function on an indexed column, never a BLOB you do not use, no per-item query for a per-run
  answer; check `EXPLAIN QUERY PLAN` against the real library, read-only.
- A fix has a test that fails on the old code first. A branch is about 1,200 lines or fewer, one concern, at
  most two review rounds (CLAUDE.md, "Keeping the project small").

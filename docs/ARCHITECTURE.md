# TagPup architecture

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

This is a **map**: what each layer and package owns, the data model on one page, the processes and
apps, and where to look for each concern. It is short on purpose (a session can read all of it).
For new code, this document wins over [DEVELOPMENT.md](DEVELOPMENT.md) (how to work in the code).

| Read | For |
|---|---|
| [INVARIANTS.md](INVARIANTS.md) | The rules whose violation loses data or misleads, each with its owner and the test that enforces it. Review against this. |
| [DECISIONS.md](DECISIONS.md) | The owner's decisions, dated, one line each. Not reopened without a new fact. |
| [findings.md](findings.md) | Open findings. Closed ones are archived in [history/](history/README.md). |
| [DATABASE.md](DATABASE.md) | Every table and column. |
| [history/](history/README.md) | The design logs: each phase's design as built, measurements, review rounds. Read only when asked, or to learn why something is as it is. Every section that used to be here is there, verbatim. |

## Where to look

| A concern | Its owner | Guard |
|---|---|---|
| Opening the database, journal mode, the per-file write lock | `tagpup/store/db.py` | `tests/test_db_access.py` |
| Every SQL statement | `tagpup/store/` | `tests/test_sql_single_owner.py` |
| Schema and migrations (each declares additive, data-changing or destructive) | `tagpup/store/schema.py` | `tests/test_migrations.py`, `tests/test_schema.py` |
| Bulk edits and migrations recorded, rehearsed, undoable | `tagpup/store/journal.py`, `tagpup/store/file_journal.py`, `tagpup/services/journal.py`, `tagpup/services/file_changes.py` | `tests/test_journal*.py`, `tests/test_file_journal.py` |
| A photo path's spelling, comparison and SQL form; the root-relative form | `tagpup/core/paths.py`, `tagpup/store/roots.py` | `tests/test_paths_single_owner.py` |
| Tags, people, captions taken apart; the person tag | `tagpup/core/vocabulary.py`, `tagpup/store/taxonomy.py` | `tests/test_vocabulary.py` |
| A person by the id of their tag-tree node | `tagpup/store/person_ids.py` | `tests/test_person_ids.py` |
| ExifTool | `tagpup/files/exiftool_session.py` | `tests/test_exiftool_single_owner.py` |
| Writing a photo file's fields | `tagpup/files/field_values.py`, `tagpup/files/keywords.py`, `tagpup/services/file_changes.py` | `tests/test_file_journal.py` |
| Child processes | `tagpup/core/processes.py` | `tests/test_processes_single_owner.py` |
| The graphics card, one process at a time | `tagpup/ml/gpu.py` (turns through `tagpup/runtime.py`) | `tests/test_gpu_single_owner.py` |
| Models built once, from a library's settings | `tagpup/runtime.py` | `tests/test_models_single_owner.py` |
| A library's settings; where the libraries are | `tagpup/services/settings.py`; `tagpup/config.py` | `tests/test_config_single_owner.py` |
| What may be set (one rule per kind of input) | `tagpup/core/validation.py`, `web/common/validate.js` | `tests/test_validation.py` |
| Derived tables (`photo_tags`, `folders`, `photo_folder`, `photo_meta`, the search words) | `tagpup/store/derived.py`, `tagpup/store/search_index.py` | `tests/test_derived_writers.py` |
| Who is who (clustering, a face's name looking wrong) | `tagpup/core/clustering.py`, `tagpup/services/identities.py` | `tests/test_face_values_have_one_owner.py` |
| Keeping a library in step with its folders | `tagpup/services/sync.py`, `tagpup/jobs/watching.py` | `tests/test_sync.py` |
| A folder renamed outside the apps | `tagpup/services/folder_moves.py`, `tagpup/services/folder_ids.py`, `tagpup/files/folder_marker.py` | `tests/test_relink_folders.py`, `tests/test_folder_ids*.py` |
| Roots (where a library's folders are, per machine) | `tagpup/services/roots*.py`, `tagpup/store/adoption.py`, `tagpup/config.py` | `tests/test_roots*.py` |
| The library views (grid, navigator, search) | `tagpup/services/library_view.py`, `tagpup/store/library_view.py`, `web/tagpup/` | `tests/test_library_view.py`, `tests/frontend/` |
| A page's request, its DOM output | `web/common/api.js`, `web/common/dom.js` | `tests/frontend/` |
| Updating a running server | `tagpup/launcher.py`, `tagpup/supervisor.py`, `tagpup/web/lifecycle.py` | `tests/test_launcher_hands_over.py`, `tests/test_supervisor.py` |
| Which files are checked by every guard | `tests/shipped_sources.py` | |

## Why change

TagPup is organized by program: a TagPup server (`scripts/tagpup_server.py`, 4,088 lines), a TagTuner server (`scripts/tuner_server.py`, 5,326 lines), a CLI, a desktop runner, and 28 more modules in `scripts/`. Each carries its own copy of what a photo library is and does. On 2026-09-23:

- The two servers held 215 SQL calls and 74 routes, and 51 function names were defined in both. Eight TagTuner routes were never called by its own page; only tests reached them.
- ExifTool was opened from 22 places, images from 10 (with different orientation handling), and `config.ini` was read in 26.
- The database mixed copies of file metadata, decisions that exist nowhere else, and derived data, without saying which is which. `photos.people` was missing 1,626 names across the two libraries.
- A photo's identity was its path, so every rename had to re-point faces, embeddings and suggestions by hand.

Nearly every bug fixed that day had one of four shapes:

1. Many writers, no owner.
2. One fact stored in several places.
3. State cached in one process while three processes write the same database.
4. "Success" meaning "I tried".

Each time the codebase gained a single owner with a guard test (`db.py`, `paths.py`, `exiftool_session.py`, the tag vocabulary), a class of bug ended. This design does that for everything, on purpose, instead of after the fourth bug.

## Principles

1. **One owner per concern.** Every concern (a table, a file format, a rule) has exactly one module that implements it, and everything else calls that module. A guard test enforces each owner.
2. **Three kinds of data, one writer each.**
   - *File copies:* keywords, captions, dates, orientation, raw metadata, DocumentID. The photo file is the truth and the row is a cache of it. Only `sync_photo()` writes these, straight after a file is read or written.
   - *Decisions:* faces, the names people give them, exclusions, the tag tree. They exist only in the database and change only through services.
   - *Derived data:* people lists, embeddings, face crops, the vector index, Identify grids, suggestions, thumbnails. Feature code never writes them. One function rebuilds each, or it is computed on read, and caches are keyed by generation.
3. **A photo has an id.** Rows refer to photos by integer id, and the path is an attribute. A rename is one update.
4. **Every write reports what it changed.** Service functions return a `Result`: changed, attempted, skipped, errors.
5. **Context is explicit.** A `Library` object is passed to whatever needs it. There are no thread-locals and no class-level registries keyed by an implicit current library.
6. **Caches are keyed by library and generation.** Database triggers bump a generation when the data a cache depends on changes, whichever process changes it.
7. **The app you use runs from an installed copy, not the working tree.** Editing code never restarts it.

## Layers

```
   TagPup page    TagTuner page            web/: ES modules sharing web/common/
         \            /
   one web server (tagpup.web)     CLI (tagpup.cli)     scripts/, tools/
                \                        |                  /
                 +-----------------------+-----------------+
                 |                                         |
                 |                     jobs (tagpup.jobs): queue, status,
                 |                     cancel, worker processes for GPU work
                 v                                         v
        services (tagpup.services): one function per user action; the only writers
                 |
     +-----------+-----------+-------------+
     v           v           v             v
   core        store       files           ml
   rules       SQL         photo files     models
```

Imports only go down:

| Layer | Package | Owns | May import |
|---|---|---|---|
| core | `tagpup.core` | Pure rules: path identity (and a root's row form, `paths.to_row` / `from_row`), a library's name and the files that belong to it (`Library`), the tag vocabulary (leaf, root, person), people derivation, suggestion scoring, clustering decisions; and `core.machine`, the one place the store asks for the machine's map of the roots | nothing but `core` |
| config | `tagpup.config` | `TAGPUP_HOME` and where the libraries are (its `data/`), which ExifTool the machine has, where this machine keeps each root (`machine_roots.json`, read, and written only when an adoption finds the root missing from it; it registers its reader with `tagpup.core.machine` when imported, and `core.machine` imports it itself when asked with none registered, so no entry point has to remember to), and nothing of an old `config.ini`, which no code reads (phase 7.6). A library's settings are its own (`tagpup.services.settings`). Never written by the app (#100) | `core` (to spell and check a root's locations) |
| logs | `tagpup.logs` | Each program's log file in `data/logs/`, each line carrying the runs under way (`tagpup.core.runs`), and reading them back, bounded, for the Activity page. Set up by entry points | `core`, `config` |
| supervisor | `tagpup.supervisor` | The always-on process (phase 8): runs the web server as its child, restarts it, moves it onto a newer installed version once drained, one per home; and the names it shares with the server (the token, `data/server.json`, the exit code for ports another holds), which `tagpup.web` reads | `core`, `config`, `logs` |
| launcher | `tagpup.launcher` | Replacing a running server with the version being launched, without the always-on process (#726): each server's record of where it answers -- its ports, version, and a token, the launcher's authority to drain it -- in the user's own folder (`%LOCALAPPDATA%\TagPup\servers`); and `make_way`, a launcher's: ask the version, drain another, end it once drained or hung, serve; and `hand_over`, an install's: the same `make_way`, opening no page, then the installed version started on the old one's ports (#795) | `core`, `config`, `logs`, `supervisor` |
| store | `tagpup.store` | The library database: connections and locks, schema and migrations, generations, one repository per table, caches keyed by generation, backups | `core` |
| files | `tagpup.files` | The photo files: ExifTool sessions, reading metadata, writing keyword, caption and orientation fields, identities, opening images (upright or as stored), crops and thumbnails | `core` |
| ml | `tagpup.ml` | Models: CLIP embeddings, face detection and embeddings, the vector index; and the graphics card, one process at a time (`tagpup.ml.gpu`) | `core`, `files` |
| services | `tagpup.services` | One function per user action. The only code that writes. Returns a `Result` | all of the above |
| jobs | `tagpup.jobs` | Background work: queue, status, cancel, persistence, worker processes for GPU work | `core`, `services` |
| runtime | `tagpup.runtime` | The composition root: turns the settings an entry point read into the process's long-lived objects -- CLIP and the face models, the per-library job runners -- builds each once, warms the models on a thread, and hands them down as arguments | every layer above but the entry points |
| entry points | `tagpup.web`, `tagpup.cli`, `tagpup.mcp`, `scripts/`, `tools/` | HTTP, the command line, the MCP server Claude works through, maintenance and development tools. Each reads the settings, builds one `Runtime`, and passes it on | `config`, `logs`, `runtime`, `services`, `jobs` (and `core` for formatting); `tagpup.web` also `supervisor` and `launcher`, for the names they share |

Guard tests, each of which fails the build. The ones marked *exists* are in place; the rest arrive with their phase.

- Imports inside `tagpup/` go down the layers, and package code never imports an old module from `scripts/` by name. *Exists:* `tests/test_layers.py`.
- `sqlite3.connect` only in `tagpup.store.db`. *Exists:* `tests/test_db_access.py`.
- pyexiftool's classes constructed only in `tagpup.files.exiftool_session`. *Exists:* `tests/test_exiftool_single_owner.py`.
- Photo paths spelled and compared only by `tagpup.core.paths`. *Exists:* `tests/test_paths_single_owner.py`.
- Tags taken apart only by `tagpup.core.vocabulary`. *Exists:* `tests/test_vocabulary.py`.
- SQL only inside `tagpup.store`. *Exists:* `tests/test_sql_single_owner.py`.
- `config.ini` read by nothing (an old file in a home is ignored, never written or deleted); inside `tagpup/`, only `tagpup.config` finds folders from `__file__`. *Exists:* `tests/test_config_single_owner.py`.
- A model (CLIP, the face models) built only by `tagpup.runtime`, and what it is made of only in its own `tagpup.ml` module. *Exists:* `tests/test_models_single_owner.py`.
- The graphics card's turns taken only through `tagpup.runtime` (and each model's own check before it loads), its lock made only by `tagpup.ml.gpu`, and every module that loads weights checks for a turn first. *Exists:* `tests/test_gpu_single_owner.py`.
- ExifTool and `Image.open` only inside `tagpup.files`.
- Entry points import services and jobs, never store, files or ml directly.
- Every POST route returns a `Result`.
- Derived tables written only by their rebuild functions. *Exists:* `tests/test_derived_writers.py`, for `photo_tags`, `folders`, `photo_folder` and `photo_meta`: a function that writes a photo's tags, path or metadata, or the tag tree, goes through `tagpup.store.derived`.
- Pages: `/api/` URLs built only by `web/common/api.js`. Tags split only by the vocabulary helpers (*exists:* `tests/frontend/tag-vocabulary.test.mjs`).
- What may be set: one rule for each kind of input, `tagpup.core.validation`, which the pages apply from `/api/rules` and keep no copy of. *Exists:* `tests/test_validation.py`, `tests/frontend/validation.test.mjs`.
- Pages: nothing but a fixed string written into `innerHTML`, `outerHTML` or `insertAdjacentHTML`; elements are built from text by `web/common/dom.js`. *Exists:* `tests/frontend/dom-output.test.mjs`.

Every guard checks the same list of files, `tests/shipped_sources.py`: the launchers, `scripts/`, and `tagpup/`.

### The layers, revisited (2026-09-24)

Phase 5 found three things with nowhere legal to live, and four modules that stayed in
`scripts/` because each spans layers:

- **A model that needs its settings.** The embedder and the face models read
  `config.ini` themselves, and `ml` may not import `config`.
- **Wiring.** Something has to build the models once and hand them to the suggestion
  runs. Today that is `scripts/suggest_models.py`, a script that imports
  `tagpup.web.state` and fills a module-level slot in `tagpup.jobs.suggestions`
  (findings #112): a registry by another name, the thing principle 5 forbids.
- **A generic helper several layers need.** `PerLibrary` lives in `tagpup.web`, and a
  script needs it.
- **The mixed modules.** `ClipEmbedder` is a model plus a store cache. `FaceProcessor`
  is a model plus library-wide identity resolution, which is a service. `PhotoIndex` is
  store wrappers plus a vector index. `TagSuggester` is service logic.

What was weighed:

| | Alternative | Verdict |
|---|---|---|
| A | Let `ml` import `config`. | Rejected. A model reading global settings is how 26 readers of `config.ini` came to disagree; a model's test would need a home of its own; two settings in one process (a sandbox beside the app) would be impossible. |
| B | Merge `store`, `files` and `ml` into one infrastructure layer. | Rejected. The single-owner guards hang on those boundaries: SQL outside `store` is caught because `store` is a layer. Fewer rules, less caught. |
| C | Ports and adapters throughout: every service takes Protocol-typed dependencies, adapters wired at the edge. | Rejected as a rule: forty services, nearly all with one implementation, would gain ceremony and no second adapter. Taken where it pays: a service that uses a model takes the model as an argument, and its test passes a fake. |
| D | **Keep the layers; add one composition root, `tagpup.runtime`.** | **Chosen.** It is the only place that turns settings into objects. Entry points read `config`, build a `Runtime`, and hand it to the web factory or the CLI command. Services and jobs never reach for a model or a setting; they are given them. It replaces the slot and `scripts/suggest_models.py`. |

With it, `PerLibrary` moves to `tagpup.core.per_library` (a locked map keyed by library;
nothing of Flask's), and each mixed module splits along the layers (phase 5.5).

## Package layout

```
tagpup/                config (where the libraries are), logs, runtime (composition root), supervisor, launcher
  core/                pure rules: paths, vocabulary, validation, clustering, dates, fields, renaming, library,
                       machine, result, per_library, processes, byte_lock, idle, runs, gpu_turns, photo_meta
  store/               the only SQL. db (connections, locks), schema (migrations), one module per table or
                       family: photos, faces, people, person_ids, taxonomy, embeddings, suggestions, journal,
                       file_journal, settings, roots + adoption + root_rows, derived, search_index, folders,
                       folder_ids, library_view, snapshots, generations, locks, job_runs, sync_runs,
                       added_folders, damaged_files, faces_pending, faces_detected, name_review, checks
  files/               the photo files: exiftool_session, metadata, field_values, keywords, identity, images,
                       thumbs, names, folder_marker, shares, lock_owners, recycle_bin, job_files
  ml/                  models: clip, faces, vector_index, grouping, and gpu (the card's turn)
  services/            one function per user action; the only code that writes: tagging, tags, people, faces,
                       identities, identify, indexing, sync, refresh_rows, relink_photos, folder_moves,
                       folder_ids, roots*, settings, journal, file_changes, bulk_edit, selection,
                       library_view, search, suggester, suggestions, snapshots, thumbnails, name_review,
                       faces_from_tags, tags_from_faces, face_people, inspect, activity, file_access, maintenance
  jobs/                background work: indexing queue, suggestions, bulk_edits, face_assignments,
                       naming_faces, identify, verifying, watching, recurring
  web/                 one Flask app per page from one factory (app.py), *_routes.py by concern, security,
                       lifecycle, libraries, state, responses
  cli.py (tagpup_cli.py at the root is its launcher)    mcp/ (the MCP server, one tool per read, four maintenance operations)
web/                   the pages (ES modules, no build step): common/ (api, paths, vocabulary, validate, dom,
                       dialog, tag-editor, settings, history), tagpup/, tuner/, activity/
scripts/               programs to run (install_app, startup, measure_*, maintenance scripts); nothing imports from it
tools/                 development tools: run_tests, affected_tests, doctor, add_findings, patch, watch_processes
tests/                 Python (unittest) and tests/frontend (node --test)
```

`tests/test_layers.py` says what may import what; `tests/test_scripts_are_entry_points.py` says `scripts/` holds
programs only. A moved module takes every importer with it and leaves nothing at its old name.

## Processes, servers and apps

- **One server, two apps.** One Flask app per page from one factory (`tagpup.web.app.create_app`), served by
  one Waitress process: TagPup (browse, tag, the library views, search) on port 8090 and TagTuner (Identify
  Faces, the tag tree, settings, roots, history) on 8080. The library comes from the URL. It listens on this
  PC only (127.0.0.1 and ::1) until phase 10 adds logins. Also served: the Activity page (`/activity/`, the
  background work of every library, loopback only). The desktop **Runner** (`runner.py`), the **CLI**
  (`tagpup_cli.py`: index, sync, write, search, history, undo, jobs, folder-ids, roots, ...) and the **MCP
  server** (`python -m tagpup.mcp`, for Claude: reads give counts and ids, names and paths only with
  `reveal=true`) are the other entry points. All build one `Runtime` and call services.
- **One composition root** (`tagpup.runtime.Runtime`): models built once from the library's own settings,
  warmed on a thread, released after an idle period; each library's job runners; `begin`/`gpu_turn` take the
  card's turn. Services and jobs are given models; they never build one.
- **Background jobs** run through one runner per library (`tagpup.jobs`): the index queue (an indexing child
  process, via the CLI's `index`), Suggest runs, bulk edits by photo id (resumable, one journaled change per
  chunk), face assignments, Name faces from tags, Verify of a root, Identify caches. **Recurring jobs**
  (`tagpup.jobs.recurring`: snapshots, sync, pruning the journal) are run by the web server only; the
  folder **watcher** syncs a folder when it changes. Event-driven first: a schedule is only for safety.
- **The graphics card**: one process at a time on the machine has a model on it (`tagpup.ml.gpu.Card`, a byte
  lock in the user's own folder, waiters queued and named on the Activity page).
- **The installed copy.** The owner runs `%LOCALAPPDATA%\TagPup\*.cmd`, made by `scripts/install_app.py`, with
  `TAGPUP_HOME` naming the folder that holds `data/` (the libraries, with their backups and locks). Editing
  the working tree changes nothing they run. Each launcher installs a newer clean commit, then replaces a
  server of another version (`tagpup.launcher.hand_over`: drain, wait for work in progress, end, serve);
  `install_app.py --apply` does the same with no window. An optional always-on process
  (`tagpup.supervisor`, from the Startup folder) runs the server as a child and moves it onto newer versions.
- **Logs** go to `data/logs/`, one rotating file per program, each line carrying the runs under way.
- The full text of the old Runtime section (the card's rules, the hand-over, the always-on process) is in
  [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md), "Runtime".

## Data model on one page

One SQLite file per library (`data/<name>.db`, WAL, migrations 1 to 29 in `tagpup.store.schema`; each runs in
one transaction with its checks, and only a destructive one takes a backup). Columns are in
[DATABASE.md](DATABASE.md). Every table has one kind and one writer.

**Ids.** A photo is `photos.id` (an integer kept across renames and moves; `path` is an attribute, and
`document_id` is the XMP identity the file may carry). A face is `faces.id`, pointing at `photos.id`. **A
person is the id of a node in the tag tree** (`tag_taxonomy.id`): `faces.tag_id` and `photo_people.tag_id`
are the key and `name` a cache of the node's leaf; a name with no id is an *unresolved name*, left as it is
and listed for the owner (names to review). A person tag is a leaf; a branch is a group, never a person.
A folder has two forms: the derived `folders` row (rebuilt, its integer ids change) and, once marked, the
`folder_ids` row (a UUID per library, also written in the folder's `.tagpup` marker file).

**Roots.** `roots` (name, the share's address) is empty until the owner adopts a root. A library with roots
holds a path under one as `@<root>/<relative>` (`paths.to_row`/`from_row`, converted at the database
boundary by `store/roots.py`), so a library follows a share that is mapped differently on each machine
(`machine_roots.json`, read by `tagpup.config`); a library with none holds native absolute paths.

| Kind | Tables | Written by |
|---|---|---|
| File copy (the file is the truth) | `photos` (`tags`, `captions`, `raw_metadata`, `taken`, `year`, `document_id`, `mtime`, `size`) | only after a file is read or written: indexer, `refresh_rows`, `record_tags_in_index` (`store/photos.py`) |
| Decision (exists only here) | `faces` (`name`, `name_source`, `excluded`, `tag_id`), `tag_taxonomy` (the tree), `roots`, `settings`, `folder_ids`, `library_identity`, `added_folders`, `name_review_dismissals`, `damaged_files` | services, through a journaled change where bulk; the tree through `people.tree_edit` |
| Derived (rebuilt or computed; feature code never writes) | `photo_people`, `photo_tags`, `folders`, `photo_folder`, `photo_meta`, `search_words`/`search_names`/`search_gear` (FTS5), `embeddings`, `tag_embeddings`, `face_crops`, `suggestions`, `faces_pending`, `faces_detected` | `store/derived.py` (the four view tables), `store/search_index.py`, `store/people.py`, `store/embeddings.py`, `store/faces.py`, `store/suggestions.py` |
| Infrastructure | `schema_version`, `generations` (triggers bump a counter when a cache's data changes, from any process), `job_runs`, `sync_runs` | `store/schema.py`, `store/generations.py`, `store/job_runs.py`, `store/sync_runs.py` |
| The journal | `changes`, `change_rows` (one row per changed column), `change_files` (each photo file a bulk edit writes, with its state) | `store/journal.py`, `store/file_journal.py` |

**The journal** (7.5). Every bulk edit, maintenance operation and data-changing migration is one `changes`
row: what it found and what it left, applied only where the rows are still what the plan read, rehearsed by
a dry run that applies and undoes inside a rolled-back transaction, undoable on the same terms, and kept
90 days. A bulk edit that writes photo files marks each file `writing`, then `done` in the same transaction
that records its row; a crash is settled at the next start by what the file holds. Keys the journal can
restore are AUTOINCREMENT (never reused). `History`/`undo` are in the CLI, the MCP server and the pages' gear.

**A photo's tags and people** flow one way: the file's keywords are read into `photos.keywords`; `photo_tags`
and `photo_people` are derived from them, the faces and the tree whenever any of the three changes, in the
same transaction (`store/derived.py`, `people.tree_edit`).

## Frontend

- `web/common/` holds ES modules shared by the pages: `api.js` (library-aware URLs and JSON, with a read
  retried across a server update), `paths.js`, `vocabulary.js`, `library.js`, `validate.js` (the rules
  `/api/rules` publishes), `dom.js` (elements built from text), `dialog.js`, and the gear's tag editor,
  settings and history dialogs.
- `web/activity/` is the Activity page; `web/tagpup/` and `web/tuner/` have a `main.js` that only wires the
  page, plus one module per feature, and their mutable state in one `state` object.
- No build step. Tests import the modules (`node --test tests/frontend/*.test.mjs`), jsdom loads a page's
  modules in import order for whole flows (`tests/frontend/harness.mjs`).

## Testing

| What | How |
|---|---|
| core | Plain unit tests. |
| store, services | A temporary library in a home of its own (`tests/own_home.py`, `tests/service_fixture.py`); rows seeded as the indexer stores them (`tests/photo_rows.py`). |
| files, ml | Temporary files; real ExifTool where a file is written; no test loads a model. |
| web | Flask's test client: no sockets, no ports, no sleeps (`tests/web_client.py`). |
| pages | Modules directly; a few flows in jsdom. |
| performance | A sandbox run in a real browser (`scripts/measure_*.py`); a claim is about a user action. |

The whole Python suite is `tools/run_tests.py`. The linter runs inside it (`tests/test_lint.py`).

## Where the old sections went

Every section of the former `ARCHITECTURE.md` is in `docs/history/`, unedited. Code comments and tests that say
"docs/ARCHITECTURE.md, phase 9d-1" or "...'Roots and machines'" mean the section of this table.

| Former section | Now in |
|---|---|
| Runtime | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Data model | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Frontend | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Testing | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Where everything goes | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phases | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 1: Foundations | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 2: Services | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 3: Store | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 4: Data model | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 4.5: One owner for each rule | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 5: One server | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 5.5: Models in the package, one composition root | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 6: Pages | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 6.5: No shims | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 7: MCP | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 7.5: A journal for every bulk edit and migration | [history/ARCHITECTURE_jobs_sync_journal.md](history/ARCHITECTURE_jobs_sync_journal.md) |
| Phase 7.6: Settings in the library, and a gear on each page | [history/ARCHITECTURE_jobs_sync_journal.md](history/ARCHITECTURE_jobs_sync_journal.md) |
| Phase 8: Sync | [history/ARCHITECTURE_jobs_sync_journal.md](history/ARCHITECTURE_jobs_sync_journal.md) |
| Phase 8.5: Activity | [history/ARCHITECTURE_jobs_sync_journal.md](history/ARCHITECTURE_jobs_sync_journal.md) |
| Folder ids | [history/ARCHITECTURE_roots_and_markers.md](history/ARCHITECTURE_roots_and_markers.md) |
| File access check | [history/ARCHITECTURE_jobs_sync_journal.md](history/ARCHITECTURE_jobs_sync_journal.md) |
| Roots and machines | [history/ARCHITECTURE_roots_and_markers.md](history/ARCHITECTURE_roots_and_markers.md) |
| Phase 9: Library views | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9a-1: the derived tables the views stand on | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9a-2: the thumbnail cache, the library views' query service and their routes | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9b-1: the windowed grid, and the folder view on it | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9b-2: a library source for the grid | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9c: the navigator, the move between a folder and its view, stale marks, the grid's keys | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9d-1: bulk edits by photo id, as a job; a selection's tally | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9d-2: editing across folders -- the selection by id, bulk edits through the job, the strip, the tally | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9, the owner's review: several rows at once, People by branch, one sort | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| The owner's first review of the library views | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| The owner's second review of the library views | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9e-1: search on the server | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Phase 9e-2: the search page | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Identity by id | [history/ARCHITECTURE_identity.md](history/ARCHITECTURE_identity.md) |
| People by id, stage 2 | [history/ARCHITECTURE_identity.md](history/ARCHITECTURE_identity.md) |
| Ideas taken from Windows Live Photo Gallery's database | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Faces on the photo | [history/ARCHITECTURE_faces_and_naming.md](history/ARCHITECTURE_faces_and_naming.md) |
| Name faces from tags | [history/ARCHITECTURE_faces_and_naming.md](history/ARCHITECTURE_faces_and_naming.md) |
| Backlog: face regions and the expensive results in the photo file | [history/ARCHITECTURE_faces_and_naming.md](history/ARCHITECTURE_faces_and_naming.md) |
| The library view's selection details navigate, and a photo has a way back | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Camera and lens words | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| Backlog: which photo fields are searchable | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| The owner's decisions for the three big projects | [history/ARCHITECTURE_faces_and_naming.md](history/ARCHITECTURE_faces_and_naming.md) |
| Phase 10: Family albums from many sources | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| After the re-architecture | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Decisions | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Progress | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |

Named things inside those sections that code and tests refer to by name:

| Name | Now in |
|---|---|
| Analyse-only Suggest | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Changing where a root lives | [history/ARCHITECTURE_roots_and_markers.md](history/ARCHITECTURE_roots_and_markers.md) |
| Faces and the photo's person tag | [history/ARCHITECTURE_faces_and_naming.md](history/ARCHITECTURE_faces_and_naming.md) |
| One owner of the graphics card | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Phase 9: the owner's review (written as "Phase 9, the owner's review") | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
| The `jobs` table (planned in the first Data model; never built, jobs live in memory) | [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md) |
| Tree operations, by id | [history/ARCHITECTURE_identity.md](history/ARCHITECTURE_identity.md) |

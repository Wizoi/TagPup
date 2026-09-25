# TagPup architecture

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

Where the code is going, why, and in what order. Agreed 2026-09-23. Known problems and review findings are tracked in [findings.md](findings.md).

[DEVELOPMENT.md](DEVELOPMENT.md) covers how to work in the code as it is today. This covers the shape it is moving to. For new code, this document wins.

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
| core | `tagpup.core` | Pure rules: path identity, a library's name and the files that belong to it (`Library`), the tag vocabulary (leaf, root, person), people derivation, suggestion scoring, clustering decisions | nothing but `core` |
| config | `tagpup.config` | `TAGPUP_HOME` and where the libraries are (its `data/`), which ExifTool the machine has, and the one reading of an old `config.ini`, for stamping a library that holds no settings yet (phase 7.6). A library's settings are its own (`tagpup.services.settings`). Never written by the app (#100) | nothing |
| logs | `tagpup.logs` | Each program's log file in `data/logs/`. Set up by entry points | `config` |
| store | `tagpup.store` | The library database: connections and locks, schema and migrations, generations, one repository per table, caches keyed by generation, backups | `core` |
| files | `tagpup.files` | The photo files: ExifTool sessions, reading metadata, writing keyword, caption and orientation fields, identities, opening images (upright or as stored), crops and thumbnails | `core` |
| ml | `tagpup.ml` | Models: CLIP embeddings, face detection and embeddings, the vector index | `core`, `files` |
| services | `tagpup.services` | One function per user action. The only code that writes. Returns a `Result` | all of the above |
| jobs | `tagpup.jobs` | Background work: queue, status, cancel, persistence, worker processes for GPU work | `core`, `services` |
| runtime | `tagpup.runtime` | The composition root: turns the settings an entry point read into the process's long-lived objects -- CLIP and the face models, the per-library job runners -- builds each once, warms the models on a thread, and hands them down as arguments | every layer above but the entry points |
| entry points | `tagpup.web`, `tagpup.cli`, `tagpup.mcp`, `scripts/`, `tools/` | HTTP, the command line, the MCP server Claude works through, maintenance and development tools. Each reads the settings, builds one `Runtime`, and passes it on | `config`, `logs`, `runtime`, `services`, `jobs` (and `core` for formatting) |

Guard tests, each of which fails the build. The ones marked *exists* are in place; the rest arrive with their phase.

- Imports inside `tagpup/` go down the layers, and package code never imports an old module from `scripts/` by name. *Exists:* `tests/test_layers.py`.
- `sqlite3.connect` only in `tagpup.store.db`. *Exists:* `tests/test_db_access.py`.
- pyexiftool's classes constructed only in `tagpup.files.exiftool_session`. *Exists:* `tests/test_exiftool_single_owner.py`.
- Photo paths spelled and compared only by `tagpup.core.paths`. *Exists:* `tests/test_paths_single_owner.py`.
- Tags taken apart only by `tagpup.core.vocabulary`. *Exists:* `tests/test_vocabulary.py`.
- SQL only inside `tagpup.store`. *Exists:* `tests/test_sql_single_owner.py`.
- `config.ini` read only by `tagpup.config.config_ini`, called only by the one-time stamping of a library's settings (`tagpup.runtime.library_settings`); inside `tagpup/`, only `tagpup.config` finds folders from `__file__`. *Exists:* `tests/test_config_single_owner.py`.
- A model (CLIP, the face models) built only by `tagpup.runtime`, and what it is made of only in its own `tagpup.ml` module. *Exists:* `tests/test_models_single_owner.py`.
- ExifTool and `Image.open` only inside `tagpup.files`.
- Entry points import services and jobs, never store, files or ml directly.
- Every POST route returns a `Result`.
- Derived tables written only by their rebuild functions.
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

## Runtime

- **One server, two apps.** One Flask app, served by Waitress, answers on both ports used today: 8090 for TagPup, 8080 for TagTuner. The library comes from the URL as it does now, and becomes a `Library` object for the request. The Host and Origin check is a `before_request` hook.
- **One composition root.** `tagpup.runtime.Runtime` holds what lives as long as the process: the models, built once from the settings it was given and warmed on a thread, and each library's job runners. The web factory and each CLI command are handed one; nothing below them builds a model or reads a setting.
- **Background jobs** (indexing, suggestions, clustering, refresh) run through one job runner per library, with status, cancel and persistence. GPU-heavy work runs in a worker process, as indexing does today through the CLI.
- **The installed copy.** `scripts/install_app.py` copies the code into a version folder under `%LOCALAPPDATA%\TagPup` and writes launchers that run it. `TAGPUP_HOME` names the folder that holds `data/`: the libraries, which hold their own settings, with each library's backups and locks beside them. Updating is a deliberate step, installing again, and the two versions before stay to go back to. The auto-reloader is for development only.
- **Logs** go to `data/logs/`, one rotating file per program. Each holds everything the console shows, plus every request slower than a second with its time, and every failed request with its traceback.

## Data model

| Table | Kind | Notes |
|---|---|---|
| `photos` | file copy | `id` INTEGER PRIMARY KEY; `path` UNIQUE, compared without case; `document_id`; `mtime`, `size`; `keywords`, `captions`, `raw_metadata`, `taken_at`, `orientation` as read from the file; `indexed_at`, NULL until indexed. Every photo the apps have looked at has a row, because faces need one to point at (today the suggester saves faces for photos with no row). |
| `faces` | decision | `id`; `photo_id` → `photos.id`; `box`; `embedding`; `name`; `name_source`; `excluded`; `excluded_reason`. |
| `face_crops` | derived | `face_id` → `faces.id`; the JPEG. Kept out of `faces`, so reading faces never drags 6 KB crops along. |
| `photo_people` | derived | `photo_id`, `name`, `source` (keyword or face). Rebuilt for a photo by one function whenever its keywords, its faces or the tag tree change. Replaces the `photos.people` JSON column. |
| `tag_taxonomy` | decision | The tag tree. The database is its only home; the `*_taxonomy.json` files become an optional export. |
| `tag_embeddings` | derived | As today. |
| `embeddings` | derived | `photo_id`, model key, vector. Replaces the path-keyed `embedding_cache` and `photos.embedding`. |
| `suggestions` | derived | `photo_id`, tags, people, title, raw scores, model, created. Replaces the JSON cache file and the in-memory registry. |
| `generations` | infrastructure | `name`, `value` for photos, faces and taxonomy. Replaces `faces_generation` and `taxonomy_generation`. |
| `schema_version` | infrastructure | Migrations applied in order, from `tagpup.store.schema`. |
| `jobs` | infrastructure | id, kind, arguments, status, progress, message, created, finished. |
| `changes`, `change_rows` | infrastructure | The journal (phase 7.5): each bulk edit, and what it found and left of each row, one row per changed column. |
| `settings` | decision | The library's settings (phase 7.6): `key`, `value`, written only through the journal. |

Each change ships as a migration with a dry run and a backup, and `tools/doctor.py` checks the library's invariants before and after.

## Frontend

- `web/common/` holds ES modules shared by both pages:
  - `api.js`: library-aware URLs and JSON. It replaced the monkeypatched `fetch` and image `src`.
  - `paths.js`, `vocabulary.js` and `library.js` (the picker and the library the browser remembers). Only what both pages really share lives here: they have no unsaved-edit handling or status line in common.
  - `validate.js` (what may be set, by the rules `/api/rules` publishes), `dom.js` (elements built from text) and `dialog.js` (`dialogOpen()`, which the pages' shortcuts ask before they act).
- `web/tagpup/` and `web/tuner/` each have a `main.js` plus one module per feature: folder list, photo details, faces strip, Identify grid, tag tree. Each page keeps its state in one store object, not in 130 to 150 variables at the top of one closure.
- There is no build step: the browser loads native modules. Tests import the modules directly with `node --test`, and jsdom page loads are kept for whole flows.

## Testing

| What | How |
|---|---|
| core | Plain unit tests, milliseconds each. |
| store | A temporary library. |
| files, ml | Temporary files; few tests, slower. |
| services | One shared fixture, a temporary library with a photos folder, used by every service test (`tests/service_fixture.py`). |
| web | Flask's test client: no sockets, no ports, no sleeps. |
| pages | Modules directly; a few flows in jsdom. |
| performance | Baselines for the clicks that matter, recorded and re-run on demand in a sandbox (`tools/`). |

Target: the full check in under a minute.

## Where everything goes

| Today | Target |
|---|---|
| `scripts/paths.py` | `tagpup/core/paths.py` |
| `scripts/db.py` | `tagpup/store/db.py` |
| `scripts/exiftool_session.py` | `tagpup/files/exiftool_session.py` (not `exiftool.py`: it imports pyexiftool's `exiftool`, and a module of the same name reads as importing itself) |
| `scripts/identity.py` | `tagpup/files/identity.py` |
| `scripts/metadata.py` | `tagpup/files/metadata.py` (reading), `tagpup/core/vocabulary.py` (tags, people, captions), `tagpup/store/taxonomy.py` and `tagpup/store/faces.py` (the library's people, face names) |
| keyword writing in `tagpup_server.py` | `tagpup/files/keywords.py`, `tagpup/core/vocabulary.py` |
| `scripts/index.py` (`PhotoIndex`, `PathLocker`) | `tagpup/store/` (`schema`, `photos`, `faces`, `locks`), `tagpup/ml/vector_index.py`, `tagpup/services/search.py` (a library's index, loaded from the store), `tagpup/services/faces.py` (the faces detection finds, recorded) |
| `scripts/taxonomy.py` | `tagpup/store/taxonomy.py`, `tagpup/core/vocabulary.py` |
| `scripts/faces.py` | `tagpup/ml/faces.py` (detection and embeddings), `tagpup/services/identities.py` (resolving who is who across a library), `tagpup/core/clustering.py` (the rules) |
| `scripts/suggester.py` | `tagpup/core/suggesting.py` (the rules, the caption made from tags), `tagpup/services/suggester.py` (`TagSuggester`, given its models; what a run calls) |
| `scripts/embedder.py` | `tagpup/ml/clip.py` (the model); the embedding cache is `tagpup/store/embeddings.py`'s, read and written by `tagpup/services/search.py` (`PhotoEmbeddings`); squaring a picture is `tagpup/files/images.py`'s |
| `scripts/suggest_models.py` | `tagpup/runtime.py` |
| `scripts/writer.py` | `tagpup/services/tagging.py` |
| `scripts/tagpup_server.py`, `scripts/tuner_server.py` | `tagpup/web/` (routes), `tagpup/services/` |
| `scripts/localserver.py` | `tagpup/web/security.py` (Waitress does the rest) |
| `scripts/reloader.py` | `tagpup/dev/reloader.py` |
| `tagpup_cli.py` | `tagpup/cli.py`; the file at the root stays as a launcher |
| `tagpup_gui.py`, `tagtuner.py` | one launcher for the one server |
| `runner.py` | `tagpup/desktop/runner.py`, over services |
| maintenance scripts in `scripts/` | stay, each a thin entry point on one scaffold: dry run (a rehearsal), apply (one change of the journal), report |
| `measure_*`, `verify_workflow.py`, `generate_screenshots.py`, `prepare_test_environment.py` | `tools/`, sharing one sandbox module |
| `gui/`, `gui_tagpup/` | `web/tuner/`, `web/tagpup/`, `web/common/` |

A moved module leaves a short shim at its old name until nothing imports it, so each move is a small commit that changes no behaviour.

## Phases

Each phase ships on its own with the full check green. Nothing changes behaviour unless the phase says so.

### Phase 1: Foundations
- [x] This document, and a findings tracker in `findings.md`.
- [x] `.gitattributes` for line endings.
- [x] The `tagpup/` package, with the foundation modules moved into it: paths, db, the ExifTool session, identity. One list of shipped files for every guard, and the layer guard.
- [x] `tagpup.config`: one loader, honouring `TAGPUP_HOME`, used by all 26 places that read `config.ini`.
- [x] `tagpup.logs`: file logs, and slow-request logging for both servers.
- [x] `tagpup.core.library.Library`: one name for a library and the files that belong to it, and backups beside the library.
- [x] The installed-copy launcher: `scripts/install_app.py`. Opt-in until the owner switches to it.

Exit: the guard tests for config, database connections, ExifTool and layers pass; both servers log to files; the app can run from an installed copy.

### Phase 2: Services
- [x] Delete TagTuner's eight unused routes: its copies of rename, time shift, delete, open in Explorer, rotate, save metadata and bulk tags (the TagPup page calls its own server's), and `/api/faces/recluster`, which no page calls.
- The rules, bottom-up. A service cannot live in `tagpup/` while what it calls is still in `scripts/`, and `metadata.py`, `index.py` and `taxonomy.py` each mix layers (files, store, rules). So the pure rules move first, then file reading into `tagpup.files`, then the tables into `tagpup.store` (phase 3 work the services pull forward):
  - [x] When a photo was taken: `tagpup.core.dates` (seven copies became one).
  - [x] The tag vocabulary: `tagpup.core.vocabulary` (55 hand-written splits became one reading).
  - [x] Reading photo files: `tagpup.files.metadata`. `metadata.py` split by layer: what the fields mean (tags, people, captions) went to `tagpup.core.vocabulary`, and the library's people and face names to `tagpup.store.taxonomy` and `tagpup.store.faces`. `scripts/metadata.py` joins them for the old callers.
  - [x] Writing keyword and caption fields: `tagpup.files.keywords`. Which person a bare name means is still resolved in `tagpup_server.py` before the write, until saving becomes a service.
  - [x] The tables, as the services need them: `tagpup.store` (taxonomy, photos, faces, people). `TagTaxonomy` moved into `tagpup.store.taxonomy`, and `scripts/taxonomy.py` is a name for it. The rest of `scripts/index.py` moves with phase 3.
- [x] `Result`: `tagpup.core.result.Result`, shaped by the first service. It is in core rather than a layer of its own: a plain value that every layer passes along.
- One service per user action. Start with the ones both servers implement (rename, rotate, delete, save metadata, bulk tags, time shift, indexing), then tag-tree edits, face identification and suggestions.
  - [x] Rotate: `tagpup.services.photos.rotate`.
  - [x] Delete: `tagpup.services.photos.delete`.
  - [x] Time shift: `tagpup.services.photos.shift_date_taken`.
  - [x] Smart Rename: `tagpup.services.photos.smart_rename`. The route still orders the photos, by the Date Taken in its folder cache.
  - [x] Save metadata (the photo panel): `tagpup.services.tagging.save_photo`. A tag that may not be set comes back as the Result's `refused`, which the route answers with 400.
  - [x] Bulk tags, and Apply All on a folder's suggestions: `tagpup.services.tagging.change_tags` and `add_tags`, one write loop.
  - [x] The library picker -- listing, choosing and creating a library, in both servers and the runner: one set of rules in `tagpup.core.library`. Creating one is still `create_library` in `tagpup_server.py`, until the schema moves into the store.
  - [x] Replacing a tag on every photo carrying it: `tagpup.services.tagging.replace_tag`. TagPup's tag-tree rename and delete, and TagTuner's merge and person rename, which reached into TagPup's server for it.
  - [x] The small routes both servers served: a photo (`tagpup.services.photos.page_copy`, upright and cached in TagPup, as stored in TagTuner), a face's crop (`photos.face_crop`, now kept in its row through the write lock), the people list (`tagpup.services.people.names`) and the folder dialog (`localserver.ask_for_folder`).
  - [x] The tag tree's edits: `tagpup.services.tags` (the tree view, create, the face and hidden switches, what a delete touches, delete, rename), over `tagpup.store.taxonomy`, which now makes every new node (`add_path`), moves and deletes branches, repairs the tree through the lock, and seeds a library's tree.
  - [x] TagTuner's Rename, Merge and Retire of a tag, and Rename Person: `tags.merge` and `tags.rename_person`. They now share the tree's code for changing a tag everywhere, and reach the same places (findings.md, #38).
  - [x] Suggestions: `tagpup.jobs.suggestions`, the second job. It holds each library's runs per folder, the saved file and the only place it is named, starting and running a folder, retrying failed photos, and consensus before "completed". TagPup hands a run its folder scan and its suggester (`suggestion_work`), which stay in `scripts/` until the suggester and CLIP move (phase 3).
  - [x] Face identification's writes: `tagpup.services.faces` (name one face or many, unname, exclude, restore, automatch a photo or a folder, remove a folder). Each photo's people are kept by one function, `tagpup.store.photos.update_people`. Naming and excluding write through `tagpup.store.faces.accounted_write`, and return the fingerprints either side, so TagTuner takes their faces off its cached grids as it did. The grids themselves, and the Identify reads, are still TagTuner's.
  - [x] Adding folders. `tagpup.services.indexing.index_folder` runs the CLI's `index`, then `cluster-faces` when asked. The first job, `tagpup.jobs.indexing`, keeps one queue per library and indexes one folder at a time. The queue was TagTuner's; TagPup's index-start ran a thread per folder and now uses the same queue. Nothing about the queue is saved yet.
- Tests move down to the service level.

Exit: no action is implemented in two places, and migrated handlers only parse the request, call a service and reply.

### Phase 3: Store
On 2026-09-24, 210 SQL calls sat outside `tagpup.store`: 65 in `scripts/index.py`, 43 in TagTuner's server, 21 in `tagpup.services.faces`, 7 in TagPup's server, 7 in the desktop runner, and the rest in maintenance scripts, the embedder, the suggester and the writer. The schema was made in four places (findings.md, #48).
- [x] The schema: `tagpup.store.schema`, the only place a table, column, index or trigger is made, as versioned migrations recorded in `schema_version`. The first brings a library of any age to today's tables; `PhotoIndex.load`, TagTuner's start-up, the runner, the tag tree and each request that names a library call it instead of making their own.
- [x] Generations: one `generations` table for photos, faces and the tag tree, kept by triggers, and one cache helper, `tagpup.store.generations.Cache`. The Suggest index reloads by the photos generation (#52).
- [x] `PhotoIndex` split: its SQL into `tagpup.store` (`photos`, `faces`, `embeddings`, `taxonomy`), the vector index into `tagpup.ml.vector_index`. `scripts/index.py` keeps the class as a composition of the two, with no SQL; the embedder and the suggester read through the store too. Checked against the old class on a copy of `photo_index`: the same rows, faces and neighbours, loaded in the same time.
- [x] The services' SQL: `tagpup.services.faces` calls store functions. Unmatching many faces reads them in one query, not one per face.
- [x] TagTuner's reads: the Identify queue and grids, the person grid, counts, the excluded list, the tag list and the people list (#49, #50), as store functions. Every read route answered byte for byte as before on copies of both libraries.
- [x] TagPup's reads, the runner, the CLI, the embedder, the suggester and the writer.
- [x] The maintenance scripts: each calls store functions, or retires once a dry run shows it has nothing left to do on either library. Five retired *(owner, 2026-09-24)*: `canonicalize_paths`, `repair_bare_person_tags`, `tidy_exclusion_reasons`, `restore_face_names`, `cache_all_crops`.
- [x] Guard: no SQL outside `tagpup.store` (`tests/test_sql_single_owner.py`). It finds all 43 statements in TagTuner's server as it was.
- [x] `tools/doctor.py`: the library's invariants, read-only, with counts (`tagpup.store.checks`). The schema is current; every face points at a photo; no named face is excluded; face names are among their photo's people (#42); the tree has no orphans; rows whose file is gone, by folder.

Exit: the servers contain no SQL.

### Phase 4: Data model
Each step that changes a table is a migration in `tagpup.store.schema` that takes a backup, is tried first on copies of both libraries, and is checked by `tools/doctor.py` before and after. Every index entry whose file was gone was dropped on 2026-09-24 *(owner)*, so the doctor starts clean on both libraries. In the order the inventory set, each after what it needs:
- [x] The tag tree in the database only. `TagTaxonomy` takes the library, not a JSON file whose name decides which library (#61, #13); `tagpup_cli.py export-tree` writes a copy on request. The files beside the owner's libraries held nothing the tables lack, but two flat tags no photo carries, left from before they were given paths; they were not brought in.
- [x] Face roots from the tree only (#66). A root holds faces when its node says so; the lists of root names in the code, the page's included, went. A new library is seeded with one face root, People, and its last face root cannot be taken away *(owner)*.
- [x] `face_crops`: the crops out of `faces`, keyed by face id (migration 3). 1.1 GB of `photo_index`, so the rebuild of `faces` that follows moves 6 KB less per face. A trigger takes a crop with its face, whichever connection deletes it. Tried on copies of both libraries: every crop moved, the doctor clean, 42 s on `photo_index` with its backup. The column's space stays free in the file (2.8 GB to 4.0 GB) until one VACUUM at the end of the phase, 41 s, back to 3.0 GB.
- [x] Photo ids. `photos` rebuilt with `id INTEGER PRIMARY KEY` and `path` unique; `faces.photo_id` in place of `faces.photo_path`, and every join by id (joins by path were case-sensitive where lookups were not). A photo the apps see gets a row the first time -- Suggest's faces and embeddings for photos never indexed included (`photos.ensure_row`). A rename is one update of `photos.path`.
- [x] `embeddings`: one vector per photo and model, keyed by `photo_id` and all five model settings, with the stamp of what it was computed from. Replaces `photos.embedding` and `embedding_cache` (#62, #65).
- [x] `photo_people`: each photo's people, written only by `tagpup.store.people.rebuild`, from its keywords, its faces and the tree, by the one rule in `tagpup.core.vocabulary`. Replaces `photos.people` and the seven patches of it (#63); tree edits that change who is a person rebuild what they change.
- [x] `suggestions`: keyed by `photo_id`, with the model they were made with and when. Replaces the JSON cache files and their re-keying on rename; a deleted photo takes its suggestions (#64). Run status stays in memory until `jobs`.
- [x] Doctor rules for each: no crop without a face, no vector, people or suggestion without a photo, people as the rule gives them; photos without a vector for the configured model are counted.
- [x] `tagpup_cli.py compact`: the space the migrations left free given back, backed up first.

Exit: nothing in the database is keyed by path, and `doctor.py` is clean on both libraries.

### Phase 4.5: One owner for each rule
The sweep of 2026-09-24 found rules, lists and thresholds written in more than one place: nine disagreeing, the rest bound to (findings.md, #66-#75). Placed after the data model, which rewrites the code several of them live in (the people rule, the centroids, the suggestions), and before one server, which builds on them *(owner, 2026-09-24)*. Each gets one owner, a stated scenario, and a guard where the pages keep a copy.
- [x] The thresholds (#75): each decision one value, named and explained where it lives, measured on the owner's data first. Done: offering a face a name (0.70) and naming one unasked (0.80), in `tagpup.core.clustering`. Left: flagging a name as possibly wrong and clustering's unnaming (both against an average face, which measured worse than the best single face), clustering's radius, the page's bands, and the tag values -- shown at 0.6, written at 0.5 by the CLI (#70) -- measured by running the suggester on photos already tagged.
- [x] Date Taken read by the pages as the server reads it (#67): each record carries `taken`.
- [x] Names reserved for TagTuner's buckets refused as people's names, and the buckets named by the server (#68).
- [x] One way to compare a face with a person, used by clustering, the person grid and the suggester (#71): `tagpup.core.clustering.KnownFaces`, the closest face of the years around the photo, as measured.
- [x] What counts as a photo, once, in `tagpup.files.images` (#72).
- [x] Library names the URLs reserve refused (#73).
- [x] The agreeing copies (#74), each to one owner.
- [x] The tests that use the checkout's `data/` or `config.ini` in homes of their own (#14), so `tools/run_tests.py` spreads the whole suite across the cores. Done: `tests/own_home.py`; the lane is empty.

Exit: every rule the sweep found has one owner, and the full check runs across the cores.

### Phase 5: One server
- Flask and Waitress: both ports, one process, services behind thin routes.
- The two standard-library servers and their launchers retire.
- Web tests use the test client.
- No startup library (#100): a library is reached by its URL; the pages remember the last one in the browser and show the picker without one; the CLI requires `--db`; `default_db` goes, and nothing writes config.ini.

Exit: one server process, and no test opens a socket to check logic it could check in-process.

Done 2026-09-24. Suggest's models still live in `scripts/` and reach the runs through `scripts/suggest_models.py` (findings #112), which goes when they move into `tagpup.ml`.

### Phase 5.5: Models in the package, one composition root
Suggest's models and the resolution of who is who still live in `scripts/` (embedder,
faces, index, suggester: 2,150 lines) because each spans layers, and they reach the web
through a script that fills a module slot (findings #112). See "The layers, revisited".
- [x] `tagpup.core.per_library.PerLibrary`; `tagpup.web.state` imports it from there.
- [x] `tagpup.ml.clip`: CLIP from the settings it is given. It embeds an image or a text and nothing else; the embedding cache is `tagpup.store.embeddings`', read and written by the service that asks (`tagpup.services.search.PhotoEmbeddings`).
- [x] `tagpup.ml.faces`: detection and face embeddings, from the settings it is given.
- [x] `tagpup.services.search`: a library's vector index loaded from the store (`PhotoIndex.load`, `search`, `reload_if_changed`). `PathLocker` goes to `tagpup.store.locks`; PhotoIndex's face wrappers go, their callers using `tagpup.store.faces` and `tagpup.services.faces`.
- [x] `tagpup.services.identities`: resolving who is who across a library (`FaceProcessor.cluster_and_resolve_identities`) over `tagpup.core.clustering`'s rules.
- [x] `tagpup.services.suggester`: `TagSuggester`, given its models.
- [x] `tagpup.runtime`: the composition root. `tagpup_web.py`, `tagpup_cli.py` and `prepare_test_environment.py` build one (the measurement tools and `verify_workflow.py` start `tagpup_web.py`, which does); `tagpup.jobs.suggestions` is handed its models with each run's work (`work_for(library, photos, models)`); `scripts/suggest_models.py` goes.
- [x] Shims at the old names in `scripts/` while anything imports them; the tests move to the package names. Nothing that ships imports them; they stay for the tests that are not a rename away (the behaviour anchors among them).
- [x] Guards: `tests/test_layers.py` knows `runtime`; nothing but `tagpup.runtime` builds a model.

Exit: no module in `scripts/` holds a model, SQL or a rule; the package imports no script; the web apps and the CLI get every model from a `Runtime`.

### Phase 6: Pages
Each page is one `app.js` of about 5,000 lines: one closure holding 130 to 160 variables
and 90 to 130 functions, started on `DOMContentLoaded`. Thirteen helpers are written in
both (`pathKey`, `samePath`, `leafOf`, `samePerson`, the picker's library memory, the tag
and name checks), and the tests that hold them to one rule cut them out of each page's
source text. Every request reaches the right library only because `fetch` and
`HTMLImageElement.src` are monkeypatched to put the library in front of `/api/`.

How the pages are tested decides how they are split. jsdom cannot load ES modules; a
bundler is a build step, which this project does not have; and running the modules
under node with jsdom's globals would put their timers on node's clock, which closing a
page's window cannot stop -- the harness closes windows precisely so that polling ends.
So the harness loads a page's modules the way the browser orders them and evaluates them
in the page's window as one script, each module in a function of its own that is handed
its imports and returns its exports -- its own scope, as in a browser. It refuses an
import cycle, and a module using another's export without importing it. The shared modules in `web/common/` hold no DOM
state and are also imported directly by node tests.

- [x] The folders: `gui_tagpup/` becomes `web/tagpup/`, `gui/` becomes `web/tuner/`, and `web/common/` holds the shared modules. The factory serves a page's `index.html`, `style.css` and `*.js`, and `web/common/*.js` at `common/`, uncached and as `application/javascript`; `common` is a reserved library name. Each page loads `main.js` as a module, imported relative to the page (`./common/api.js`), so every request carries the library. What names the old folders follows: the snapshot, the installer, the tests, the documents.
- [x] The harness loads a page as modules (above); each `app.js` becomes `main.js`, unchanged inside, and all 528 page tests pass under it before anything else moves.
- [x] `web/common/api.js`: the library from the page's URL, `api.url(path)`, `api.json(path, options)` and `api.image(path)`, and `api.fetch(path, options)` for a caller that reads the Response's status. Every request goes through it, and the two monkeypatches go. `database-routing.test.mjs` holds the same behaviour through it.
- [x] The shared modules, each helper written once: `paths.js` (`pathKey`, `samePath`), `vocabulary.js` (`leafOf`, `rootOf`, `samePerson`, `photoAlreadyHas`, the tag, name and text checks), `library.js` (the picker and the library the browser remembers; what choosing another library asks first is `beforeLeaving`). The tests that cut helpers out of source text import these instead. `unsaved.js`, `dialogs.js` and `status.js` were not made: TagTuner has no unsaved edits and no status line, and the two pages' dialogs share no code.
- [x] Each page split by feature -- TagPup: folder list, photo details, tagging, suggestions, tag tree, rename and time shift; TagTuner: photos, faces strip, Identify grid, Review People, indexing and the folder picker -- with the page's state in one store object instead of a closure's variables.
- [x] Guards: no page module over about 1,000 lines; no function written in both pages' modules -- the same text; `selectPhoto` means something else in each page -- (it belongs in `web/common/`); the copies of server rules a page keeps (`tests/test_rules_have_one_owner.py`) are pinned in the module that holds them.

Exit: no page file over about 1,000 lines, and the two pages share every common helper.

### Phase 6.5: No shims
Every move left a shim at the old name in `scripts/`, so that old code kept working
(`import db` is `tagpup.store.db`). On 2026-09-25 they are still imported: `db` by 29
files, `paths` by 14, `taxonomy` by 10, `exiftool_session` by 7, `identity` and
`suggester` by 2, and the three compositions phase 5.5 kept for tests -- `index` by 7,
`faces` by 3, `embedder` by 1 (findings #139). Two modules are not shims but sit where
they should not: `writer.py`, the CLI's `write` command, and `metadata.py`, imported by 6
and 11. Phases 7 and 8 add features, not structure; they should find one name for
everything.
- [x] The re-export shims (`db`, `paths`, `taxonomy`, `exiftool_session`, `identity`): every importer uses the package name, and the shims go.
- [x] The compositions (`embedder`, `faces`, `index`, `suggester`): their tests use `tagpup.ml` and `tagpup.services`, handing in fakes as arguments -- the two behaviour anchors pass `faces=` rather than setting a module slot (findings #136) -- and the shims go.
- [x] `writer.py` into `tagpup.services.tagging` (`suggestion_writes`, `write_suggestions`), which the CLI's `write` calls; which of a suggestion's tags it writes is `tagpup.core.suggesting.written_tags`. `metadata.py` into the owners "Where everything goes" names: its callers read with `tagpup.files.metadata`, handing each batch the library's people from `tagpup.store.taxonomy`.
- [x] Guard: every module in `scripts/` is an entry point (it has a `__main__` block) or a helper the entry points share (`_root`, `code_snapshot`, and `reloader` until it is `tagpup/dev/reloader.py`), and nothing but a script's own tests imports one (`tests/test_scripts_are_entry_points.py`; two sandbox scripts share code until they move to `tools/`). CLAUDE.md stops describing shims.

Exit: `scripts/` holds entry points only, and `import db` fails.

### Phase 7: MCP
An MCP server, `tagpup.mcp`, so that Claude works with a library through the same services the apps use, rather than through a one-off script per question. Settling #42 in findings.md took four throwaway scripts to learn that its 35 names sat on 28 rows of a folder deleted on purpose. Its reads need the repositories, so it follows phase 3; its order among phases 4 to 6 does not matter.
- [x] An entry point beside web and cli, `python -m tagpup.mcp`, over stdio, in a process of its own; it never talks to a running app's port. There is no default library (#100): every tool names the library it asks about, a name in the home's data folder, and `libraries` lists them. The project's `.mcp.json` starts it for Claude Code sessions in this repository, through `node` and `tools/mcp_launcher.cjs`, which finds the checkout from its own path and the interpreter in its `.venv`, or the main checkout's from a worktree (#173).
- [x] Its reads are a service, `tagpup.services.inspect`, read-only (`db.readonly_uri`): the entry point may not import `store`. What a library holds; photos by folder, tag or person; a photo's row against its file; the faces in a photo; the consistency checks `tools/doctor.py` runs, one tool each or together; rows whose file is gone, by folder; and the query plan of a query (`EXPLAIN QUERY PLAN` only, a `SELECT` only, never run). The reads no store module had are `tagpup.store.inspection`; the folders a library's photos are in is a tool too, so that photos by folder can be asked without a path.
- [x] Each question a finding in findings.md needed a throwaway script to answer is one tool call; the tests name the finding each tool answers (#42 first: which names sit on rows of a folder whose files are gone).
- [x] Answers give counts and ids by default, and paths and names only when a tool is asked for them (`reveal=True`): the library is photographs of real people, many of them minors.
- [x] Write tools only by calling a service: the maintenance scripts' operations (re-reading rows from their files, merging duplicate person tags, removing duplicate faces) become services the scripts and the tools both call, on one scaffold -- a dry run unless told to apply, a backup before it applies, and the service's `Result` back. The scaffold is `tagpup.services.maintenance`; the operations are `tagpup.services.refresh_rows`, `person_tags` and `duplicate_faces`, and the tools `refresh_rows`, `merge_duplicate_person_tags` and `dedupe_faces`. The service takes the one backup; a tool takes none of its own.
- [x] Tests call every tool through an in-process MCP client session over a library in a home of its own; one test starts the stdio server as a process (`tagpup.core.processes`) and lists its tools.

Exit: every check in `tools/doctor.py`, and each question asked of the library while settling a finding, is one tool call. Every tool calls a service, and each has a test.

### Phase 7.5: A journal for every bulk edit and migration
Every explicit bulk operation copies the whole library first (`db.backup`: 1.4 GB for
photo_index, and five kept), and cannot be undone except by restoring that copy over
everything done since. A photo file written in a batch has no record at all of what it
held before. The owner asked for something lighter that keeps the durability
*(2026-09-25)*: each change recorded as what it found and what it left, applied only
where the rows are still what the plan saw, undoable on the same terms, and rehearsed
before it is applied.

What others do (researched 2026-09-25): SQLite's session extension records changesets
of old and new column values and inverts them, with conflict handling that is exactly
these preconditions -- but it is reachable from Python only through APSW, a second
SQLite library beside `tagpup.store.db`, so its semantics are copied, not the library.
Photo managers (darktable, digiKam, Lightroom, Immich) do not make a batch of file
writes atomic; they find disagreement between the database and a file on the next pass
and resolve it per file. dpkg's per-item states and recovery at start are the model
for files here.

- [x] **The journal** (a migration): `changes(id, operation, status, schema_version, created, applied, undone, summary)` -- status planned, applied, derived_pending, undone, failed, pruned -- and `change_rows(change_id, table_name, row_key, column_name, old, new)`, one row per changed column, the values stored as SQLite values (a BLOB as a BLOB). An inserted or deleted row records every column.
- [x] **Forward**, under the write lock in one transaction: read each row's current values, refuse the whole change if any differs from what the plan read, write, record, mark the change `derived_pending`, commit; then rebuild the derived data the change touched (the photos' people, generations) and mark it `applied`. A change left `derived_pending` by a crash is finished at start. An operation whose rows do not depend on each other (the refresh) marks its edits skippable: a row saved in the app during the run is left out and reported, and the rest are written; merging and deduplicating stay all or nothing.
- [x] **Undo**: the same with old and new swapped. Refused when any row is not what the change left, when a newer applied change touched the same rows (named in the refusal), or when the schema has moved on since the change was made.
- [x] **The schema's constraints, checked by the journal**: it writes with foreign keys off, so before any write, forward or undo, every foreign key of a written row must resolve (counting rows the same write puts back) and no UNIQUE constraint may break -- both read from the schema; the refusal names the row and the column. An IntegrityError SQLite raises all the same is a refusal, the transaction rolled back.
- [x] **The rehearsal**: a dry run applies the change and its undo inside a transaction that is rolled back, and says whether the undo restored every row exactly. Nothing is written; the real apply writes once.
- [x] **Keys never reused**: rows a journaled operation can delete (`faces`, `tag_taxonomy`, `photo_people`...) get keys SQLite never hands out again (AUTOINCREMENT), or an undo that re-inserts a deleted row could collide with -- or silently match -- a newer one. Cascades into journaled tables are either recorded or forbidden; a guard test holds it.
- [x] **The maintenance operations** (phase 7's scaffold) record a change instead of taking a backup; the MCP server and the CLI gain `history` and `undo` (a dry run by default). Merging photo_index's 98 duplicate person tags is the first journaled change *(owner, 2026-09-25)*.
- [x] **Retention**: how long a change stays undoable, and what pruning keeps (the summary stays; the values go; the change becomes `pruned`). 90 days (`journal.RETENTION_DAYS`), pruned after every apply and on request (`prune-journal`, the MCP tool `prune_journal`).
- [x] **Migrations**: each in one transaction, with the checks it names run before it commits. One that only adds needs no backup; one that changes data records its rows like any change; only one that destroys information takes a full backup, taken under the write lock. Recorded in `changes` as well. Done: each `schema.Migration` declares its kind -- `ADDITIVE`, `DATA` or `DESTRUCTIVE` -- with a one-line reason, the tables it touches and its checks (`RowsKept`, `RowsNotFewer`, `ForeignKeys` against the violations the library already had, `Integrity` as `quick_check` of each touched table, and its own, such as migration 3's `CropsMoved`); a failed check rolls it back and raises `schema.CheckFailed` naming the check. The runner adds the kind's own: an additive or data-changing one must drop nothing, and runs under a watch -- TEMP triggers on every table, what SQLite's session extension records -- so an additive one that changed a row that was there is refused, and a data-changing one's rows are recorded through `journal.record` (the record-without-apply path), undoable while no newer change touched them and no later migration has run; one that writes a table the journal does not key is refused and must be declared destructive. A destructive one takes one backup per run, under the process's lock and with the migration's `BEGIN IMMEDIATE` held, of a library with any row in it. Each run is a change, `migration N: name`, its summary the kind, the checks passed and the backup's name; one with no rows is recorded at the version before, so the journal refuses to undo it. Migrations that ran before the journal existed (a library below 9) are recorded when it does, and a data-changing one there is backed up instead; a library being made records nothing. Of 1-11: 1, 8, 9, 10, 11 additive; 7 data-changing; 2-6 destructive. A table is rebuilt only through `schema.rebuild_table`, SQLite's twelve steps, which also drops and remakes the triggers and views elsewhere naming the table (the rename at step 7 fails on them) and keeps its AUTOINCREMENT counter; migration 4, which rebuilt by hand before it, is left as it ran.
- [x] **Photo files** (the last stage): a bulk edit that writes files -- Add to all selected, Apply All, Shift Date Taken, Smart Rename, a person's rename -- records each file's fields before and after, commits that plan, marks a file `writing` before ExifTool runs and `done` in the same transaction that records the row (`record_tags_in_index`). At start, a file left `writing` is settled by what it holds: the before, redo it; the after, mark it done; neither, a conflict, reported and never overwritten. Undo rewrites a file only where it still holds what the edit wrote. Done: migration 11, `change_files(change_id, photo_id, path, new_path, fields_before, fields_after, state, note)` and `changes.owner`; the rows are `tagpup.store.file_journal`, the engine `tagpup.services.file_changes` (`write_fields`, `rename`, `settle`, `settle_once`, `rehearse_undo`, `undo`), the fields read and written through one writer, `tagpup.files.field_values`, and each row follows its file through `tagpup.store.photos.follow_fields` (record_tags' rule for every field) or `move_rows_in`. A file is read again just before its write, and one no longer holding what the plan read is a conflict; a file already holding what it is to hold is not in the change. Through it: Add to all selected and Apply All (`tagging._change_each`), every rewrite of a tag -- renaming, deleting or merging a node, a person's rename -- (`tagging.replace_tag`), Shift Date Taken (each date the photo holds moved by `core.dates.shifted`, ExifTool's relative shift and `tagpup/files/times.py` gone), Smart Rename (the renames and moves aside planned first, `names.aside_for`; a file known by its size and mtime under either name), and both scripts: `relink_renamed_photos` a change of rows on the maintenance scaffold (`tagpup.services.relink_photos`), `backfill_document_ids` a change of rows for the identities files hold and a change of files for the ones it mints (`tagpup.services.document_ids`); neither copies the library. Settled at start by `tagpup.runtime.library_settings` (it knows the ExifTool) and before each change of files; a change another live process owns is left to it. `history` lists each change's files by state; `undo` (CLI, MCP) rehearses by reading every file and puts back each still holding its after, naming by photo id each it refuses. Saving one photo and the CLI's `write` are not journaled yet (findings).
- [x] Tests that crash an operation between each of its steps and prove recovery; forward-then-undo restoring the touched tables exactly on rows shaped like the real ones; and rehearsals on copies of both real libraries. Done for the database stages (`tests/test_journal.py`, `tests/test_journal_keys_and_cascades.py`, `tests/test_journal_through_the_scripts.py`; rehearsed on copies of both libraries) and for migrations (`tests/test_migrations.py`: each kind's path, a failed check, the twelve steps, and a library at every older version, built from the migration list, brought to the latest; copies of both libraries migrated from 10 to 11 with every row unchanged); and for photo files (`tests/test_file_journal.py`, real JPEGs and the real ExifTool: forward and undo of a bulk add, a tag rewrite, a time shift and a Smart Rename with a file moved aside; a crash after the plan commits, after a file is marked writing, after it is written before its row, and mid-batch, each settled; a file changed outside between the plan and its write, and before settling, a conflict not overwritten; a change a live process owns left alone; both scripts applied and undone without a copy).

Exit: no bulk operation or data-changing migration takes a full copy of the library; each is recorded, rehearsed before it is applied and undoable after, and a crash at any step is settled at the next start.

### Phase 7.6: Settings in the library, and a gear on each page
The settings a library depends on live in `config.ini`, one file for the machine, which
nothing in the pages shows *(owner, 2026-09-25)*: the CLIP model its vectors were made
with, the face-detection thresholds, Suggest's candidate words, the rename format, and
where ExifTool is. A library opened on another machine, or with the file edited, is
read with settings it was not made with, and nothing says so.

- [x] **The gears.** TagPup's top bar has a gear holding the tag editor (moved from its own button) and a link to TagTuner on the same library; TagTuner's has the tag editor, the library's settings, and a link to TagPup on the same library. The tag editor is one module in `web/common/`, which both pages load, and its routes are served by both apps. The editor (`web/common/tag-editor.js`) builds its own markup, and the menu's behaviour is `web/common/gear.js`; their look is `web/common/*.css`, which the factory serves at `common/`. The tree's routes are one blueprint, `tagpup.web.taxonomy_routes`, which the factory registers for both apps, and `/api/apps` tells a page the other app's address for its library, from the ports the launcher gives both apps (`tagpup_web.PORTS`, or the ports it is told). Library settings is in TagTuner's gear, disabled until the settings below.
- [x] **One validator** *(owner, 2026-09-25)*: `tagpup.core.validation` registers every kind of input -- a tag, a person's name, caption text, a library's name (reserved names included), a rename grouping, a folder, a time shift, each setting's value (`setting <section>.<key>`: its type, range, choices) -- and the four `problem_with_*` checks moved into it; their callers ask it. The rules are data (patterns, forbidden text, byte lengths, ranges, choices, messages); the checks that apply them are a dozen, written in Python there and in JavaScript in `web/common/validate.js`. Every service checks what it is given before it writes and refuses with the rule's message: saving a photo (new tags, and a caption the file does not hold already), adding tags to many photos and Apply All, the CLI's write, Smart Rename, Shift Date Taken, the tree's create, rename, delete-and-move and merge, a person's rename, naming faces, a new library and indexing a folder. The server publishes the rules at `/api/rules` (`tagpup.web.rules_routes`, on both apps, versioned by their digest); `validate.js` fetches them once, and `tagProblem`, `nameProblem`, `textProblem`, TagPup's `groupingProblem` and the picker's name check ask it, keeping no copy. `tests/validation_cases.json` runs through both languages (`tests/test_validation.py`, `tests/frontend/validation.test.mjs`); the page tests are served `tests/validation_rules.json` as the rules, which the Python test holds to what the server publishes. Output: `web/common/dom.js` builds elements from text, the 25 places a page wrote markup build them now, and `tests/frontend/dom-output.test.mjs` fails a page module writing anything but a fixed string into `innerHTML`, `outerHTML` or `insertAdjacentHTML`. And while a dialog is open the pages' shortcuts do not act behind it: each page handler asks `dialogOpen()` (`web/common/dialog.js`), which the tag editor's own key guard gave way to.
- [x] **Settings in the library**: a `settings` table (a migration), each value recorded with the change that set it (phase 7.5), so a change is in the library's history and can be undone. A new library is stamped with the defaults; a library without settings is stamped once from `config.ini` when it is next opened, so nothing changes for a library in use. Then nothing reads `config.ini`, and it goes, with `config.example.ini` and its copy in `setup.bat`. Where the libraries are is not a setting: `data/` in `TAGPUP_HOME`. ExifTool is found where its installer puts it or on PATH, and a library may name another. Done: migration 10, `tagpup.store.settings` (reads) and `tagpup.services.settings` (`of`, `read`, `stamp`, `change`; journaled as `stamp settings with the defaults`, `stamp settings from config.ini`, `stamp settings from the library it replaced`, `change settings`). A new library is stamped when it is made (`tagpup.services.libraries.create`); any other is stamped the first time an entry point reads its settings (`tagpup.runtime.library_settings`); the read-only looks -- the MCP server's inspections, the doctor, the warm-up -- stamp nothing (`peek_settings`). A value config.ini holds that the validator refuses is stamped as its default and named in the change's summary; an ExifTool it names where the installer puts it is stamped empty, found on each machine. `CLI index --reset` stamps the new library with the old one's settings (`stamp settings from the library it replaced`). A stamp cannot be undone: it is the library's first settings (`tagpup.services.journal`). The CLI's read-only commands -- `stats`, `list-index`, `search`, `inspect` -- peek too. A locked setting is changed only when the caller names its group as acknowledged (`change(..., acknowledged=[group])`), whoever the caller is. The runtime keeps a model while a library it serves names it or a run holds it, and unloads it after; the warm-up loads one set, the startup library's or the one most libraries share; a run keeps the photo index it began with, closed when the last run holding it ends. `config.example.ini` and `setup.ps1`'s copy of it are gone; the installer neither copies nor expects a `config.ini`. The owner's own `config.ini` stays until both libraries have been stamped from it, and is unused after.
- [x] **The settings dialog** (TagTuner's gear), made from the settings' one declaration -- type, range, whether it is locked, its consequences, its info text -- which the validator reads too (`tagpup.core.validation.SETTINGS` holds the type and range today): each setting with an info button saying what it changes and when. Settings with consequences are locked -- shown, not editable -- and open only through Change..., which lists what changing it does and asks for each to be acknowledged before it is saved; the page reloads after:
  - the CLIP model (name, weights, framing, size): every photo's vector was made with the old one, so Suggest finds nothing until every folder is indexed again;
  - face detection (smallest face, confidence, thresholds): only photos indexed after the change; indexing a folder again applies it to that folder;
  - the ExifTool program: every read and write of a photo file goes through it.
  Suggest's candidate words and the rename format apply from the next run, and are not locked.

  Done: `locked`, `consequences`, `info`, `label`, `group` and `default` are in each declaration of `tagpup.core.validation.SETTINGS` (the consequences once per group, `SETTING_GROUPS`); `tagpup.web.settings_routes` (both apps) answers `GET /api/settings` with them and the library's values, and `POST /api/settings` changes them through `tagpup.services.settings.change`. The dialog is `web/common/settings-dialog.js`, built with `dom.js`, checked with `validate.js`, a `.modal` for `dialog.js`; tested in `tests/frontend/settings-dialog.test.mjs` and `tests/test_settings_routes.py`.
- [x] **Everything reads the library's settings**: the runtime keys its models by a library's model settings (two libraries on one model share it), the CLI and the MCP server read the library they are given, and the settings owner is `tagpup.services.settings` over `tagpup.store.settings`. `tests/test_config_single_owner.py` becomes: nothing reads `config.ini` but the one-time stamping. Done: `Runtime()` takes no settings; `clip(library)`, `faces(library)`, `model_key(library)`, `candidate_words(library)` and `exiftool(library)` read the library's, and each model is built once per set of settings (`tests/test_settings.py`). The web routes take ExifTool and the rename format from the request's library (`tagpup.web.state`), the CLI from `--db`, the MCP server from each tool's `library`, and the scripts from `--db`. `tagpup.config` is `home`, `data_dir`, `library_path`, `default_exiftool`, `exiftool_path(named)` and `config_ini`.

Exit: `config.ini` is gone; every setting a library depends on is in the library, shown in TagTuner's gear, changed only through a recorded, undoable change, with its consequences acknowledged.

### Phase 8: Sync
A job that keeps each library in step with its folders. Today a row changes only when an app writes the photo or someone indexes its folder again, so the library drifts: files added outside the apps are missing, a deleted folder leaves its rows and face work behind (#42 and #47 in findings.md), and a file edited elsewhere keeps a row describing what it used to hold. It needs photo ids (phase 4), which let a moved or renamed file keep its row, and its faces with it.
- **Recurring jobs** *(owner, 2026-09-25)*: `tagpup.jobs.recurring`, a registry where each recurring operation declares its name, its period (daily, weekly, monthly, every N hours), whether it runs per library, and the service it calls -- the snapshots, sync, pruning the journal, compacting. A job's runs are recorded in its library (a `job_runs` table: job, started, finished, outcome, what it changed), so any TagPup process knows what is due and the apps can show when each last ran. One runner runs what is due: a missed period runs once, not once per period missed, and a lock per job and library keeps two processes from running the same job at once. It runs inside whatever TagPup process is up -- the web server checks periodically, the CLI and the MCP server when they start. No Windows Task Scheduler.
- **Always on, from login** *(owner, 2026-09-25)*: one background process -- the web server and the recurring-jobs runner together -- started at login from a shortcut in the owner's Startup folder, hidden, so both apps answer and the jobs run whenever the owner is logged in. It refuses a second copy of itself (the server already answering), restarts after a crash through a small supervisor, and logs to `data/logs`; `TagPup.cmd` and `TagTuner.cmd` then open the browser on it. A Windows service was weighed and not chosen: it runs outside the login session, with no desktop, so the folder dialog, Open in Explorer and Open photo would do nothing, photos on network drives or under the profile may be out of its reach, and keeping a Python program alive as a service needs pywin32's service host or a wrapper. `scripts/startup.py install|uninstall` (a dry run unless `--apply`) makes or removes the shortcut, starts or stops the process, and says what it did; installing the app again repoints it at the new version. The models it keeps loaded (ViT-H-14 takes a few GB of GPU memory) are released after an idle period and loaded again by the next Suggest.
- `tagpup.jobs.sync`: for each indexed folder, compare what is on disk with the rows, by path, size and modified time. Content identity (the DocumentID) links a file that moved.
- What it finds, sorted: new files (indexed through the indexing job's queue), changed files (their rows re-read from the file, as `refresh_rows_from_files.py` does now), moved files (the row follows the file), and missing files.
- A missing file is reported, never removed on its own: a folder on an unplugged drive looks the same as a deleted one. Removing rows stays the owner's choice, and the report says which folders are wholly gone.
- It runs when a library opens and when asked, and may watch the indexed folders while an app runs. A scan that finds nothing costs one directory walk and no file reads.
- Each run reports what it changed, not what it looked at, and leaves a record the apps can show ("last in step: ...").
- **Snapshots of each library, as its backup** *(owner, 2026-09-25)*: three dailies, one weekly and one monthly, in `data/backups/<library>/daily|weekly|monthly`, apart from the one-off copies phase 7.5 mostly retires. A daily is taken when the newest is more than a day old; the weekly and the monthly are refreshed from that same copy when they are more than 7 or 30 days old, so the library is read once, not three times. Each is taken with the library held against writers (SQLite's backup restarts whenever another connection writes), written under a temporary name, checked (`PRAGMA quick_check`), and only then renamed into place; an old snapshot is removed only after its replacement has passed, so a failed night never leaves fewer good copies. A daily job in the recurring registry, so it is taken by whichever TagPup process is up, or by the background runner. A tool lists them (date, size, the journal's changes since) and restores one: a dry run by default, saying how many journaled changes since the snapshot would be lost, and snapshotting the current file first so a restore can itself be undone. Sync's report gives the disk the snapshots take (photo_index is about 1.4 GB, so about 7 GB for five).

Exit: after files are added, edited, moved or deleted outside the apps, one sync brings the rows back in step. `tools/doctor.py` finds nothing it would change, except missing files it has reported.

### Phase 9: Library views (planned for October 2026)
The owner's idea *(2026-09-25)*: TagPup shows the whole library, not only the folder it
has open -- by folder, by keyword, by person, by date -- as Windows Live Photo Gallery
did, from the database, with the editing TagPup gives a folder today. It follows phase 8:
a view of the rows is only as good as the rows are current.

The design, to be settled before it starts:
- **One grid, two kinds of source.** A source says which photos are shown: a folder on
  disk (today's view: it walks the folder and reads what is new or changed), or a
  library query -- a folder and its subfolders, a keyword and everything under it, a
  person, a year or month (`photos.taken`). The grid, the details panel, the selection
  and the bulk edits are the same components on photo ids; an edit does the same
  whichever source showed the photo. Two grids drifting apart is the failure to avoid.
- **The transition.** A navigator beside the grid: Folders (the library's tree, counts,
  each marked on disk or gone), Keywords (the tag tree with counts), People, Dates. A
  library folder opens its library view; where the disk holds files the library does
  not, a banner says so and offers to index them. "Show in library" goes from a disk
  view to the same folder's library view. The header always says which source is shown,
  and the URL names it, so Back and a bookmark work.
- **Staleness is shown, not hidden.** A library view checks the thumbnails on screen
  cheaply (size and modified time, no ExifTool) and marks a photo changed on disk or
  missing; a missing photo is shown and not editable.
- **Edits and sync.** Every edit writes the file and records its new size and modified
  time in the transaction that marks the file written (phase 7.5), so sync (phase 8)
  never takes our own edit for an outside one. A file changed outside while an edit is
  planned fails the edit's precondition: a conflict, reported for sync to settle per
  file, never overwritten. The views show sync's state ("last in step: ...").
- **What it needs underneath.** Keywords are JSON in `photos.tags`, so "everything under
  Trips/" reads every row: a derived `photo_tags(photo_id, tag)` table, indexed and
  rebuilt from `photos.tags` as `photo_people` is. Folder counts scan the same way
  (findings #168): a derived folders table, or an index-friendly path range. Browsing
  thousands of photos needs cached thumbnails (derived, keyed by photo and modified
  time) and a grid that renders only what is on screen -- the Identify Faces work showed
  what rebuilding tens of thousands of cards costs.

Open questions for the owner: sources beyond folder, keyword, person and date (ratings,
saved searches such as "Trips/ and a person, 2019"); bulk edits across folders from a
library view (they go through the same journaled writes, so they can be undone); which
of Photo Gallery's habits to keep (the date slider, the tag pane with counts, the info
pane).

### Phase 10: Family albums from many sources (idea, after phase 9)
The owner's idea *(2026-09-25)*: once the local folders, the views and their management
are right (phases 8 and 9), bring in photos from where the family keeps them --
OneDrive, Google Photos, Instagram, Facebook and the like -- and copy them into the
family's own photo folders, under one common root. An event's photos, taken by several
people and posted to several services, become one album the family holds itself: like a
shared Google album, except that the photos are ours, on our disks. First the owner and
their spouse on the home network; later the children, remote, adding their own photos to
family groups and albums.

Design questions, to be settled before it starts:
- **We hold the originals.** An import copies the file into the library's folders
  (where, and named how: the event's folder, Smart Rename's format) and records where it
  came from: service, account, the service's id for it, when it was taken and imported.
  The copy is then an ordinary photo -- indexed, tagged, in the views -- and the source
  is provenance, not a link to keep in step.
- **The same photo from two places is one photo.** A photo shared by two people, or
  posted and also in someone's camera roll, arrives more than once, often recompressed
  and without its metadata. Matching by content (a perceptual hash beside the CLIP
  vector), by Date Taken and by the service's id, with the duplicate offered for review
  rather than silently dropped. Services strip metadata on upload; what was stripped
  (Date Taken, place, people) is the import's to restore from what the service still
  says, or to leave for tagging.
- **How each source is reached.** Each service's access is its own and changes: some
  offer an API for the account's own photos (OneDrive through Microsoft Graph); some
  have narrowed theirs to what an app itself made or what the user picks each time
  (Google Photos' picker), or retired it (Instagram's personal-account API); most offer
  an export of everything (Google Takeout, Facebook's and Instagram's "download your
  information"). An importer per source, behind one interface, and an export archive as
  the fallback that always works. Checked against each service's terms when the phase
  starts, not from memory.
- **Albums and groups.** An album is a set of photos, possibly from many folders and
  sources (a library view of phase 9 whose source is "album"); a group is the people who
  may see and add to an album. Albums live in the library, journaled like any edit.
- **People beyond this PC.** At first the home network: the apps already serve pages;
  another person on the network needs a login of their own and what they may do
  (view, add, tag, delete). Remote family members need the server reachable from outside
  -- through a tunnel or a relay rather than an open port -- with accounts, sessions and
  upload limits: security work the phases before have not needed. Their uploads land in
  a holding area and join an album when accepted.
- **Space and sync.** Imports grow the disk; the snapshots (phase 8) grow with the
  library. Sync must tell an imported copy from an outside change.

## After the re-architecture

Behaviour changes queued behind the phases. They wait so that they land once, in the new code, rather than in both servers and again afterwards.

- [x] Remove Folder chooses from the library's indexed folders, not from the folders on disk (findings.md, #47). Done: `GET /api/folder/indexed` (`tagpup.services.photos.indexed_folders`); sync's report of missing folders (phase 8) is to read the same list. Each folder shows its photo count and whether it is still on disk, and one that is gone is the obvious one to pick. Removing takes the folder out of the library and never touches the files. The list comes from the same place as sync's report of missing folders (phase 8).

## Decisions

| Date | Decision |
|---|---|
| 2026-09-23 | The web layer moves to Flask and Waitress. |
| 2026-09-23 | One server process serves both apps, on the ports they use today. |
| 2026-09-23 | Photo ids arrive in phase 4, after the store layer exists. |
| 2026-09-23 | Face detection on sideways photos is tabled for a future discussion. |
| 2026-09-23 | PNG rotation is left as it is. |
| 2026-09-23 | `Library` lives in `core`: it only names files, and the web layer, which builds one per request, may not import `store`. `Result` moves to phase 2, so that its first real callers set its shape. |
| 2026-09-23 | `Result` lives in `core`, not in a layer of its own: it is a plain value every layer passes along, so it needs no place in the import rules. |
| 2026-09-24 | A service that reads returns what it read, not a `Result`, and raises `NotFound` (a route's 404) or `Refused` (400), which live beside `Result` in `tagpup.core.result`. |
| 2026-09-24 | Opening a photo, showing it in Explorer and the folder dialog stay web routes, not services: they act on the desktop of the machine the server runs on, and write nothing. |
| 2026-09-24 | The web layer is two Flask apps from one factory (`tagpup.web.app.create_app`), one per page, served by one Waitress process that hands each request to the app for the port it arrived on. The pages ask for the same paths (/api/people, /api/photo-file, eight in all) and mean different things by them; one app would ask which port at every such route. The sockets are bound by us with SO_EXCLUSIVEADDRUSE, since Waitress's own SO_REUSEADDR lets a second server bind a port a live one holds on Windows. |
| 2026-09-24 | One composition root, `tagpup.runtime`, and the layers kept as they are. Letting `ml` read `config`, merging the infrastructure layers, and ports-and-adapters throughout were weighed and rejected ("The layers, revisited"). A service that uses a model is given it. |
| 2026-09-24 | `PerLibrary` lives in `core`: a locked map keyed by library that web, jobs and the runtime all use. |
| 2026-09-25 | The page tests load a page's ES modules into jsdom as one script, in import order, each module in a function of its own handed its imports (first as one shared scope; two splits hit its false positives, so each module got its scope). jsdom cannot load modules, a bundler is a build step, and modules run under node would keep their timers on node's clock, which closing the page's window cannot stop. |
| 2026-09-25 | The pages move to `web/tagpup/` and `web/tuner/`, sharing `web/common/`, imported relative to the page so every request carries its library. |
| 2026-09-25 | The MCP server names a library in every tool call; it has no library of its own (#100). Its reads are a read-only service, `tagpup.services.inspect`, since an entry point may not import `store`; its writes are the maintenance scripts' operations, moved into services the scripts call too. |
| 2026-09-25 | Bulk edits and migrations are recorded in a journal in the library, per changed column, applied and undone only where the rows are what the change expects, and rehearsed by a dry run that applies and undoes inside a rolled-back transaction. SQLite's session extension was weighed and its semantics copied, not the library: it needs APSW, a second owner of the database beside `tagpup.store.db`. Full backups remain only for migrations that destroy information. |
| 2026-09-25 | A library's settings live in the library and are changed from TagTuner's gear, through the journal; `config.ini` is retired *(owner)*. The data folder is fixed (`TAGPUP_HOME/data`), not a setting. Settings with consequences are locked behind a Change... that asks for each consequence to be acknowledged. |
| 2026-09-25 | Each library keeps three daily, one weekly and one monthly snapshot, taken under the write lock, checked before an old one is removed, and restorable with the journal's changes since counted *(owner)*. |
| 2026-09-25 | Recurring operations (snapshots, sync, pruning the journal, compacting) are registered in `tagpup.jobs.recurring` with their periods and run by one runner inside any TagPup process; their runs are recorded in the library. No Windows Task Scheduler *(owner)*. |
| 2026-09-25 | The server and the jobs runner run as one process started at login from the Startup folder, installed and removed by `scripts/startup.py`; not a Windows service, which has no desktop for the folder dialog and Explorer and may not reach the owner's drives. Idle models are released *(owner asked for always-on; the login process is the recommendation)*. |
| 2026-09-25 | Input rules have one owner, `tagpup.core.validation`: services refuse on them, the pages check early from the rules the server publishes as data, and shared cases hold any JavaScript twin to the Python rule. Output is escaped by building elements from text; `innerHTML` with a value is refused by a guard *(owner)*. |

## Progress

| Phase | Status |
|---|---|
| 1. Foundations | done, 2026-09-23 (the installed copy is opt-in) |
| 2. Services | done, 2026-09-24 |
| 3. Store | done, 2026-09-24 |
| 4. Data model | done, 2026-09-24 |
| 4.5. One owner for each rule | done, 2026-09-24 |
| 5. One server | done, 2026-09-24 |
| 5.5. Models in the package, one composition root | done, 2026-09-24 (the shims it left for tests went in 6.5) |
| 6. Pages | done, 2026-09-25 |
| 6.5. No shims | done, 2026-09-25 |
| 7. MCP | done, 2026-09-25 |
| 7.5. A journal for every bulk edit and migration | done, 2026-09-25 (the single save and Smart Rename's PreservedFileName write are not journaled yet: #266) |
| 7.6. Settings in the library, and a gear on each page | done, 2026-09-25 |
| 8. Sync | not started |
| 9. Library views | planned for October 2026 *(owner, 2026-09-25)*; design questions open |
| 10. Family albums from many sources | idea *(owner, 2026-09-25)*, after phase 9; design questions open |

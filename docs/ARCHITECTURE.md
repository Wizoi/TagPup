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
| core | `tagpup.core` | Pure rules: path identity (and a root's row form, `paths.to_row` / `from_row`), a library's name and the files that belong to it (`Library`), the tag vocabulary (leaf, root, person), people derivation, suggestion scoring, clustering decisions; and `core.machine`, the one place the store asks for the machine's map of the roots | nothing but `core` |
| config | `tagpup.config` | `TAGPUP_HOME` and where the libraries are (its `data/`), which ExifTool the machine has, where this machine keeps each root (`machine_roots.json`, read, and written only when an adoption finds the root missing from it; it registers its reader with `tagpup.core.machine` when imported, and `core.machine` imports it itself when asked with none registered, so no entry point has to remember to), and the one reading of an old `config.ini`, for stamping a library that holds no settings yet (phase 7.6). A library's settings are its own (`tagpup.services.settings`). Never written by the app (#100) | `core` (to spell and check a root's locations) |
| logs | `tagpup.logs` | Each program's log file in `data/logs/`, each line carrying the runs under way (`tagpup.core.runs`), and reading them back, bounded, for the Activity page. Set up by entry points | `core`, `config` |
| supervisor | `tagpup.supervisor` | The always-on process (phase 8): runs the web server as its child, restarts it, moves it onto a newer installed version once drained, one per home; and the names it shares with the server (the token, `data/server.json`, the exit code for ports another holds), which `tagpup.web` reads | `core`, `config`, `logs` |
| store | `tagpup.store` | The library database: connections and locks, schema and migrations, generations, one repository per table, caches keyed by generation, backups | `core` |
| files | `tagpup.files` | The photo files: ExifTool sessions, reading metadata, writing keyword, caption and orientation fields, identities, opening images (upright or as stored), crops and thumbnails | `core` |
| ml | `tagpup.ml` | Models: CLIP embeddings, face detection and embeddings, the vector index | `core`, `files` |
| services | `tagpup.services` | One function per user action. The only code that writes. Returns a `Result` | all of the above |
| jobs | `tagpup.jobs` | Background work: queue, status, cancel, persistence, worker processes for GPU work | `core`, `services` |
| runtime | `tagpup.runtime` | The composition root: turns the settings an entry point read into the process's long-lived objects -- CLIP and the face models, the per-library job runners -- builds each once, warms the models on a thread, and hands them down as arguments | every layer above but the entry points |
| entry points | `tagpup.web`, `tagpup.cli`, `tagpup.mcp`, `scripts/`, `tools/` | HTTP, the command line, the MCP server Claude works through, maintenance and development tools. Each reads the settings, builds one `Runtime`, and passes it on | `config`, `logs`, `runtime`, `services`, `jobs` (and `core` for formatting); `tagpup.web` also `supervisor`, for the names they share |

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

## Runtime

- **One server, two apps.** One Flask app, served by Waitress, answers on both ports used today: 8090 for TagPup, 8080 for TagTuner. The library comes from the URL as it does now, and becomes a `Library` object for the request. The Host and Origin check is a `before_request` hook. It listens on this PC only -- 127.0.0.1 and ::1, a socket each (`tagpup.web.app.bind`) -- until phase 10 adds logins; `tagpup_web.py --listen lan` binds every interface, for then.
- **One composition root.** `tagpup.runtime.Runtime` holds what lives as long as the process: the models, built once from the settings it was given and warmed on a thread, and each library's job runners. The web factory and each CLI command are handed one; nothing below them builds a model or reads a setting. What it and the web keep only while used -- models, vectors, New Person's pool, the Identify Faces and folder-scan caches -- is let go after an idle period (`Runtime.idle`, phase 8).
- **Background jobs** (indexing, suggestions, clustering, refresh) run through one job runner per library, with status, cancel and persistence. GPU-heavy work runs in a worker process, as indexing does today through the CLI.
- **The installed copy.** `scripts/install_app.py` copies the code into a version folder under `%LOCALAPPDATA%\TagPup` and writes launchers that run it. `TAGPUP_HOME` names the folder that holds `data/`: the libraries, which hold their own settings, with each library's backups and locks beside them. Updating is a deliberate step, installing again, and the two versions before stay to go back to. The auto-reloader is for development only.
- **Always on** (phase 8). `scripts/startup.py install --apply` puts a shortcut in the owner's Startup folder to `TagPup Background.pyw`, a stable launcher beside the others that reads `current.txt`; it runs the supervisor (`tagpup.supervisor`) under `pythonw.exe`, which runs the web server as its child and moves it onto each newer committed version once the server has drained (`tagpup.web.lifecycle`).
- **Logs** go to `data/logs/`, one rotating file per program. Each holds everything the console shows, plus every request slower than a second with its time, and every failed request with its traceback.

## Data model

| Table | Kind | Notes |
|---|---|---|
| `photos` | file copy | `id` INTEGER PRIMARY KEY; `path` UNIQUE, compared without case, native in a library with no roots and `@root/relative` for a file under one of its roots (the same for every other column of paths: `change_files`, `added_folders`, `damaged_files`, the two folder settings, the paths inside `raw_metadata` and `suggestions.raw`); `document_id`; `mtime`, `size`; `keywords`, `captions`, `raw_metadata`, `taken_at`, `orientation` as read from the file; `indexed_at`, NULL until indexed. Every photo the apps have looked at has a row, because faces need one to point at (today the suggester saves faces for photos with no row). |
| `faces` | decision | `id`; `photo_id` → `photos.id`; `box`; `embedding`; `name`; `name_source`; `excluded`; `excluded_reason`. |
| `face_crops` | derived | `face_id` → `faces.id`; the JPEG. Kept out of `faces`, so reading faces never drags 6 KB crops along. |
| `photo_people` | derived | `photo_id`, `name`, `source` (keyword or face). Rebuilt for a photo by one function whenever its keywords, its faces or the tag tree change. Replaces the `photos.people` JSON column. |
| `photo_tags` | derived | `photo_id`, `tag_id` (the tag-tree node's id). Made by `tagpup.store.derived` from `photos.tags` and the tree, kept by every write of a photo's keywords and by the tree's edits; a keyword with no node has no row and makes none (migration 19). |
| `folders`, `photo_folder` | derived | The folder tree of the photos' paths (`folders`: `id`, `parent_id`, `path` in row form, `name`) and the folder each photo is directly in (migration 19). A folder has a row for each ancestor of a folder holding a photo, and goes with its last one. The adoption of a root rebuilds them. |
| `photo_meta` | derived | `photo_id`, `rating`, `make`, `model`, `width`, `height`, `latitude`, `longitude`, from `photos.raw_metadata` (migration 19); width and height are empty until the indexer reads the size. |
| `tag_taxonomy` | decision | The tag tree. The database is its only home; the `*_taxonomy.json` files become an optional export. |
| `tag_embeddings` | derived | As today. |
| `embeddings` | derived | `photo_id`, model key, vector. Replaces the path-keyed `embedding_cache` and `photos.embedding`. |
| `suggestions` | derived | `photo_id`, tags, people, title, raw scores, model, created. Replaces the JSON cache file and the in-memory registry. |
| `generations` | infrastructure | `name`, `value` for photos, faces and taxonomy. Replaces `faces_generation` and `taxonomy_generation`. |
| `schema_version` | infrastructure | Migrations applied in order, from `tagpup.store.schema`. |
| `jobs` | infrastructure | id, kind, arguments, status, progress, message, created, finished. |
| `changes`, `change_rows` | infrastructure | The journal (phase 7.5): each bulk edit, and what it found and left of each row, one row per changed column. |
| `settings` | decision | The library's settings (phase 7.6): `key`, `value`, written only through the journal. |
| `roots` | decision | The library's roots (migration 18; "Roots and machines"): `name`, the share's `address`, `added`. Empty until the owner runs `roots adopt`; opening a library only ever makes the empty table. Written by that one change only, in the transaction that converts the rows under the root. |

Each change ships as a migration with a dry run and a backup, and `tools/doctor.py` checks the library's invariants before and after. Migrations 1 to 19 are listed in `tagpup.store.schema.MIGRATIONS`, each with its kind (additive, data-changing or destructive) and why; 18, `roots`, is additive and empty, and converts nothing; 19, the derived tables of phase 9a, is additive and fills them from the photos' rows, which it checks before it commits.

## Frontend

- `web/common/` holds ES modules shared by both pages:
  - `api.js`: library-aware URLs and JSON. It replaced the monkeypatched `fetch` and image `src`.
  - `paths.js`, `vocabulary.js` and `library.js` (the picker and the library the browser remembers). Only what both pages really share lives here: they have no unsaved-edit handling or status line in common.
  - `validate.js` (what may be set, by the rules `/api/rules` publishes), `dom.js` (elements built from text) and `dialog.js` (`dialogOpen()`, which the pages' shortcuts ask before they act).
- `web/activity/` is the Activity page (phase 8.5): the background work of every library, at `/activity/` under no library, asking through `api.site`, `web/common/api.js`'s form for what covers every library.
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
- [x] Photo ids. `photos` rebuilt with `id INTEGER PRIMARY KEY` and `path` unique; `faces.photo_id` in place of `faces.photo_path`, and every join by id (joins by path were case-sensitive where lookups were not). A photo the apps see gets a row the first time -- Suggest's faces and embeddings for photos never indexed included (`photos.ensure_row`). A rename is one update of `photos.path`. *Amended 2026-09-28:* only in a folder the library holds, or one added to it -- a row makes its folder the library's, and Suggest in another library's folder made 25 rows in kr-track without asking; adding is asked for (`tagpup.services.libraries.add`, TagPup's "Add this folder to kr-track?"). *Amended 2026-10-02:* the prompt names only the open library -- no other library is opened, read or named -- and Just look is not read-only: what needs no row (a caption, tags and people, Smart Rename, rotate, delete, Date Taken, Time Shift) writes the photo's FILE and nothing of the library's, decided per photo at the time of the write by `libraries.split` (the library's own answer, never a flag the page sends) and done by `tagpup.services.file_only` -- no row, no journal change, nothing derived, no thumbnail, no damaged-photo record, nothing for sync or the watcher; the database is not changed (`tests/test_just_look_edits.py` dumps every table before and after). Suggest, Apply All, carry-forward and face naming need the database and stay refused (`409`) there. What a file-only write gives up: History's undo and the settling of a write a crash cut short (a Smart Rename killed between its two passes leaves `tmp_rename_` names beside the photos); when the folder is added, the indexer reads the files as edited.

  *Review fixes, 2026-10-02 (#520-#527).* **Delete** (held or not) sends only an existing regular photo FILE to the Recycle Bin (`recycle_bin.problem`: a folder, a `.txt`, a missing path, a path ending in a separator, the 8.3 spelling of a non-photo are each refused, 400, nothing moved; `send_to_recycle_bin` itself raises on them). The Bin is per local volume: a file on a network share (UNC, mapped network drive), a removable drive or a SUBST drive is deleted for good while `SHFileOperation` reports success -- checked by test on a UNC path to local storage (`\\localhost\C$\...`): success reported, file gone, nothing in `$Recycle.Bin`. `recycle_bin.no_bin_reason` says it beforehand and why, judging the path as spelled (a SUBST drive is found by `QueryDosDevice` returning `\??\` where a disk returns `\Device\Harddisk...`; the volume by `GetVolumePathName` so a volume mounted in a folder is judged by its mount point; a `\\?\` prefix is looked through) and again where its links lead (`realpath`: a junction or symlink to a share); only a fixed local disk with a real volume goes to the Bin, and when unsure it answers permanent. The folder's membership answers `permanent_delete` and `permanent_reason` ("on a network share" / "on a removable drive" / "on a substituted (SUBST) drive" / "on a drive without a Recycle Bin"), the page's confirmation says "This file is <reason>: it will be deleted permanently, not moved to the Recycle Bin" before, and the reply (`permanent`, `permanent_reason`, `message`) says what happened after. The file is judged by its long name (`GetLongPathNameW`): `A4413~1.JPG` may be `holiday.jpgold`. A Smart Rename that fails after its held part committed, whatever the exception, answers what did change; if the folder cannot then be read again the cache is dropped and the answer is a plain 500. **Held means rows:** `libraries.split` and the write checks hold a folder with a row directly in it whatever the library's ignored list says (the rows are the library's; the settings promise they stay in step); an ignored folder with no rows stays not held, and Suggest still leaves ignored folders out. **The file-only check is cheap, and at parity with the held path (#528):** a caption, tag, people, Date Taken, time shift or Smart Rename reads only the END of each file (`images.tail_check`) and refuses exactly two things -- an empty file, and a zero-filled tail by `images.zero_tail_of`'s own rule (the same last 64 KiB, the same threshold as the indexer and the held path; a test holds the two to a table of tails) -- plus ExifTool's own failure to read the file. It judges nothing about what follows the picture: a Samsung trailer, an MP4 appended to a motion photo, 0xFF or zero padding below 64 KiB and bytes after a PNG's IEND are all good photos, and refusing one is the worst thing a cheap check can do (an earlier version that required the end marker did). Only a rotate, which rewrites picture data, decodes the picture (`zero_tail_if_whole`). So a JPEG cut short in the middle is written by a caption edit as it would be by the held path; the indexer's full decode finds it later (0.1158 s against 0.0002 s on a 17 MB JPEG, measured 2026-10-02; the cost that mattered was a 500-photo Smart Rename on a share decoding every file before the first rename). **Smart Rename decides once:** `smart_rename` splits held from not held once and hands the split to `preserve_names`, so a folder added in between cannot send the names one way and the renames the other; a mixed rename whose second part fails answers what DID change (`updated_paths`, `updated_photos`) and the page shows the new names. **Recovery without a journal:** if a file-only Smart Rename stops part-way (the process killed between its two passes), run Smart Rename on the folder again -- it regenerates the same names; a file left named `tmp_rename_*` is a photo waiting for its new name (its earlier name is in its PreservedFileName); no sidecar plan is kept. **Accepted:** the watcher notices a file-only write under a root and sync records a `sync_runs` row (no photo row), and the page makes no tag-tree node while just looking, even for the held photos of a partly held folder (#526); Just look reverses the 2026-09-28 refusal of writes in a folder not held, for these operations only (#527, the owner).
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
- [x] **Undo**: the same with old and new swapped. Refused when any row is not what the change left, when a newer applied change touched the same rows (named in the refusal), or when a migration since the change was made could have changed what its rows mean (*owner, 2026-10-02*: an ADDITIVE migration that touches no table a change can name or the journal derives -- `journal.KEYS`, `journal.DERIVED` -- cannot, so it is exempt: migrations 11 to 18 are, 8 (photos), 10 (settings) and every data-changing or destructive one are not, and a migration that is not in the list is never exempt; the refusal names the one that blocks, `journal.schema_gap_blocker`).
- [x] **The schema's constraints, checked by the journal**: it writes with foreign keys off, so before any write, forward or undo, every foreign key of a written row must resolve (counting rows the same write puts back) and no UNIQUE constraint may break -- both read from the schema; the refusal names the row and the column. An IntegrityError SQLite raises all the same is a refusal, the transaction rolled back.
- [x] **The rehearsal**: a dry run applies the change and its undo inside a transaction that is rolled back, and says whether the undo restored every row exactly. Nothing is written; the real apply writes once.
- [x] **Keys never reused**: rows a journaled operation can delete (`faces`, `tag_taxonomy`, `photo_people`...) get keys SQLite never hands out again (AUTOINCREMENT), or an undo that re-inserts a deleted row could collide with -- or silently match -- a newer one. Cascades into journaled tables are either recorded or forbidden; a guard test holds it.
- [x] **The maintenance operations** (phase 7's scaffold) record a change instead of taking a backup; the MCP server and the CLI gain `history` and `undo` (a dry run by default). Merging photo_index's 98 duplicate person tags is the first journaled change *(owner, 2026-09-25)*.
- [x] **Retention**: how long a change stays undoable, and what pruning keeps (the summary stays; the values go; the change becomes `pruned`). 90 days (`journal.RETENTION_DAYS`), pruned after every apply and on request (`prune-journal`, the MCP tool `prune_journal`).
- [x] **Migrations**: each in one transaction, with the checks it names run before it commits. One that only adds needs no backup; one that changes data records its rows like any change; only one that destroys information takes a full backup, taken under the write lock. Recorded in `changes` as well. Done: each `schema.Migration` declares its kind -- `ADDITIVE`, `DATA` or `DESTRUCTIVE` -- with a one-line reason, the tables it touches and its checks (`RowsKept`, `RowsNotFewer`, `ForeignKeys` against the violations the library already had, `Integrity` as `quick_check` of each touched table, and its own, such as migration 3's `CropsMoved`); a failed check rolls it back and raises `schema.CheckFailed` naming the check. The runner adds the kind's own: an additive or data-changing one must drop nothing, and runs under a watch -- TEMP triggers on every table, what SQLite's session extension records -- so an additive one that changed a row that was there is refused, and a data-changing one's rows are recorded through `journal.record` (the record-without-apply path), undoable while no newer change touched them and no later migration has run; one that writes a table the journal does not key is refused and must be declared destructive. A destructive one takes one backup per run, under the process's lock and with the migration's `BEGIN IMMEDIATE` held, of a library with any row in it. Each run is a change, `migration N: name`, its summary the kind, the checks passed and the backup's name; one with no rows is recorded at the version before, so the journal refuses to undo it. Migrations that ran before the journal existed (a library below 9) are recorded when it does, and a data-changing one there is backed up instead; a library being made records nothing. Of 1-12: 1, 8, 9, 10, 11, 12 additive; 7 data-changing; 2-6 destructive. A table is rebuilt only through `schema.rebuild_table`, SQLite's twelve steps, which also drops and remakes the triggers and views elsewhere naming the table (the rename at step 7 fails on them) and keeps its AUTOINCREMENT counter; migration 4, which rebuilt by hand before it, is left as it ran.
- [x] **Photo files** (the last stage): a bulk edit that writes files -- Add to all selected, Apply All, Shift Date Taken, Smart Rename, a person's rename -- records each file's fields before and after, commits that plan, marks a file `writing` before ExifTool runs and `done` in the same transaction that records the row (`record_tags_in_index`). At start, a file left `writing` is settled by what it holds: the before, redo it; the after, mark it done; neither, a conflict, reported and never overwritten. Undo rewrites a file only where it still holds what the edit wrote. Done: migration 11, `change_files(change_id, photo_id, path, new_path, fields_before, fields_after, state, note)` and `changes.owner`; the rows are `tagpup.store.file_journal`, the engine `tagpup.services.file_changes` (`write_fields`, `rename`, `settle`, `settle_once`, `rehearse_undo`, `undo`), the fields read and written through one writer, `tagpup.files.field_values`, and each row follows its file through `tagpup.store.photos.follow_fields` (record_tags' rule for every field) or `move_rows_in`. A file is read again just before its write, and one no longer holding what the plan read is a conflict; a file already holding what it is to hold is not in the change. Through it: Add to all selected and Apply All (`tagging._change_each`), every rewrite of a tag -- renaming, deleting or merging a node, a person's rename -- (`tagging.replace_tag`), Shift Date Taken (each date the photo holds moved by `core.dates.shifted`, ExifTool's relative shift and `tagpup/files/times.py` gone), Smart Rename (the renames and moves aside planned first, `names.aside_for`; a file known by its size and mtime under either name), and both scripts: `relink_renamed_photos` a change of rows on the maintenance scaffold (`tagpup.services.relink_photos`), `backfill_document_ids` a change of rows for the identities files hold and a change of files for the ones it mints (`tagpup.services.document_ids`); neither copies the library. Settled at start by `tagpup.runtime.library_settings` (it knows the ExifTool) and before each change of files; a change another live process owns is left to it. `history` lists each change's files by state; `undo` (CLI, MCP, and the pages' gear: **History...**, `web/common/history-dialog.js` over `tagpup.web.history_routes`) rehearses by reading every file and puts back each still holding its after, naming by photo id each it refuses. Saving one photo (`save photo`), the CLI's `write` (`write suggestions`) and the name Smart Rename keeps of each photo (`smart rename: original names`) are journaled too (findings #266); the rename a saved caption makes of a Smart-Renamed photo is not.
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
- **Event-driven first** *(owner, 2026-09-26)*: work that follows from an event -- a file added or changed outside, a user's edit, a photo indexed -- is done right away or put on a queue for processing when the event happens, never left for a schedule to find: sync reacts to the roots' change notifications, thumbnails and derived tables are made when a photo is indexed or changes. A schedule is only for what no event announces: the snapshots (safety), the journal's retention, and a catch-up check in case an event was missed. A new recurring job says which of these it is.
- **Recurring jobs** *(owner, 2026-09-25)*: `tagpup.jobs.recurring`, a registry where each recurring operation declares its name, its period (daily, weekly, monthly, every N hours), why it is scheduled (`safety`, `retention` or `catch-up`: one without a reason is refused as it is registered), whether it runs per library, and the service it calls -- the snapshots, sync, pruning the journal, compacting. A job's runs are recorded in its library (a `job_runs` table: job, started, finished, outcome, what it changed), so any TagPup process knows what is due and the apps can show when each last ran. One runner runs what is due: a missed period runs once, not once per period missed, and a lock per job and library keeps two processes from running the same job at once. It runs in the web server alone -- the always-on process, which checks periodically; the CLI lists the jobs and runs one by hand (`jobs run`), and neither it nor the MCP server runs what is due as it starts *(owner, 2026-09-26)*. No Windows Task Scheduler.
- **Always on, from login** *(owner, 2026-09-25)*: one background process -- the web server and the recurring-jobs runner together -- started at login from a shortcut in the owner's Startup folder, hidden, so both apps answer and the jobs run whenever the owner is logged in. It refuses a second copy of itself (the server already answering), restarts after a crash through a small supervisor, and logs to `data/logs`; `TagPup.cmd` and `TagTuner.cmd` then open the browser on it. A Windows service was weighed and not chosen: it runs outside the login session, with no desktop, so the folder dialog, Open in Explorer and Open photo would do nothing, photos on network drives or under the profile may be out of its reach, and keeping a Python program alive as a service needs pywin32's service host or a wrapper. `scripts/startup.py install|uninstall` (a dry run unless `--apply`) makes or removes the shortcut, starts or stops the process, and says what it did; installing the app again repoints it at the new version. The models it keeps loaded (ViT-H-14 takes a few GB of GPU memory) are released after an idle period and loaded again by the next Suggest.
  Done (8d): the supervisor is `tagpup.supervisor`, run by `TagPup Background.pyw` (written by `install_app.py` beside the other launchers; it reads `current.txt`, so the shortcut never names a version and installing again needs no repointing) under `pythonw.exe`. It runs `tagpup_web.py` as its child with a hidden console, its output in `data/logs/tagpup_web.console.log`; holds a lock on `data/supervisor.lock`, so a second copy in the home logs and exits; restarts the server after 2, 5, 15, 30, then 60 s and gives up after five crashes in ten minutes, saying why in `data/logs/supervisor.log` and `data/supervisor.json`; and waits, not counting crashes, while another server holds the ports (the child exits 3). `scripts/startup.py install|uninstall|status` makes or removes the shortcut and `always-on.txt` in the installed app's folder, starts the process or asks it to stop (`data/supervisor.stop`: it drains its server first; `--force` ends it at once), and says what it did. `TagPup.cmd` and `TagTuner.cmd` pass `--installed`: where the owner chose always-on, they start the process if it is not running and open the page once it answers, never a server of their own. The web server runs what goes beside its requests from one registry, `tagpup.runtime.BACKGROUND` -- the recurring jobs, the release of idle caches and the folder watcher.
  What the process keeps only while it is used is in one idle registry, `tagpup.core.idle.IdleCaches` (`Runtime.idle`) *(owner, 2026-09-26: "can the in-memory cache be minimized when not in use?")*: each cache registers how to let it go and what holds it, and says when it is used; the background task `release idle caches` lets go of each unused for `--release-models-after` (30 minutes) and not held, and its next use builds it again. Registered: the models (held by a Suggest run), each library's photo index -- its vectors, 267 MB on photo_index, closed with its connection (held by a run) -- New Person's pool (185 MB), the rest of Identify Faces' caches (held while a grid is built) and TagPup's folder scans (held while a Suggest run is under way). The photo index keeps only the vectors, by photo id: a search reads its results' records from the library by id, `records()` every photo's for the CLI and identity resolution; it held every row's record as well (232 MB on photo_index). FAISS searches on the caller's thread alone (`omp_set_num_threads(1)`): each OpenMP thread of a flat search kept about 128 MB for the process's life, 2 GB on a 16-thread machine, and one thread searches as fast. Measured on a copy of photo_index: a library opened and searched held 947 MB for its index and 2.3 GB more after the first search; now 277 MB for the index and 190 MB for the pool while used, and they go after the idle period; made again in 0.8 s and 1.0 s.
- **Updating itself** *(owner, 2026-09-26)*: the launchers install a new commit when an app starts (`install_app.py --if-changed`), but an always-on process rarely starts. It notices when a newer committed version is available (the checkout's HEAD is not the installed one and holds no uncommitted code: the launchers' rule), installs it, and moves onto it at the next request: it stops taking new work, lets the requests and jobs already running finish (a Suggest run, an index, a journaled write), then restarts on the new version, which settles anything left unfinished as the journal already does at startup. What it did goes to `data/logs`, and the pages say which version answers. An update never interrupts a write.
  Done (8d): every ten minutes, with no move pending, the supervisor runs the checkout's `install_app.py --apply --if-changed` (the launchers' rule) and compares `current.txt` with the version its server runs, so an install made by hand is moved onto too. It then asks the server to drain (`POST /api/server/drain`, carrying the token it gave the server): refused at once while a Suggest run, an index or a recurring job is under way, and asked again every minute; else the server answers new requests 503 (`X-TagPup-Updating`, which `web/common/api.js` waits out and sends again), stops its background tasks, and lets the requests in flight finish, a journaled write among them, within two minutes -- or takes work again. Drained, the server is stopped and a supervisor started from the new version takes over (`--handed-over`) and starts it. No second version is installed while a move waits.
  Reviewed before merge (2026-09-26), for an owner who installs it unattended: `--if-changed` installs only a newer commit (the installed one an ancestor of HEAD, `git merge-base --is-ancestor`), never a checkout moved back; an install never removes a version a live supervisor or server runs from; a move waits for a quiet moment (no request for 2 minutes, the drain's `quiet`) until it has waited an hour; the new version's supervisor is started first and must say it is up (`supervisor.handover.json`) before the server is stopped, and take the lock after -- else the old one stays on its version and tries again after an hour; one drain at a time, within one deadline; a server alive but not answering `/api/server` three times in a row (a minute apart, 20 s each) is ended and counted as a crash; a stop drains for at most 30 minutes, then ends the server; a supervisor that fails ends its server, and one that starts finds and ends (drained, when its predecessor kept the token) a server left running. The pages ask again for an `/api/` image that failed while the server was away. The watcher syncs the parent of a folder deleted or moved out (Windows reports it as a file deleted), reads the folders a library holds again only when its photos change, and makes no catch-up at start for a library synced whole in the last hour. `GET /api/server` names the version answering, which the gear shows.
- **Moving or removing people by tag** *(owner, 2026-09-26)*: nothing new in this phase. TagTuner's Tags view already renames, merges or retires a tag across every photo, journaled (e.g. `People/<Name>` to `Friends/...`); browsing by keyword is phase 9's.
- `tagpup.jobs.sync`: for each indexed folder, compare what is on disk with the rows, by path, size and modified time. Content identity (the DocumentID) links a file that moved.
- What it finds, sorted: new files (indexed through the indexing job's queue), changed files (their rows re-read from the file, as `refresh_rows_from_files.py` does now), moved files (the row follows the file), and missing files.
- A missing file is reported, never removed on its own: a folder on an unplugged drive looks the same as a deleted one. Removing rows stays the owner's choice, and the report says which folders are wholly gone.
- **Watching, with the schedule as the safety net** *(owner, 2026-09-26)*: the always-on process watches each library's root folders for changes (Windows' directory-change notifications, through the `watchdog` package, one recursive watch per root) and syncs just the folder that changed, a few seconds after the changes stop (a copy of 500 photos is one sync, not 500). The app's own writes are not taken for outside ones: the journal records the stamp each write leaves, so a notification for a file that already describes its row changes nothing. Notifications can be missed -- the process was not running, a network drive, the buffer overflowed, a drive was plugged back in -- so a whole-library sync also runs when the process starts and once a day, as a check in case something was missed; a scan that finds nothing costs one directory walk (about 1.4 s on photo_index) and no file reads. It also runs when asked.
  Done (8d): `tagpup.jobs.watching.Watcher`, the background task `folder watcher` (`tagpup.runtime.BACKGROUND`), in the process that runs the recurring jobs -- never one a test started unless it asks, and not with `TAGPUP_NO_JOBS`. It watches each library's root folders and the folders it holds photos in outside them, each the topmost of those under it (`tagpup.services.sync.watch_folders`; none for a library behind this version's schema), one recursive watch per folder through `watchdog` (6.x), a folder two libraries share watched once. A notification is only noted -- the photo's folder (`images.is_photo`; any other file is dropped; a folder deleted or moved, its parent), per library -- and the watcher's own thread syncs a folder three seconds after its last notification (`runtime.sync(library, folder=..., apply=True)`), a folder whose parent also waits left to the parent's sync. The whole library is synced when the watcher starts, when Windows says the buffer overflowed (its emitter is told of a read of nothing), when a watch fails, and when a folder that was not there is back; libraries, folders and absent folders are looked at again every 30 s. A library whose folders need more than 64 watches (no root folders, photos in thousands of folders) is not watched and says so; the daily job keeps it in step. `busy()` while a sync runs, so an update waits for it; stop() waits for it. TagPup's own write is notified and synced, and the sync finds the row describing its file: one walk, no file read (tests/test_folder_watcher.py).
- Each run reports what it changed, not what it looked at, and leaves a record the apps can show ("last in step: ...").
- Done (8c): `tagpup.services.sync` (`sync`, `look`, `last`), on the maintenance scaffold -- a service, not `tagpup.jobs.sync`, since it is given its ExifTool and its queue; `tagpup.runtime.sync(library, folder=None, apply=False, index_new=True)` fills both in (the library's ExifTool; this process's index queue, `tagpup.jobs.indexing`, running the CLI's `index`), and is what the recurring job calls: `sync`, daily, a catch-up (`tagpup.jobs.recurring`, through `Runtime.sync` of the Runtime the entry point gives the runner). The walk is one `os.scandir` pass per topmost folder the library holds photos in (`tagpup.files.images.stamps_under`), against `store.photos.stamps`; changed rows are re-read with `refresh_rows.reread`/`edits_for`, moved rows matched first by name, size and modified time (`sync.pair_by_stamp`, one-to-one only: most rows hold no DocumentID; a match that is not one-to-one is reported as ambiguous and its files' folders are not queued), then with `relink_photos.claims_of`/`pair` for what that left (DocumentIDs read only from those new files, and only when a row is missing) and written with `relink_photos.edits_for`, both as one change, `sync`. Missing files are counted by folder, `folders_gone` and `roots_gone` (a whole walk root not there: an unplugged drive), and never removed. The record is `sync_runs` (migration 14, `tagpup.store.sync_runs`). Entry points: the CLI's `sync [--folder] [--apply]` (waits for the indexing it queued), the MCP tools `sync` (counts the new files, queues nothing) and `sync_state`, and `GET`/`POST /api/sync` on both apps (`tagpup.web.sync_routes`). Not yet: running when a library opens, and watching the folders.
- **Library roots, and folders to review** *(owner, 2026-09-26)*. Done: each library has root folders and ignored folders, two settings (`library.roots`, `library.ignored`; the validator's `folders`: full paths, one a line, none a whole drive), in TagTuner's Library settings. A library has no roots until the owner sets them (`settings set library.roots`, an ordinary journaled change, which can be undone): nothing sets them on its own *(owner, 2026-09-26: kr-track's automatic root would have been its drive's training folder, and its stamp, which could not be undone, would have ignored 57 folders, 81,148 photos, had the server started before the owner set them)*. With no roots, sync keeps the held folders in step as indexing walked them -- new files and new subfolders under a held folder are queued, each folder of new files indexed on its own -- and nothing is reviewed. When a library's roots are set, every folder under a new root that holds photos and no indexed photo at that moment is added to the ignored folders in the same journaled change (`settings.excluded_under`; owner, 2026-09-26: "any folders not added assume excluded"): only a folder that appears later is offered for review. Sync walks the roots and every folder the library holds photos in, each once from the topmost. New files in a folder the library holds are queued with that folder alone (`index --no-subfolders`), every such folder of one sync as one job of the index queue and one run of the indexer (`IndexQueue.start(..., together=True)`); a folder under a root that holds photos and no indexed photo, not under an ignored folder, is not indexed on its own: it is a folder to review (`sync.review`, `GET /api/sync/review`), listed as the topmost such folder below a root with its photo count, and TagTuner offers each with Include (indexed with its subfolders, `sync.include`) or Ignore (added to the ignored folders, a journaled change of the settings) -- a dialog from the gear, Folders to review, and a notice with their number. Only a folder under one of the roots is offered: a folder the library holds outside every root (indexed by hand elsewhere) is kept in step like any held folder, and what is new beside it is counted (`outside_roots_files`), not offered. The CLI sets a library's roots: `settings set library.roots "<folder>" [--apply]`. A folder-limited sync (the watcher's, 8d) also keeps the row of a file moved in from another folder, looking only at the rows with its name and size.
- **Snapshots of each library, as its backup** *(owner, 2026-09-25)*: three dailies, one weekly and one monthly, in `data/backups/<library>/daily|weekly|monthly`, apart from the one-off copies phase 7.5 mostly retires. A daily is taken when the newest is more than a day old; the weekly and the monthly are refreshed from that same copy when they are more than 7 or 30 days old, so the library is read once, not three times. Each is taken in one step of SQLite's backup API, one read transaction, so it is the library as it stood when the step began while writers go on (a backup in several steps restarts whenever another connection writes; holding the writers back only stalled the app's saves for the copy), written under a temporary name, checked (`PRAGMA quick_check`), and only then renamed into place; an old snapshot is removed only after its replacement has passed, so a failed night never leaves fewer good copies. A daily job in the recurring registry, so it is taken by whichever TagPup process is up, or by the background runner. A tool lists them (date, size, the journal's changes since) and restores one: a dry run by default, saying how many journaled changes since the snapshot would be lost, and snapshotting the current file first so a restore can itself be undone. Sync's report gives the disk the snapshots take (photo_index is about 1.4 GB, so about 7 GB for five).

Exit: after files are added, edited, moved or deleted outside the apps, one sync brings the rows back in step. `tools/doctor.py` finds nothing it would change, except missing files it has reported.

### Phase 8.5: Activity
One page for the background work of every library *(owner, 2026-09-26)*: what runs, what
ran, what failed, and the logs -- modelled on Immich's Jobs, Jellyfin's Scheduled Tasks and
Logs, Hangfire's and Sidekiq's history (failures kept in view, run now) and Home Assistant's
logs (by source, filtered, raw, downloaded). Done:
- **The page**, `web/activity/`, served by both apps at `/activity/`, under no library, and
  to this PC only (`tagpup.web.activity_routes` refuses any other address, 403, beside the
  loopback bind). Linked from both gears (**Activity...**, a new tab). Sections: Now (asked
  every 3 s while the page is in view, never while hidden, a poll waiting for the one
  before), Scheduled jobs (each run's counts, duration, error and run; a failure flagged
  until a later success; Run now, asked first), Sync & watcher, Snapshots, Always on,
  Recent activity (one timeline, More reads further back), Logs.
- **Runs in the logs**: a recurring job's run, a sync and a run of the indexer hold a tag
  while they run (`tagpup.core.runs`), and every line of a program's log carries the tags
  of the runs under way on its thread (`tagpup.logs.RunTag`); "Logs for this run" filters
  by it. The indexer the queue starts writes a log of its run's own,
  `data/logs/indexer-<library>-<run>.log` (two processes may index one library at once, and
  one file rotated by two fails on Windows), quietly -- its stderr is the progress bar --
  told its run's tags in its environment; the oldest beyond 30 go as a run starts.
- **Logs read bounded**: `tagpup.logs.read` reads a log from its end, never more than 2 MB,
  paged back by offset and followed by offset (a file smaller than the offset has rotated);
  `tail` gives the raw end (1 MB); the download streams the file. Every log rotates (5 MB,
  five kept) through `tagpup.logs.to_file`; the server's console output is rotated as it
  starts; `tests/test_logs_are_bounded.py` fails a file handler made elsewhere and a file
  appended to for ever.
- **What it reads**: each library's `job_runs`, `sync_runs` and journal
  (`tagpup.services.activity`), the jobs layer's state in this process (the index queues'
  runs, Suggest's runs, the runner's runs, the watcher's roots and last notice), and
  `data/supervisor.json` without its token. Its reads do not count as somebody using the
  app, so an open page does not hold an update back from its quiet moment.

### Roots and machines (design *(2026-10-02)*; root-relative stored path approved by the owner, 2026-10-02; stages 1 to 4 built)
The owner's model *(2026-10-02)*: the library is rooted at the share `\\idziserver\Pictures`
(`D:\ServerFolders\Pictures` on the server). Everything under it is the family's catalog and
part of the backups; what TagPup **indexes** is a subset of folders under it (the library's
roots, less its ignored folders). Today photo_index indexes a desktop copy of the share's
`Pictures\` subfolder (`D:\Training\Pictures`), kept there so the vectors run on the desktop's
graphics card. The master is the share, not the copy. Later the indexing may move to the server
(not now): a server component and a client-only TagPup. The server has no GPU and 8 GB today;
the design assumes a GPU and more memory later and does not wait for them.
- **A folder outside every root** can be opened and tagged (the disk-folder source) and uses the
  database, but cannot be added to the index; adding says it is outside the roots and where to
  move it. The owner tags, verifies, then copies the folder under a root by hand; sync lists it
  for Include / Ignore.
- **The problem.** Rows hold absolute paths in this machine's spelling: `photos.path`,
  `change_files.path` and `new_path`, `added_folders.path`, `damaged_files.path`, and the
  settings `library.roots` and `library.ignored`. Moving the library, or running it from the
  server, re-spells every one at once; re-pointing rows once made 233 duplicate faces.
- **Proposed: a stored path is a root's name and a path under it**, so the library says
  "`pictures`, `Pictures\2024\...`" and not where the machine keeps it.
  - The library holds its roots (name and the share's own address). Each machine holds where it
    keeps each root, in `TAGPUP_HOME` beside the libraries (`tagpup.config`'s concern), not in
    the library, which moves between machines. Longest match wins, so the desktop maps
    `\\idziserver\Pictures\Pictures` to `D:\Training\Pictures` and the server maps
    `\\idziserver\Pictures` to `D:\ServerFolders\Pictures`.
  - **Native in memory, root-relative in the database** *(refined 2026-10-02, after the audit of
    about 150 places that open, walk, rename or delete a file by a stored path)*. Everything above
    the store -- the walks, ExifTool, the watcher, sync, renames, the browser, the lock files --
    keeps handling this machine's native path, as it does today, and does not change. Only the
    boundary with the database converts: `tagpup.core.paths` gains `to_row()` (native to a root's
    name and the path under it) and `from_row()` (back), `sql_equals` / `sql_under` / `sql_in`
    convert their argument and still answer a range on the same indexed column, and the store's
    reads and writes of a path column call the two. A photo under no root (a tag-only folder, which
    can have rows) keeps its native path, with no mark (decided 2026-10-02): a native absolute path
    starts with a drive letter, a separator or `/`, never `@`, so the two forms cannot be confused.
  - **The row form, as built in stage 1** (`tagpup.core.paths`, tested by `tests/test_paths_roots.py`):
    `@name` is the root itself, `@name/a/b.jpg` a path under it, with `/` on every machine and the
    case the file has; a root's name is `[a-z0-9_-]`, 1 to 32 characters, lower case. The name ends
    at the first `/`, so `@photos/` is never a prefix of `@photos2/`, and `sql_under` is still one
    range on the NOCASE index (the prefix, and the prefix with its `/` raised by one); a folder
    above a root's location adds the root's own range to an OR. `from_row` refuses a part that is
    empty, `.`, `..`, holds a colon or a backslash, or an empty part after `@name/`, and matches the
    root's name in any case.
  - **The machine's map** is `TAGPUP_HOME/machine_roots.json`, owned by `tagpup.config`:
    `{"version": 1, "roots": {"pictures": ["D:\\Training\\Pictures"]}}`, UTF-8, the first location
    being where a path is put and all being recognised. Absent means nothing is mapped; malformed
    is an error naming the file, never an identity. Refused at load: a location that is not
    absolute or starts `\\?\` or `\\.\` (the long-path and device prefixes), one place listed twice or under two roots, a root's own places
    (locations and share address) nested in one another, a share address equal to another root's
    location. A UNC share root with or without its trailing separator is one place. Nesting across
    different roots is allowed and the deeper wins. The map is re-read only when its (mtime, size)
    changes; an operation builds one `Roots` and passes it down.
  - **A root with no location on this machine is refused both ways** (`UnmappedRoot`): `from_row`
    of its rows, and `to_row` and every `sql_*` of a path under its share address, so a row is never
    written that cannot be read. So is a path under no root when such a root exists, since it might
    be its. The message names `machine_roots.json`, its path, the root and the line to add. A
    library with roots opened on a machine with no map therefore refuses; it does not behave as
    if nothing were rooted.
  - What the audit adds to the change: the paths inside JSON (`photos.raw_metadata`'s `SourceFile`,
    `suggestions.raw`), the journal (`change_files`, `change_rows` values of `photos.path` and the
    roots settings, which an undo replays), the six raw-SQL comparisons that bypass `key()`
    (`store/photos.py` 163 and 771, `store/faces.py` 589, `store/inspection.py` 116,
    `store/file_journal.py` 341), and the two-copy files beside the library (the suggestions JSON).
    The journal holding root-relative paths makes an undo portable between machines.
  - One migration, with a dry run, a backup and a doctor check: each table's prefix is rewritten
    in one transaction, a row under no root is reported and not guessed at, and the journal keeps
    what it was. Moving a library afterwards is a change to the machine's map, not to rows.
  - Cheaper alternative, not preferred: keep absolute paths and add a journaled "relocate root"
    that rewrites a prefix (Lightroom's "update folder location"). It costs less now, but a
    server and a client cannot then share one library, and every move is another bulk rewrite.
- **Stage 2, built** *(2026-10-02: the library holds its roots, the store converts, an explicit command adopts)*:
  - **Nothing converts a library unasked** *(owner)*. Migration 18 (additive) makes the empty `roots` table and
    nothing else; a library with no roots has `core.machine.IDENTITY` for its Roots, which never asks the map, and
    every store function is then what it was, byte for byte. The explicit command is the one step that converts.
  - **`tagpup.store.roots`**: the library's roots (`listing`, `every`, `insert`, `delete`), and `roots_for(conn)`,
    the `paths.Roots` a connection converts its paths by, built from the table and the machine's map
    (`core.machine`, answered by `config.roots_of`). It is kept on the connection (`db.Connection.roots_state`;
    `db.connect` makes them), so an operation that holds a connection converts every path of it by one Roots and
    asks neither the table nor the map a row: re-read only when `PRAGMA data_version` says another connection
    changed the library (2.6 microseconds, outside a transaction; inside one, nothing), and the map looked at again once a second at most (a
    stat; the same Roots back when the file is as it was), so an idle connection finds an edited map. `pinned(db_path)` holds one Roots
    for every connection of a library in this process for a whole run -- a map edited meanwhile changes
    nothing until the run ends, and a change of the library's own roots by another process stops it
    (`RootsChanged`); the adoption, the journal and every `db.write_with_connection` hold one Roots for their whole
    length by holding one connection. A write prepared before another process adopted the library is refused
    at its commit and run again (`write_with_connection` retries it, `roots.unchanged`); a write on a connection the
    caller commits (the index's `record_indexed`, `remove`) begins its transaction first (`roots.begin_write`), so the
    roots are the library's at the moment the write lock is taken. The operations that pin -- an index run, a sync pass, a file
    change -- are pinned in `services` (stage 3, below); one that is not holds the Roots of its connection, which keeps
    a single operation consistent.
  - **The boundary.** Every read of a path column returns the native path and every write stores `to_row` (idempotent:
    a path already in row form is written as it is). `store.roots.sql_equals` / `sql_under` / `sql_in` convert their
    argument by the connection's Roots and answer the same ranges on the same NOCASE indexes (checked with
    `EXPLAIN QUERY PLAN` on a library of 68,466 photos: `SEARCH`, never `SCAN`, for a photo by its path, a folder, a
    folder's own photos, a photo's faces); `tests/test_roots_store.py` fails a store module that calls `paths.sql_*`
    without the connection's Roots. The six comparisons that bypassed it (`photos.row_as_recorded`,
    `faces.counts_on`, `inspection.ids_of_stored`, the lookups in `store/photos.py`, `faces_pending` and
    `file_journal`) convert their argument. Rows read in a loop are converted after they are read, never a function of
    the column in a WHERE. **Order**: an `ORDER BY path` over rooted rows sorts `@pictures/...` among native paths, so
    what the owner sees listed in path order (`damaged_files.every` / `under`, `inspection.ids_and_paths`,
    `whose_file_is_gone`) is sorted again after the paths are native, in the order the NOCASE index gave
    (`roots.ordered`); every other `ORDER BY` is by id or time.
  - **Caches of native paths.** The Identify Faces grids, the folders the watcher watches and the index hold
    paths of this machine, built while a generation of photos or faces stood; moving a root in the map moves no row,
    so `store.generations` adds a salt -- the library's roots and where this machine keeps them, 0 for a library with
    none -- to the generations of photos and faces, and every cache keyed by them is built again when the map moves.
  - **The paths inside JSON.** `photos.raw_metadata`'s `SourceFile` is ExifTool's spelling of the path (forward
    slashes) and is compared with a fresh read of the file (`refresh_rows.differences`), and after a rename it names
    the old file, so it is converted, not derived: `@pictures/2024/a.jpg` when the file is under a root and
    converting back gives exactly the string (any other spelling stays as it is, which loses nothing), read back
    native on every read of the column (`roots.raw_to_native`). `suggestions.raw`'s `path` and each
    `nearest_neighbors[].path` are converted the same way, only where the round trip is exact.
  - **The journal speaks one form** (`journal._canonical`): an edit's values are native, the library holds rows;
    both sides of every comparison are converted, what a change records is the row form, and a change recorded before
    the adoption (native) is converted as it is read, so undoing it writes the rooted form for a rooted photo, never
    a native row beside the rooted one. `history` shows what was recorded. The folder settings are shown native
    (`store.settings`), and their old and new values in the journal hold the row form.
  - **`roots adopt --name pictures --address <share> --location <folder here>`** (`tagpup.services.roots`,
    `tagpup.store.adoption`, the CLI's `roots` group; no MCP tool: a one-way step on the owner's data is theirs, not
    a tool's): a dry run unless `--apply`. The dry run counts, for each table, the rows it would convert, the rows under
    no root (grouped by folder with `paths.outside_roots`; they keep their native path), rows that would not convert
    back, rows that would become one, the settings it rewrites, and says why it would be refused; it reads and
    writes nothing. `--apply`: the map first if it lacks the root (atomic, one editor at a time, only then), then in
    ONE transaction under the library's write lock: a new backup of the library (always a fresh one, under the
    lock, so it is the library as it stands; a full copy, so another process's write waits for it -- a note beside
    the library tells that write why if it gives up, `db.busy_note`, and the dry run and `--apply` say to run it with
    TagPup and TagTuner stopped and how long to expect, from the speed the last copy ran at, kept in the backups
    folder), every table converted, verified before it commits (row counts equal, every rooted row converts back, no
    row is held under one root though it lies under another's place, the map places the root where the rows were
    converted by), one journaled change, `roots adopt: pictures`, whose summary holds counts and never a name. Every
    place the map lists for the root is equivalent: a row under any of them converts, taking the first one's spelling
    (counted as respelled). **One root holds each folder**: nested roots are the model's (`core.paths` resolves
    them, the deeper winning) and are refused at adoption, in either direction -- a root whose place, or address, is
    under, over or the same as another root's of the library -- naming both, since moving the outer root's rows under
    the inner would be a conversion an undo could not reverse once a rename or an index had touched them. It refuses,
    writing nothing: a root of that name already, a nested root, a location that is not a folder here, a location no
    row lies under, a map that places the root elsewhere, a row that is not an absolute path here or does not convert
    back, rows spelled by the share's address (named as their own count and folder list: converting them would
    retarget them from the master, the share, to this machine's copy; fix their spelling first, or adopt a root whose
    location is the share), two rows that would become one when at least one of them converts (two rows of one file
    that both stay outside the root are only reported as duplicates), another process holding the write lock, an
    unfinished change of photo files. `undo` reverses it (`adoption.undo_in`: the same conversion back by the map,
    verified) and also converts back the paths that later changes recorded, in the same transaction, so those changes
    stay undoable in a library with no root: `change_files`, and in `change_rows` only the values that hold a path
    by their structure (`photos.path`, the two folder settings, the `SourceFile` of `raw_metadata`, the path fields of
    `suggestions.raw`) and only when the value is a row of the undone root; no text is searched, and a title or an
    address with an "@" in it is the same bytes afterwards. The dry run says plainly before `--apply` what it will do
    and which roots remain (the undo's rehearsal notes, which the CLI prints). It is refused only when this machine
    does not place the root, or a later change recorded a path of the root that cannot be converted back (named).
    Every step is `_reached`, and tests stop the process at each: the library is exactly as it was. A refused
    `--apply` still migrates the library to schema 18 first (additive and empty, *decided 2026-10-02*).
  - **Checks**: `tools/doctor.py` and the MCP's checks gain `rooted_rows_convert` (rooted rows that name a root the
    library does not have, that this machine does not place, that are not what `to_row` writes, or that are held
    under one root though they lie under another's place) and
    `native_rows_under_a_root` (a write made between another process's adoption and its own commit, the one race
    nothing can close), and the doctor lists the photos under no root, by folder. A library whose map does not place
    a root is told of at once (`services.roots.problem`, which the CLI prints when it opens the library) and its
    paths are refused with a message naming `machine_roots.json`, its path and the line to add: never an empty library.
  - **What the owner sees**: nothing, until they run it; then `roots` lists each root with where this machine keeps it,
    and History lists `roots adopt: pictures` with its counts. On photo_index's shape (68,466 photos, 225,000 faces,
    made here) the dry run takes 1.5 s and `--apply` 5.9 s with the backup, an undo 6.6 s.
  - **Stages 3 and 4, built** *(2026-10-02: TagTuner's Roots, the server's answer for an unplaced root, pinned runs, the sandbox's own map)*:
    - **Verify** (`tagpup.services.roots_verify`, read-only: no row, no file, never the map). For a root and a CANDIDATE
      location -- a hypothetical map, `paths.Roots` built from the library's roots with the candidate in the root's
      place -- it reads the root's rows as the library holds them (`store.root_rows.stamps`: path, mtime, size; never a
      BLOB; 0.08 s on photo_index's 68,466 rows) and looks at the disk: per row **matches** (size and modified time as
      recorded, `store.photos.describes`), **differs** (there, and changed since indexed, or a copy with other times:
      sync's to settle, never "missing"), **missing**, **unreadable**; and, in a full run, the photos at the place no
      row has, the rows under no root grouped by folder (`paths.outside_roots`), and the rows kept native where the
      place would be the root's (they would be missed by every lookup and indexed again). **A place that cannot be
      reached is not "all missing"**: a drive not connected, a share away, a folder not there, a share that stops
      answering half-way are each said (`state`: `no_drive`, `away`, `no_folder`, `unreadable`) and the rows not looked
      at are not counted. Every look at the disk is a thread waited for 15 s at most; a share that did not answer is
      "away" for 30 s by its drive or server and share, and no second thread is started for it (`damaged_photos` has
      its own away-cache, which cannot say missing from away, so Verify keeps one). A **sample** (2,000 rows, at least one
      from every folder -- 2,674 on photo_index, so about 2,700 -- the same rows for the same library, visited in a
      spread order so one cut short has seen folders from everywhere) lists each folder once, answered in the request
      within 25 s; **all** walks the place, listing each folder once, and is a job (`tagpup.jobs.verifying`): its
      progress and cancel (between folders) are this process's, its claim and its result are the library's `job_runs`
      (`verify root <name>`), which the Activity page lists, so a second Verify of the root -- another tab, another
      process -- is told one is under way. Measured on a synthetic library of 68,466 rows in 2,674 folders (real small
      files, a warm local disk): the sample 1.0 s, all 1.0 s; a share's cost is the 2,674 listings, which the
      deadlines bound.
    - **Moving a root** (`tagpup.config.set_location` / `change_back`, `tagpup.services.roots_location`): the map's edit
      alone, never a row. `set_location` puts the new place first and keeps the old after it (so Change back is the
      reverse: the first two swap); **only the current and the previous place are kept**: a move drops anything older
      (a place the map still lists is a place a stale spelling is recognised by), and the dialog says so, written whole to a temp file and `os.replace`d under the one `_edit_lock`; it
      refuses a place that is not absolute, has the long-path or device prefix, does not exist, is another root's, or
      is nested with the root's own other places, and, given `expected` (the place the page saw), a map that moved
      since -- another tab, a hand edit. The service asks first: a **dry run** shows Verify's sample of the new place;
      a **poor result** (more than 5% of the rows looked at missing, an unreachable place, rows kept native where it
      would be) is refused unless the request says `override` -- but a drive or folder that is not there is refused
      whatever is said, and so is a place that would put the root inside or over another root of the library (the same
      photos reachable under two roots). It is refused **while TagTuner's own runs or a Roots verify are running or queued** -- an index
      run or Suggest in this process, its watcher's sync, a verify -- and, from the library's own `job_runs`
      (any process's, ignoring a run whose process has ended), a recurring job; and names which. **TagTuner cannot see
      TagPup's Suggest, a CLI index or a CLI sync** (no cross-process lock was built, *owner, 2026-10-02*): the refusal
      and the dialog tell the owner to stop those before moving a root. A run that is going keeps the map it started
      with (pinned) and so keeps writing to the old copy until it ends. The machine's map is one for
      every library of the home, so a root of one name in two libraries moves in both: the dialog says which others use
      it, and a run queued or running in any of them holds the move up too. Two requests at once:
      the claim of the change in `job_runs` lets one in and tells the other; a second Confirm, or a tab that came after
      and found the root already at that place, changes nothing and says so. Each change is a run in `job_runs`
      ("moved root pictures to X (was Y)", which the Activity page lists: a run may say what it did in `changed.what`).
      A running server needs no restart: a connection that sits idle finds the new map within a second
      (`store.roots.RECHECK_SECONDS`), the generations' salt rebuilds the caches of this machine's paths, and the web
      layer lets go of its folder scans (`tests/test_roots_routes.py`: the next `/api/photos` after a change names the
      new place, and no row changed -- a full dump compared).
    - **TagTuner's Roots** (the gear's "Roots...", `web/tuner/roots.js`; `GET /api/roots`, `POST /api/roots/verify`,
      `/verify-cancel`, `/change-location`, `/change-back` in `tagpup.web.tuner_routes`, loopback only, specified in
      SPEC_TAGTUNER): one small dialog -- a row per root with where it is kept, "Tags and renames are written to files at
      <place>", the earlier place (a separate copy: nothing written now goes there), the last check as a sentence,
      Verify, Verify all (progress, Cancel), Change location (a place typed or picked, Check this place, then Move it
      here -- once; a poor result shows why and needs "I mean it"), Change back. A library with no roots says "This
      library has not adopted a root yet; nothing to change here." and shows the CLI command. A root this machine does
      not place is listed so, and Change location is how it is placed.
    - **The server's answer for an unplaced root** (`tagpup.web.roots_gate`, the MCP's `find_library`): a request that
      needs a photo's path on a library whose root this machine does not place, or whose map cannot be read, is answered
      409 with the sentence that names `machine_roots.json` and the line to add (`services.roots.problem`, kept for a
      second per library) and `X-TagPup-Roots-Problem`; both pages show it as a banner (`web/common/roots-banner.js`,
      fed by `api.js`). The picker, rules, version, Activity, history and Roots are let through, so the page opens
      and the dialog can place the root. The MCP's tools say the same, except `history`, `undo`, `prune_journal`,
      `sync_state` and `query_plan`, which read no photo's path.
    - **Pinned runs** (`services.roots.pinned`): an index run (the CLI's `index`, in its own process, holding the map
      it started with), a sync pass (`services.sync.sync`) and a change of photo files (`file_changes.write_fields`,
      `rename`) convert every path by one Roots however the map is edited meanwhile, and stop with a sentence when
      another process changes the library's roots (`RootsChanged`: the indexer exits 75, which the server's queue turns
      into the sentence; a sync or a write answers a refused Result; a rename raises). A root this machine does not
      place refuses the run in words (`Unplaced`).
    - **The sandboxes** (stage 4): `services.roots.place_in_sandbox` -- called by `measure_identify_faces.py` and
      `measure_suggest_folder.py` after they copy a library -- writes the sandbox's OWN `machine_roots.json` (in its
      own `TAGPUP_HOME`) placing each root of the copy in an empty folder of the sandbox, and fails loudly for a root it
      did not place, a place outside the sandbox, or a map that is not the sandbox's: a converted copy would otherwise
      point, through the machine's map, at the real photos. `generate_screenshots.py` finds its photos from its own
      checkout.
    - **An old place's spelling** *(review of stage 3, #465)*: after a move the previous place is still recognised, so a page that
      still holds a path spelled by it, a bookmark, or a folder typed under it, names the same row. No request takes such a
      path as written: **one ingress** (`tagpup.web.roots_ingress`, a before_request after the gate) turns every
      path-bearing parameter of an `/api/` request -- `path`, `paths`, `folder`, `folder_path(s)`, `photo_path(s)`, in
      the query or the JSON body -- into the first place's spelling (`paths.canonical`: `from_row(to_row(path))` for a
      path under another listed place of the root, the path as given for any other) before any route, cache or
      pre-check reads it. TagPup's folder scans are therefore one entry for a folder however it is spelled, the photo
      file served and opened is the first place's, and a request holding old paths works once the old place is gone.
      A folder to open or add (`/api/folder/scan`, `membership`, `subfolders`, `add`, `index-start`) that exists only at
      the previous place is refused naming both places and the root. The ingress costs a request with no path
      parameter nothing (it returns before opening anything), and one with a path one look per library per second
      (about 9 us and 60 us per request measured; it opened a connection per request, 4.2 ms, before); `/api/roots`
      and the folder box's autocomplete (half-typed text) are left alone. **A page that sent an old place's
      spelling is told**: the response carries `X-TagPup-Roots-Moved: <root>`, and `web/common/api.js` shows the
      banner "The place of root X changed since this page loaded; reloading" and reloads once (not again within 30 s,
      kept in sessionStorage); the page's held paths are not translated. **Accepted**: a Suggest run keyed by a folder's
      old spelling does not survive a move (the run is lost, not wrong). The
      services keep `services.roots.canonical_args` as the second line (the CLI, the MCP tools, jobs): the writes
      (`tagging`, `photos` delete / rotate / Smart Rename / time shift, `file_changes.write_fields` and `rename`), the
      folder scans, Add, `sync` and the photo's details -- not `index_folder`, which runs on the queue's worker and
      is handed what Add and sync resolved; the CLI's `index` resolves its folders itself, through the roots it pins,
      refusing one that exists only at the previous place. An undo of a change recorded before the adoption writes the
      first place's file (`file_journal` reads a path through `paths.canonical`).
    - **Verify, as reviewed**: a result is also poor when more than half the rows checked differ (a stale copy: sync
      would re-read those rows from the files there, replacing tags newer in the rows than in those files, which the
      dry run says and counts); a row the index never read is counted apart ("never read"), not as "differs"; a library
      older than the schema says to open it with TagPup or the CLI once. A write stopped half-way by `RootsChanged`
      says how many files it wrote and the change that recorded them. "A share is away" has one owner,
      `tagpup.files.shares`, which Verify and the damaged photos' lists both use. An unplaced root is a check result
      (`roots_placed`, its sentence in `message`) in `inspect.all_checks` / `check` / `summary` and the MCP tools, and
      the Activity page's attention list carries the sentence.
    - **Not built**: `roots remove` (an undo is the way back); Verify from the CLI or the MCP; moving a root while a
      run is under way (refused, by design); a library behind more than one machine map (one map per `TAGPUP_HOME`).
- **Changing where a root lives, in TagTuner** *(owner, 2026-10-02; built, see "Stages 3 and 4")*: for now the libraries stay on
  `D:\Training`, and once the core features are in and trusted the same libraries are pointed at
  the official share, losing nothing. TagTuner's gear (the server-side component's page) shows
  each root with where this machine keeps it, and offers:
  - **Verify** a location, before and after a change, read-only: for the root's rows, how many
    files exist there, match the row's size and modified time, differ (changed since indexed;
    sync's to settle), are missing, and how many photos there are that no row has. It samples
    first and can run over every row.
  - **Change location**: a dry run first showing Verify's counts for the new location, then
    the change, which only edits the machine's map, never a row. The previous location is kept
    and **Change back** is one click. A location whose Verify is poor (many missing) is refused
    unless the owner overrides it. Every change is logged on the Activity page.
  - The two locations are two copies until the old one is retired; the page says which one
    writes go to, so a tag written to the share is not mistaken for one on the desktop copy.
- **Server and client.** The boundary is the HTTP API the pages already use, plus the CLI. The
  server component is the supervisor, the job runners, the models, the database and file
  access; the client is a page or the CLI. Inference is a job the runner starts, so where it
  runs (a worker on the desktop now, the server later) is the runner's business and not the
  callers'. Decided here: nothing but the abstraction is built before the move.
- **Decided** *(owner, 2026-10-02)*: the root-relative stored path, not the relocate alternative.
  Logins and a client on another machine are not part of this: the server keeps listening on this
  PC only until phase 10 (family access), *(owner, 2026-10-02)*. The design here serves the
  library views (phase 9) and a later move of the indexing, not remote users.
- **Before any of it is built:** an audit, read-only, of every place that touches the filesystem
  with a stored path, to size the change; the research on similar tools (`reports/`) read for how
  they relocate libraries.

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
  Trips/" reads every row: a derived `photo_tags(photo_id, tag_id)` table, by the tree node's id,
  indexed and kept by the writes as `photo_people` is. Folder counts scan the same way
  (findings #168): derived `folders` and `photo_folder` tables and an index-friendly path range,
  both built (see "Phase 9a-1" below). Browsing
  thousands of photos needs cached thumbnails (derived, keyed by photo and modified
  time) and a grid that renders only what is on screen -- the Identify Faces work showed
  what rebuilding tens of thousands of cards costs.

The owner's answers *(2026-09-26)*:
- **The tag pane is there already**: the folder view's details panel and bulk tags are
  what Photo Gallery's tag pane was used for. A library view gets the same panel.
- **What is missing is the left navigation over the whole library**: by date (year, then
  month) and by tag (the tag tree), as Photo Gallery's navigation pane did.
- **Search, as Photo Gallery's**, with results shown as a folder view is. Photo Gallery
  searched file name, tags, caption, author and camera for the words typed, within what
  the navigation pane had selected; tags picked in the pane with Ctrl were OR (any of
  them), the words in the search box were AND (all of them); it had no way to leave a
  tag out ([Find your photos 2](https://ludwigkeck.wordpress.com/2009/09/21/find-your-photos-2-%E2%80%93-windows-live-photo-gallery/)).
  2011's Find tab filtered by people, descriptive tags, date, place, folder, rating or
  flag, in any combination. The owner wants more than that: three family members and
  not a fourth -- *all of*, *any of* and *none of*, over tags and people, with text.
- **Albums are folders**, here and in phase 10: no album entity.
- **The thumbnail cache is not bounded.**
- **Libraries are always distinct**: a view shows one library.

**Where the code stands** *(2026-09-26)*: the grid (`web/tagpup/grid.js`, `folder.js`)
builds a card for every photo of the folder it has open, keyed by path; nothing renders
only what is on screen. A thumbnail is made on every request (`files.images.smaller_copy`,
Pillow, `size=300`), never kept. Keywords are JSON in `photos.tags`; there is no
`photo_tags` table. Folders under a folder are an index range now (#168); Remove Folder
already lists the library's folders with counts (#47). `photos.taken` and `photos.year`
exist. The journal, History and sync (phase 8) are what a library view's edits and
staleness stand on.

**Stages**, each reviewed and merged on its own; the performance ones measured as the
click in a real browser on a sandbox copy (CLAUDE.md, "Performance work"):
- **9a. What views stand on (server only).** In two. **9a-1, built** *(2026-10-02, migration 19; see
  "Phase 9a-1" below)*: the derived tables `photo_tags(photo_id, tag_id)` *(owner, 2026-10-02: identity by id,
  not by name; see "Identity by id" below)*, `folders` and `photo_folder`, and `photo_meta`, kept by the writes
  that keep `photo_people` and rebuilt by the migration and the doctor; "a keyword and everything under it" is
  the node's descendants, a range on `tag_taxonomy.tag` (unique, indexed) that gives ids, joined to
  `photo_tags`. **9a-2, built** *(2026-10-02, migration 20; see "Phase 9a-2" below)*: A
  thumbnail cache on disk: derived, keyed by photo id and the file's size and modified
  time, under `data/cache/<library>/thumbs`, made when a photo is indexed or its file
  changes (queued, phase 8's events) and on first ask, dropped when its stamp changes or it
  leaves the library -- never by a schedule; not bounded
  *(owner)*: about 20-40 KB a 300 px thumbnail, 1.5-3 GB for photo_index, measured. One query
  service, `tagpup.services.library_view`: a source (folder and subfolders, keyword and
  everything under it, person, year or month) to an ordered page of photo ids and the
  total; and the navigator's counts (folder tree, tag tree, people, years and months),
  each one query. Routes and specs; EXPLAIN QUERY PLAN on photo_index for each.
- **9b. One grid on photo ids.** *(9b-1, built 2026-10-02, not merged: the windowed grid and the folder view on it, see
  "Phase 9b-1" below; 9b-2, a library source fed by the order of a view and the cards near the window, built 2026-10-03, see
  "Phase 9b-2" below.)* The grid, the details panel, the selection and the bulk
  edits take a source and work on photo ids; today's folder view becomes the "folder on
  disk" source through the same components. Only the cards on screen (and a screen
  either side) exist in the DOM. Exit: a 20,000-photo keyword scrolls without a stall,
  measured; the folder view's behaviour and tests unchanged.
- **9c. The navigator and the move between views.** Folders, Keywords, People and Dates
  beside the grid, with counts; the header and the URL name the source, so Back and a
  bookmark work; "Show in library" from a disk folder; a banner where the disk holds
  files the library does not (offering to index them, through sync); staleness marks on
  the cards on screen (size and modified time, no ExifTool) and a missing photo shown
  but not editable; "last in step" from sync.
- **9d. Editing from a library view.** Bulk edits on a selection that spans folders,
  through the same journaled writes (History lists and undoes them); a file changed
  outside while an edit is planned is a conflict for sync to settle, never overwritten.
- **9e. Search.** A search is a source: *all of* these tags or people, *any of* those,
  *none of* these, and words matched against file name, tags, captions and people (as
  Photo Gallery's search box did), optionally within the folder, tag or date the
  navigator has selected. Built on `photo_tags` and `photo_people` as set operations in
  one query; its results open in the same grid, panel and bulk edits as a folder, and
  the URL holds the search so it can be bookmarked or opened again. A picker that
  completes tag and person names (the tag editor's vocabulary) builds the three lists.

Exit: the owner can open the whole library by folder, keyword, person or date, move
between a disk folder and its library view without losing place, and edit from either,
with every edit undoable and nothing overwritten that changed outside.

### Phase 9a-1: the derived tables the views stand on *(built 2026-10-02; migration 19; branch `arch/phase-9a-derived`)*
Server and store only: no page, no route, nothing the owner sees until 9a-2 reads them. One additive
migration makes four tables from the photos' own rows, and `tagpup.store.derived` owns them (docs/DATABASE.md,
tables 21 to 24):
- **`photo_tags(photo_id, tag_id)`**, `tag_id` the `tag_taxonomy` node's id *(owner: identity by id)*, indexed on
  (`tag_id`, `photo_id`). A keyword is matched to the node whose tag it is, else the node it is without case, with
  its segments trimmed and `|` and `\` read as `/` (`vocabulary.normalize`), the lowest id when two nodes differ only
  in case (none do on photo_index). **A keyword with no node gets no row and no node is made**: the owner's tree
  does not change by indexing a photo. On photo_index that is 4 distinct keywords on 32 uses (counted 2026-10-02:
  151,425 uses on 876 keywords, 872 of them a node's exact tag, none differing only in case or spacing); the doctor
  lists them by count and, with `--show`, by keyword (`photo tags with no tree node`), reported and not broken. A bare
  leaf is such a keyword: it is the people rule's to resolve, not the tree's. "A keyword and everything under it" is
  two seeks of the tree's unique index (`tag = ?` and `tag >= 'tag/' AND tag < 'tag0'`, never LIKE) giving ids, joined
  to `photo_tags` by `tag_id` (`derived.under`, `photos_under_tag`, `count_under_tag`). A node renamed or moved keeps its
  id, so its rows are unchanged and its range follows its new path -- the photos' keyword text is rewritten in the files
  by TagTuner, one transaction after the tree's, and until each is, a photo's old text names no node and is reported; a
  node deleted takes its rows by a trigger and the keyword is reported, not made again.
- **`folders(id, parent_id, path, name)` and `photo_folder(photo_id, folder_id)`**, derived from `photos.path`: the
  folder tree and the folder each photo is directly in. There is **no `folder_id` on `photos`**: it would have been a
  second migration of every photo row beside the roots one, and a join through `photo_folder` costs one seek. `path`
  is a path column like the others: the folder in ROW form (`@pictures/2024/Coast` in a library that holds a root,
  native in one that holds none), unique, compared without case where the filesystem is, `from_row`ed on the way out
  (`derived.folder_tree`). A folder has a row for every ancestor of a photo's folder up to the top of its spelling --
  the root of a rooted path, the drive or share of a native one (photo_index would have 2,746 folders: its 2,674 and
  72 above them, in 3 trees: `@pictures` and two native tops) -- so the navigator can draw the tree. **A folder
  emptied goes**: its row, and the ancestors only it held, are pruned in the transaction that emptied it
  (`derived.prune`, called by `move_rows_in`, `forget_photo`, `remove`, `remove_under`); a delete that did not come
  through the store leaves them, which the doctor reports and does not count as broken (`folders holding no photo`).
  **The roots' adoption and its undo rebuild the folders** in their own transaction (`derived.rebuild_folders`), verified
  before it commits, with a step (`folders rebuilt`) the tests stop the process at; an adopted library's folders are those
  of an unconverted twin, by `from_row`, except that the root is a top (`tests/test_derived_follow_writes.py`). A folder's
  id is kept while the folder is (AUTOINCREMENT, never reused) and **not kept across an adoption or its undo**, which
  change every path's spelling: a page names a folder by its path. Per-folder counts: the photos directly in a folder are
  one GROUP BY of `photo_folder`, those in it and below that rolled up the tree in Python (`derived.folder_tree`, 7 ms for
  2,700 folders), or one range of `photos.path` (`derived.recursive_count`, which agrees with the roll-up).
- **`photo_meta(photo_id, rating, make, model, width, height, latitude, longitude)`**, one row for every photo, from
  `photos.raw_metadata` by one pure function (`tagpup.core.photo_meta`) written after counting the spellings on photo_index:
  `XMP:Rating` over `EXIF:Rating` (never disagree), an integer from -1 (rejected) to 5; `EXIF:Make`/`Model` over `XMP:`;
  the signed `Composite:GPSLatitude`/`Longitude` (the EXIF pair has no sign), both or neither, in range, and not the pair
  0, 0 that a camera without a fix writes (83 latitudes and 107 longitudes on photo_index are exactly 0). **`width` and
  `height` are empty in every row: no row of photo_index holds `ImageWidth`, `ImageHeight`, `ExifImageWidth` or
  `Orientation`**, since the indexer does not ask ExifTool for them (`fields.METADATA_FIELDS`). The extraction reads them
  where a row has them (the picture's own size, then the one EXIF declares, swapped for Orientation 5 to 8 so they are as
  shown). Asking for them changes what every read records, makes every row of the library differ from a fresh read, and is
  **the owner's decision, not made here**: until then a size filter has nothing to filter. photo_index would fill 58,551
  ratings (most 0), 65,011 makes, 63,020 models and 1,485 places.

**Kept by the writes, in their transactions** (the lesson of the bulk tag writes that once did not tell the index): the
index's `record_indexed` (the batch shares one `derived.Batch`, the tree read inside its transaction, so a record costs 0.103 s
for 500 photos where it cost 0.080 s), `record_tags`, `record_saved`, `follow_fields` (so a change of files and its undo),
`move_rows_in` (one refresh for all the photos it moves) and `ensure_row`; the tree's edits (`people.tree_edit` reads every photo's keywords once, as
`people.follow_tree` does, to find those a changed node names: 0.31 s on photo_index warm, measured read-only, under the
write lock of an edit that adds, moves or takes away a node, and nothing for a flag); the journal's `_derive` after an apply, an undo or a settle,
and its rehearsal, which compares the derived rows too. A photo deleted, or a node, takes its rows by trigger on any
connection. They are `journal.DERIVED` and `schema.UNWATCHED`: never journaled, rebuilt, and a migration that makes or
fills one no longer blocks the undo of an older change (`journal.schema_gap_blocker` asks only of `journal.KEYS`).
`tests/test_derived_writers.py` fails the build on a function of `tagpup/` that writes a photo's tags, path or
raw_metadata, inserts or deletes a photo, or writes the tag tree outside `people.tree_edit`, without going through
`derived`.

**Checks and repair.** `photo_tags_out_of_date`, `photo_folders_out_of_date`, `folders_out_of_date` (the parent chain complete,
every folder a photo needs there) and `photo_meta_out_of_date` are in `checks.RULES`, so the doctor and the MCP pick them up;
`tools/doctor.py --rebuild-derived` is a dry run and `--apply` one write that makes the four tables what the photos say and
verifies it, touching no photo, tag or file (so no backup). Migration 19 itself checks, before it commits, that what it made
is what a read of the photos finds (`derived tables agree with the photos`). A version of the app from before 19 still writing
photos to a library at 19 leaves the rows stale; the doctor says so and the repair is the one above.

**Measured** on a synthetic library of photo_index's scale (68,466 photos, 157,000 keyword uses, 2,674 folders, 889 nodes; rows
as the indexer stores them): migration 19 takes 3.6 s under the write lock, of which 1.6 s is its own check; the plans, with
no SCAN of the photos' or the tree's tables (checked on photo_index, read-only: a folder's range `SEARCH photos USING COVERING
INDEX idx_photos_path_nocase`, photos by id `SEARCH ... INTEGER PRIMARY KEY`; the one-per-run reads of every photo, a tree edit's
and the doctor's, are a `SCAN photos` by design): a tag's ids `SEARCH tag_taxonomy USING COVERING INDEX ... (tag=?)` and
`(tag>? AND tag<?)`; its photos `SEARCH photo_tags USING COVERING INDEX idx_photo_tags_tag (tag_id=?)` and a temp b-tree for the
order; the direct counts `SCAN photo_folder USING COVERING INDEX idx_photo_folder_folder` (a scan of the index, 4 ms); a folder
by path `SEARCH folders USING COVERING INDEX ... (path=?)`. A filter on rating or camera scans `photo_meta` (2.5 ms); an index is
9e's if a measured click needs it.

**What 9a-2 uses.** `derived.photos_under_tag(conn, tag, limit, offset)` and `count_under_tag` for a keyword source;
`derived.folder_tree(conn)` for the navigator's Folders (native paths, counts direct and recursive, one query) and
`photo_folder` (`folder_id` -> photo ids) or the `photos.path` range for a folder's photos; `photo_people` for a person; `photos.taken`
and `photos.year` for dates; `photo_meta` for the rest. The navigator's Keywords counts are a roll-up over `photo_tags` (151,393
rows on photo_index) by the tree's parents, one query and a pass in Python, as the folders' are. The thumbnail cache is keyed
by the photo's id and stamp, as before. `folders.path` is row form: ask for a folder by path through `store.roots.sql_equals`-style
conversion, never by an id kept in a page.

### Phase 9a-2: the thumbnail cache, the library views' query service and their routes *(built 2026-10-02; migration 20; branch `arch/phase-9a2-view`)*
Server only: no page, nothing the owner sees until 9b. Three things, each in its layer.

- **The thumbnail cache** (`tagpup.files.thumbs`, the files; `tagpup.services.thumbnails`, what an entry is and when it
  goes). DERIVED and not bounded *(owner)*: `<library folder>/cache/<library>/thumbs` (`Library.thumbs`; a test home holds its
  own), a 300 px JPEG for each photo, `<id // 1000>/<id>_<path hash>_<mtime ms>_<size>.jpg`. The key is **the photo's id, eight
  hex digits of the path its ROW holds, and the stamp of the file it was made from**, the stamp read from the file at each
  ask: a file changed is a new entry and the old is deleted as the new is made; a photo renamed keeps its id and gets a new
  hash, made once; an id handed out again after a restore never serves another photo's picture (its path hash differs);
  **a library moved to another root place keeps its entries**, since the rows' paths and ids are unchanged (tested through
  the roots' map). Two libraries on one machine have two folders, so the same ids never meet. **Made on first ask** (`serve`)
  and ahead of time by `tagpup_cli.py thumbs warm [--folder F] [--limit N] [--apply]` (a dry run: counts, the bytes the missing
  ones would take from the average entry kept, writes not even the folder; `--apply` makes them four at a time, can be stopped
  and run again, brings a library behind up to date first and deletes entries of photos the library no longer holds). **Not
  made when a photo is indexed or changed** -- a queue for that was in the first design and is the owner's to ask for: every
  entry is made by the first ask or the warm command. **Atomic**: a temporary file in the shard, `os.replace`d over the name
  (a rename that meets a reader on Windows leaves the entry that is there); two requests for one photo make it once (a lock
  for each of 64 stripes, the entry looked for again inside it); another process making it too -- `warm` beside the
  server -- writes the same whole file. A crash leaves a `.tmp-` file, taken when it is an hour old (`sweep`, `warm --apply`).
  **Taken away by the code that takes the photo away, not by a schedule**: `photos.delete` (its id read before the row goes),
  `faces.remove_folder` (the ids under the folder, likewise) and the indexer's `PhotoIndex.remove_paths` (`sweep`: every entry of
  a photo the library does not hold, or holds at another path); `index --reset` clears the library's whole cache. A restore from a
  snapshot, a journal undo of inserted photos (an undone index run or sync), or a delete outside the services leaves entries
  nothing will serve, which `warm --apply` sweeps.
  **A damaged photo** -- a `damaged_files` record whose stamp still describes the file, and not a possibly incomplete copy,
  which decodes -- is answered a placeholder (`X-TagPup-Thumb: damaged`) **without its file being decoded**; one that does not
  decode though nothing says so is answered the same and remembered in the process while its stamp holds (a damaged file is
  not decoded for each of the thousand cards that ask), and **nothing is written to the library for it**: recording it is the
  indexer's finding, and a false one refuses writes to a good photo. Nothing is cached for either. **A cache that cannot be
  written** (the folder missing and not makeable, read-only, full) costs only the cache: the thumbnail is made and answered
  with no ETag, and the first time is logged. **A share that is away** shares the folder listings' memory (`tagpup.files.shares`): the first look waits one second, the share is then taken as away for 30 s and answered at once,
  with no second thread while one hangs, so 24 cards on it cost one wait (a 503 with `Retry-After` and "the network share is away"); `thumbs warm` asks a share once and leaves its photos, and says so.
  **A file that is there and cannot be read** (permissions, a lock) is a different answer from one that is gone (`damaged_photos.stamp_of`: None, `CANNOT_READ`, `UNANSWERED`): a 503 "cannot be read", never "not there". The older `_stamp` keeps
  its None for both, so for the other callers nothing changed: `prune` and `forget_to_reindex` treat it as gone, `check_again` counts it unreachable, and `for_write` does not find an unreadable file damaged -- the write meets it itself.
  Either is `Unavailable` and nothing is decided about the photo. **A cache that cannot be written ends `warm --apply` at the first one** (exit 1) before more is decoded.
  `page_copy` and `/api/photo-file?size=` are unchanged: the folder view still makes its thumbnails per request until 9b.
- **`tagpup.services.library_view`** over **`tagpup.store.library_view`** (the SQL; `tagpup.services` holds none). A source is
  `all`, `folder` (by its PATH, never an id; `recursive` takes its subfolders), `keyword` (the tag as the tree spells it, else the node it is without case -- an exact match wins, none is empty --,
  read as a keyword is: `|` and `\` as `/`, trimmed; and everything under it), `person` (the leaf, compared without case),
  `year` (`photos.year`) or `month` (`YYYY-MM`, a range of `photos.taken`). `view` returns `{source, total, ids, next, limit,
  cards}`: **ordered by Date Taken then id, photos with no date after them by id, by a KEYSET** -- the token is the (phase,
  taken, id) of the last photo, opaque, checked when read (too long, not base64, not the shape, a part out of range: `400`),
  never an offset, so a photo added, taken away or re-dated between two pages neither repeats nor skips another beyond what
  that change itself explains (each is a test). `limit` is 200, at most 500, `400` below 1. A **card** is `{id, name, path
  (native), taken, damaged, damage, thumb}`, one read in batches of 500 of the columns it needs -- no `raw_metadata`, no
  BLOB, no query for each photo, no look at the disk (a test counts the statements for 3 and for 62 photos: equal) -- and
  `thumb` is `/api/photo-thumb?id=<id>&v=<the row's mtime>`. `damaged` is the record describing the row's stamp. A keyword's
  photos are `p.id IN (photo_tags of the nodes under it)`, so a photo holding two tags under the keyword is one row (the
  `EXISTS` form scans the date index and is 2.5 times slower; a `DISTINCT` join 46% slower). A source with nothing is an empty page,
  not an error. The navigator, `navigator(library, section)`: `folders` (`derived.folder_tree`: direct and recursive counts,
  by native path with `parent` a path), `keywords` (every node with the photos at or under it, each once per node: one pass of
  `photo_tags` by photo, `group_concat`ed and rolled up the parent ids in Python, **equal node for node to `count_under_tag`**,
  a test), `people` (`photo_people` by name, names differing only in case one entry, a photo counted once), `dates`
  (`{years: [{year, count, months: [{month, count}], other}], undated}`: by `photos.year` and, within it, `taken`; `other` the
  photos of the year whose `taken` is no month of it -- photo_index has 5 written with dashes, and 1,180 with no date, whose
  years come from their names). Every function reads through a read-only connection and migrates nothing.
- **Routes** (`tagpup.web.tagpup_routes`, loopback only -- `403` from any other address -- through the Roots gate and ingress,
  specified in SPEC_TAGPUP_GUI): `GET /api/library/navigator?section=`, `GET /api/library/view?kind=&value=&folder=&recursive=&after=&limit=`
  (a folder is named by `folder`, which the ingress resolves through the first place of its root, as any path of a request; the
  other kinds by `value`) and `GET /api/photo-thumb?id=&v=`. The thumbnail's URL carries the stamp: when `v` is the file's modified
  time the answer is `private, max-age=31536000, immutable`, with an `ETag` (the entry's name); otherwise `no-cache`, revalidated by
  that ETag (`If-None-Match` is a `304` with no body); a placeholder or a last-known picture is `no-cache` with no ETag. An id with no
  photo is `404` with a sentence; an id SQLite cannot hold is the same (it was a 500 until a test found it). A library not at
  migrations 19 and 20 -- the app brings a library up to date as it opens it, so only one that could not be -- is `409` and a
  sentence naming it and what to do; an unplaced root is the gate's `409` with `X-TagPup-Roots-Problem` before a route runs, and
  `paths.RootsError` from a service called any other way.
- **Migration 20** (additive, indexes only, touches no table, blocks no undo of an older change): `idx_photos_taken (taken, id)` and
  `idx_photos_year (year, taken, id)`. Without them "all photos" is `SCAN p` and a sort of 68,466 rows (checked read-only on
  photo_index's plan), and a month and a year are as bad. It is written to a library when the app opens it next, as every migration
  is -- **photo_index is not migrated by this branch**; `tests/test_taken_indexes.py` holds that nothing but the two indexes changes.

**Measured** on a SYNTHETIC library of photo_index's scale, made by the production writers (`record_indexed` with a shared
`derived.Batch`, `taxonomy.add_path`; 68,466 photos in 2,699 folders -- one of 20,000 --, 143,851 `photo_tags` rows on 700 nodes, 95,721
`photo_people` rows for 400 people, 810 undated; photo_index itself has 151,425 keyword uses on 876 keywords, 895 nodes, 80,053 people
rows for 413 names, 1,180 undated, counted 2026-10-02; the synthetic one is a little lighter). Best of several, warm, a library opened
for each call (about 12 ms of each is opening the library and SQLite reading its schema):

| A page of 500 with its cards | first page | page 100 of 137 / the last |
|---|---|---|
| all | 12 ms | 14 ms |
| a folder of 20,000, alone / with subfolders | 23 / 26 ms | 24 / 29 ms |
| a folder holding no photo | 14 ms | |
| keyword, 3,970 photos / 54,634 photos | 26 / 125 ms | 29 / 100 ms |
| a person (232) / a year (2,196) / a month | 19 / 9 / 6 ms | |

The statements alone are 0.2 to 9 ms except the 54,634-photo keyword (73 ms: the sort of its ids). The navigator: folders 19 ms (2,699),
keywords 138 to 179 ms (700 nodes, 68,466 photos: the pass in Python is most of it), people 12 ms, dates 33 ms. Plans (the exact statements,
`tests/test_library_view_plans.py` asserts them for each source on a library made the same way): all, a year, a month `SEARCH ... COVERING INDEX
idx_photos_taken` / `idx_photos_year` with no sort; a folder with subfolders `SEARCH p USING INDEX idx_photos_path_nocase (path>? AND path<?)`
and a temp b-tree for the order (checked on photo_index itself, read-only: the same lines); a folder alone `SEARCH folders ... (path=?)`, `SEARCH pf USING
COVERING INDEX idx_photo_folder_folder (folder_id=?)`, `SEARCH p USING INTEGER PRIMARY KEY`; a keyword `SEARCH tag_taxonomy` twice (`tag=?`, `tag>? AND tag<?`),
`SEARCH photo_tags USING COVERING INDEX idx_photo_tags_tag (tag_id=?)`, `SEARCH p USING INTEGER PRIMARY KEY`; a person `SEARCH photo_people USING INDEX
idx_photo_people_name (name=?)`; the cards `SEARCH photos USING INTEGER PRIMARY KEY`. The only scans are of a covering index: the totals of all
(`SCAN photos USING COVERING INDEX`, 2.4 ms), the person's spellings (400 names), and the navigator's passes. **Thumbnails**, 500 photos of 3000 x 2000 and 2.9 MB
(files on a local disk): first ask 47 ms each (23.7 s for 500; the per-request path the folder view still uses is 36 ms each), a cached ask 4.4 to
5.1 ms each (2.5 s for 500, nearly all of it SQLite opening a connection to find the row and the path), a revalidation `304` 5.2 ms, `warm --apply`
four at a time 14 ms each (7.1 s), a dry run when everything is cached 0.09 ms a photo. My test pictures are noise and made 6.3 KB entries; the owner's
estimate is 20 to 40 KB.

**How it fails**, each a test: interrupted part-way (a write that fails after its temporary file leaves no entry and no temp, an old temp is swept);
two at once (two requests make one entry; six writers leave one whole file); a read that fails (a file unreachable is a 503, a damaged one a
placeholder, a cache that cannot be written still answers, a library behind or an unplaced root is a sentence); a library moved to another root place; two
libraries holding one folder or the same ids; keyset pages while photos are added, deleted, re-dated, and while another thread writes; dates in the
future, before 1970, with a zone, with no day; ties; `%`, `_` and a quote in a folder, a keyword and a name; a forged, huge or corrupt token; a limit of
0, below 0, not a number, or huge.

**What 9b must know.** A card's `thumb` is for `api.image()`, which puts the library in front. `next` is the only way on (opaque; a `null` is the end) and
`total` comes with every page; ask 200 or fewer for a scrolling grid -- a page of 500 cards is about 150 KB of JSON. A photo is named by `id` in
the view and by `path` (native) in every write route today; the card carries both; identity by id for the writes is not built. A rotate changes the file's
stamp: refetch the card (its `thumb` has a new `v`) or the picture is the browser's old one for a year. A folder is named in the navigator by its `path`,
which is what `view` takes, never an id; a keyword by its `tag`, a person by `name`, a year by `year` and a month by `month` ("2024-06"), as `dates` gives
them. A month and a year do not always sum: `other` says by how many. `damaged` marks a photo to show as such; its thumbnail is the placeholder and says so
(`X-TagPup-Thumb`). A photo whose file is gone is answered the last picture kept or a `404` sentence: show it, not editable (9c). Counts are read at each
call, so the navigator is asked again after an edit. **Not built**: thumbnails made when a photo is indexed or changes, a bound on the cache, a
person by id.

### Phase 9b-1: the windowed grid, and the folder view on it *(built 2026-10-02; branch `arch/phase-9b1-grid`)*
The page only: no route, no migration. A folder opens as it did; what changes is that only the cards on screen exist.
`web/tagpup/vgrid.js` owns the windowing; `grid.js` is what a card is and what selecting does; `selected.js` owns the selection.

- **The grid** (`createVGrid`). A *source* -- `count()`, `recordAt(i)`, and optionally `indexOfKey(key)` (else a scan) --
  shown by `buildCard(record, i)` and `cardKey(record)` in the stylesheet's own CSS grid: the rows in view and two either
  side are in the DOM, and the rows before and after are the grid's padding (`--vgrid-before` / `--vgrid-after`,
  `style.css`), so the scroll bar is right for N cards. The columns are *read* from the browser (`gridTemplateColumns`
  of the grid, so a size class, a dragged sidebar and browser zoom stay the stylesheet's), and the height of a row from a
  rendered card (`getBoundingClientRect`, not `offsetHeight`, since a zoom makes it fractional), measured again when the
  grid's width or a size class changes, never assumed. Rows are then fixed (`grid-auto-rows`) at the measured height, so
  the arithmetic is exact. The scroller is `#folder-view-main` (the grid sits below the folder's header in it);
  `overflow-anchor: none` there, since the padding changes under the browser's feet. **Nodes are reused, not pooled**: a
  card whose key stays in the window is the same element, one that left is dropped; a pool would have needed a `bind()` for
  every card builder and measured nothing worth it (35 cards, 350 nodes drawn at most 500 while scrolling the whole
  folder, a frame costing a row's cards). `refresh()` after a data change draws the window again, keeping the scroll
  position; `reset()` goes to the top; `relayout()` after a size change keeps the row at the top of the view at the top
  (the same photos stay near the top of the view); `scrollToIndex(i, 'nearest' | 'start')`, `indexOfKey(key)`, `eachCard(fn)`.
  Recomputed on `scroll` (once a frame), on `ResizeObserver` (the grid's width: read the columns again; the scroller's
  height: redraw) and when asked. `destroy()` takes the listeners off.
- **Measured through one function**, `measure({grid, scroller, card})` (`measureDom` by default; `view.setMeasure(fn)`):
  jsdom has no layout, so a test gives numbers (`viewport`, `scrollTop`, `gridTop`, `columns`, `cardHeight`, `rowGap`,
  `cardWidth`, `columnGap`). **With no layout** (jsdom, a hidden grid: `viewport` or `columns` 0) the first 120 records
  are drawn whole and every picture at once, which is what the older page tests see. The page tests reach the grid through
  `pageExports(window, 'web/tagpup/state.js').state.grid` (`tests/frontend/harness.mjs`, tests only).
- **Pictures.** A card's `<img>` carries `data-src`; the grid sets `src` for a card that has stayed in the view's rows
  for 120 ms (one timer; when it fires it looks at where the view is *now*, since a slow frame is not a pause), and for the
  row either side only once the view has stood still that long. A card that leaves the window while its picture is
  loading has `src` taken off (the request is cancelled). A card drawn again by `refresh()` shows the picture it had at
  once. A picture that failed to load is not asked for again by that card. A damaged photo has no `<img>`; `markCard`
  (`damaged.js`) works on the cards there are, and a card built later is marked as it is built.
- **A title being typed** (`isBusy`): the card is kept as it is by `refresh()`, and when it scrolls out of the window it
  is not removed (that moves focus away, and a blur is a save) but laid, absolutely, where it would have been, at the
  grid's edge, where it cannot be seen and takes no room; it is the same element when it returns. Once the save is under
  way (`data-saving`) it is an ordinary card again and the redraw after the save shows the new title.
- **Selection by photo, not by card** (`selected.js`). `state.selectedThumbnails` is unchanged for every other module (an
  array of paths in pick order, copied with `slice()` by the bulk edits); `state.selectedKeys` is the same photos by
  `pathKey`, a Set, for O(1) membership. `selected.js` is the one writer of both (`setSelection`, `addToSelection`,
  `removeFromSelection`, `clearSelection`, `renameInSelection`; a test fails any other module that assigns, pushes or
  splices). A Shift-click range is computed over the source's order (`state.shownPhotos`, `state.shownIndex`: key to
  index), so it spans cards that are not in the DOM; Select all / Invert / Select none are one pass over the folder's photos
  and then `eachCard` marks the cards that exist (no card is built again). A bulk write receives `selectedThumbnails`,
  built once. The selection panel's tally no longer searches the array once per photo.
- **A photo renamed by saving its title** keeps its selection under the new path (it kept the old one), and its card is
  built again under the new key. "Extend selection to here" with nothing selected no longer throws.
- **The place in the list.** `renderThumbnails()` is `refresh()` for the same folder and filter, `reset()` for another folder
  or another filter text (`state.shownSource`). A photo open and then closed (the grid is hidden, which loses its scroll
  offset in the browser) comes back at the same place: a hidden grid keeps the window it had, builds no card and asks for no
  picture however often the photo view saves, and remembers where the view was and puts it back when it has a layout again
  (also when it was shown again before any render saw it hidden). The selection belongs to its folder: another folder starts
  with none, the same folder scanned again keeps what is still in it; Select all and Invert take what the filter shows. A source that shrinks while scrolled past its end shows its end.
- **What 9b-2 must know.** A library source implements `count()` / `recordAt(i)` / `indexOfKey(key)` over the pages it has
  fetched; `recordAt(i)` for a record not fetched yet must return a placeholder record the card builder can draw (its key
  stable, e.g. `'#' + i`) and ask for the page; when the page arrives call `refresh()`. The folder source builds
  `state.shownIndex` once per `renderThumbnails()` (O(N) with `pathKey`; for 20,000 it is the cost to watch); a library
  source should not do that per refresh. `cardKey` is the photo's id there, not its path, or a rename rebuilds the card.
  Selection is by path today (`selected.js`): 9d's selection that spans folders is by id, and `selected.js` is where that
  changes. The sidebar list (`renderFileList`) still builds a row for every photo; it is not a grid and 9c's navigator
  replaces it, but at 20,000 photos it is the next cost. Tab visits only the cards that exist; both are known limits,
  left to 9c.
- **Measured** with `scripts/measure_grid.py --run` (`--code-root <a `git archive` of the commit before>` for the baseline;
  without `--run` it prints what it would do). Its sandbox is `scripts/sandbox.py`'s, which `measure_identify_faces.py` and
  `measure_suggest_folder.py` share: a copy of photo_index (2.6 GB) in the temp directory under its own `TAGPUP_HOME`, its
  root placed at an empty sandbox folder, a free port, 759 synthetic JPEGs generated there (no real photo is read), headless
  Chromium 1600x1000, deleted afterwards; a fresh browser for each open; the trunk's code is the baseline, run the same
  way, two runs; an empty page's frame is 16.7 ms here). The click is the
  person's: open the folder, scroll it, select. Run to run the numbers move by up to 40%; the direction did not.

  | the click (759-photo folder) | trunk | this branch |
  |---|---|---|
  | folder opens: scan reply to cards painted, main thread idle | 128-168 ms | 55-57 ms |
  | longest main-thread task of the open | 80-106 ms | none over 50 ms |
  | cards / DOM nodes after opening | 759 / 7,590 | 35 / 350 |
  | scroll top to bottom in 3 s: DOM nodes at the peak | 7,590 | 500 |
  | the same: longest task, frames over 100 ms | none, 0-1 | none, 0-1 |
  | the same: frame interval, 95th percentile | 50-83 ms | 50 ms |
  | the same: pictures requested | 669 on the first sweep (native lazy loading), then none (kept in place) | 79-224, of which 25-119 from the network (the committed script, last) |
  | Select all / Invert / Select none: in the handler | 40-62 / 60-101 / 28-48 ms | 0.9-1.1 / 3.1-3.3 / 0.2 ms |
  | the same, until painted and the main thread runs | 84-197 ms | 60-78 ms |

  | 20,000 synthetic records through the folder view | trunk | this branch |
  |---|---|---|
  | render: in the handler / until painted and idle / longest task | 281-616 ms / 2.0-4.3 s / 1.2-2.6 s | 9 ms / 57-59 ms / none |
  | cards / DOM nodes | 20,000 / 200,000 | 35 / 350 |
  | scroll top to bottom in 10 s: peak nodes, longest task, frames over 100 ms | 200,000, 249-279 ms, 38-48 | 500, none, 0 |
  | the same: pictures requested | 4,132-5,523 | 0 |
  | Select all / Invert / Select none: until painted and idle | 6.0-7.8 / 4.8-5.6 / 3.3-4.0 s | 68-80 / 67-78 / 62-67 ms |

  What the numbers do **not** show: on the 759-photo folder, scrolling is the same 50 ms a frame before and after
  (headless Chromium, software compositing; an empty page runs at 16.7 ms), so a person scrolling a folder of that size
  will not feel a difference there, and the folder opens in about half the time (a tenth of a second or less either way).
  The gain is the bound: opening, selecting and scrolling cost the window, not the folder, and 20,000 photos are possible.
- **Tests.** `tests/frontend/vgrid.test.mjs` (the grid alone: the window, padding arithmetic, reuse, order, an editor kept,
  pictures asked for and cancelled, a size change, a width change, hidden and shown, the source interface),
  `grid-window.test.mjs` (the page on a layout it is given: the window, a Shift-range across unrendered cards, Select all
  of 400 and the bulk write that follows, a card's menu after it was recycled, a title being typed while scrolling away,
  a rename, the filter, a photo opened and closed, two folders, a size change, a damaged photo scrolled to, one owner of the
  selection), `selection-follows-the-photo.test.mjs` (the two fixes, which failed on the trunk).

### Phase 9b-2: a library source for the grid *(built 2026-10-03; branch `arch/phase-9b2-library`)*
The page, and three routes under it. The grid of 9b-1 shows a view of the library as it shows a folder: the whole library, a year,
a month, a keyword and everything under it, a person, or a folder and its subfolders, from the database, in the same grid, details
panel and selection. Nothing is migrated and nothing of a folder's machinery runs while one is open.

- **Three routes** (specified in SPEC_TAGPUP_GUI; loopback only, through the Roots gate and ingress, a library not at migrations 19
  and 20 answered the same sentence as the others). `GET /api/library/ids?kind=&value=|folder=&recursive=` answers `{source, total,
  ids, complete}`: the source's whole id list in `view`'s order (Date Taken, then id, the undated after by id), **one read of ids
  alone**, one statement for the dated photos and one for the undated, each an index-ordered read (`tagpup.store.library_view.all_ids`;
  the plans are `view`'s without the keyset, `tests/test_library_view_plans.py`), cut at 200,000 with the real `total` and `complete:
  false`. `GET /api/library/cards?ids=1,2,3` answers `{cards}` for at most 200 ids, the cards 9a-2 builds, in the order asked, each
  once, an id the library has no photo of simply absent. `GET /api/library/photo?id=` answers `{photo}`, the record a folder's scan
  gives a photo (`services.photos.page_record`: tags, people, title, mtime, size, year, taken, raw metadata) read from the library's
  row, no file touched, and `id`: **this third route was not in the brief**; the details panel and every edit work on such a record by
  path, a card (id, name, path, taken, damaged, thumb) is not one, and the other way to a photo's tags was `/api/folder/scan` of its
  whole folder, which walks the disk. `/api/library/view` is unchanged and unused by the page: the keyset cannot jump to the middle of
  68,000 photos.
- **The address is the view**: `?view=<all|folder|keyword|person|year|month>&value=<v>[&recursive=1]` (a folder by its path, a
  keyword by its tag, a person by name, a year `2024`, a month `2024-06`; `value` absent for `all`). `library-source.js`
  (`viewSpecFromSearch`, `viewSearch`) reads and writes it: an unknown kind, a missing value, a year or month that is not one, a value
  over 1,000 characters is an empty view with a sentence and no request. Opening a view **pushes** an entry (with the scroll offset of
  the one left saved in its history state), so Back and Forward move between views and between a view and the folder, and a bookmark
  opens one; a view opened from Back or Forward writes nothing. A `?view` at start wins over a `?path` (kept as the folder Back returns
  to). Choosing another dog park goes to that library without the `view`, `value` and `recursive` of the address
  (`common/library.js`, `goToLibrary`): ids belong to a library.
- **The source** (`web/tagpup/library-source.js`; every bit of its state is `state.library`, state.js). It implements the grid's three
  questions: `count()` is the ids; `recordAt(i)` is the card if it is held, else **a placeholder** of the same key (`#<id>`), and asks
  for the cards around the window; `indexOfKey` is the position of an id. Cards are **keyed by the photo's id**, so a rename redraws
  nothing. The request for cards is made **once the window has stood still for 100 ms** (the first window of a view at once), at most
  every 100 ms x 5 under a scroll that never stops (so about two a second), for the window and half a window either side, in batches
  of 200, **two at a time**; a batch for a part of the view the window has left is **cancelled** (the request is aborted, nothing is
  painted from it), and the cards that arrive are drawn by `vgrid.patch(keys)` (new: the cards of these keys are drawn again from the
  source, the rest left as they are). **The cards held are the 2,000 last used**; the far ones are let go. A batch that fails is tried
  once more -- after 2 s or at the next scroll, whichever is first -- and its placeholders then say "Could not load" and are not
  asked for again until Refresh view: no loop. A photo whose card the library does not return (deleted since the order was read) is
  dropped from the order and the total, quietly. The order is **one array** (`lib.ids`, the response's own, 68,000 numbers); `indexOf`
  finds a position on a click, not per frame.
- **The view** (`web/tagpup/library-view.js`): opening and closing, the strip above the grid (the source, its total, "Opening...",
  "Refreshing...", "Selecting N of M...", what went wrong; **Refresh view** and **Back to folder view**), the address, and the gear's two
  items. **Browse the whole library** opens `all`; **Library view of this folder** (greyed with no folder open) opens the open folder
  and its subfolders. **Refresh view** (and the sidebar's Refresh) asks for the order again, forgets the cards, and scrolls back to the
  photo that was first in the view; an order that cannot be read leaves the view as it was and says so. The order is **not recomputed
  by an edit**: a photo edited so that it would sit elsewhere, or no longer be in a keyword view, keeps its place until the view is
  refreshed, and its card is updated in place (`applyEditedRecords`: path, name, title, date from the record the details panel holds).
- **The folder's machinery is idle while a view is open** (`quietTheFolder`): the scan under way is aborted, `scannedFolder` is null,
  the suggestion and index pollers stop, nothing asks `/api/folder/membership` or `/api/folder/damaged` (every photo of a view is held;
  a card says whether its photo is damaged), the add-folder question cannot open, Suggest, Auto-apply, Smart Rename and Camera Time
  Shift are off (the last two work on a folder; across folders they arrive with 9d, and the brief's "keep" would have sent the server
  a folder it does not have), the filter is off with a tooltip, **no list is built** (`renderFileList` returns at once: "68,472 photos
  in this view" and "N of M" for the open photo), nothing is kept in this browser's folder cache, and a folder opened from the box or
  by Back closes the view and reads the folder as ever.
- **A card is its photo's cache thumbnail** (`/api/photo-thumb` by id with its `v`, through `api.image`), name, date written whole
  (a view spans years), a damaged or incomplete mark from the card's own flag. The card has no title: **the 9a-2 card read has no
  `captions` (a test holds it), so a library card shows the file name**, which Smart Rename makes of the caption; a title edited in
  the details panel shows on its card at once. Its title cannot be edited on the card (the card has no tags to send with it).
- **Selecting stays by path** (`selected.js`, unchanged). A click is the card's path; a Shift-range or Select all over cards not held
  **fetches those cards for their paths** (3 requests of 200 at a time, "Selecting N of M..." in the strip, replaced by a newer
  selection, abandoned when the view closes, all or nothing), and **Select all / Invert of more than 5,000 photos asks "Select all
  N photos?" first**. The selection panel does not tally tags and people of a selection it cannot count ("Not tallied for a library
  view"); the bulk editor writes by path through the existing routes, a body of 68,000 paths for a Select all.
- **The details panel** opens a card by id: the record is read (`/api/library/photo`) and is the page's only full record, in
  `state.folderPhotos` as a folder's photos are (so title, tags, people, date, rotate, delete work as ever, by path). Previous and
  Next (arrow keys, swipe) and "N of M" follow the view's order by `ids`; two quick presses go two photos on. A photo the library no
  longer has is dropped from the view. A rotate asks for its card again (its thumbnail's `v` changed); a delete takes its card away,
  drops the total and opens the next photo. Same-as-previous (Ctrl+D) is off: the photo before is not in the page.
- **The record is as fresh as a folder scan's, and a write names the file it was built from** *(review of 9b-2, findings #533)*. The panel
  sends a photo's whole tag list on a tag add, a Date Taken edit and carry forward, and the server writes it as given; a record built from
  the library's row (a view) or from this browser's half-hour cache of a scan (a folder) says less than the file when another program
  added a keyword after the index read it, or when the row is Suggest's (a path and no stamp): the next save removed what the file held.
  So: (1) `/api/library/photo` takes the file's stamp and sets it against the row's (`store.photos.describes`: size equal, time within
  0.1 s); where they agree the row is the record, where they do not -- or the row has no stamp -- the file is read with ExifTool as the
  folder scan reads it (`services.photos.read_file`); the record's `mtime` and `size` are the file's; a file that is gone is the row with
  `missing`. (2) Every metadata write the panel makes -- a save of tags, title or people, the Date Taken edit, carry forward, Apply a
  suggestion, the card's title in a folder -- sends `stamp: {mtime, size}` of its record, and `tagging.save_photo` (under the lock of
  changes to files, before it reads) refuses with `409`, `changed_on_disk` and "This photo changed on disk since you opened it: reload it
  first." when the file's stamp differs or the file is gone, writing nothing; held and file-only (Just look) photos alike, and a call
  with no stamp (the CLI, the MCP) as ever. The page (`edits.js photoChangedOnDisk`, `photo.js reloadChangedPhoto`) reads the photo again
  -- a view's by id, a folder's by scanning the folder (`scanFolder(true, {keepTyped})`, which asks nothing about what was typed: it stays
  in its fields) -- and says so in the status line and the alert; nothing is merged and nothing overwritten. A successful write's reply
  carries the new `mtime` and `size` (a rename by the caption: of the file under its new name) and the record takes them, so the next save
  passes; bulk tags reports each photo's (`stamps`; it adds and removes against the file's own tags, so it needs no precondition),
  rotate reports `size` with `mtime`. Auto-apply and Smart Rename read the folder again, so their records are fresh.
- **A bulk write is asked about and capped** *(findings #535)*. A selection over 200 photos asks "Add Trips/Coast to 3,412 photos?" (the
  write named, `selection.js confirmBulkWrite`) before any bulk tag, person or removal; the server refuses a request of more than 5,000
  photos with `400` "Narrow the selection: bulk edits over 5000 photos arrive with the editing stage" -- bulk tags and Smart Rename; the
  page says the same without sending. A job with progress, cancel and undo of its own is 9d's.
- **The tag tree's counts are one pass of `photo_tags`** *(findings #534)*. `/api/taxonomy/tree` parsed every photo's tags JSON for
  `usage_count` (`store.photos.tag_usage`): 350 ms on a synthetic library of photo_index's scale (68,466 photos, 159,651 `photo_tags`
  rows, 931 nodes), which held the Python process at page start so that `/api/library/ids` and `/api/tags` waited behind it. It now
  reads `photo_tags` (`library_view.keyword_counts`, whose per-photo grouping SQLite does: Python sees the distinct tag sets, not 68,000
  photos): **68 ms**, the same counts node for node (`tests/test_tag_usage_from_photo_tags.py`); a library without the derived tables
  is counted from the JSON as before. With the page's first three requests in flight together: tree 785 -> 122 ms, ids 830 -> 170 ms,
  tags 920 -> 260 ms (`/api/tags` is itself 260 ms there: not looked at). So the "85-330 ms in the page" of the ids request was mostly
  the tree route holding the process, not the route (30 ms alone). Re-measured on the sandbox copy with `measure_library_view.py` on a
  machine eight times slower than the earlier runs (copying the library took 219 s, not 15), so its absolute numbers are not comparable;
  navigation to the ids reply of `?view=all` was 385 ms (304-529) against 458 ms (448-591) before.
- **A reply about the folder after a view opened shows nothing** *(findings #536)*: an index-status or suggest-status reply that lands
  once a view is open returns at once (it unhid the progress containers and set `folderSuggestions` over the view). A photo opened from a
  view carries what the library records of its damage (`damaged`, `damage` in the record), so the panel shows the note and asks for no
  picture of a file recorded unreadable; `/api/library/photo` reads `id` as digits only.
- **Measured** with `scripts/measure_library_view.py --run` (plan without `--run`; `--code-root <a git archive of the trunk>` for the
  baseline, which has no library view, so only the folder view is measured). The sandbox is `scripts/sandbox.py`'s: a copy of
  photo_index (68,472 photos, 2.6 GB; the server migrated it to 20 as it opened it), a free port, headless Chromium 1600 x 1000, a fresh
  browser for each open, the thumbnail requests answered by the browser's own routing with a 1-pixel picture (the sandbox has no
  photos; the requests are counted, not decoded), deleted afterwards. Three rounds, medians; this machine was not quiet (the trunk's
  759-photo folder opens in 112 ms here, 55 in 9b-1's table). There is **no library view on the trunk to baseline against**, so the
  library numbers are read against the folder view's of 9b-1 and of the trunk in the same session (last two rows).

  | the click (68,472 photos) | this branch (median of 3; range) |
  |---|---|
  | (a) open `?view=all`: the ids reply to the first window of cards painted, main thread idle | 41 ms (40-55) |
  | the same: thumbnails first asked for (the 120 ms rule) / navigation to painted / navigation to the ids reply | +152 ms / 504 ms (498-631) / 458 ms (448-591) |
  | the ids request in the page (390 KB, 68,472 ids): the route in-process, 30 ms (23 SQLite, 4 JSON) | 85-330 ms before the tree fix (it waited behind `/api/taxonomy/tree`); a request of one card is 5-6 ms |
  | (b) open the keyword node of 41,448 photos: the ids reply to painted, idle (ids 236 KB) | 39 ms (38-45); navigation to painted 438 ms; thumbnails asked +151 ms |
  | (c) scroll `all` top to bottom in 10 s (2.87 million px): frames / longest / over 33 ms / over 100 ms | 601 at 16.8 ms at most / no long task / 0 / 0 |
  | the same: DOM nodes at the peak / JS heap at the peak (4.4 MB after opening) | 500 / 4.8 MB |
  | the same: requests made: ids / cards / thumbnails | 0 / 15 (1.5 a second) / 27 |
  | (d) the scroll bar dragged to the middle, a third, two thirds: until the cards in view are real and painted | 208 ms (48-273; the 48 is a place already drawn); 1 card request; no long task |
  | (e) the library view of the largest library folder (759): the ids reply to painted, idle | 56 ms |
  | (e) the 759-photo folder view: scan reply to painted, idle / longest task / scroll 3 s p95 frame, frames over 100 ms | 93 ms / 50 ms / 50.1 ms, 0 (the trunk: 112 ms / 60 ms / 50.1 ms, 0) |

  What the numbers do **not** show: the photos are not in the sandbox (the thumbnail requests were answered by the browser with a
  1-pixel picture, so the server's 5 ms a cached thumbnail and 47 ms a first one, 9a-2, are not in them), a person's browser is not
  headless, and the ids request was 3 to 10 times its handler on this machine: that was the tag tree's route holding the process at page
  start (see the tree's counts above), not the route. `EXPLAIN QUERY PLAN` of the id lists on the copy: `all`, a year and a month
  `SEARCH ... COVERING INDEX idx_photos_taken` / `idx_photos_year` with no sort; a folder with subfolders `SEARCH p USING INDEX
  idx_photos_path_nocase (path>? AND path<?)` and a temp b-tree for the order; the keyword two seeks of the tree, `photo_tags` by
  `tag_id`, `p` by primary key and a temp b-tree (104-124 ms for 41,448 ids in SQLite alone, as 9a-2's page of it); no `SCAN` of photos
  (`tests/test_library_view_plans.py`).
- **How it fails**, each a test (`tests/frontend/library-view.test.mjs`, `tests/test_library_ids_routes.py`): a scroll through thirty
  windows asks for the one it stopped at; a batch for a window left is cancelled and not painted; the scroll bar dragged to the end
  (placeholders, then the last photos); cards bounded; a failed batch tried once more and then not at all; a partial answer dropped
  quietly; two quick changes of view (the first's order and cards never painted into the second); Back and Forward between a folder and
  a view and between two views (the offset restored); a bad address, a keyword with no node, a library behind (the sentence), an
  unplaced root (the banner); a source of 0 and of 1; the panel open while the view refreshes; a selection through a refresh; Select all of
  68,000 asking first and selecting every path once; right-click on a placeholder naming no photo; damaged and incomplete cards;
  thumbnails asked only after 120 ms in view; ids that change under the page (the total as it was until Refresh view); a scan still
  out when a view opens landing nowhere; another dog park dropping the view.
- **What 9c must know.** A view is opened by `openLibraryView({kind, value, recursive})` (`library-view.js`); the navigator's Folders,
  Keywords, People and Dates call it, and `/api/library/navigator` is asked again after an edit (a count is read at each call). The
  strip is where its "header says which source" lives; the sidebar is empty in a view (the navigator takes its place). A card carries
  `damaged` and `damage` only: 9c's staleness marks (size and modified time, "missing") are new card fields, and a card whose photo is
  gone shows a broken picture today (the thumbnail route answers a 404 sentence). `state.libraryReturn` is the folder Back returns to.
  The folder cache and the folder's scroll are not restored by Back (a folder is read again from the cache of the scan, at the top).
- **What 9d builds on.** `selected.js` is by path because every write route is; a card has its `id`, and `selectInLibrary` is the one
  place that turns a range of ids into paths -- with id-based writes it disappears and Select all of 68,000 is instant. The selection
  panel's tallies need a server answer for a selection by ids. Smart Rename and Camera Time Shift need a form across folders.
- **Known limits.** The cards of a view show file names, not captions. The order is not live. A body of 68,000 paths is a long request.
  The first ask of a keyword view of 41,000 photos sorts its ids (100 ms in SQLite); `all`, a year and a month read an index in order.
  The ids of a source over 200,000 photos are cut there. Tab visits only the cards that exist (as in 9b-1).

### Identity by id *(owner, 2026-10-02; `photo_tags` built in 9a-1, the rest design)*
Today a person is a leaf name in `faces.name`, `photo_people.name` and the suggester, and a tag
is a path (`People/<name>`) in the files and in `photos.tags`; CLAUDE.md's rule exists because
every site converting between the two by hand shipped a bug. `tag_taxonomy` already gives every
node an integer id and a parent id, and a person is a node with `has_face` set. The design:
- **Files, pages and the API keep names and paths.** XMP keywords are text and the files are the
  source of truth; nothing outside the store learns an id. The conversion lives at the store
  boundary, as root-relative paths do (`to_row` / `from_row`), in one module, with the same
  single-owner test.
- **`photo_tags(photo_id, tag_id)`** (9a-1, built 2026-10-02) and, in a later migration, **`photo_people`
  and `faces` naming a person by `tag_id`** (a column beside `name` first, filled and checked,
  then the name column read from the taxonomy). That later migration touches the 225,000 faces
  of photo_index and every identity path, so it follows 9b-9e and is asked about before it runs.
- **What an id fixes:** two people of one name filed in different branches (the ambiguous
  person path, #27) are two nodes; renaming a person is one taxonomy change in the database,
  and merging two is one id merge (the files still take their keyword rewrites, as now);
  counts and the navigator key on the id and survive a rename. Storage is a minor gain: the
  database is 2.55 GB, nearly all crops, vectors and metadata.
- **What must hold:** ids are stable. The taxonomy lives only in the database
  (`*_taxonomy.json` is an export), so a re-import or a rebuild from files must find a node by
  its path and keep its id, and add a node, never reuse an id, for a tag a file holds that the
  tree lacks. A node deleted in the tree leaves no `photo_tags` row naming it (cascade), and a
  file still holding its keyword makes the node again with a new id on the next read.
- **How it fails, to be tested before it is built:** a tag in a file the tree lacks; a rename of
  a person or branch while an index run or Suggest is running; two libraries sharing one name
  with different ids; the tree edited in TagTuner while a bulk tag write is under way; an
  undo that replays a name recorded before the change; a restore of a snapshot taken before ids
  were used; a backfill read of 225,000 faces (one lookup per name, not per row).

### Ideas taken from Windows Live Photo Gallery's database *(2026-10-02)*
The owner's WLPG index (`Pictures.pd6`, a SQL Server Compact 3.1 file: 73,184 files, 836
hierarchical tags, 159,325 tag uses, a 1.3-million-row word index, 17,486 face regions) was
read as a design reference only; nothing is imported from it and tags and people are not
compared. What is worth lifting, in the order it would be decided:
- **A `folders` table with parent ids and a membership flag** (phase 9a, with the derived
  folders table already planned): WLPG keeps volume, folder tree (parent id, "is a root",
  "in the library" or not) and file name apart, so renaming or moving a folder is one row and
  a folder's counts are a join. TagPup's `photos.path` repeats the whole path in 68,466 rows.
  A `folder_id` on `photos` is the cheap form; decide when 9a is designed, not before, because
  it is a second migration of every photo row beside the roots one. *Decided in 9a-1: no
  `folder_id` on `photos`; `folders` and `photo_folder` are derived tables, with no second
  migration of the photo rows, and a flag for "in the library" is not needed (a folder has a row
  because a photo is in it).*
- **Derived metadata columns**, as `taken` and `year` already are (phase 9a): rating, camera
  make and model, dimensions, GPS. WLPG keeps all of them as columns and the 2011 Find tab
  filtered on them; ours sit inside `raw_metadata` JSON, so a filter reads every row. Rebuilt
  from the file's metadata, never edited. *Built in 9a-1 as `photo_meta`; its width and height are
  empty until the indexer reads the size, which is the owner's to decide.*
- **Full-text search by SQLite FTS5** (phase 9e): WLPG built its own word index over file name,
  tags, title, caption and author. FTS5 over the same fields does that without a hand-made
  index, and the words-AND, tags-OR behaviour of the owner's notes sits on top of it.
- **A content signature per file** (small, can come before phase 9): WLPG stores one for every
  file (`ObjectSig`) and pairs moves and renames by it. Sync pairs by (name, size) and Verify
  can only call a copy "differs" when the times differ; a cheap signature (size plus a hash of
  the first and last blocks, read when the file is read anyway) tells a moved or re-timed copy
  from a changed file, and is the fallback phase 10's duplicate review needs.
- **Volume identity** (small): WLPG recognises a drive by its serial number and type, so a
  portable drive on another letter is the same drive. The machine's map could record a
  removable root's volume serial, and Verify say "this is not the drive that held it".
- **Cached usage counts** (only if measured slow): WLPG stores a count per tag and per person.
  The navigator's counts (9c) are one indexed query each; keep the cache out unless a measured
  click needs it.
- **Not lifted:** the face tables' top-five suggestions per face (ours are computed from the
  vectors and the pool); a recycle table (the real Recycle Bin is used); a separate people
  table keyed by contact id (identity here is the leaf name, CLAUDE.md); WLPG's own
  database-only flags (flagged, emailed, printed), which no file holds.

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
- **Albums and groups.** An album is a folder *(owner, 2026-09-26)*: an event's photos,
  from whatever source, are copied into one folder under the common root, and that
  folder is the album -- no album entity, nothing a folder view cannot already show. A
  group is the people who may see and add to a folder.
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
| 2026-09-25 | Each library keeps three daily, one weekly and one monthly snapshot, taken under the write lock, checked before an old one is removed, and restorable with the journal's changes since counted *(owner)*. *Amended 2026-09-26: a snapshot is one read transaction and holds no writer back (cf00823); consistent without the lock.* |
| 2026-09-25 | Recurring operations (snapshots, sync, pruning the journal, compacting) are registered in `tagpup.jobs.recurring` with their periods and run by one runner inside any TagPup process; their runs are recorded in the library. No Windows Task Scheduler *(owner)*. |
| 2026-09-25 | The server and the jobs runner run as one process started at login from the Startup folder, installed and removed by `scripts/startup.py`; not a Windows service, which has no desktop for the folder dialog and Explorer and may not reach the owner's drives. Idle models are released *(owner asked for always-on; the login process is the recommendation)*. |
| 2026-09-25 | Input rules have one owner, `tagpup.core.validation`: services refuse on them, the pages check early from the rules the server publishes as data, and shared cases hold any JavaScript twin to the Python rule. Output is escaped by building elements from text; `innerHTML` with a value is refused by a guard *(owner)*. |
| 2026-09-26 | The server answers this PC only -- it binds 127.0.0.1 and ::1, not every interface -- until phase 10 adds logins; `--listen lan` is for then, off by default *(owner)*. The Activity page and its routes also refuse any request not from loopback, as a second guard. |

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
| 7.5. A journal for every bulk edit and migration | done, 2026-09-25 |
| 7.6. Settings in the library, and a gear on each page | done, 2026-09-25 |
| 8. Sync | done, 2026-09-26 (8a jobs, 8b snapshots, 8c sync with library roots, 8d always on with self-update, the folder watcher and idle memory); installing it at login waits for the owner |
| 8.5. Activity | done, 2026-09-26 (the page, runs in the logs, the indexer's own log, bounded reads) |
| 9. Library views | planned for October 2026 *(owner, 2026-09-25)*; design questions open. 9a-1, the derived tables (`photo_tags`, `folders`, `photo_folder`, `photo_meta`; migration 19), and 9a-2, the thumbnail cache, `library_view` and their routes (migration 20), built on branches 2026-10-02, not merged; 9b-1, the windowed grid and the folder view on it (`web/tagpup/vgrid.js`), built on its branch 2026-10-02, not merged |
| 10. Family albums from many sources | idea *(owner, 2026-09-25)*, after phase 9; design questions open |

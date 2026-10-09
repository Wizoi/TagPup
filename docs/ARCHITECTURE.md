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
- `config.ini` read only by `tagpup.config.config_ini`, called only by the one-time stamping of a library's settings (`tagpup.runtime.library_settings`); inside `tagpup/`, only `tagpup.config` finds folders from `__file__`. *Exists:* `tests/test_config_single_owner.py`.
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

## Runtime

- **One server, two apps.** One Flask app, served by Waitress, answers on both ports used today: 8090 for TagPup, 8080 for TagTuner. The library comes from the URL as it does now, and becomes a `Library` object for the request. The Host and Origin check is a `before_request` hook. It listens on this PC only -- 127.0.0.1 and ::1, a socket each (`tagpup.web.app.bind`) -- until phase 10 adds logins; `tagpup_web.py --listen lan` binds every interface, for then.
- **One composition root.** `tagpup.runtime.Runtime` holds what lives as long as the process: the models, built once from the settings it was given and warmed on a thread, and each library's job runners. The web factory and each CLI command are handed one; nothing below them builds a model or reads a setting. What it and the web keep only while used -- models, vectors, New Person's pool, the Identify Faces and folder-scan caches -- is let go after an idle period (`Runtime.idle`, phase 8).
- **Background jobs** (indexing, suggestions, clustering, refresh) run through one job runner per library, with status, cancel and persistence. GPU-heavy work runs in a worker process, as indexing does today through the CLI.
- **One owner of the graphics card** (#750, 2026-10-04). Adding a folder started its index (the CLI's `index`, a child of the server) and Suggest (in the server) on the same 165 photos at once; each loaded its own ViT-H-14 onto the owner's 10 GB card, which sat full at 100% (9,503 of 10,240 MiB) while both stayed at 0 of 165 for 48 minutes: the server's face-model load ran 15:41:53 to 16:29:49 and the indexer's first photo finished at 16:30:07 (from the logs, #758; the first report said "over 3 minutes", when it was looked at). What one process held on the card was not measured (an estimate: 3 to 4 GB). Now one process at a time on the machine has a model on the card. **The turn** (`tagpup.ml.gpu.Card`) is a byte lock on `card.lock` in the user's own folder (`%LOCALAPPDATA%\TagPup\gpu`, or `TAGPUP_GPU_LOCK`; a test run gets one of its own), which the system releases when its process ends however it ends; waiters queue in order (`queue/`, a ticket file each keeps locked, so a dead waiter's is seen and cleared), and the holder says what it does and since when (`card.json`). Threads of one process share its turn. **Per job, not per batch:** a job keeps the turn from its first model use to its end, so no model is reloaded mid-job; per batch would be fairer, but two resident ViT-H-14 sets are what filled the card, and a waiter must hold no model on it -- it loads only once it has the turn. **Who takes it:** a Suggest run when it first uses a model (`Runtime.begin` hands the run views of its models that take the turn on use, `_OnTheCard`) -- a run over photos the index has already read, their vectors kept and their faces' detection recorded, found or not (`faces_detected`, migration 25, #773: a photo with no face had nothing on file and was detected again each time; one indexed before migration 25 is detected once more, then recorded), uses none and waits for no one; having waited, it asks the library again before it embeds (`PhotoEmbeddings.of`, the suggester's faces), since the wait may have been the index writing those very rows; the CLI's `index` before its first photo (`Runtime.gpu_turn`), after the scan and the metadata read, which need no card; each model before it loads (`ClipModel._init_model`, `FaceModel._init_models`), the check for a load no run took a turn for (the CLI's `search` and `suggest`). **The web server keeps its turn** while its models stay loaded between runs (`keep_when_idle`), and gives it up the moment another process waits -- unloading them first (`on_release`) -- or when the idle release lets them go. **The warm-up** is off by default (2026-10-05: the owner's log showed CLIP and the face models on the card 15 s after a start, nobody asking, 9.2 of 10.2 GB in use while Lightroom crawled): the models load when Suggest or indexing first needs them, the first one after a start paying about 15 s; `tagpup_web.py --warm-up` loads them at start, taking the card only if no one would wait (`try_hold`), and with an index or a Suggest holding it or waiting it is skipped. **Let go sooner** (same day): the idle release is 5 minutes (`--release-models-after`, default 5; the release thread looks every minute at most, `max 60 s`), and gives up the turn with the models (`Card.release_if_idle`); the Activity page's **Unload models now** (`POST /api/activity/models/unload`, `Runtime.unload_models_now` through `IdleCaches.release_now`) does the same at once, and refuses, saying so, while a Suggest run holds a model; the server section says what is loaded (`Runtime.models_state`) and since when it was last used. **Suggest of a folder being indexed** -- this process's index queue runs or holds a job for it or a folder above it (`IndexQueue.indexing`) -- waits for that index first, its status saying so ("Waiting for this folder to be indexed first: ..."); an index run from another console is not in that queue, and Suggest then waits for the card the index holds, and reads what it wrote. **Never silently:** a waiting job's progress line is `Waiting for the graphics card (in use by: indexing Regatta (harbour), since 15:41)...` (`tagpup.core.gpu_turns`), on the Activity page and the folder's status, for the index (the indexer prints it; the queue shows its lines) and for Suggest (its status `message`). **Cancel works at once on a waiting job:** Suggest's Cancel (`/api/folder/suggest-cancel`) stops a wait for the index or the card within half a second, and a run under way after the photos in hand, keeping nothing made after the cancel; the index queue's Cancel stops a run still waiting for the card (the indexer watches `TAGPUP_STOP_FILE` while it waits and exits 76, having indexed nothing) -- the indexer decides, so one that took the card that moment carries on, as a run under way always did. **A CPU-only machine** takes no turn: the lock is about the card's memory, and two runs on the CPU share the cores as any two programs do. Not done: two different model sets in one process (two libraries naming different CLIP models, Suggest in both at once) would both load under the one turn; the three live libraries name one set (counted 2026-10-04).
- **The installed copy.** `scripts/install_app.py` copies the code into a version folder under `%LOCALAPPDATA%\TagPup` and writes launchers that run it. `TAGPUP_HOME` names the folder that holds `data/`: the libraries, which hold their own settings, with each library's backups and locks beside them. Updating is a deliberate step, installing again, and the two versions before stay to go back to. The auto-reloader is for development only.
  **A launch replaces a server of another version** (#726, 2026-10-04; without the always-on process, which the owner does not run). Each launcher installs a newer commit, then runs `tagpup_web.py --open`; finding a server on its port, it asks its version (`/api/server`). The same: its page is opened. The always-on process's: left to it. Any other -- older, newer, or one run from a checkout -- is drained (`POST /api/server/drain`: after 15 s with no request, so a save under way is not cut, until it has waited 2 minutes for one; 20 s for the requests in flight; a lease of 20 s, after which a server not ended takes work again) with the token in the server's record (`tagpup.launcher`: every server, once it serves, writes one per port in the user's own folder, `%LOCALAPPDATA%\TagPup\servers`, which another user cannot read, and the server takes the token only from this machine). A Suggest run, an index, a bulk edit or the startup migrations refuse the drain: the launcher's window says what it waits for and that closing it leaves the update to the next launch, opens the running page, and asks every 10 s -- no ceiling, the work being the owner's. Drained, the server is ended (`kill_tree`, only while its record's process id and start time are alive), and the launcher binds the ports before anything else, so of two launches at once one serves and the other opens its page. A server that does not answer `/api/server` three times, 20 s each, is ended the same way -- never the always-on process's, which ends its own; one that answers with no record (a version from before records, another user's) is not touched: the launcher says to close it and opens its page. Every response names the version answering (`X-TagPup-Version`), and a page left open on a replaced server shows a banner asking to reload (`web/common/api.js`); a read (GET, HEAD) that meets the second between the old server's end and the new one's listening -- nothing answering, no update said -- is sent once more after 1.5 s; a write is not, since a connection lost after it was sent may have lost the answer of a write that was done, and shows its error as before. `install_app.py --apply`, run by hand, then hands the running server over itself (`tagpup.launcher.hand_over`, #795, 2026-10-07): a server of an earlier version this installed app runs is made way for exactly as a launch makes way (drained with its record's token; a Suggest run, an index, a bulk edit or the migrations waited out, the install's window saying what for and that Ctrl+C leaves the update to the next launch; ended once drained, or hung, under the same process-id-and-start-time guard), and the version installed is started on the same ports as a process of its own with a hidden console (`tagpup.core.processes`, its output in `data/logs/tagpup_web.installed.log`), opening no tab: a page left open shows the "updated" banner, and a reload reaches the new version. The old server is ended before the new one starts, since they share the ports the pages use and two servers starting on one library would run its migrations at once; so the new version is first checked to start at all (its `tagpup_web.py --help`, which imports every module the server does: about 2 s), and the running server is not disturbed when it does not; when the new one, once started, does not answer within 120 s, it is ended and the old version started again, the install saying so and how to go back (`current.txt` is left as installed). With nothing running it only installs. A server run from a checkout, one of a version this installed folder does not hold, and the always-on process's (which moves its own) are left alone; when the owner chose the always-on process, it is started, if it is not running, rather than a server of the install's. A server the install starts has no window: the next install or launch of another version replaces it. Its ports are bound before anything else is started, in every mode of the server (#805: `--open none` too): a start that loses them -- a launch took them while the install waited out a job -- exits PORTS_TAKEN at once, having begun no migration or folder watcher, and the install says what answers instead (#806); one that lost to a launch of the same version is a success. The wait for the new server is ten times what its `--help` check took (120 s to 600 s), the install saying so. The install refuses to replace a server when `--home`'s `data/` holds no `.db` library (the home defaults to the installer's folder: #808). `--no-restart` installs only, as before: the next launch replaces the server. The launchers' own `--if-changed` install never hands over -- the launcher makes way itself. Installs take turns (`install.lock`, now taken by a hand-run install too), and so do hand-overs (`handover.lock`): a second leaves the server to the first, which starts what `current.txt` names when it starts it. An install never removes a version a server runs from. A sandbox's or a test's server writes its records, and has its Downloads and Recycle Bin, in a home of its own: `tagpup.config.own_home_environment` (TAGPUP_SERVERS, TAGPUP_DOWNLOADS, TAGPUP_RECYCLE_BIN, and TAGPUP_GPU_LOCK), used by `tests/own_home.py` and `scripts/sandbox.py` alike (#796). A test's home has its own card lock; a measurement sandbox takes TAGPUP_GPU_LOCK out again (`sandbox.environment`/`enter`, #810) and shares the owner's line for the card: it never sits on the 10 GB card beside the owner's index, a sandbox waiting makes the owner's idle server yield its models (its next Suggest loads them, about 15 s), and a measurement that loads models measures a cold load. Whether the process a record names still runs is `processes.recorded_alive` (its id and start time), and which addresses are this PC is `tagpup.web.security.LOOPBACK`, one owner each.
- **Always on** (phase 8). `scripts/startup.py install --apply` puts a shortcut in the owner's Startup folder to `TagPup Background.pyw`, a stable launcher beside the others that reads `current.txt`; it runs the supervisor (`tagpup.supervisor`) under `pythonw.exe`, which runs the web server as its child and moves it onto each newer committed version once the server has drained (`tagpup.web.lifecycle`).
- **Logs** go to `data/logs/`, one rotating file per program. Each holds everything the console shows, plus every request slower than a second with its time, and every failed request with its traceback.

## Data model

| Table | Kind | Notes |
|---|---|---|
| `photos` | file copy | `id` INTEGER PRIMARY KEY; `path` UNIQUE, compared without case, native in a library with no roots and `@root/relative` for a file under one of its roots (the same for every other column of paths: `change_files`, `added_folders`, `damaged_files`, the two folder settings, the paths inside `raw_metadata` and `suggestions.raw`); `document_id`; `mtime`, `size`; `keywords`, `captions`, `raw_metadata`, `taken_at`, `orientation` as read from the file; `indexed_at`, NULL until indexed. Every photo the apps have looked at has a row, because faces need one to point at (today the suggester saves faces for photos with no row). |
| `faces` | decision | `id`; `photo_id` → `photos.id`; `box`; `embedding`; `name`; `name_source`; `excluded`; `excluded_reason`. |
| `face_crops` | derived | `face_id` → `faces.id`; the JPEG. Kept out of `faces`, so reading faces never drags 6 KB crops along. |
| `photo_people` | derived | `photo_id`, `name`, `source` (keyword or face); a group tag names nobody. Rebuilt for a photo by one function whenever its keywords, its faces or the tag tree change. Replaces the `photos.people` JSON column. |
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
| `roots` | decision | The library's roots (migration 18; "Roots and machines"): `name`, the share's `address`, `added`. Empty until the owner runs `roots adopt`; opening a library only ever makes the empty table. Written by two changes only: `roots adopt`, in the transaction that converts the rows under the root, and `roots repair-address`, which restores a share address that lost a leading backslash (#914). |

Each change ships as a migration with a dry run and a backup, and `tools/doctor.py` checks the library's invariants before and after. Migrations 1 to 19 are listed in `tagpup.store.schema.MIGRATIONS`, each with its kind (additive, data-changing or destructive) and why; 18, `roots`, is additive and empty, and converts nothing; 19, the derived tables of phase 9a, is additive and fills them from the photos' rows, which it checks before it commits.

## Frontend

- `web/common/` holds ES modules shared by both pages:
  - `api.js`: library-aware URLs and JSON. It replaced the monkeypatched `fetch` and image `src`.
  - `paths.js`, `vocabulary.js` and `library.js` (the picker and the library the browser remembers) with `library-picker.js` and `.css` (the picker's listbox, #909). Only what both pages really share lives here: they have no unsaved-edit handling or status line in common.
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

Added by the batch of services findings (2026-10-08): `tagpup/core/byte_lock.py` is the one lock on a byte of a file (the installer's, the hand-over's, the supervisor's and the graphics card's turn: #762; `tests/test_gpu_single_owner.py` fails any other call of `msvcrt.locking` or `fcntl.flock`); `tagpup.core.processes.python_command_lines` lists the command line of every running python, which the installer reads before it removes a version folder (#756; unlistable means nothing is removed); `tagpup/services/duplicate_rows.py` is `tagpup_cli.py dedupe-spelled-rows` (#479), which merges the rows of a file the library holds more than once, only where the file system proves they are one file (volume and file index), as one journaled change; a copy of a file keeps its row. **A caption saved on a Smart-Renamed photo is TWO changes in History** (#298): the field write (`save photo`) and then the rename (`rename after caption`, `file_changes.rename`), each undoable; the journal cannot group two changes, so one Save shows two entries. A journaled change that leaves a photo without a face row clears its `faces_detected` record (`journal._derive`, through `faces_detected.forget_faceless`, #779).

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
- [x] Photo ids. `photos` rebuilt with `id INTEGER PRIMARY KEY` and `path` unique; `faces.photo_id` in place of `faces.photo_path`, and every join by id (joins by path were case-sensitive where lookups were not). A photo the apps see gets a row the first time -- Suggest's faces and embeddings for photos never indexed included (`photos.ensure_row`). A rename is one update of `photos.path`. *Amended 2026-09-28:* only in a folder the library holds, or one added to it -- a row makes its folder the library's, and Suggest in another library's folder made 25 rows in kr-track without asking; adding is asked for (`tagpup.services.libraries.add`, TagPup's "Add this folder to kr-track?"). *Amended 2026-10-02:* the prompt names only the open library -- no other library is opened, read or named -- and Just look is not read-only: what needs no row (a caption, tags and people, Smart Rename, rotate, delete, Date Taken, Time Shift) writes the photo's FILE and nothing of the library's, decided per photo at the time of the write by `libraries.split` (the library's own answer, never a flag the page sends) and done by `tagpup.services.file_only` -- no row, no journal change, nothing derived, no thumbnail, no damaged-photo record, nothing for sync or the watcher; the database is not changed (`tests/test_just_look_edits.py` dumps every table before and after). Suggest, Apply All and carry-forward were refused there until 2026-10-03; they are not now (below). Face naming needs the database and stays refused. What a file-only write gives up: History's undo and the settling of a write a crash cut short (a Smart Rename killed between its two passes leaves `tmp_rename_` names beside the photos); when the folder is added, the indexer reads the files as edited.

  *Analyse-only Suggest, 2026-10-03 (owner: "I thought previously we could do the photo analysis and get the suggestions from the index -- but in this case we just do not want to add it to the index").* In a folder the library does not hold Suggest **only looks**. `tagpup.services.libraries.suggest_how` decides at the start of a run, from the library's own answer and never a flag from the page; the run is then `SuggestionRuns.run_looking` (`tagpup.jobs.suggestions`), over the same photos, workers and steps as the one that saves. **The MEMORY-ONLY rule:** the library is only READ -- its photo vectors (the neighbours), its named faces (a face is compared with them, the likeness rule of `tagpup.core.clustering`), its tag tree and the embeddings it already holds of candidate words -- and *nothing is written to it*: no suggestion, face, crop, vector, candidate-word embedding or photo row (the models are built with `remember=False`: `TagSuggester` records no detected face and saves no tag embedding, `PhotoEmbeddings` keeps no vector), nothing recorded of a photo that does not decode, no thumbnail, no file beside the photos; `tests/test_analyse_only_suggest.py` dumps every table before and after the run and after Apply All: every table's rows are unchanged (the dump reads rows, not the -wal/-shm files). The test does not lean on the refusal of rows for a folder nobody added: with the folder held and the run forced to look, and with a partly held folder whose held photo has no vector, nothing is written either, and each of the three ways a run could keep something (a vector, a face, a candidate word's embedding) was checked by forcing it and watching these tests fail. **What any request of the app does, a looking run included, and is not the run's:** the first open of a library brings its tables up to date (`schema.ensure`), settles a change of photo files a crash left (`journal.settle_once`) and stamps settings on a library that holds none (`settings.of`); the run's own part writes nothing. What it finds is `SuggestionRuns.looks`, in this process: `{folder: {photo: entry}}` in the shape the library keeps, so `suggest-status` and `auto-apply` answer from it in the shape the page already reads (plus `in_memory`, `notes`, `unread`). **Limits:** 2000 photos' results a folder (the rest are said to be left out, `notes`), 3 folders a library (the oldest not under way is dropped), all let go when the library is forgotten or unused for the process's idle period (`tagpup.web.tagpup_routes.LOOKED_SUGGESTIONS` in the idle registry; any request of the page -- a status poll, an Apply, a scan -- counts as use of the library the request is for, each library having its own entry, so an owner reviewing is not stranded and another library's look is not kept alive by it: #558). When the page then finds them gone (Apply All answered "No suggestions found", or a status of idle for a folder it showed in-memory suggestions of) it says "The analysis was let go after a while; run Suggest again", clears what it held, here and in the browser's cache, and enables Suggest. A file-only Smart Rename of a looked-at folder renames what is kept (`SuggestionRuns.renamed`, from the reply's updated paths). The year prompts a held run keeps in the library are kept for a looking run in an in-process, per-library, bounded (5000) cache (`suggester.TextEmbeddingCache`), never persisted, dropped with the library; a process restart, an update, a closed page that nobody comes back to, lose them and the owner runs Suggest again -- nothing was saved to lose. **A folder added while a run is going:** the run finishes in memory (its mode was decided at its start), saves nothing, and its results can be applied (with rows, as any held write); what was kept is dropped where a folder is ADDED -- the add routes, after `tagpup.services.libraries.add` (`suggestions.dropped_by_add`: the folder, and its subfolders as `record_added` adds them, each only if the library holds it now) -- never by the index queue, which a sync or a damaged-photo check also uses (a held folder does not hold its subfolders: #556); and a run that ends in a folder the library holds meanwhile (its indexing finished first) drops what it found (`Work.held_now`). The status then answers from the library and the page enables Suggest, whose next start is the run that saves; a page whose status turns idle says "The folder was added: Suggest again to save its suggestions" when the library holds the folder, "let go after a while" when it does not. The page keeps what it was shown (`state.suggestionsInMemory`, kept in its cache entry) and enables Suggest while it is held, whatever the page already holds. A partly held folder is looked at whole, none of it saved. **Applying** is file-only per photo, as Just look's other edits (`tagging.add_tags` splits held from not held as `change_tags` does): the tag tree is read and no node is made. **A suggested person is filed as a click on their chip files them**, by one rule written once, `tagpup.core.vocabulary.person_tag` (used by `tagging.person_filer`), of which the page's resolveTagOrPerson (web/tagpup/tags.js) is the mirror: where the tree files them once, there; a name it does not hold, under the library's one people root, under `People` when the tree has none; with several people roots, or a name filed twice, left as it was for the page to ask. **And left out when the file already names them by their leaf**, whatever the spelling or path (`vocabulary.same_person`, the page's photoAlreadyHas), read from the file under the write's lock (`tagging.apply_suggestions`, `_change_each`): no second spelling of a person is written (#555). `tests/fixtures/person_filing.json` is the table both sides are fed (`tests/test_person_filing_table.py`, with real ExifTool, held and not held; `tests/frontend/person-filing-table.test.mjs`, a click on the chip), so they cannot drift. Apply All used to write such a name bare, held or not -- two spellings of one person, against the full-path keyword convention (#546); the CLI's `write` of a suggestions file still does. **Face naming** stays off: it creates a person record. A library that cannot answer whether it holds the folder (locked) starts nothing (`503`); an ignored folder itself is still refused. The models' work -- decoding, CLIP, faces -- is the same as the saving run's and is what a real run costs; with stand-in models over 60 photos (one scan, measured 2026-10-03) the run that only looks took 0.13 s and the run that saves 0.93 s, the difference being its writes. Models that cannot be made ready say so and change nothing.

  *Review fixes, 2026-10-02 (#520-#527).* **Delete** (held or not) sends only an existing regular photo FILE to the Recycle Bin (`recycle_bin.problem`: a folder, a `.txt`, a missing path, a path ending in a separator, the 8.3 spelling of a non-photo are each refused, 400, nothing moved; `send_to_recycle_bin` itself raises on them). The Bin is per local volume: a file on a network share (UNC, mapped network drive), a removable drive or a SUBST drive is deleted for good while `SHFileOperation` reports success -- checked by test on a UNC path to local storage (`\\localhost\C$\...`): success reported, file gone, nothing in `$Recycle.Bin`. `recycle_bin.no_bin_reason` says it beforehand and why, judging the path as spelled (a SUBST drive is found by `QueryDosDevice` returning `\??\` where a disk returns `\Device\Harddisk...`; the volume by `GetVolumePathName` so a volume mounted in a folder is judged by its mount point; a `\\?\` prefix is looked through) and again where its links lead (`realpath`: a junction or symlink to a share); only a fixed local disk with a real volume goes to the Bin, and when unsure it answers permanent. The folder's membership answers `permanent_delete` and `permanent_reason` ("on a network share" / "on a removable drive" / "on a substituted (SUBST) drive" / "on a drive without a Recycle Bin"), the page's confirmation says "This file is <reason>: it will be deleted permanently, not moved to the Recycle Bin" before, and the reply (`permanent`, `permanent_reason`, `message`) says what happened after. The file is judged by its long name (`GetLongPathNameW`): `A4413~1.JPG` may be `holiday.jpgold`. A Smart Rename that fails after its held part committed, whatever the exception, answers what did change; if the folder cannot then be read again the cache is dropped and the answer is a plain 500. **Held means rows:** `libraries.split` and the write checks hold a folder with a row directly in it whatever the library's ignored list says (the rows are the library's; the settings promise they stay in step); an ignored folder with no rows stays not held, and Suggest still leaves ignored folders out. **The file-only check is cheap, and at parity with the held path (#528):** a caption, tag, people, Date Taken, time shift or Smart Rename reads only the END of each file (`images.tail_check`) and refuses exactly two things -- an empty file, and a zero-filled tail by `images.zero_tail_of`'s own rule (the same last 64 KiB, the same threshold as the indexer and the held path; a test holds the two to a table of tails) -- plus ExifTool's own failure to read the file. It judges nothing about what follows the picture: a Samsung trailer, an MP4 appended to a motion photo, 0xFF or zero padding below 64 KiB and bytes after a PNG's IEND are all good photos, and refusing one is the worst thing a cheap check can do (an earlier version that required the end marker did). Only a rotate, which rewrites picture data, decodes the picture (`zero_tail_if_whole`). So a JPEG cut short in the middle is written by a caption edit as it would be by the held path; the indexer's full decode finds it later (0.1158 s against 0.0002 s on a 17 MB JPEG, measured 2026-10-02; the cost that mattered was a 500-photo Smart Rename on a share decoding every file before the first rename). **Smart Rename decides once:** `smart_rename` splits held from not held once and hands the split to `preserve_names`, so a folder added in between cannot send the names one way and the renames the other; a mixed rename whose second part fails answers what DID change (`updated_paths`, `updated_photos`) and the page shows the new names. **Recovery without a journal:** if a file-only Smart Rename stops part-way (the process killed between its two passes), run Smart Rename on the folder again -- it regenerates the same names; a file left named `tmp_rename_*` is a photo waiting for its new name (its earlier name is in its PreservedFileName); no sidecar plan is kept. **Accepted:** the watcher notices a file-only write under a root and sync records a `sync_runs` row (no photo row), and the page makes no tag-tree node while just looking, even for the held photos of a partly held folder (#526); Just look reverses the 2026-09-28 refusal of writes in a folder not held, for these operations only (#527, the owner).
- [x] `embeddings`: one vector per photo and model, keyed by `photo_id` and all five model settings, with the stamp of what it was computed from. Replaces `photos.embedding` and `embedding_cache` (#62, #65).
- [x] **A photo's person tag names its face** (#788, 2026-10-07). `tagpup.store.face_tags` is the one rule, called after `people.rebuild` by the writes that change a photo's keywords (`photos.record_tags`, `record_saved`, `follow_fields`), by the indexer reading a photo again (`record_indexed`, when the photo had a row) and by the recording of a photo's faces (`services.faces.record_detected`, `replace_detected`, `record_batch`), in the writer's transaction. Not called by the writes of faces themselves (naming, unnaming, restoring, clustering), so an Unmatch or an Undo is not undone by the rule: unmatching is a decision (`name_source` 'manual') the rule never overrules. One face to be named and one keyword person no face carries (a leaf person: `photo_people.tag_id` set) is decided by the tag alone, as an automatic name, **if the face looks like the person (#833)**: when the person has decided faces, the face's best cosine to them must reach `clustering.OFFER_A_NAME` (0.70) -- on photo_index a third of the faces the tag alone would have named were below it and 1,241 below 0.50, the detector often finding the other person in frame -- else it is left for Identify Faces and counted as "not like them"; a person with no decided face (a new person on one photo) is named by the tag alone **only when exactly one photo of theirs has a face to be named** (#839) -- there being nothing to compare, naming the first of several would make it the reference the next is compared with, and the answer would depend on the photos' order and on batch against one at a time; the count is the whole library's (their keyword person on photos with an unnamed, non-speck face), so the backfill and the live rule agree, and with several such photos all are left (`not_decidable_yet`) for clustering or Identify Faces (on a copy of photo_index, 104 faces of 15 people were that case); a face under `clustering.BACKGROUND_AREA` (2,000 px^2) is never named by the tag. The same function serves the saves and the backfill, so a second run, or a save after the backfill turned a two-face photo into a one-face photo, applies the same gate. By construction a face named this way is a keyword-confirmed reference only when it passed the gate or its person had no decided face, so a stranger cannot seed a person (#843: `decided_faces` itself cannot tell a face a tag named from one automatch named -- both have `name_source` NULL and a keyword that bears them out -- so that holds by the rule's gate, not in the matrix); several faces or people are decided only by comparison with the decided references, and only in the backfill (`tagpup_cli.py faces-from-tags`, `tagpup.services.faces_from_tags`, on the maintenance scaffold, one undoable change): a save does not build a 35,000-row matrix inside a write. Nothing is added to the journal for a save: faces named by a save are written as automatch's are.
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
photo_index when this was written; 2.6 GB on 2026-10-08, #320; and five kept), and cannot be undone except by restoring that copy over
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
- [x] **Retention**: how long a change stays undoable, and what pruning keeps (the summary stays; the values go; the change becomes `pruned`). 90 days (`journal.RETENTION_DAYS`), pruned after every apply and on request (`prune-journal`, the MCP tool `prune_journal`). The changes of a bulk time shift that has a record to resume from (its list of photos and its state, kept 30 days from its last activity, and while it runs) are never pruned, whatever the age: they are the only account of which photos of its last chunk were shifted, and a resume without it could shift them twice. Once the record goes their changes are pruned like any other. The rule is in `store.journal._prunable`, which every prune goes through (the set to keep is asked for inside the prune's write transaction, `services.journal.kept_operations`); the dry run and the apply report `kept` (changes), `kept_jobs` and `note` ("kept for a resumable bulk job: N"), printed by `prune-journal` and returned by the MCP tool `prune_journal` (`{ok, dry_run, changes, pruned, values, days, kept, note}`).
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
- **Recurring jobs** *(owner, 2026-09-25)*: `tagpup.jobs.recurring`, a registry where each recurring operation declares its name, its period (daily, weekly, monthly, every N hours), why it is scheduled (`safety`, `retention` or `catch-up`: one without a reason is refused as it is registered), whether it runs per library, and the service it calls -- the snapshots, sync, pruning the journal. Compacting is not among them and is run by hand (`tagpup_cli.py compact`, phase 4): VACUUM needs the file to itself and fails while an app has it open, which a job inside the always-on server always would (#326). A job's runs are recorded in its library (a `job_runs` table: job, started, finished, outcome, what it changed), so any TagPup process knows what is due and the apps can show when each last ran. One runner runs what is due: a missed period runs once, not once per period missed, and a lock per job and library keeps two processes from running the same job at once. It runs in the web server alone -- the always-on process, which checks periodically; the CLI lists the jobs and runs one by hand (`jobs run`), and neither it nor the MCP server runs what is due as it starts *(owner, 2026-09-26)*. No Windows Task Scheduler.
- **Always on, from login** *(owner, 2026-09-25)*: one background process -- the web server and the recurring-jobs runner together -- started at login from a shortcut in the owner's Startup folder, hidden, so both apps answer and the jobs run whenever the owner is logged in. It refuses a second copy of itself (the server already answering), restarts after a crash through a small supervisor, and logs to `data/logs`; `TagPup.cmd` and `TagTuner.cmd` then open the browser on it. A Windows service was weighed and not chosen: it runs outside the login session, with no desktop, so the folder dialog, Open in Explorer and Open photo would do nothing, photos on network drives or under the profile may be out of its reach, and keeping a Python program alive as a service needs pywin32's service host or a wrapper. `scripts/startup.py install|uninstall` (a dry run unless `--apply`) makes or removes the shortcut, starts or stops the process, and says what it did; installing the app again repoints it at the new version. The models it keeps loaded (ViT-H-14 takes a few GB of GPU memory) are released after an idle period and loaded again by the next Suggest.
  Done (8d): the supervisor is `tagpup.supervisor`, run by `TagPup Background.pyw` (written by `install_app.py` beside the other launchers; it reads `current.txt`, so the shortcut never names a version and installing again needs no repointing) under `pythonw.exe`. It runs `tagpup_web.py` as its child with a hidden console, its output in `data/logs/tagpup_web.console.log`; holds a lock on `data/supervisor.lock`, so a second copy in the home logs and exits; restarts the server after 2, 5, 15, 30, then 60 s and gives up after five crashes in ten minutes, saying why in `data/logs/supervisor.log` and `data/supervisor.json`; and waits, not counting crashes, while another server holds the ports (the child exits 3). `scripts/startup.py install|uninstall|status` makes or removes the shortcut and `always-on.txt` in the installed app's folder, starts the process or asks it to stop (`data/supervisor.stop`: it drains its server first; `--force` ends it at once), and says what it did. `TagPup.cmd` and `TagTuner.cmd` pass `--installed`: where the owner chose always-on, they start the process if it is not running and open the page once it answers, never a server of their own. The web server runs what goes beside its requests from one registry, `tagpup.runtime.BACKGROUND` -- the recurring jobs, the release of idle caches and the folder watcher.
  What the process keeps only while it is used is in one idle registry, `tagpup.core.idle.IdleCaches` (`Runtime.idle`) *(owner, 2026-09-26: "can the in-memory cache be minimized when not in use?")*: each cache registers how to let it go and what holds it, and says when it is used; the background task `release idle caches` lets go of each unused for `--release-models-after` (5 minutes; 30 until 2026-10-05) and not held, and its next use builds it again. Registered: the models (held by a Suggest run), each library's photo index -- its vectors, 267 MB on photo_index, closed with its connection (held by a run) -- New Person's pool (185 MB), the rest of Identify Faces' caches (held while a grid is built) and TagPup's folder scans (held while a Suggest run is under way). The photo index keeps only the vectors, by photo id: a search reads its results' records from the library by id, `records()` every photo's for the CLI and identity resolution; it held every row's record as well (232 MB on photo_index). FAISS searches on the caller's thread alone (`omp_set_num_threads(1)`): each OpenMP thread of a flat search kept about 128 MB for the process's life, 2 GB on a 16-thread machine, and one thread searches as fast. Measured on a copy of photo_index: a library opened and searched held 947 MB for its index and 2.3 GB more after the first search; now 277 MB for the index and 190 MB for the pool while used, and they go after the idle period; made again in 0.8 s and 1.0 s.
- **Updating itself** *(owner, 2026-09-26)*: the launchers install a new commit when an app starts (`install_app.py --if-changed`), but an always-on process rarely starts. It notices when a newer committed version is available (the checkout's HEAD is not the installed one and holds no uncommitted code: the launchers' rule), installs it, and moves onto it at the next request: it stops taking new work, lets the requests and jobs already running finish (a Suggest run, an index, a journaled write), then restarts on the new version, which settles anything left unfinished as the journal already does at startup. What it did goes to `data/logs`, and the pages say which version answers. An update never interrupts a write.
  Done (8d): every ten minutes, with no move pending, the supervisor runs the checkout's `install_app.py --apply --if-changed` (the launchers' rule) and compares `current.txt` with the version its server runs, so an install made by hand is moved onto too. It then asks the server to drain (`POST /api/server/drain`, carrying the token it gave the server): refused at once while a Suggest run, an index or a recurring job is under way, and asked again every minute; else the server answers new requests 503 (`X-TagPup-Updating`, which `web/common/api.js` waits out and sends again), stops its background tasks, and lets the requests in flight finish, a journaled write among them, within two minutes -- or takes work again. Drained, the server is stopped and a supervisor started from the new version takes over (`--handed-over`) and starts it. No second version is installed while a move waits.
  Reviewed before merge (2026-09-26), for an owner who installs it unattended: `--if-changed` installs only a newer commit (the installed one an ancestor of HEAD, `git merge-base --is-ancestor`), never a checkout moved back; an install never removes a version a live supervisor or server runs from; a move waits for a quiet moment (no request for 2 minutes, the drain's `quiet`) until it has waited an hour; the new version's supervisor is started first and must say it is up (`supervisor.handover.json`) before the server is stopped, and take the lock after -- else the old one stays on its version and tries again after an hour; one drain at a time, within one deadline; a server alive but not answering `/api/server` three times in a row (a minute apart, 20 s each) is ended and counted as a crash; a stop drains for at most 30 minutes, then ends the server; a supervisor that fails ends its server, and one that starts finds and ends (drained, when its predecessor kept the token) a server left running. The pages ask again for an `/api/` image that failed while the server was away. The watcher syncs the parent of a folder deleted or moved out (Windows reports it as a file deleted), reads the folders a library holds again only when its photos change, and makes no catch-up at start for a library synced whole in the last hour. `GET /api/server` names the version answering, which the gear shows.
- **Moving or removing people by tag** *(owner, 2026-09-26)*: nothing new in this phase. TagTuner's Tags view already renames, merges or retires a tag across every photo, journaled (e.g. `People/<Name>` to `Friends/...`); browsing by keyword is phase 9's.
- `tagpup.jobs.sync`: for each indexed folder, compare what is on disk with the rows, by path, size and modified time. Content identity (the DocumentID) links a file that moved.
- What it finds, sorted: new files (indexed through the indexing job's queue), changed files (their rows re-read from the file, as `refresh_rows_from_files.py` does now; **a file whose size changed is COUNTED always** (`size_changed`, `size_changed_decided`, shown by the CLI), and its picture is read again only with the library setting `library.reread_resized_pictures`, **off by default** (#336, #903-#905). The owner decided on 2026-10-08 that a size change should re-embed and re-detect, on the premise that a size change means an edited picture. The data says otherwise: a keyword or caption write changes a file's size too, and of 1,229 photos whose size a sync changed on photo_index (588 syncs of its journal) all 1,229 also changed raw_metadata and none changed size alone; and kr-track's root is under photo_index's `@pictures`, 1,139 photos being rows in both, so naming faces in one would have the other's sync take away its faces and vectors and queue a graphics-card re-index. So it is a setting, journaled like any other, and the way to find a real picture edit is a small pixel fingerprint (planned for the face-regions work), not the size. ON, applied: a photo with no face a person decided loses its vector and faces, is marked to be detected again (`faces_pending`, which the Activity page and the doctor count, so an index that never runs is seen: the queue is memory-only) and its folder is queued; a photo with a decided face keeps its faces and names and loses only its vector, which the index makes again (its faces are on file and are not detected again, so nothing else is cleared); done before the rows are written, so a crash leaves the rows stale and the next sync does it again; details `pictures`, `pictures_reread`), moved files (the row follows the file), and missing files.
- A missing file is reported, never removed on its own: a folder on an unplugged drive looks the same as a deleted one. Removing rows stays the owner's choice, and the report says which folders are wholly gone.
- **Watching, with the schedule as the safety net** *(owner, 2026-09-26)*: the always-on process watches each library's root folders for changes (Windows' directory-change notifications, through the `watchdog` package, one recursive watch per root) and syncs just the folder that changed, a few seconds after the changes stop (a copy of 500 photos is one sync, not 500). The app's own writes are not taken for outside ones: the journal records the stamp each write leaves, so a notification for a file that already describes its row changes nothing. Notifications can be missed -- the process was not running, a network drive, the buffer overflowed, a drive was plugged back in -- so a whole-library sync also runs when the process starts and once a day, as a check in case something was missed; a scan that finds nothing costs one directory walk (about 1.4 s on photo_index) and no file reads. It also runs when asked.
  Done (8d): `tagpup.jobs.watching.Watcher`, the background task `folder watcher` (`tagpup.runtime.BACKGROUND`), in the process that runs the recurring jobs -- never one a test started unless it asks, and not with `TAGPUP_NO_JOBS`. It watches each library's root folders and the folders it holds photos in outside them, each the topmost of those under it (`tagpup.services.sync.watch_folders`; none for a library behind this version's schema), one recursive watch per folder through `watchdog` (6.x), a folder two libraries share watched once. A notification is only noted -- the photo's folder (`images.is_photo`; any other file is dropped; a folder deleted or moved, its parent), per library -- and the watcher's own thread syncs a folder three seconds after its last notification (`runtime.sync(library, folder=..., apply=True)`), a folder whose parent also waits left to the parent's sync. The whole library is synced when the watcher starts, when Windows says the buffer overflowed (its emitter is told of a read of nothing), when a watch fails, and when a folder that was not there is back; libraries, folders and absent folders are looked at again every 30 s. A library whose folders need more than 64 watches (no root folders, photos in thousands of folders) is not watched and says so; the daily job keeps it in step. `busy()` while a sync runs, so an update waits for it; stop() waits for it. TagPup's own write is notified and synced, and the sync finds the row describing its file: one walk, no file read (tests/test_folder_watcher.py).
- Each run reports what it changed, not what it looked at, and leaves a record the apps can show ("last in step: ...").
- Done (8c): `tagpup.services.sync` (`sync`, `look`, `last`), on the maintenance scaffold -- a service, not `tagpup.jobs.sync`, since it is given its ExifTool and its queue; `tagpup.runtime.sync(library, folder=None, apply=False, index_new=True)` fills both in (the library's ExifTool; this process's index queue, `tagpup.jobs.indexing`, running the CLI's `index`), and is what the recurring job calls: `sync`, daily, a catch-up (`tagpup.jobs.recurring`, through `Runtime.sync` of the Runtime the entry point gives the runner). The walk is one `os.scandir` pass per topmost folder the library holds photos in (`tagpup.files.images.stamps_under`), against `store.photos.stamps`; changed rows are re-read with `refresh_rows.reread`/`edits_for`, moved rows matched first by name, size and modified time (`sync.pair_by_stamp`, one-to-one only: most rows hold no DocumentID; a match that is not one-to-one is reported as ambiguous and its files' folders are not queued), then with `relink_photos.claims_of`/`pair` for what that left (DocumentIDs read only from those new files, and only when a row is missing) and written with `relink_photos.edits_for`, both as one change, `sync`. Missing files are counted by folder, `folders_gone` and `roots_gone` (a whole walk root not there: an unplugged drive), and never removed. The record is `sync_runs` (migration 14, `tagpup.store.sync_runs`). Entry points: the CLI's `sync [--folder] [--apply]` (waits for the indexing it queued), the MCP tools `sync` (counts the new files, queues nothing) and `sync_state`, and `GET`/`POST /api/sync` on both apps (`tagpup.web.sync_routes`). Not yet: running when a library opens, and watching the folders.
- **Library roots, and folders to review** *(owner, 2026-09-26)*. Done: each library has root folders and ignored folders, two settings (`library.roots`, `library.ignored`; the validator's `folders`: full paths, one a line, none a whole drive), in TagTuner's Library settings. A library has no roots until the owner sets them (`settings set library.roots`, an ordinary journaled change, which can be undone): nothing sets them on its own *(owner, 2026-09-26: kr-track's automatic root would have been its drive's training folder, and its stamp, which could not be undone, would have ignored 57 folders, 81,148 photos, had the server started before the owner set them)*. With no roots, sync keeps the held folders in step as indexing walked them -- new files in a held folder are queued, each folder of new files indexed on its own -- and a new subfolder of a held folder is a folder to review, as under a root, never queued unasked (#395, the owner 2026-10-08; include takes a folder under a folder the library holds). When a library's roots are set, every folder under a new root that holds photos and no indexed photo at that moment is added to the ignored folders in the same journaled change (`settings.excluded_under`; owner, 2026-09-26: "any folders not added assume excluded"): only a folder that appears later is offered for review. Sync walks the roots and every folder the library holds photos in, each once from the topmost. New files in a folder the library holds are queued with that folder alone (`index --no-subfolders`), every such folder of one sync as one job of the index queue and one run of the indexer (`IndexQueue.start(..., together=True)`); a folder under a root that holds photos and no indexed photo, not under an ignored folder, is not indexed on its own: it is a folder to review (`sync.review`, `GET /api/sync/review`), listed as the topmost such folder below a root with its photo count, and TagTuner offers each with Include (indexed with its subfolders, `sync.include`) or Ignore (added to the ignored folders, a journaled change of the settings) -- a dialog from the gear, Folders to review, and a notice with their number. Only a folder under one of the roots is offered: a folder the library holds outside every root (indexed by hand elsewhere) is kept in step like any held folder, and what is new beside it is counted (`outside_roots_files`), not offered. The CLI sets a library's roots: `settings set library.roots "<folder>" [--apply]`. A folder-limited sync (the watcher's, 8d) also keeps the row of a file moved in from another folder, looking only at the rows with its name and size.
- **A folder renamed outside the apps** *(owner, 2026-10-08; built, `tagpup.services.folder_moves`, the CLI's `relink-folders`)*. Sync pairs a missing row with a file only among the files its walk finds. Under a root (or a folder the library holds) a renamed folder is walked, and sync already relinks its files that are unchanged -- the same name, size and modified time as the row -- unasked, and the watcher's new parent watch fires that sync; `relink-folders` is for what that pairing does not reach: files changed or renamed meanwhile, a library without roots, where the renamed folder is a new name no walk reaches (its rows were reported as `folders_gone` and kept; `roots_gone` counts walked folders that are not there -- `library.roots`, folders holding rows, added folders -- not the `roots` table), and the added-folder records of a folder whose rows already moved. The renamed folder is found by content, not by walking: a gone folder whose parent is there is the unit (the topmost gone folder, so subfolders go with it; if no parent is there at all -- an unplugged drive -- it is left as it always was); the folders beside it whose name begins with the same date are the candidates (a filter, proving nothing); a candidate's files without a row are read for DocumentID and Date Taken; a row and a file are one photo by an equal DocumentID (an unequal one rules the pair out), else an equal size and Date Taken (or, neither having one, the same name), never a name alone, one to one across all candidates (a copy or a burst is ambiguous and left). One candidate holding at least 80% of a unit's rows, none ambiguous, is relinked; several candidates or a thin match is a proposal, which the owner confirms with `--from OLD --to NEW`. `relink-folders` is a dry run unless `--apply`, prints counts and a verdict per folder (names behind `--reveal`), and writes one journaled change, `relink_folders`: the rows follow their files (ids, faces, names kept; a file with a row already is no destination), and the `library.roots` and `library.ignored` entries under the old folder follow in the same change. `added_folders`, a record that is not journaled, follows after it only on a clean one-to-one rename -- the new name not added already and no other folder of the run going into it; otherwise the record is left and reported, and no added folder is ever merged -- and an undo of the change points it back with the rows (derived from the change's own photo rows; not when the change left any, and an error in it is reported in the undo's reply, never undoing the undo). An added folder gone with no row under it -- its rows moved already, by hand or by sync, the ghost every sync reports and the watcher polls -- is only reported *(owner, 2026-10-08)*: counted, and named with `--reveal`; nothing is followed, merged or dropped for it, and the owner repairs it. `--from` and `--to` are spelled by the first place of their root, and a `--to` that is not there, or a `--from` that is, is refused in its own words. It is a command and not part of sync, which would otherwise rewrite rows by itself after every rename. The watcher watches the parent of each watched folder without its subfolders (a watch on a folder sees nothing of its own rename), and syncs the libraries whole at once when a watched folder is renamed, moved or removed; the sync reports it gone. Parent watches count toward the limit of watches, a dead one is watched again, and a watch is never unscheduled while the watcher's lock is held (watchdog dispatches with its own lock held and calls into the watcher).
- **Snapshots of each library, as its backup** *(owner, 2026-09-25)*: three dailies, one weekly and one monthly, in `data/backups/<library>/daily|weekly|monthly`, apart from the one-off copies phase 7.5 mostly retires. A daily is taken when the newest is more than a day old; the weekly and the monthly are refreshed from that same copy when they are more than 7 or 30 days old, so the library is read once, not three times. Each is taken in one step of SQLite's backup API, one read transaction, so it is the library as it stood when the step began while writers go on (a backup in several steps restarts whenever another connection writes; holding the writers back only stalled the app's saves for the copy), written under a temporary name, checked (`PRAGMA quick_check`), and only then renamed into place; an old snapshot is removed only after its replacement has passed, so a failed night never leaves fewer good copies. A daily job in the recurring registry, so it is taken by whichever TagPup process is up, or by the background runner. A tool lists them (date, size, the journal's changes since) and restores one: a dry run by default, saying how many journaled changes since the snapshot would be lost, and snapshotting the current file first so a restore can itself be undone. Sync's report gives the disk the snapshots take (photo_index is 2.6 GB, counted 2026-10-08 from its page count, so about 13 GB for the five, and up to two before-restore copies, `KEEP[BEFORE_RESTORE]`, another 5 GB: 18 GB at most for this library; it was 1.4 GB when phase 7.5 and this bullet were written, #320).

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

### Folder ids *(owner, 2026-10-08; built, branch `worktree-agent-a83e22241d527a5ec`)*
Following a renamed folder by what its photos hold (phase 8, "A folder renamed outside the apps") is evidence and a filter, and it fails where the evidence is thin: a folder of photos with no DocumentID and no Date Taken, a rename that changes the date at the start of the name, a folder moved to another parent. A folder that carries its own id follows any rename exactly. It is opt-in, and it is the key folder tags come to hang on later.
- **The marker** *(owner, 2026-10-08)*. A file named `.tagpup` in each **leaf** folder -- a folder that holds photos directly; a folder holding only subfolders gets none -- hidden on Windows (the hidden attribute set as it is written). A hidden file in each leaf folder is acceptable on the server master as well as on a working copy. It holds a list of entries, a line each: a library's identifier and that library's id for the folder (a random UUID), and nothing else -- no name, path, date or anything that names a person. No other TagPup file is called that (searched 2026-10-08). A file of that name that does not parse as that list is not a marker, is never rewritten, and is reported. It is written only by an explicit command, never by indexing, sync or the watcher.
- **The folder id is per library.** A folder in several libraries holds one entry for each, and an entry is added when the folder is added to another library (the same explicit command, run for that library: idempotent, a folder already carrying the library's entry is adopted and skipped). The file is rewritten whole to a temporary name and renamed into place, every other line kept byte for byte, so an interrupted run leaves the old file or the new. **A library never rewrites an entry another library wrote**, nor one that another location of the same root records.
- **The library identifier.** There is none today (searched 2026-10-08): a library is known by its file name and path (`tagpup.core.library.Library`), the settings are a validated registry of the owner's settings and not a place for an id, `schema_version` records migrations, and the only unique ids in the code are DocumentIDs, which belong to photos. A file name will not do: it changes when a library is copied, renamed or restored. **Needed, and proposed, not built**: a migration (additive, creating an empty one-row table `library_identity`: `id`, a random UUID, and `stamped`) which opening a library never fills; the id is stamped by the explicit command that first marks a folder for the library (`folder-ids mark --apply`), in the same transaction, and by nothing else. Stamping writes the owner's library and cannot be taken back (an id handed out in markers is in files), so it is the owner's to approve before it is built. A snapshot restored keeps its id -- it is that library -- while a library file copied for a trial carries the same one: a second library file in the data folder with an id already seen is refused by `folder-ids` and named by `tools/doctor.py`, since two libraries answering to one entry would follow each other's folders.
- **In the library.** A table `folder_ids` (a migration, additive, empty): `id` (this library's id for the folder, the key), `path` (the folder as last seen, in the stored form, `@name/rel` under a root), `marked` (when). A primary table, journaled, not derived: the derived `folders` table (9a-1) is rebuilt from the photos' rows and its integer ids change with them, which is why a folder tag cannot hang on it. Folder tags, when they come, reference `folder_ids.id`.
- **`folder-ids mark`** (a dry run unless `--apply`): for the leaf folders the library holds, it counts those it would mark, those already carrying its entry, those it cannot write (never written: a read-only share, a read-only attribute, a refused write is counted and left, never made writable), and those whose marker does not parse; `--apply` writes the entries and records the ids in one journaled change. A library that never runs it never has a marker written for it, and `relink-folders` is what it was.
- **Relinking by the marker is exact and automatic** *(owner, 2026-10-08)*, per library. A sync of any scope (the watcher's, which syncs the folder a folder was renamed in, included) that finds a `folder_ids` row whose recorded path is gone, and a marker carrying that id under this library's identifier in a folder where it found files new or moved to, or beside the folder that is gone, points the rows under the old path at the new folder (ids, faces and names kept; the destination checked first, as relink does), the `folder_ids.path`, and the roots and ignored folders under the old path, in one journaled change, with no evidence needed.
- **A copy** carries the marker, so every entry in it, and two folders then hold one id of a library. Per library: the folder whose recorded path is gone takes the link (it was moved or renamed); if the recorded path is there and another folder carries the entry, that one is a copy, nothing follows it, and the library may give the copy a fresh id of its own -- only its own line, and only when no other location of the same root records the folder (a mirror of the share holds the same marker at the same place under the root, and is the same folder, not a copy). If the copy cannot be written, it keeps the shared id and is reported; a shared id never moves a row. Three folders with one id: the recorded path keeps it, the others are copies.
- **Decided** *(owner, 2026-10-08)*: `relink-folders` does **not** mark a folder it has followed; the sync report only prints a hint that `folder-ids mark` exists (when folders are wholly gone and the library has marked none).
- **Built**, in the layers' places: `tagpup/files/folder_marker.py` (the file: parse, `with_entry`, stage/publish/discard, the hidden attribute, `find`), `tagpup/store/folder_ids.py` (`library_identity`, `folder_ids`, twins), `tagpup/services/folder_ids.py` (`mark`, `follow`), migration 26 (additive, both tables empty; it lists only `library_identity` among its `touches`, so the schema-gap rule that decides whether an older change may still be undone is unchanged: no change of the journal can have recorded a row of a table that did not exist), `folder_ids` in the journal's `KEYS` and `NAMED`, its `path` converted by the roots machinery (`store.roots.row_value`) and by `roots adopt` / its undo (`store.adoption.TABLES`). `tools/doctor.py` names a second library file carrying the identifier.
- **The order of a mark** (crash-safe at every point): (1) every file is **staged** under a temporary name beside its marker (`.tagpup.<random>.tmp`, hidden), which finds the places that refuse a write *before* anything is recorded; (2) the ids of the staged files, and the identifier with the first of them (`library_identity`, stamped by `journal.apply(..., also=)` in the same transaction as the change's rows), are recorded as ONE journaled change, `mark_folders`; (3) each staged file is renamed into place after checking the marker is still as it was read, and read back. A crash after (1) leaves a stale temporary file (swept after an hour); after (2) an id whose file the next run writes again (*restore*, the recorded id, never a second); a marker holding this library's line and no row (an undone change, a table restored) is *adopted*, not given another id. A snapshot restored from before the first mark brings back a library with no identifier while the folders still carry the old one: `folder-ids mark` then says some folders hold "a line of an identifier that is not this library's -- another library's, or this one's from before a snapshot restore", keeps every such line, and adds this library's new line beside it (the old line follows nothing; to have the old identifier again, restore a snapshot taken after the first mark; nothing else puts it back) (#970). Two runs at once: the second's rows break the unique path and the whole change is refused before it writes; its staged files are removed. A file replaced meanwhile by another program (or another library marking the same folder in the same instant) is left as that program wrote it and counted; the next run adds the line. The one gap a rename cannot close -- two libraries replacing one file in the same instant -- is read back, counted and healed by the next run (#969).
- **Following**: the one place that decides "this new folder is a lost marked folder" is `folder_ids.follow`, a step at the start of the tail of **every** sync, whatever its scope -- the watcher syncs the folder a folder was renamed in, not the library, and a step only in the whole sync let it queue the renamed folder as new, indexing fresh rows beside the old ones (reviewed 00c5d16, #974). It runs after the sync's own change and before any folder is queued, when the sync found a file missing or moved and the library has marked folders, and looks only where the sync already looked: the marked folders asked about are those of the missing or moved rows (one stat each), the markers are read in the folders where it found files new or moved to and under the folders it would only review, and, for an id still not found, beside the folder that is gone (one listing of its parent, the same path under each sibling that no other marked folder is recorded at); never a walk of a root (measured, #975). A folder none of whose rows can go -- every file already has a row of its own -- is **left and reported**, its id not moved: moving it would say "followed" and leave the old rows and their named faces missing for ever (#974). Before a folder is queued as new its marker is read (`folder_ids.lost_among`: one read per new folder, no ExifTool, no stat of a recorded folder): a marker carrying this library's entry for an id recorded for another folder makes that recorded folder one to follow whatever this sync's own missing and moved counts are -- a marked folder moved into a folder the library holds shows the sync of its new parent nothing missing or moved, and its source's sync cannot find it (#980); asked only of a library that has marked folders, 7 ms for 300 new folders without a marker, about 3.5 ms for each that has one. `relink-folders` (no sync) looks beside the folder only; a folder moved to another parent is found by the sync that finds its files there. **Undone is done again**: `undo` of a `follow_folder_markers` puts the rows and the id back at the old folder, but the marker in the new folder still carries the id, so the next sync that finds the old folder gone follows it again (#981). To stop it, take the line out of the folder's `.tagpup` (or delete the file; there is no command that removes a marker yet) and then `undo`; `folder-ids mark` writes the recorded id again into a folder whose marker is missing. What a sync could not follow is not silent: the counts `left` (its files already have rows of their own) and `not_found` are printed by `sync` and logged by the watcher when not zero (never names) (#982). A root kept in two places on the machine holds one folder, not a copy (places are compared as the library holds them). Photos pair by the file of the same name that has no row, else by DocumentID or size and Date Taken (`folder_moves.pair_by_evidence`); a file with a row is no destination. The change `follow_folder_markers` is undone by History like `relink_folders`, the folders added following back. Not done: following by marker a folder tag (folder tags do not exist yet).

### File access check *(owner, 2026-10-03; built, branch `arch/file-access-check`)*
*"Defender can be an issue: warn the user if it is also checking the photo folder. Other processes scanning
or reading files WILL interfere, and the user should exclude them if possible."* The bulk time-shift job's
review found one: its state file's `os.replace` failed with WinError 5 because another handle (the other
TagPup process, Defender, the Search indexer) had the file open. Two parts, both **read-only**:

- **Naming the holder after a failure** (`tagpup.files.lock_owners`). Windows' Restart Manager (the API an
  installer asks "which programs hold this file?"; `RmStartSession`, `RmRegisterResources`, `RmGetList`,
  `RmEndSession`, through ctypes) lists the processes that hold a file open, with no administrator. `holders(path)`
  gives `[{name, pid, kind, service}]`, never raises (any failure is `[]`), is `[]` off Windows, and is bounded
  to 2 s on a thread of its own: a share that does not answer leaves a daemon thread to end when Windows lets
  it, at most four at once, then `[]` at once. The session is ended in a `finally`; a test calls it 200 times
  and the process's handle count does not grow. `describe()` turns it into "held open by MsMpEng.exe (Windows
  Security / Microsoft Defender real-time scanning)" from a table of known scanners, sync clients and backups
  (`python.exe` is "another TagPup process", or "this TagPup process" when it is the caller). `explain(error,
  path)` returns the error with " It is held open by ..." added when the error is the kind a held file makes
  (`PermissionError`, WinError 5/32/33, ExifTool's "permission denied", "error renaming temporary file") and a
  holder is known; it is called **after** a failure only, so it never delays a write that succeeds. Names
  only: no path, command line or user. Used by `names.rename_all` (the sentence the owner sees), and the
  journaled and file-only writers (`file_changes._after_failure`, `file_only._write_one`). Not changed: the
  SQLite "database is locked" diagnostics (SQLite has its own lock and its own retry in `tagpup.store.db`).
  The bulk job's state-file retry (`tagpup.jobs.bulk_edits`, on `arch/phase-9d1-bulk-jobs`) is to call
  `explain()` after its retries fail, the same way.
- **Advice** (`tagpup.services.file_access.check`, `GET /api/file-access/check`; the Activity page's File
  access section). Findings, each decided from a small injectable read so a test feeds what this PC
  answered: (1) Defender's real-time and on-access state; (2) whether Defender's **exclusions** cover the data
  folder and each root's place -- `Get-MpPreference` says "N/A: Must be an administrator" to a process that is
  not one, so the registry (`HKLM\SOFTWARE\Microsoft\Windows Defender\Exclusions\Paths`) is tried, and if that
  is refused too the finding is a **warning that it is not known**, with the exact elevated commands for the
  owner (`Get-MpPreference ... ExclusionPath` to look, `Add-MpPreference -ExclusionPath '<data folder>'` to add);
  covered means equal or under, compared by `tagpup.core.paths`; (3) Defender's scanning of network files when a
  root lies on a share; (4) other antivirus products registered with Windows (`SecurityCenter2`), named, since
  TagPup cannot read their exclusions; (5) Windows Search: the `NOT_CONTENT_INDEXED` attribute and the scope
  rules in `...\CrawlScopeManager\Windows\SystemIndex\WorkingSetRules` (`search_scope_includes`: the most specific
  rule that covers a folder decides, an exclusion wins a tie); (6) cloud sync: a folder inside OneDrive,
  Dropbox, Google Drive or iCloud (the `OneDrive*` variables, and the profile's default folders); (7) the
  scanning, sync and backup programs running now, as a note.
  **What it never does**: change a setting. No `Add-MpPreference`, registry write, service change or
  attribute set is run -- the one program it starts is `powershell.exe -NoProfile -NonInteractive -Command`
  with a fixed script that only `Get-`s, through `tagpup.core.processes`, hidden, with a 20 s deadline; the
  registry is opened `KEY_READ`; the commands are text for the owner to copy (`tests/test_file_access.py`
  greps for a writer and for `subprocess`). A finding that fails is "could not be checked" and the others stand;
  PowerShell missing or blocked is an info finding, not an error. The whole check is bounded (30 s), runs on a
  thread of its own, one at a time, and is remembered 10 minutes per process (`refresh=1` asks again).
  **Why the data folder is the recommendation**: SQLite's write-ahead and shared-memory files, the thumbnail
  cache and the bulk-edit state files are opened, written and replaced all the time, and a scan in the middle
  makes a save or a replace fail; none of it is a photo. **The photo folders are the owner's choice**, marked a
  trade-off in the commands: excluding them leaves the photos never scanned. The places are the first place
  of each root on this machine (`tagpup.services.roots.listing`); the route passes every library's when none
  is named.
- **The page**: File access on Activity, a row for each finding (level icon, title, why, "What to do", the
  commands in a box with a Copy button), "Check again", "Last checked <time>". Asked once when the page opens,
  never on a timer. "I've dealt with this" on a warning is kept per browser in `localStorage`
  (`tagpup.fileAccess.dismissed`, by finding id) and shows the finding greyed as ok. Elsewhere one quiet line,
  "Other programs may be scanning these files: see Activity > File access" (TagTuner's Roots dialog;
  `web/common/file-access-note.js`, for the bulk-job summary too): no popup, no banner on the main page.
- **Not done**: the Defender Operational event log (scan started and finished, 1000-1002) is readable but
  says nothing about the files a write meets; the bulk job's summary line and state-file hook wait for that
  branch to be merged.

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
  - **`roots repair-address`** (`tagpup.services.roots.repair_addresses`, `tagpup.store.adoption`; the CLI's `roots`
    group; no MCP tool): a share address starts with two backslashes, and Git Bash turns a leading pair in an argument
    into one, so `roots adopt` refuses an address that starts with a single separator (#914) and this command gives a
    root already stored so its two back. A dry run unless `--apply`; one journaled change (`roots repair-address:
    <names>`) that `undo` reverses, refused once the address is no longer the one the repair left (a hash of it is kept,
    never the address). It takes no backup (one text column, the journal is the way back) and, like any change of the
    library's roots, stops runs in other processes that hold them (`RootsChanged`): run it with TagPup and TagTuner stopped.
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
    - **Verify reads the folder markers too** *(#984)*: for the root's folders that have a `folder_ids` row, the `.tagpup` at the
      CANDIDATE place (the row's `@name/rel` turned into a path by the same Roots built for the candidate; the machine's map is
      never touched, so a root kept in two places is read at the one asked about). Counted as `markers` in the answer:
      **match** (the marker holds this library's entry for the row's id), **differs** (this library's entry for ANOTHER id:
      likely a different folder at that path; only our entry is evidence, so another library's line, beside ours or alone,
      is never a difference),
      **unmarked** (no marker, or none with an entry of ours: the library predates marking, the folder is new, or `mark`
      recorded the id before a publish that was then skipped, as with two libraries over one folder -- sync reads it the same), **malformed** (a hand-edited file: counted,
      never a reason to refuse), **unreadable** and **not there**, with `line`, one sentence for the dialog ("N of M marked
      folders match; K differ; L not marked"; never a folder's name). A sample reads at most 300 folders, each
      once, starting no read after 10 s (the worst case is that plus one look, 15 s), and reads them BEFORE the rows, so a sample
      that runs out of its row budget still has its marker verdict; a full run all of them, with the job's progress and cancel. Each read is made on the bounded thread (a share that
      stops answering ends the run as unreachable, never as "all differ"). Any DIFFERING marker makes the result poor, so
      Change location refuses it, with the counts, unless the owner overrides, as it does for missing files. A library with
      no `folder_ids` row for the root (marked nothing; or behind migration 26, which has no such table: the read-only
      connection answers empty) has `markers: null` and nothing is said. The live libraries are marked
      (the owner marked all three on 2026-10-09; the `folder-ids mark` dry run counted photo_index 2,672 of 2,673 leaf
      folders, kr-track 20 of 20, renton_parkrun 8 of 8, 16 folders of photo_index and 16 of kr-track holding another
      library's line too), so Verify and Change location read markers for real: 300 in a sample, every marked folder in a
      full run. A COPY of a marked
      folder keeps its marker and so matches: a marker proves a folder is the same folder, not that it is the original.
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
- **9c. The navigator and the move between views.** *(Built 2026-10-03; see "Phase 9c" below.)* Folders, Keywords, People and Dates
  beside the grid, with counts; the header and the URL name the source, so Back and a
  bookmark work; "Show in library" from a disk folder; a banner where the disk holds
  files the library does not (offering to index them, through sync); staleness marks on
  the cards on screen (size and modified time, no ExifTool) and a missing photo shown
  but not editable; "last in step" from sync.
- **9d. Editing from a library view.** *(9d-1, the server's half -- bulk edits by photo id as a job, a selection's tally -- built 2026-10-03, see "Phase 9d-1" below; 9d-2, the page: the selection by id, the edits through the job, the strip and the tally, built 2026-10-03, see "Phase 9d-2" below.)* Bulk edits on a selection that spans folders,
  through the same journaled writes (History lists and undoes them); a file changed
  outside while an edit is planned is a conflict for sync to settle, never overwritten.
- **9e. Search.** *(9e-1, the server's half -- a search as a source, migration 24's word index -- built 2026-10-04, see "Phase
  9e-1" below; 9e-2, the page -- the Library pane's search box, the picker, a search as a view -- built 2026-10-04, see "Phase
  9e-2" below.)* A search is a source: *all of* these tags or people, *any of* those,
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
- **Selecting stays by path** (`selected.js`, unchanged) *(superseded in a library view by 9d-2: selection by photo id, see "Phase 9d-2")*. A click is the card's path; a Shift-range or Select all over cards not held
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
- **The stamp is the cheap first check; the base is the real one** *(review of the fix, findings #549 to #552)*. A stamp can be kept: a
  copy keeps its times, and a same-length rename of a tag with the time put back changes the keywords and nothing the stamp sees. So
  the page also keeps, with each record, what it read of the two things a save overwrites -- `base: {tags, title}` (`edits.js baseOf`,
  taken from the record the first time it is needed) -- and sends it with every write that carries a whole list or caption: a save of
  tags, title or people, the Date Taken edit, carry forward, applying a suggestion, a folder card's title. `tagging.save_photo`, under
  the file-changes lock and after it has read the file's current state, compares the file's keywords (as a SET of paths, `_tags_held`)
  and caption (the first, trimmed) with `base`; any difference is `409` `changed_on_disk` with the same sentence and nothing written;
  a field the save does not touch (a rating) is not compared, so another program's rating is no refusal. The page takes the next
  `base` from each reply (`base`: the tags and caption as written), and from the replies of bulk tags (`written`) and of undo, which
  also give the stamp (`edits.js takeWritten`: undo had left the record with the old one, so the next save was refused and the old
  stamp survived in the folder cache); the server's cached scan takes the file's stamp from a bulk write too (`_records_written`).
  A call with neither `stamp` nor `base` (the CLI, the MCP) is no check, as it was. **A photo ExifTool could not read when it was opened**
  (`read_error` of the reader, an empty file for one: `page_record` keeps it as `unreadable: true`) is not saved from: the page says
  "This photo could not be read just now: reopen it." and sends nothing, and the server refuses a page's save (one with a stamp) whose
  `base` is null with the same sentence, so a record that shows no tags because the read failed never replaces the keywords of a file
  that reads now. The cost is about a hundred bytes a save (`base` of a photo with three tags and a caption).
- **A bulk write is asked about and capped** *(findings #535)*. A selection over 200 photos asks "Add Trips/Coast to 3,412 photos?" (the
  write named, `selection.js confirmBulkWrite`) before any bulk tag, person or removal; the server refuses a request of more than 5,000
  photos with `400` "Narrow the selection: bulk edits over 5000 photos arrive with the editing stage" -- bulk tags and Smart Rename; the
  page says the same without sending, **before** a typed name is resolved (resolving may make a tree node: a refused or cancelled bulk
  makes none, findings #554). A job with progress, cancel and undo of its own is 9d's. Accepted and left: time-shift and auto-apply take a
  folder and have no cap, and the undo of an auto-apply over 5,000 photos is refused by the cap.
- **The tag tree's counts are one pass of `photo_tags`** *(findings #534)*. `/api/taxonomy/tree` parsed every photo's tags JSON for
  `usage_count` (`store.photos.tag_usage`): 350 ms on a synthetic library of photo_index's scale (68,466 photos, 159,651 `photo_tags`
  rows, 931 nodes), which held the Python process at page start so that `/api/library/ids` and `/api/tags` waited behind it. It now
  reads `photo_tags` (`library_view.keyword_counts`, whose per-photo grouping SQLite does: Python sees the distinct tag sets, not 68,000
  photos): **68 ms**, the same counts node for node (`tests/test_tag_usage_from_photo_tags.py`); a library without the derived tables
  is counted from the JSON as before. **What a count means** *(findings #553)*: it follows the views' derivation, so a keyword with no
  node counts toward nothing (the old lineage count credited the levels above it: 4 such keywords on 32 uses in photo_index), and a
  keyword that differs from a node only in case or spacing counts toward that node (`tests/test_tag_usage_from_photo_tags.py` pins both). With the page's first three requests in flight together: tree 785 -> 122 ms, ids 830 -> 170 ms,
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
  gone shows a broken picture today (the thumbnail route answers a 404 sentence). `state.libraryReturn` is the folder Back returns to
  *(gone with #670, with the strip's Back to folder view)*.
  The folder cache and the folder's scroll are not restored by Back (a folder is read again from the cache of the scan, at the top).
- **What 9d builds on.** `selected.js` is by path because every write route is; a card has its `id`, and `selectInLibrary` is the one
  place that turns a range of ids into paths -- with id-based writes it disappears and Select all of 68,000 is instant. The selection
  panel's tallies need a server answer for a selection by ids. Smart Rename and Camera Time Shift need a form across folders.
- **Known limits.** The cards of a view show file names, not captions. The order is not live. A body of 68,000 paths is a long request.
  The first ask of a keyword view of 41,000 photos sorts its ids (100 ms in SQLite); `all`, a year and a month read an index in order.
  The ids of a source over 200,000 photos are cut there. Tab visited only the cards that existed (as in 9b-1): 9c made the grid one tab stop.

### Phase 9c: the navigator, the move between a folder and its view, stale marks, the grid's keys *(built 2026-10-03; branch `arch/phase-9c-navigator`)*
The page, and two small routes and a card field under it. Nothing is migrated. The owner sees: a switch at the top of the sidebar, **Library** and
**Folder**; behind Library, four tabs of what the whole library holds; a click opens the view; and a grid that can be walked with the keyboard.

- **The sidebar is two panes** (`index.html`, `navigator.js`): **Folder** is what it was (the folder box, Suggest, the open folder's file list), **Library** is
  the navigator. A library view shows the Library pane and a folder view the Folder pane; a person who chooses the other keeps it for that kind of view until
  the page is left (`state.nav.choice`, never written anywhere). The switch is an ARIA tab list (arrow keys), as the navigator's four tabs are.
- **The navigator** is three modules. `navigator-model.js` is data only: the route's lists made into the rows a tree shows (`indexFolders`, `indexKeywords`,
  `indexPeople`, `indexDates`, `sectionRows`, `locate`, `sectionOf`); `navigator-tree.js` draws rows as the ARIA tree (or listbox) pattern asks and owns the
  keys' arithmetic (`paintRows`, `treeKeyAction`, `tabKeyTarget`, all pure but the draw); `navigator.js` asks, listens and keeps the state (`state.nav`).
  *Folders* and *Keywords* are lazy trees from the route's flat list by parent (a branch is drawn only when open; folders in the route's order, keywords
  alphabetical by `compareTagNames`), *People* an alphabetical list, *Dates* the years newest first opening to January..December (and an "Other" row for the
  photos of the year whose date names no month). **At most 1,500 rows of a tree's open branches (5,000 of a flat list: the people, the years and months, what a filter finds) are drawn**, and a line says how many were left out, counted over the whole tree (a parent with 1,000 children shows all of them; all 413 of photo_index's people are on the page; findings #567); a filter box
  (120 ms after the last key) lists what matches wherever it is filed, with its place. **Years before 1900 or after next year** (the route marks them `implausible`; the server owns the rule, the page shows what it is told, #510) are one collapsed
  **Other years (N)** entry at the end, still reachable. A folder row shows the photos with its subfolders (the direct count is in its tooltip and its
  label); a click on a row opens the view of it **and** opens the branch; a click on the arrow only opens the branch. A keyword no tree node holds cannot be
  navigated and is not listed: the route does not say how many there are, so nothing says so under the tree (the doctor lists them).
- **Reading.** A section is read from `/api/library/navigator?section=` when its tab is first shown. Each ask has a number, and an answer that is not the
  newest of its section is dropped; a section is drawn into its own panel only, and only while it is on screen (else when its tab is shown). It says
  "Loading the folders...", "The library holds no photos in any folder yet." and the server's own sentence for a library behind or a root this computer
  does not place (a 409; logged as a warning, not an error), or "Could not read the people (Failed to fetch)." and is read again when its tab is opened. A
  section already read stays on screen when a re-read fails. **Counts are read again** when a write finishes (`write-queue.js markEntry` -> `upper.navigatorCountsChanged`:
  every write of this page goes through the one queue) after 1.2 s, once for a run of writes; and at once on Refresh view. The re-read is quiet: the rows are
  the same elements, what is open stays open, the focus stays; only the numbers change. A section not on screen is marked out of date and read when its tab is shown.
- **The view is followed** (`library-view.js` calls `upper.navigatorFollows` as a view opens or closes): the source is highlighted in its tab (`aria-selected`),
  the tab is selected and the path to the row opened -- also for a view opened from the address, by Back and Forward -- by `locate` over the parents the route
  gives (a keyword by its tag, else without case; a person without case; a folder by `pathKey`; a month opens its year; a year among the odd ones opens the group).
  A source with no row (a keyword with no node, a folder the library holds nothing in) is an empty view with its sentence and no highlight. Two libraries are two
  pages: nothing of one's navigator can reach the other's.
- **The move between disk and library** (`library-moves.js`): **Show in library** (beside Select All, with a folder open on disk) opens the folder's library
  view with its subfolders; **Show on disk** (in the strip of a folder's view) scans the folder FIRST and closes the view only when the scan has answered, through the history entry Back to folder view uses -- a folder that is not on disk any more (400), or cannot be read, keeps the view and says so in its strip, so the page is never left with neither a view nor a folder (#569);
  **This folder only / With subfolders** is another view of the same folder *(Show on disk and This folder only went with #670; the scan-first move is
  `openInOrganize`, a folder of the selection's Folders to Organize)*. The selection is cleared by every one (a view opening or closing clears it, 9b-1). The
  photo at the top of the grid is the one to land on: a folder view finds it by `pathKey` in `shownIndex`, a library view asks the library which id a path is
  (`GET /api/library/find?path=` -> `{id}`, `null` for a photo it does not hold: one seek of `idx_photos_path_nocase`, `COVERING INDEX (path=?)` on photo_index) and
  `scrollToIndex(..., 'start')`s there, below the sticky strip (`topInset`). Best effort: a photo in neither leaves the grid at the top.
- **The banner** (`library-banner.js`): a library view of a *folder* asks, once it has painted (never before), what the disk holds of it
  (`/api/folder/membership`, the call a folder opened makes) and, if `photos_not_held > 0`, says "N photos in this folder are not in <library>." with **Add them** and
  **Dismiss**. Add them opens the add-folder question of a folder opened -- the library's name, what it holds and lacks, the warnings -- with **Not now** in place of Just
  look; nothing is indexed until **Add to <library>** is pressed (`/api/folder/add` for the banner's folder, `state.moves.addFor`; a folder view's own Add still adds the
  open folder), and then the banner goes and the strip says it is being indexed and to Refresh view when it has finished (the folder's index pollers are idle in a view). The
  question has a deadline (15 s, then the request is aborted; the server gives up at 12) and **any** failure -- a share away, a folder gone from the disk (400), a library that cannot say, the
  view left meanwhile -- shows nothing and logs nothing: it is an offer. Dismiss lasts until the page is left, per folder.
  **A walk is a folder's worth of disk, so it is not made on every view change** *(findings #568)*: (a) the answer is kept per folder for the page's life (`state.moves.cache`) and forgotten by
  Refresh view, an Add, and a change in `last_in_step` or `syncing` (sync-state.js: the moments the disk and the library may have moved apart); a "could not check" is never kept; (b) it is
  asked by itself only for a view of **this folder only** and for one with subfolders of **under 1,000 photos** (the view's total); a larger one shows a quiet link, "Check this folder on disk for
  new photos", that asks when clicked and says "Every photo on disk in this folder is in the library." or "Could not check this folder on disk just now (why)."; (c) the server
  (`libraries.membership_checked`) first asks a folder on a network share (a UNC path or a mapped drive: `shares.on_a_network_drive`) to say it is there within a second on a thread that is
  waited for -- else the answer is `{could_not_check: true, why}` and no thread is left walking it -- then walks on a thread of its own and waits 12 s (`it took too long`); a request for a
  folder under walk is **answered by that walk**, never a second one; the route no longer calls `isdir` on a share itself. On the sandbox copy with 68,000 empty photo files under the
  root place, a click on the library's largest folder (68,324 photos) made one membership request taking 2.9 s (2.7-3.2); it makes none now and shows the link.
- **When the library was last in step** (`sync-state.js`): the strip says "Library last in step with its folders: 5 min ago" (or "never"; ". A sync is running now." while one is) from
  `GET /api/sync` -- the answer the Activity page's "Last in step" is made of, plus `syncing` (new: whether this process's folder watcher is syncing the library now, `activity_routes.is_syncing`;
  false when it runs none). Asked once as a view opens and again with Refresh view; a request that fails says "Could not read when the library was last in step with its
  folders." and nothing else changes; an older answer arriving late is dropped.
- **Stale marks** (`library_view.cards(check_disk=True)`, what `GET /api/library/cards` asks; `view` and the CLI do not): one `os.stat` for each card through
  `damaged_photos.stamp_of` (the bounded stat a page's request already uses: a share is asked within a second, one found away is answered at once for 30 s, so a page of
  cards on it waits once), at most 200 a request. A card gains `stale`: `"changed"` when the file's size and time no longer describe the row (`store.photos.describes`, the
  folder scan's own test), `"missing"` when the file is gone, and **no key** otherwise -- for a file that cannot be read, a share that did not answer, and a row nobody
  stamped (Suggest's row for a photo never read: "changed" would claim a difference nothing knows of; photo_index has none, counted 2026-10-03: 0 of 68,472 rows lack a size or time).
  The card carries a small badge ("changed on disk" at its bottom right, red "missing"), the picture that cannot be had is a grey card, and a screen reader hears it. A **missing**
  photo is shown (the library still holds its row) and **not editable**: opening it (`photo.missing` from `/api/library/photo`) shows a notice, hides the picture, and every
  element marked `data-writes` in the page -- rotation and delete, the date, title, people and keywords, the suggestions -- is `inert` and `aria-disabled` (`stale.js`; the server
  already refused a write to a file that is not there). A **changed** photo, opened, is read from its file (9b-2's rule), and its badge clears on the card; the library's row is
  sync's to bring up to date, so a view refreshed before it has says "changed" again, which is true. Of 400 rows of photo_index sampled evenly (read-only; counts), 400 were as their
  row said: the marks are rare on this library, and a person will mostly meet them after editing in another program.
- **The keyboard** (`grid-keys.js`, `vgrid.js`): the grid is a listbox and **one tab stop** (roving tabindex: the card the keys are on has `tabindex=0`, every other card, its
  checkbox and its detail button `-1`, a placeholder none). Arrows: one card, one row (the last short row's last card below a column with none); Home / End: the first / last photo
  of the view; PageUp / PageDown: the rows in view (`vgrid.geometry()`); Enter: opens the photo (a placeholder's too, by its id); Space: selects or deselects it (Shift+Space: from the last
  one picked, as Shift-click); Shift with an arrow: every photo from where the run began to the new one is **added** (stepping back does not take away). Keys move by index in the
  view's order, so they cross the window's edge and 20,000 photos: the grid scrolls to the index (`scrollToIndex`, below the sticky strip) and the focus goes to its card, or --
  a place is not focusable until its card arrives -- waits on the grid itself and goes to the card when it is drawn (`afterDraw`). The shortcuts of the photo panel and Ctrl+D/Ctrl+Z are
  unchanged and everyone's; the arrow keys stay the photo's when the focus is not in something marked `data-own-keys` (the grid, the navigator's trees and tabs, the sidebar's switch),
  and never fire from a filter box or a dialog. Cards are options of the listbox, named "file name, date, selected / not selected, damaged, changed on disk / file missing"; the
  navigator's tabs and trees follow the ARIA tabs and tree patterns (arrow keys, Home/End, Right/Left to open and close, Enter, `aria-expanded`, `aria-selected`, `aria-level`).
  **vgrid.js** learned three things for this: `afterDraw` (after every draw), `topInset` (a card scrolled to goes below what is sticky over the scroller), `geometry()`; and
  **the focused card is never taken from under the focus**: where a draw would release it (it left the window) it is kept, laid out of sight as a card being typed in is, and
  one rebuilt for changed data (a refresh, an edit) gives the focus to the card built in its place.
- **Measured** with `scripts/measure_navigator.py --run` (plan without `--run`; `--code-root <a git archive of the trunk>` runs only (f) and (g) on the trunk): a sandbox copy of
  photo_index (68,472 photos, 2,746 folders, 895 keyword nodes, 413 people, 61 years), headless Chromium 1600 x 1000, a fresh browser for each, 3 rounds, thumbnail requests answered by the
  browser. **There is no navigator on the trunk, so (a) to (e) have no baseline**: they are read against 9b-2's budgets (a window of cards painted in 40 to 60 ms, no task over 50 ms
  while scrolling); (f) and (g) are on both. The machine was not quiet; run to run these move by up to 40%.

  | the action | this branch (median; range) |
  |---|---|
  | (a) click Library: Folders rows painted, main thread idle -- cold (first after the server started) | 115 ms (the request 55 ms; the reply 792 KB, the 2,746 folders), before gzip; 245-430 ms on later, busier runs |
  | (a) the same, warm | 151 ms (109-162; the request 57-122) before gzip; 196 ms (138-461) after, the machine busier: the request is the server's 30-50 ms either way, and the reply is now 58 KB (792 KB gzipped, below) |
  | (b) the folder tree opened to depth 3: each of three clicks until painted and idle | 47 / 46 / 47 ms (45-48), 74 rows after, no task over 50 ms |
  | (c) a click on the largest keyword node (41,448 photos): click to the first window of cards painted and idle | 480 ms (6 rounds: 413-597); the same view by its address: navigation to painted and idle 500 ms (440-1,119) |
  | (d) five letters typed in the People filter, 60 ms between keys | the list redrawn 17 ms after the filter's own 120 ms wait (15-21); no task over 50 ms; 400 rows to 2-118 |
  | (e) ArrowDown held through 2,000 photos (5 columns, 400 keys at 30 a second, 17.6 s) | 1,056 frames, p95 16.8 ms, longest 16.8 ms, none over 100 ms, no long task, peak 550 grid nodes, the focus in the grid at every frame and on a card at the end |
  | (f) `GET /api/library/cards` for 200 ids, the sandbox holds no photo file so all 200 are `missing`: branch / trunk | 19.8 ms (18.8-21.0) / 8.8 ms (8.3-9.1): the 200 stats and the 200 looks at the drive's kind cost 11 ms (6 ms before the mapped-drive check; asked of Windows per card it cost 0.6 ms each, 130 ms a batch, until the answer was kept per drive letter for 30 s); 200 stats of files that are there, on a local disk, 5.2-6.6 ms (26-33 us each), of files that are gone 3.0-3.9 ms |
  | (h) a click on the largest folder row (68,324 photos) with 68,000 files on disk under it: membership requests, click to painted and idle | before (`866e279`): 1 request, 2.9 s (2.7-3.2), 597 ms (514-781); now: 0 requests and the quiet link, 480 ms (447-583) |
  | the navigator's folders reply and a view's order, through the real Waitress server with `Accept-Encoding: gzip` | 811,009 -> 59,588 bytes (13.6 times) and 399,821 -> 159,182; the browser fetch decodes it unseen (58.2 KB in the page's resource timing); server time 30-50 ms either way |
  | (g) open the 759-photo folder view, scan reply to painted and idle: branch / trunk | 93 (90-95) / 94 (52-103) ms |
  | (g) the same, scrolled top to bottom in 3 s: p95 frame, frames over 100 ms, peak nodes | 50.0 ms, 0, 500 on both |

  What the numbers do **not** show: the click on a 41,000-photo keyword is the server's ids request (190-440 ms in the page, the same 9b-2 measured, 100 ms of it a sort in
  SQLite), and in many of its rounds the page acted on the reply 85-266 ms after Chromium's own `responseEnd` for it. **That gap is not the page's work** *(CDP trace, findings #572)*: in
  eight traced click rounds the renderer's main thread ran nothing over 2 ms between `responseEnd` and the page having the response (no task, no GC -- the scavenges all came before it -- no
  layout; the navigator's painting happens at the click, before the request is even made), and in the four rounds whose network events were listed the renderer's `ResourceReceiveResponse`, `ResourceReceivedData` and `ResourceFinish` for the ids request
  all came at one instant (three of them 127-203 ms after `responseEnd`, one with no gap), after which the page asked for its cards 7 ms later. So it is the delivery of the response from the browser process to the renderer.
  It also happens when the view is opened by a bare `popstate` (no click, the navigator closed) and with the `/api/sync` request faked away, with and without the thumbnail route, with and
  without a rAF loop; it does not happen to a bare `fetch` of the same URL on an idle page (22 of 22 rounds: 1 ms) with or without an AbortController, nor when the view is opened by its
  address. The navigator's work is therefore not deferred: there is none in the window. The cause below the page is not known. Headless Chromium at 16.7 ms a frame has no compositor to fall behind; the photos are not in the sandbox (the stat is of a file that is gone, the
  thumbnail a 1-pixel picture); and nothing is measured on a network share, where each stat of a share that answers is a thread (`shares.bounded`) and one that does not costs the one
  second, once.
- **How it fails**, each a test (`tests/frontend/navigator.test.mjs`, `library-moves.test.mjs`, `stale-cards.test.mjs`, `grid-keys.test.mjs`, `vgrid.test.mjs`;
  `tests/test_library_cards_stale.py`, `test_library_find.py`): the four tabs at photo_index's size (rows bounded, all 413 people on the page, an expanded tree's count left out right: 1,549 rows is 49 left out; a parent with 1,000 children whole); a tab
  opened while another loads, an older answer for the same section dropped, a click while a view is loading; Back and Forward restoring the view, the highlight and the tab; the address
  opened cold to a folder, a keyword, a person, a month and a year among the odd ones; a folder or keyword that is gone; counts after a write (quiet, once for five writes, the section off
  screen read when shown); a library with nothing, one at schema 18, an unplaced root, a network failure; two libraries; the banner after the paint, for a folder's view only, with a
  share away, a folder gone, a library that cannot say, a question that never answers (the deadline), a view left meanwhile, Add them (nothing indexed until answered), Dismiss, Refresh
  view; "last in step" never / running / failing / an older answer late; Show in library and on disk (selection cleared, landing on the photo, the photo not held, a folder not on disk);
  stale badges, a missing photo inert, a changed one cleared, 200 in a batch, an away share (no mark); the keys at 20,000 photos with the DOM bounded, Enter and Space on a place, a
  Shift range across unloaded cards, the focus across a refresh and a wheel scroll, Ctrl+Z with the focus on a card. The server's: a file as the row says, edited, deleted, touched, a
  row nobody stamped, a share away or a file unreadable (no mark), `view` that looks at no disk, one stat each; a batch whose stats answer in 0.6 s each ends within its 1.5 s budget with the rest
  unmarked; a mapped drive is a share for the bounded stat and its type is asked of Windows once; a bulk tag write with a missing photo in the middle writes the photos after it and lists it
  (`tests/test_bulk_tags_missing.py`); a membership walk is shared by two askers, abandoned at its deadline, refused for a share away (`tests/test_membership_is_bounded.py`); a long JSON reply is gzipped
  (`tests/test_gzip_json.py`); and the page's: a bulk write leaves out missing photos and says how many (`bulk-missing.test.mjs`), the banner's cache and its link (`banner-cost.test.mjs`), Show on disk of
  a folder that is gone keeps the view, the keys of a card's magnifier and checkbox move the card.
- **What 9d builds on.** A write of a selection that spans folders is by path today and `selectInLibrary` is the one place that turns ids into paths (Shift+arrows and Select all use
  it): with id-based writes it goes and a range is instant. A card says `stale`: a bulk edit must skip a **missing** photo and say how many (the server refuses it), and take the new
  stamp a write returns into the card (`applyEditedRecords`). Both halves exist for bulk tags *(findings #566)*: the page leaves a photo whose card said `missing` out of the paths it sends
  (`state.library.missing`, by `pathKey`, kept as cards arrive; the confirmation names the count actually written; the selection panel says how many are left out; a selection of only missing
  photos sends nothing), and `tagging.change_tags` skips a photo whose file is gone and writes the rest (`details["skipped_missing"]`, the reply's `skipped_missing` and `skipped`), so a stale card
  never stops a batch; 9d's other bulk writes should take the same two steps. Every write of the page goes through `queuePhotoWrite`, which is what re-reads the navigator's counts: a new write path that
  does not will leave the counts as they were until Refresh view. The grid's keys call `handleCardSelectionClick`, `selectInLibrary` and `openLibraryPhoto` and know nothing else of a photo.
  `state.nav` is the place the search (9e) reads "within what the navigator has selected": `state.nav.followed`.
- **Known limits.** Shift+arrows only add *(in a library view 9d-2 lets a step back take away)*. After Enter the photo panel hides the grid and the focus is on nothing; Tab starts from the top of the page (the card the keys were on is still the
  grid's tab stop). Adding from the banner shows no
  progress (the folder's pollers are idle in a view): Refresh view when it has finished. A membership walk abandoned at its deadline goes on to its end (the next asker is answered by it);
  a view with subfolders of 1,000 photos or more asks for the disk only when told to, so it can be a day behind the disk. The card stat budget is 1.5 s a batch: past it the cards are
  unmarked, quietly. A card is an `option` of the grid's listbox, though in a folder view it can hold an editable title input: a `gridcell` would need a row structure and counts a windowed
  grid cannot give, so the option stays and the inner controls are `aria-hidden` with `tabindex=-1`; their own keys are left to them (a title being typed keeps all its keys, Space and Enter stay
  the checkbox's and the magnifier's) and every other key of an inner control acts on its card *(findings #571)*. Counts shown are `toLocaleString()`, so a thousands separator is the browser's.
  A card's title is still the file name. A library view of a folder with a very long path names it on several lines in its header. The navigator cannot show a folder "gone from disk" (the route does not say).

### Phase 9d-1: bulk edits by photo id, as a job; a selection's tally *(built 2026-10-03; branch `arch/phase-9d1-bulk-jobs`)*
Server only: no page, nothing the owner sees until 9d-2. Nothing is migrated. Today a bulk edit from a library view fetches every
selected photo's path (`selectInLibrary`: about 340 requests for 68,000) and posts them to the synchronous `/api/photos/bulk-tags`,
which holds the lock of changes of photo files for the whole request, is capped at 5,000 and has no progress or cancel. Three
pieces replace that for a library view; the folder view's routes (`bulk-tags`, `time-shift`, `rename-photos`, their 5,000 cap) are
untouched.

- **A SELECTION** (`tagpup.services.selection`) is `{"ids": [...]}` (at most 20,000; duplicates are one photo, an id with no photo
  is not in it) or `{"source": {"kind", "value", "recursive"}, "excluded": [...]}` (every photo of a library-view source but at
  most 20,000 excluded ones). A page that has Select all of a keyword and then deselects 300 photos sends the source and 300 ids,
  not 68,000. **One resolver** turns either into the ids that exist, in order, each once: an ids selection in the order named; a
  source in exactly `/api/library/ids`'s order (it calls the same `store.all_ids`, a test holds the two equal for every kind of
  source), read in **one read transaction** (`store.source_ids`: the dated photos and the undated are two statements, and a photo
  dated between them -- another bulk time shift, a sync -- would be listed twice or not at all by two snapshots; a test re-dates a
  photo between the statements and fails without the transaction). A selection of more than 200,000 photos is refused, naming how
  many it was, and the cap counts the source, not what is left after the exclusions.
- **THE TALLY** (`POST /api/library/selection/tally`) replaces the panel's "Not tallied for a library view": `{"total", "tags":
  [{"tag","count"}], "more_tags", "people": [{"name","count"}], "more_people"}`, alphabetical by `tag_sort_key`, at most 500 each (the
  most used kept, the rest counted). From `photo_tags` (by tree node: a tag no node holds is not counted, as the navigator's counts
  leave it out) and `photo_people` (names that differ only in case are one entry, each photo once), one grouped read over the
  selection. The ids go into a TEMP table of the read-only connection (a read-only connection may make one) and the photos are
  looked up by key from it (`sel CROSS JOIN photos p ON p.id = sel.id`: left to itself the planner scanned the whole library's index for
  one photo, 20 ms for one id, 1 ms now); a source is joined in SQL and its excluded ids taken out there. **Measured on photo_index
  itself, read-only** (`scripts/measure_bulk_jobs.py --scale`, 68,472 photos, 151,414 `photo_tags`, 80,060 `photo_people`; medians of
  3): resolve the whole library 23 ms; tally the whole library as a source 160 ms (872 tags, 413 people), minus 20,000 excluded 139 ms;
  20,000 ids 88 ms, 68,000 ids 304 ms, one id 1 ms. The tally is not capped at a job's 200,000.
- **THE JOB** (`tagpup.jobs.bulk_edits`; the work of a chunk is `tagpup.services.bulk_edit`). `POST /api/library/bulk/start` with
  `{"op": "tags"|"people"|"time_shift", "selection", "params"}` reads and checks the edit (`bulk_edit.prepare`: a tag the rules
  refuse, a person the tree files in two places, a shift of 0, nothing to do, a tag both added and taken off -- each a `400` with a
  sentence and nothing begun), resolves the selection on the server and returns `{"job", "total", "requested", "missing",
  "excluded"}` while a thread of the server does the work; `GET .../status?job=` is an in-memory read (no query of the library:
  a test fails if the status reads the library's run or the job's record), `POST .../cancel`, `POST .../resume`.
  - **Chunks of 25, the lock per chunk.** Each chunk resolves its ids to their rows' paths **at that moment** (a photo renamed
    meanwhile is found by id; one deleted is `skipped_missing`), takes `file_changes.exclusively()` for the chunk only and writes it
    by the machinery a single save and Add to all selected use: the photos' fields read, planned and committed in the journal, each
    file **read again before its write** (a file another program changed meanwhile is a conflict, reported and never overwritten), written,
    its row and the derived tables told in the same transaction that marks it done, the files read back. A job **adds and removes
    against each file's own tags** (`change_tags`), never a list; a person goes through `vocabulary.person_tag` (the one filing
    rule) and the leaf rule (`persons`: a file naming the person already is not written), no node of the tree is made; a time shift
    moves each date field the photo holds (`photos.date_shift_plan`, the folder shift's own plan), a photo with no Date Taken is
    `unchanged`. A held and a not-held photo would be written as ever (`libraries.split`), but a photo named by id has a row, which makes
    its folder the library's (`store.folders.holds`), so a bulk edit by id is always journaled; a row deleted between the chunk's
    lookup and its write leaves a file-only write, a race of milliseconds on a photo being deleted.
  - **Between chunks the job lets a waiting write in.** The lock is not fair: the thread that has let go of it can take it again
    before a waiter has woken. `file_changes.waiting()` counts the threads blocked for it and the job waits (at most 5 s) while it is
    above zero. A test makes a single-photo save wait in the middle of chunk 2 and shows it complete before the job's third chunk
    writes. **Honest limit of that test:** without the wait the save is not starved on this machine either (the job's own reads and
    state writes between chunks give the waiter time to wake), so the test fails the old design of one hold of the lock for the whole
    job, not the unfair re-acquire; the wait is the guarantee, not an observed need.
  - **What is counted.** Every photo of a chunk is exactly one of `changed` (a file written and read back), `unchanged` (holds what it
    was to hold, or no Date Taken), `skipped_missing` (no row, or its file is gone), `skipped_damaged`, or an **error entry** (the
    first 50 `{id, name, why}` are kept, every one counted in `error_count`). A photo that cannot be read, an unwritable file, a
    conflict: an error, and the job goes on. A photo on a **network share that does not answer** is an error too and is never sent to
    ExifTool (`bulk_edit._reachable`: a bounded stat; asked of ExifTool it would cost its five-minute deadline and then one more for each
    photo of the batch it is retried singly for). **Five chunks in a row in which nothing could be done** stop the job as `failed`, with
    the last sentence; **an exception that is not one photo's** (ExifTool that cannot start, the library gone) stops it as `failed`
    with the message. A chunk **refused as a whole** (a share holding a recorded-damaged photo did not answer; the roots changed) is
    that chunk's photos as errors.
  - **One at a time per library.** A second start is `409` with a sentence naming the one running (its kind and how far it is); the
    claim is in the library's `job_runs` as Verify's is (`bulk edit`), so another process is refused too (a test of two starts at once
    gives one `200` and one `409`). A server drain waits for a running job (`lifecycle.long_work`: "1 bulk edit(s)"). The job holds its
    Library, not the page's: the page may close or switch library and the job goes on, its status reachable.
  - **The Activity page** lists the run (`job_runs`, `outcome` running/done/failed/abandoned) with `what` -- "bulk tags: add 1 and
    remove 1 tag(s), 1,200 of 5,000 photos [done]", no tag or person named -- and the counts, rewritten at most every 2 s while it
    runs and at the end, never per photo. A cancelled job is `done` there (the table has no cancelled) and says so in `what` and
    `state`.
  - **The journal.** Each chunk is one journaled change of its held photos, named after the job (`bulk tags (job 12)`, `bulk people
    (job 12)`, `bulk time shift (job 12)`): **a job of 68,000 photos is about 2,700 entries in History**, each undoable by the existing
    mechanism and none by a job-level undo. Decided, not gold-plated (the owner said not to build undo): the lock is held per chunk, so
    a chunk is the unit that is planned, committed and can be settled after a crash; a change for the whole job would hold a plan of
    68,000 files open for hours. Collapsing a job's changes into one History line is a page-or-History task (9d-2 or later).
  - **Interrupted part-way.** The thread dies with its process and leaves a `running` row; the status says `abandoned` (the row's
    owner is not alive) with how far it got ("TagPup was closed before this finished: 1,200 of 5,000 photos were done."), and the
    next claim marks the row so. The chunk in flight when it stopped is settled by the journal's existing settle (planned files are
    written forward at the next write or start). **Tags and people are idempotent** (adding a tag a photo holds, taking one off that
    it lacks, changes nothing): start them again; a resume of one is `400`. **A time shift is not**, so its resolved list (`<job>.ids`)
    and its record (`<job>.state.json`: counts, the first 50 errors, `done` -- the photos settled -- and `inflight` -- the end of the
    chunk about to be written, kept BEFORE it is written) are kept in `cache/<library>/bulk/` (`Library.bulk_jobs`,
    `tagpup.files.job_files`: atomic replace, fsync, a damaged file reads as none; nothing the rows or the files
    depend on). **They are kept only while a resume is possible** *(review, #580)*: the state file can hold an edit's tags and people and the
    names of files that failed, so a tags or people job removes both files when it ends (done, cancelled, failed), and a time shift when it
    ends done; a time shift that is cancelled or failed keeps them, and a job's files are swept together by its last activity (30 days when a resume is possible, 7 otherwise; see below) when the next job starts.
    A finished job's status then comes from the library's record of the run (`job_runs`: `state`, `op`, the counts; the first 50 errors are
    lost with the file). Nothing a person or an exception wrote is kept in `job_runs.note` or the state: a failed job's message is a fixed
    sentence and the exception's class name ("It could not go on (FileNotFoundError); the server's log says why."), the detail is logged;
    each error's `name` and `why` are cut at 200 characters.
    **Resume** (`POST /api/library/bulk/resume`, a time shift that is `abandoned`, `cancelled` or `failed`) *(rewritten, #577, #578; the journal rule below, #595-#634)*: the state names the journal change of the chunk in flight (`journal_chunk`, cleared before each chunk, set once the chunk is planned and before its first file is written); a resume refuses (`400`) when the journal no longer holds it (or holds another operation's under that id, or pruned it), refuses (`409`, could not settle) while any file of the chunk is still planned, writing, or in a change being undone, and takes over its own job's changes whoever owns them but never an undo. A chunk the owner undid is shifted again (said in the resumed job's `message`); an undo begun in another process between the resume's reads is not seen. A power loss between volumes is accepted. The changes of a bulk time shift that has a record to resume from (its list of photos and its state, kept 30 days from its last activity, and while it runs) are never pruned, whatever the age: they are the only account of which photos of its last chunk were shifted, and a resume without it could shift them twice. Once the record goes their changes are pruned like any other. 
    the record is the state file plus the journal, **never this process's memory of the job** (two processes share a library: the installed
    app and the repo-run app). The state file carries a **sequence number** (`seq`, one more on every write). A resume takes the claim
    first (outside the module lock: it may ask the system who is alive, which takes seconds, and status and cancel never wait for it), reads
    the state under it, settles the journal and asks it -- the one record of what was written -- which photos of the in-flight chunk a
    change **named after the job** left `done` (those are not shifted again and are counted `changed`; `inflight` is carried into the resumed
    job, and the count credited from the journal is not written into the state until the chunk is finished, so a second resume cannot count
    them twice), checks that the file's `seq` is still the one it read (else `409` "Another TagPup process moved this job on: reload and look
    at it again.", nothing begun), and ONLY THEN registers, writes and starts the job. If the settle (or anything before the start) fails --
    the library busy under another process's long write -- the claim is given back, the state file and the job are exactly as they were, and
    the route answers `409` "Could not settle the last chunk that was being written: try again in a moment." (never `200`); the next Resume
    starts from the intact record. `status` of a job that is over in this process but has moved on in the file is the file's. A test crashes the process at the 30th write, between a file's write and its row, and before a chunk planned anything, and
    after each resume every photo's file was written exactly once. `GET status?job=&ids=1` adds `shifted_ids`, from the same journal:
    exactly which photos a job shifted. **Deviation from the brief:** it said refuse a resume unless abandoned/cancelled; a `failed`
    shift (ExifTool gone mid-job) is resumable too, since otherwise its first half could never be completed without shifting it again.
  - **A cancel** stops after the chunk being written (a test: 50 of 60 done, the 3rd chunk never starts) and the report says how many were done; a cancel after
    the end is a `200` with `cancelling: false`.
- **Measured** (`scripts/measure_bulk_jobs.py --run --photos 500`: 500 small real JPEGs, the real ExifTool, rows as the indexer records
  them, the app called in-process through Flask's test client in a temporary TAGPUP_HOME, deleted afterwards; the machine was not
  quiet):

  | the action | result |
  |---|---|
  | (a) a tags job over 500 photos (add one, take one off) | 26.9 s: **18.6 photos a second**, a chunk of 25 is 1.34 s; 500 changed, 0 errors |
  | (b) a time-shift job over the same | 21.7 s: **23.0 photos a second**, a chunk 1.09 s |
  | (c) a single-photo save made during (a), 18 of them at 300 ms intervals | median 1,262 ms, longest 1,580 ms: it waits for the chunk being written and does its own work (a chunk is 1,343 ms) |
  | (d) the status request, polled every 20 ms while the job ran | median 2.8 ms, p95 17 ms (the process is busy writing), longest 49 ms; no query |
  | (e) a second start while one runs; cancel to stopped | `409`; 599 ms (the rest of the chunk) |

  So **68,000 photos is about an hour** at this machine's pace, in chunks of a second and a third each. What a photo costs is the
  per-file precondition the brief asked to keep (each file read again before its write) plus ExifTool's write and the read back; a
  longer chunk would raise throughput a little and the wait of a single save with it. Not tuned.
- **How it fails**, each a test (`tests/test_selection.py`, `test_selection_tally.py`, `test_bulk_edits.py`, `test_bulk_edits_real_exiftool.py`
  -- the last three with the real ExifTool): a restart mid-chunk, between a file's write and its row, before the chunk planned anything
  (resume writes every file once); a cancelled and a failed shift resumed; resume refused for a job that is done, running, a tags job, or
  whose list is lost; two starts at once; a photo deleted, renamed or replaced while the job runs; a file edited by another program
  between the plan and the write; a damaged photo; an unreadable file; a write that fails; 55 errors (50 named, 55 counted); ExifTool that
  cannot start; five failing chunks; a chunk refused as a whole; a share away (some photos; all of them); a library at schema 18; the roots
  gate; this PC only; a library switched meanwhile; the status of a job the process did not run; a person filed in two places, named by
  a path, already named by their leaf, taken off every way the tree files them; a blank name; a tag the rules refuse (before anything
  starts); a time shift of a photo with no Date Taken and of one holding both date fields; selection of 1, of ids nobody has, of
  duplicates, above the cap, of a source minus more than it holds.
- **A share that stops answering in the middle of an ExifTool command** *(was "could not be made safe"; #581)*. The session's deadline is 300 s
  and `field_values.read` retried a timed-out batch a photo at a time, each with its own 300 s: 2 h 10 min for one chunk of 25, with the lock
  held. Now `field_values.read` treats an `ExifToolTimeout` (or a dead process) as the batch's answer -- every photo `Unreadable("ExifTool
  did not answer in N s")`, nothing retried singly (a batch refused by one bad path is still retried singly) -- and a bulk chunk opens its
  OWN `ExifToolSession(timeout=60)` (`bulk_edit.CHUNK_TIMEOUT`), started before the lock is asked for, closed at the chunk's end, wrapped so
  that **once a command has timed out every later command of the chunk answers the same at once** (pyexiftool would start its process again
  and wait a minute more for the next file). A chunk's worst case is about a minute, counted as errors, nothing written to a file whose
  read timed out, the lock released; five such chunks in a row stop the job. The session is not reused across chunks (starting one is
  small against a chunk of a second or more); the held and single-photo paths are unchanged. `settle` inside a chunk still uses the
  default session, which matters only when a crash left changes to finish.
  Also in this pass: a shift that moves a date out of range (before the year 1, after 9999) is that photo's **error** ("the shifted date
  would be before the year 1") and not `unchanged`, only for the job (`dates.shifted_strictly`; Camera Time Shift still leaves such a
  photo alone), and `validation` refuses a shift of more than 100 years (52,560,000 minutes) up front; the journal's question
  (`file_journal.photo_ids_done`) drives from `changes` and finds each change's files by `idx_change_files_change` (the plan was a scan
  of every `change_files` row; no migration was needed; 67,500 rows in 2,700 changes asked in well under 0.5 s, a plan test holds it).
- **The durable-record principle** *(follow-up review, #583-#589)*. A time shift is the one operation that cannot be done twice, and every
  way it could be done twice was a way its progress record was missing, stale or false. The rule now: **nothing is written to a photo file
  until the record that lets a resume know about it is durably written, and a record is never older than the files.**
  - **The record before the chunk is mandatory** (#583). `inflight` (the end of the chunk about to be written) is written before the chunk's
    first file, tried 5 times with a doubling wait (0.1 s) -- and `job_files` itself replaces the file with 5 tries of its own for the
    Windows error of replacing a file another handle holds open (the other TagPup process's status poll, Defender, the search indexer);
    if it still cannot be written the job stops as `failed`, resumable, with "It stopped before writing the next chunk because it could not
    record where it had got to; nothing of that chunk was written", before the chunk's first file. The write AFTER a chunk is progress and
    best-effort: if it fails, the older record still covers the chunk (the journal says what it wrote) and the next before-chunk write is
    the mandatory one. Readers (`read_state`) open the file only for the read and ask again a few times when it is being replaced; a
    running job's status is memory and reads no file. A write that failed does not use up the sequence number (#589).
  - **A command that stalls leaves the chunk's files UNKNOWN until they are read** (#584). After a timeout in a time shift's chunk the
    chunk's session stays dead; the job then opens a fresh session (`CHUNK_TIMEOUT`, 60 s), under the same lock, and reads every planned
    file that is not recorded done (`file_changes.reconcile`): one holding the shift is DONE (its conflict row recorded done, counted
    changed -- a conflict for a file that holds the target is not a conflict), one holding what it held is NOT WRITTEN (its row taken out
    of the change; the error says "read afterwards, the file was not shifted"), one holding neither is a conflict ("could not confirm
    whether it was shifted"), and one that cannot be read is UNKNOWN: the job stops as `failed` with the chunk still IN FLIGHT in its
    record and counts nothing of it. A resume reads the journal's conflict rows of the job for the chunk in flight the same way (and
    refuses, changing nothing, if it cannot), so it never plans again from a file that may be shifted. Tags are idempotent and are not
    reconciled.
  - **All or nothing around the thread** (#585). `start` and `resume` register the job, write its files and start its thread inside one
    block: if anything raises before the thread runs, the job is unregistered (the job it replaced is put back), the claim's row is
    deleted, the files a start made are removed, and the caller gets a `409` "The bulk edit could not be set up (OSError): nothing was
    changed. Try again in a moment." -- never a registered job with no thread, which would refuse every start, ignore a cancel and hold
    an update's drain.
  - **One identity across runs** (#586). The job id is its first run's id; every later run of it carries `job` in its counts, and the
    status resolves the id to the LATEST run of the chain (`_chain`), so a resumed job that finished is `done` and not resumable after a
    restart, from the other process, or once it has left the 20 kept in memory. `resumable` is true only where a resume would work:
    the list of photos AND the state both exist (`has_record`); a job whose record is gone is never promised a Resume. A process dying
    between removing the record and ending the run is reported as `abandoned`, not resumable, with "Its record is gone, so it cannot be
    resumed (History lists what was shifted)."
  - **The sweep goes by the job's last activity, and a job's files go together** (#587). A job's `.ids` and `.state.json` are one unit,
    aged by the newest of them (the state file is rewritten whenever the job runs or is resumed), kept **30 days** when both exist (a
    time shift that can be resumed) and **7 days** otherwise; an expired resumable job's last run in `job_runs` is amended `state:
    "expired"` and the status says "Too old to resume: its record was removed after 30 days." (`state` `expired`, `resumable` false,
    Resume `400`).
  - **The library keeps a resumable job's first run** (#588): `job_runs` keeps 50 runs per job, but never deletes a run whose id is a
    job a resume can still carry on (`job_runs.finish(keep=...)`); a resume that is refused deletes the row its claim made instead of
    ending it as a failed run, so refusals do not use the rows up (sixty refused resumes leave the job resumable).
  - **Limits stated** (#589). A command of a bulk chunk has **60 seconds** (`CHUNK_TIMEOUT`): one very large file on a very slow share can
    never be written by a job -- it fails with "ExifTool did not answer in 60 s", a clear error, not a hang. The claim's refusal says
    "in another TagPup process" only when the claim is another process's.
- **What could not be made safe.** (1) (see above: made safe.) (2) A tag no tree node holds is not in the tally. (3) A
  time shift of a photo whose row vanishes between the chunk's lookup and its write is written to its file without a record; a resume
  cannot tell it (milliseconds, on a photo being deleted).
- **What 9d-2 must know.** (a) The page sends a SELECTION (ids, or the view's source and the ids it excluded) and never paths; `selectInLibrary`
  goes, a Select all of 68,000 is instant. (b) `start` -> `{job}`; poll `status` once a second (cheap; do not poll a `done`/`cancelled`/`failed`/`abandoned`
  job); show `done` of `total`, `changed`, the counts left out (`skipped_missing`, `skipped_damaged`) and `error_count` with the first 50
  `errors` (`id`, `name`, `why`), `eta_seconds`, and the `message` (a sentence for cancelled/failed/abandoned). `409` on start names the job
  running: the page should offer to show it. `resumable` says when a Resume button applies (a time shift that stopped); a tags/people job that was
  abandoned or cancelled is offered "Start again". (c) The server clears the folder view's cached scans as the job writes
  (`forget_scans`), but the page must refresh a library view's navigator counts (`navigatorCountsChanged`) and the cards it holds when
  the job ends, and re-read an open photo (a record read before the job names the old stamp: the next save is refused as changed on disk,
  which is right). The job is not in `queuePhotoWrite`: single saves interleave between its chunks, so the page need not wait for it, but it
  should not offer a second bulk edit. (d) The confirmation ("Add X to N photos?") can use the tally's `total` or the start's `total`
  (the photos that exist now, which can be fewer than the page thought: `missing`). (e) The tally route is what fills the panel for a
  selection; it is not capped at 200,000 and its lists carry `more_*`. (f) A person's name is sent bare (`params.add: ["Rowan Thackeray"]`
  for op `people`, or a full path, `People/Friends/Rowan Thackeray`, for a person filed twice); a refused name comes back as a `400` sentence.
  (g) History will show a job as one change per 25 photos.

### Phase 9d-2: editing across folders -- the selection by id, bulk edits through the job, the strip, the tally *(built 2026-10-03; branch `arch/phase-9d2-bulk-page`)*
The page for 9d-1's server. Nothing is migrated. The one server addition is `GET /api/library/bulk/current` (below). The owner sees: **Select all** of
the whole library selects at once and asks nothing; a Shift-click across 20,000 photos the grid never held selects them at once; the selection panel lists
the tags and people of the selection with their counts; **Add tag**, **Add person**, the **x** of a tag or person in that panel and **Shift Date Taken** each ask
once by name and count and then run as a job that has a strip under the header -- a bar, "done of total", what was changed, left as it was, missing, damaged
and refused, the time left, **Cancel** -- which is still there after the view changes or the page is reloaded.

- **A library view's selection is photo ids** (`selected.js`; `state.library.sel`, so it is the view's and goes with it). Two shapes, one at a time:
  `{mode: 'ids', ids: Set}` -- the photos picked -- and `{mode: 'source', excluded: Set}` -- every photo of the view's source but those. The count is `ids.size`, or the
  view's total less `excluded.size`. **Select all** assigns `{source, excluded: {}}`: no request, no id, 68,472 photos in 57 ms to the count shown and every card painted
  selected (it was 5.9 s). A click toggles an id (in `source` mode: membership of `excluded`). A **Shift range** (Shift-click, Shift+Space, Shift+arrows) is a loop over
  `lib.ids` between two indexes -- the card need not be there, nothing is fetched -- and **stepping a Shift+arrow back toward where the run began now takes
  the photos off** (the 9c limit is lifted for a library view; a folder view's keys are as they were). **Invert** swaps the shapes (picked ids become the excluded ones and
  back) while the list swapped is at most 20,000; past that it says "Too many to invert: use Select all and deselect." and changes nothing. `state.selectedThumbnails` and
  `selectedKeys` stay empty in a view (every reader audited: the folder's panel, Smart Rename and Suggest work on a folder and read nothing else); cards show
  `isIdSelected(photo.id)`. The selection is cleared by every view change (9b-1's rule), kept by Refresh view (ids the refreshed view no longer holds are let go:
  `reconcileIdSelection`) and by edits; a photo found deleted when its card is asked for leaves the view and the selection (`dropPhotos`).
  `selectInLibrary`, `pathsOfIds`, `idsBetween`, `cancelLibrarySelection`, "Selecting N of M..." and "Select all N photos?" are gone; so are `leaveOutMissing` and the page's own
  "N photos are missing" count: the server skips and counts a missing photo (9d-1) and the strip's summary says how many.
- **What a request can carry** (`selected.js selectionRequest`, decided from the counts, nothing built until it is sent): `{ids}` for at most 20,000 picked, in the view's
  order; `{source, excluded}` for at most 20,000 excluded; and -- **added here, not in the brief** -- the same selection said the other way round when only that way fits: 50,000
  picked of 68,000 is sent as the source and the 18,000 left out, a Select all less 50,000 as the 18,000 ids that remain (only when the page holds the whole order). What fits neither
  (30,000 picked of 68,000: not 30,000 ids, not all but 38,000) is **said in a sentence in the panel as soon as it is so** ("30,000 photos are selected and 38,000 are not: a bulk
  edit can name at most 20,000 photos one by one, or everything in the view but 20,000. Select fewer, or Select all and deselect.") and again, as an alert, if an edit is tried;
  more than 200,000 photos selected is refused the same way. Nothing is asked of the server for any of these. **Honest limit:** that last case is a real hole between the two
  shapes the server takes -- a selection in the middle of a large view, between 20,000 and all-but-20,000, cannot be sent. The way round is Select all and deselect, or the
  navigator to a smaller view.
- **The edits** (`bulk-edit.js`; the words are `bulk-words.js`, plain functions). Each is asked by name and count, always in a view even for one photo -- "Add Trips/Lighthouse
  to 3,412 photos? This changes the photo files and takes about 4 minutes. It cannot be undone as one step; remove the tag to reverse it." (the time at an assumed 15 photos a
  second until the job reports its own; 9d-1 measured 18 to 23) -- and over 5,000 a second time, "This is 67,997 photos. Continue?". **The selection is read before the question
  and that is what is sent**: a click made while a placement dialog is open changes nothing that was asked; a second click on Add while the first one's question or dialog is open
  asks nothing (`state.bulk.asking`: four rapid clicks and Enter are one question and one request). A name is resolved (`resolveTagOrPerson`, which may make a node of the tag tree
  and may ask where a new person goes) only **after** the question, as in a folder, and a person is sent as the path it resolved, op `people`. The pills of the tally: **x** removes the
  tag or person from the whole selection (op `tags` or `people`, `params.remove`), the arrow adds it to every photo (shown only when the count is below the total). **Shift Date Taken**
  in a view is the minutes and a direction (Later or Earlier; the camera filter is not offered), checked beside the field: 0, negative ("Enter the minutes as a positive number and
  choose Earlier or Later."), a fraction, empty, and more than ten years of minutes (5,256,000; **a limit of the page's own, not a rule of the server's**: a mistyped zero otherwise
  asks ExifTool to move a date out of the file's range) are each a sentence and nothing is asked. A note on the panel says both Date Taken fields of a photo move and one with none is
  left as it is. Smart Rename stays off in a view (a name is per folder) and so does Ctrl+D.
- **The job's client** (`bulk-job.js`; state is `state.bulk`). `startBulk` POSTs `{op, selection, params}`; **one bulk job at a time**: while one runs (or one is being started) the view's
  Add buttons, Apply Time Shift and the tally's pills are disabled with the tooltip "A bulk edit is running (see the strip at the top)..." (`lockBulkControls`; a folder's own bulk
  writes are another route and are never disabled), and a `409` from the server shows its sentence in the strip with **Show it**, which asks which job runs and picks it up. A start whose
  answer is lost (the network dropped after the server took it) says so and asks what runs, once, two seconds later. **The status is polled about once a second** (`POLL_MS`;
  every 5 s while the tab is hidden, and at once when it is brought back); a request unanswered in 15 s is a miss and is asked again; after 3 misses in a row the strip says "TagPup
  is not answering (...). The edit goes on if TagPup is running; still trying."; the pause between tries grows to six times as long; **after 40 in a row it stops** ("has not answered for a
  long time") and offers **Ask again** -- never for ever; the first answer clears the message. A `404` for the job stops the asking at once. The status of a finished job is never polled. **Cancel**
  is one request however often it is pressed ("Cancelling..."), and the job stops after the chunk it is writing; pressed as the job ends, the server's `200` carries the end and the strip shows
  that. **Resume** (a time shift that was cancelled, failed or abandoned: `resumable`) calls `/api/library/bulk/resume` for the same job; **Start again** (a tags or people job that
  was cancelled, failed or abandoned) sends the same selection and edit it started with, kept in memory -- so after a reload, when the page no longer knows the edit, the strip says what
  was done and "Select the photos and make the edit again to finish it: adding or removing what a photo holds already changes nothing", with no button to a guess.
- **Finding it again** (`GET /api/library/bulk/current`, new: `tagpup.jobs.bulk_edits.current`). The job is the library's: the strip is under the header, not in the view or the folder,
  and it survives opening another view or going back to the folder. The page asks as it starts and as a view opens (one question when they come together, none within five seconds of
  the last); it answers `{job: status}` for the job running (here or in another process), else the latest that stopped part-way -- cancelled, failed, abandoned by a restart -- if
  it began within 30 days (the cache folder sweeps its list of photos then), else `null`; `done` and `expired` are never offered. A stopped job that was **Dismiss**ed is not offered again to
  this browser (`localStorage`, per library). A different library's page asks under its own name: it cannot show this one's strip. A read that fails shows nothing and logs a warning
  (the strip is an offer; a second start is refused by the server anyway).
- **When a job ends** (`bulk-job.js jobEnded`, once for each end): the status line says the summary -- "Added Trips/Lighthouse to 3,380 photos; 32 missing on disk were skipped; 0
  errors." (a time shift: "Shifted Date Taken 90 minutes later in 4 photos; 1 with no Date Taken was left as it was; 0 errors.") -- in the error colour if the job did not finish
  or had errors; the navigator's counts are read again at once; **every folder scan this browser kept is forgotten** (`forgetFolderCaches`: they name the old stamps and tags); the
  cards in the window are asked for again (`refreshHeldCards`: their `thumb` carries the file's new stamp, and `stale` is what the file is now; the others are let go and asked for when
  scrolled to); the photo in the details panel is read again unless it has edits of its own (`reloadChangedPhoto`; else the next save is refused as changed on disk, which is right); and the
  selection is counted again. The strip keeps the sentence, **the first 50 errors** (`<details>`: file name, why; the rest "N more, not listed"; long names wrap) and the counts until
  **Dismiss**. A job is not in `queuePhotoWrite`: single saves interleave between its chunks (9d-1). "Shown on the Activity page too" links to it.
- **Reading it** (a11y). The strip is a labelled region with a native `<progress>`; the buttons are buttons; it never takes the focus when it appears (Dismiss moves the focus to the grid, not
  to the top of the page); a separate polite live region is told at the start, at each tenth, and at the end -- 25 polls with the same answer say nothing.
- **The tally** (`tally.js`; `POST /api/library/selection/tally` from 9d-1). 250 ms after the selection stops changing the selection is counted on the server; the two lists say
  "counting..." meanwhile (never the last selection's chips); a request the selection has left behind is aborted and an answer that is not the newest, or is for a view that has closed,
  is dropped; none selected asks nothing; alphabetical by `compareTagNames`; at most 500 each with "N more, not listed"; names are text, never markup. A selection that cannot be sent
  says "Not counted: see the note above." (the whole library may be counted: a tally is not limited to what a job takes). The folder view's panel is its own and is untouched.
- **Measured** with `scripts/measure_bulk_page.py --run` (plan without `--run`; `--code-root <git archive of the trunk> --only abc` runs only (a) to (c) on the trunk, the baseline): a
  sandbox copy of photo_index (68,472 photos) and a small library of 500 JPEGs of its own, headless Chromium 1600 x 1000, a fresh browser for each, 3 rounds, thumbnails answered by the browser's
  routing, dialogs accepted. **The trunk has no selection by id**: its Select all, Shift range and Invert fetch the card of every photo for its path (`selectInLibrary`), so (a) to (c) are the
  same clicks on both. The machine was not quiet; run to run these move by up to 40%.

  | the action | trunk (`selectInLibrary`; median; range) | this branch |
  |---|---|---|
  | (a) Select all of 68,472: click to the count shown and every card painted selected, main thread idle | 5,858 ms (5,601-7,501); JS heap 61 MB | 57 ms (46-69); heap 6.9 MB |
  | (b) Shift-click from the first photo to the 19,948th (the grid scrolled there; the cards between are not held) | 1,588 ms (1,463-3,492); heap 47 MB | 49 ms (41-59); heap 10 MB |
  | (c) Invert of that (68,472 less 19,948 = 48,524) | 6,316 ms (5,433-6,972) | 63 ms (51-458) |
  | (d) the tally panel for all 68,472 selected: click on Select all to the tags and people painted | no tally | 731 ms (664-768): 250 ms is the wait, the request 224 ms (211-320; a 34 KB reply, 500 tags and 413 people), and about 250 ms after the reply is not attributed (913 chips, 2,779 nodes; no task over 50 ms) |
  | (e) a tags job over 500 small JPEGs, the real ExifTool: click on Add to the strip shown / to the first progress shown | no job | 66 ms / 2,136 ms (the first poll at 1 s, and the first chunk of 25 takes 1.3 s) |
  | (e) the page while it polled: 28 status requests in 28.7 s (17.4 photos a second) | | longest task 0 (none over 50 ms), the main thread busy 1.2% of the time, 12.7 ms of it per poll (everything the page did) |
  | (f) the strip when the server reports 3,000 errors (50 named): the answer to the 51 rows painted | | 1.9 ms; 120 nodes in the strip; the longest task of the page's life 57 ms (its start, not the strip) |

  The sandbox was deleted afterwards. What the numbers do **not** show: a browser with a GPU and an extension or two; a library on a network share (the job's cost is the server's: 9d-1); and
  the (d) cost after the reply, which is unattributed.
- **How it fails**, each a test (`tests/frontend/library-selection.test.mjs`, `bulk-edit.test.mjs`, `bulk-job.test.mjs`, `library-tally.test.mjs`; `tests/test_bulk_edits.py` for `current`):
  Select all of 68,000, 3 excluded, Add tag (the request is the source and the 3 ids, never ids or paths, and not the folder's route); a range across 20,000 unloaded cards (no card request); invert
  at exactly 20,000 and at 20,001; 30,000 picked, more than 20,000 excluded, 60,000 of 68,000, a Select all less 50,000 and more than 200,000 (sentence or the other way round, no request); the
  question refused, the second question refused, the selection edited while the question was open, a tag the rules refuse, nothing selected, four rapid clicks and Enter; time shift 0, -5, 1.5, empty,
  more than ten years, exactly ten, earlier, the both-fields note, a folder's panel unchanged; the pills (alphabetical, tags with markup in them as text, a person, the arrow only below the total); a
  start while one runs (controls disabled with a tooltip, the 409 sentence, Show it, a folder's controls not disabled); the strip's bar and counts, the ETA at 15 photos a second and then the job's own,
  once a second and every 5 s hidden, no announcement for 25 identical polls, the focus left where it was; the end (summary, polling stops, navigator counts, cards, folder scans, tally), every photo
  missing, a time shift's summary, 3,000 errors with long names in 51 rows; Cancel twice, after the end, and unsent; three misses, an answer that never comes, giving up and Ask again; a restart
  (abandoned, Resume, Start again); a page reloaded mid-job, another library's page, a dismissed job, a start whose answer was lost; the tally for 1, 68,000 and 0 selected, three quick clicks (one
  request), a stale answer dropped, counting..., over 500, a failure, a view left meanwhile. The server's: `current` with no job, a running one, an abandoned one after a restart, a resumed one,
  a cancelled one and one older than 30 days.
- **Decisions made, not copied.** (1) The selection lives on the view (`state.library.sel`), not in `state`. (2) The 20,000 limits are enforced when a request is made, not when a selection is
  made (a Shift range of 30,000 is allowed to exist; Invert refuses past 20,000 as decided), and the other way round is used when only it fits. (3) A question is asked even for one photo. (4) The
  ten-year limit of a shift. (5) 15 photos a second. (6) The poll's 15 s, the 40 misses and the six-fold pause. (7) `current` offers a stopped job for 30 days and a Dismiss is remembered per
  library in the browser. (8) Every folder scan in this browser's storage is forgotten when a job ends. (9) The panel's "missing" note became the limit note (`selection-note`). (10) The page
  sends a person as the path `resolveTagOrPerson` found, not the bare name, so the placement dialog a folder shows is shown here too.
- **What could not be made safe.** (1) The hole between the two shapes (above). (2) A job started from a selection read just before another program changed the library: the server resolves the
  source when it starts, so "3,412 photos" in the question can be 3,400 when it runs; the strip says what was done. (3) Nothing on the page can undo a job as one step (the question says so); each 25
  photos is a change in History (9d-1). (4) A page reloaded after a tags job was cancelled cannot offer Start again (it does not know the edit). (5) The 9d-1 limit on a share that stops answering
  mid-command (a chunk can stall much longer than a chunk should; Cancel waits for it) is the server's.
- **What 9e builds on.** A search is a source: `selection.js`'s two shapes already name "the view's source but these" and "these", and the `source` object of `selectionRequest` is the one place that says it
  (`{kind, value, recursive}` of `/api/library/ids`); a search view needs a new `kind` in `library-source.js` and the same in `tagpup.services.library_view.source_of`, and Select all, ranges, the tally, every
  bulk edit and the strip work on it unchanged. `state.nav.followed` is still where "within what the navigator has selected" is read. `lockBulkControls` and `bulkBusy` are the one place that says an
  edit may not start. *(The navigator's union, `any_of`, is the shape a search's `any_of` is: see "Phase 9, the owner's review" below.)*

### Phase 9, the owner's review: several rows at once, People by branch, one sort *(built 2026-10-04; branch `arch/library-review-2`; findings #671-#673)*
The owner installed phase 9 and asked for three things of the navigator (#671-#673; #668-#670, #674 and #675 are another branch's). Server, page
and one index-only migration (22).

- **A source may be a union** (`any_of`, #672): `{"kind": "any_of", "value": [source, ...], "recursive": false}`, each a source of
  the existing kinds -- none a union, at least one, at most 1,000 (`library_view.MAX_MEMBERS`; Refused with a sentence past it: the
  address of more would outgrow what the server reads) -- the same source twice is one, a union of one is that source. Two kinds
  are new because a navigator row holds them alone: `keyword_only` (a keyword's node without the nodes under it) and `year_other`
  (the photos of a year whose date names no month of it: the year less its twelve month ranges, so a year is exactly its months and
  this). **One statement** over `photos p` (`store._union_scope`): the members gathered by kind -- the folders' ids (a folder with its
  subfolders is its own id and every folder under it, by `folders.parent_id`, read once), the tag tree's node ids (`derived.under` or the
  node alone), the people's spellings (one pass of the name index for the whole union, not one a person), the years -- each one
  `IN (...)`, the months and the years' "Other" OR'd; the clauses are OR'd as a **balanced tree**, since SQLite refuses an expression
  nested 1,000 deep and a chain of ORs is nested as long as it is (a test holds 1,000 months). A photo in two members is one row and
  counted once; a member that names nothing (a keyword deleted since the address was written) adds nothing. The walk under a
  folder with its subfolders keeps its own set of folders walked, so a child folder named alone before its recursive parent does
  not stop the walk at it (#695: every order of the members gives the same photos, a test). It is a source like
  any other: `source_of` reads it (the JSON text of a query's `value`, or a list in a body), so `/api/library/ids`, `/view`, a
  selection by source (`selection.read`), the tally and every bulk edit take it unchanged; the #607 widening notice compares the
  job's total with the page's count as for any source.
- **A union travels in a body** *(review, #696)*. The 1,000-source count bounds the work, not the length: 1,000 folders of
  photo_index are a 145,613-character address, and a share's longer paths pass the 262,144 bytes Waitress reads of a request's
  first line and headers. So `/api/library/ids` and `/view` also take a POST of the same words as JSON (`_source_asked`), and the
  page asks every union so (a single source stays a GET of its address's words). The page's own address still holds the union, for
  Back, Forward and a bookmark, and that address is read by the same server: the page refuses, with a sentence and no request, a
  selection whose address would pass 100,000 characters (`MAX_ADDRESS`, `addressTooLong`), the limit that bites first for long
  paths (a test: 600 share folders refused, 300 accepted and posted). An address made longer by hand is refused by Waitress before
  the page loads.
- **How 9e extends it.** A search is `{"kind": "search", "value": {"any_of": [...], "all_of": [...], "none_of": [...], "words": "..."}}`:
  `any_of` is exactly this list and compiles to this clause (`_union_scope`); `all_of` is the members' clauses joined by AND
  (each member's own `p.id IN (...)`, not gathered, since AND of a gathered IN is "any"); `none_of` is `AND NOT (<its any_of
  clause>)`; `words` the FTS match. The navigator's selection is then the `any_of` of a search (or its scope: "within what the
  navigator has selected" is `state.nav.followed`, which is this union), and nothing in the page's selection, tally or bulk
  edits changes: they carry `{kind, value, recursive}` whatever `value` holds.
- **Orders** (#671): `order=taken | taken-desc | name | name-desc` on `/api/library/ids` and `/view` (`library_view.order_of`; another is
  `400`). By Date Taken the photos with none come **after the dated ones in both directions**, by id in the order's direction (newest
  first: the undated from the highest id down). By file name: what follows the last separator of the row's path, **without case**
  (`NOCASE`, ASCII), ties by id in the order's direction; there is no undated phase. The keyset token of an order other than the
  default carries the order and is refused in another (`400`); the default's token is the 3-part one it always was. The page reads
  the order once with the ids, so a sort is a new view: Back returns to the order before.
- **Migration 22** (additive, index only, touches no table, blocks no undo): `idx_photos_name` on `(<file name> COLLATE NOCASE, id)`,
  the file name an expression of built-in deterministic functions (`library_view.name_sql`: `substr(path, length(rtrim(path,
  replace(replace(path, '/', ''), char(92), ''))) + 1)`), so SQLite keeps it and no writer of a path has to know of it -- chosen over a
  column in `photo_meta`, which every writer of a path, the doctor's checks and the migration's backfill would have had to keep.
  Checked by hand against #464 (nothing checks a migration's declared `touches`): a test holds that 22 adds exactly one index on
  `photos` and no column or trigger, that every table's rows are unchanged, and that a change journaled at 21 is still undoable. An
  order by name reads `photos p INDEXED BY idx_photos_name`, walking the index in order and testing each entry for the source (5 to
  25 ms on photo_index for the sources then measured -- *not* for a small range of another index, which walked the whole library
  for each page until #722: see the owner's second review; sorting 15,000 photos by a name computed for each was 130 ms, the whole library
  630 ms); without the index (a library not yet migrated) it is the sort, the same order. On the sandbox copy migration 22 took 0.7 to 1.1 s.
- **The navigator** (`navigator.js`, `navigator-model.js`, `navigator-tree.js`): every tab is a multi-selectable tree
  (`aria-multiselectable`). A click selects the row **and every row under it** (shown selected) and opens a closed branch; Ctrl-click
  adds or takes away a row and the rows under it; Shift-click the rows drawn from the last row picked (Ctrl+Shift adds them); Ctrl+A
  every row drawn; the keys Space/Enter, Ctrl+Space, Shift+Space. **A row's own photos stay when a row under it is taken off**
  (decided): a row holds photos of its own -- a folder's own, a keyword's node alone, a person, a month, a year's "Other" -- and, with
  the rows under it, its whole; `compress` turns the rows selected into the fewest sources (a row with every row below selected is its
  whole: Select all of the folders is the one top folder with its subfolders), so the year 2021 with March taken off is eleven months
  and `year_other`, and Trips with Trips/Coast taken off is `keyword_only` Trips and the other branches. A branch of people or the junk
  years hold nothing of their own: they are the rows under them, and are shown selected when all of those are. **The selection is the
  view's source**, held in the address (`?view=any_of&value=<JSON>&order=`), so Back, Forward and a bookmark restore it, and each tab
  draws its rows of it (`selectedRows`). Decided: **switching tabs keeps the selection** (and the grid); **Ctrl-click in another tab
  adds its rows to the same union** (a month and a person), a plain click or Shift-click anywhere starts again; **a row the filter hides
  stays selected** and the note says how many are hidden; a click that would leave **nothing** selected leaves the selection as it is and
  says so; more than 1,000 sources is a sentence and no request. The "Other" row under a year now opens the year's undated-by-month
  photos alone (`year_other`), not the whole year. The filter box was already there on every tab (9c): typing "July" in Dates lists
  each year's July, and Shift-clicking the first and the last shows every July.
- **People by branch** (#673): `/api/library/navigator?section=people` adds to each person `group`, the tag of the branch above their
  node -- by `person_ids`' one rule (a leaf `has_face` node, not a root, the only one called that name; a branch is never a person) --
  and `groups` (`tag`, `name`, `parent`, `count`: every branch above a person, the photos naming anyone under it, each photo once,
  one pass of `photo_people` grouped by photo in SQLite and rolled up the tree as the keywords' counts are) and `unfiled` (photos naming
  someone with no such node). The tab is a tree: branches nested as the tree nests them, alphabetically, branches before people,
  open at first; those not filed under "Not filed in the tag tree" at the end; a library that files no one is the flat list it was.
  A branch's row selects its people (a union of them). Counted read-only on photo_index: 413 people, 404 filed under 12 branches (two
  deep at most), 9 not filed (117 photos); kr-track: 75 people, 62 filed under 1 branch. The read is 46 ms in-process where the flat list
  was 5 ms.
- **The sort** is one control above the tabs (`#nav-sort`, `data-own-keys`): Date taken oldest or newest first, Name A to Z or Z to A.
  *(Replaced by the header's Sort by, #714: the owner's second review, below.)*
  Choosing one reads the view open again in it (a new history entry) and the views opened after it are read in it until another
  is chosen or an address names one (`state.nav.order`).
- **Measured** with `scripts/measure_library_review.py --run` on a sandbox copy of photo_index (68,324 photos; the copy migrated
  to 22 by the served code before the server started), headless Chromium 1600 x 1000, a fresh browser each, three rounds, medians,
  thumbnails answered a 1-pixel picture by the browser; the trunk measured the same way with `--code-root` (a `git archive` of
  `6714b92`), which has no union and no sort:

  | the action | this branch | trunk |
  |---|---|---|
  | (a) twelve Julys (filter "July", click the first, Shift-click the twelfth): the Shift-click to the first window painted and idle | 109 ms (73-141, five rounds); the ids request 44 ms (31-64), 4,003 photos, 23 KB; no long task | -- (one row at a time) |
  | (b) the People tab: click to its rows painted and idle | 165 ms (145-199); the request 115 ms (97-152), 23 KB; 426 rows, 13 branch headers | 65 ms (64-66); the request 19 ms, 13 KB; 413 rows |
  | (c) the whole library: navigation to painted and idle, by Date Taken | 389 ms (332-677); ids 158 ms | 373 ms (361-646); ids 175 ms |
  | (c) the whole library, the sort changed to newest first / name A-Z / name Z-A | 295 / 294 / 380 ms; ids 132 / 207 / 194 ms | -- |
  | (c) the largest keyword node (41,434 photos): navigation to painted and idle | 541 ms (509-643); ids 359 ms | 522 ms (500-557); ids 306 ms |
  | (c) the same, the sort changed to newest first / name A-Z / name Z-A | 411 / 414 / 529 ms; ids 142 / 83 / 186 ms | -- |

  An earlier run of the branch on a busier machine (the copy took 86 s, not 12) gave (b) 324 ms and (c) 815 / 679 ms for the
  unchanged date order: these numbers move by a factor of two run to run. **The People tab is slower** -- about 100 ms in the page,
  45 ms of it the branches' photo counts in-process (one pass of `photo_people` grouped by photo: 4,179 distinct sets of names), the
  rest a reply twice as long; a header counting its people instead of its photos would cost nothing, and is the owner's call.
  In-process on photo_index read-only (no index of names there yet, so the name orders are the fallback): the id list of 12 Julys
  17 ms either way by date; 400 people 181 ms (44,431 photos, the sort of them); a mixed union (a keyword, a person, a year, a
  month, a year's Other) 72 ms; all 22 ms by date either way, 490-516 ms by name without the index; the 14,838-photo keyword
  34 ms by date, 117 ms by name without the index. With the index (a copy of photos made in a scratch file): all 25 ms, that
  keyword 15 ms, a person 7 ms, either way.
- **Plans** (checked on photo_index read-only; `tests/test_library_union_and_order.py` asserts them on a library made the same way):
  twelve Julys `SEARCH p USING COVERING INDEX idx_photos_taken (taken>?)` with no sort, either direction (the index read from the
  first July, each row tested); a mixed union `MULTI-INDEX OR` of `idx_photos_taken (taken>? AND taken<?)`, `idx_photos_year (year=?)`,
  `photo_tags` by `idx_photo_tags_tag (tag_id=?)` and `photo_people` by `idx_photo_people_name (name=?)` each then `p` by primary key,
  and a temp b-tree for the order; its undated read `SEARCH p USING INDEX idx_photos_taken (taken=?)`; a union of people the name
  index for each name and a temp b-tree; newest first the same indexes read backwards with no sort; by name `SCAN p USING INDEX
  idx_photos_name` (the index walked, nothing sorted) with the source's own seeks as list subqueries, and a keyset page `SEARCH p USING
  INDEX idx_photos_name (<expr>>?)`. The people's branches: `SCAN photo_people USING INDEX sqlite_autoindex_photo_people_1` (80,000
  small rows, not covering: the name is read) and a temp b-tree for the grouping.
- **How it fails**, each a test (`tests/test_library_union_and_order.py`, `test_name_index.py`, `test_navigator_people_groups.py`,
  `tests/frontend/navigator-multi.test.mjs`): a union of overlapping keywords (one photo, counted once); a deleted keyword or person
  in it (nothing from it, the rest shows; all of it deleted is an empty view); a union holding `all`; 1,000 months in one statement;
  1,001 sources, a nested union, JSON that is not a list, a month that is not one (a sentence each); a selection by a union source,
  resolved and tallied with an id excluded; every order's keyset pages equal to its id list for all, a folder, a keyword and a union;
  a token read in another order; the order by name without the index; the migration adding one index and nothing else, an undo
  across it. On the page: twelve Julys by the filter and a Shift-click, Back and Forward to and from it; Ctrl-click on, off, and the
  last row; four Ctrl-clicks with no answer awaited between them; a parent with one child taken off (its own photos stay) and put
  back (the whole again); Ctrl+A of the whole tree (one source) and of 2,705 filtered rows (a sentence, no request); a Ctrl-click in
  another tab and the tab switch; a selected row hidden by the filter; a bookmark of a union with a deleted keyword and an order, and
  four bad ones (no request); Select all of the grid on a union (the union is the bulk edit's source); the People tree, a branch's
  row, a person opened by the address under their branch; the sort changed, kept for the next row, restored by Back; a bad order in
  the address; the sort's arrow keys not stepping the open photo.
- **Known limits.** The order is read once with the ids, as before: an edit that moves a photo in the order shows at Refresh view.
  `NOCASE` folds ASCII only, so "Émile" sorts after "Zed"; a number in a name sorts as text ("IMG_10" before "IMG_9"). A union's
  folders are read through `photo_folder` (the derived table) where a single folder with its subfolders is a range of `photos.path`:
  the two agree while the derived tables are in step (the doctor checks them). A folder spelled by a previous place of its root
  inside a union is canonicalised by `source_of`; going to another library takes the view's parameters away, the order with them,
  by the one list `common/library.js VIEW_PARAMS` (#697),, but the response does not carry the ingress's moved-root header. The People
  tab is about 100 ms slower to open than the flat list was (see Measured).

### The owner's first review of the library views *(2026-10-04; #668-#670, #674, #675 on `arch/library-review-1`; #671-#673 beside it)*
The owner installed phase 9 and reviewed the TagPup page. A library view is for seeing the library at a high level; the work on one
folder -- Smart Rename, Camera Time Shift, the folder's own mechanics -- is **Organize**, the pane that was called Folder.

- **#668, Organize.** The switch beside Library says **Organize** (it said Folder, which was taken for the navigator's Folders tab). Only
  the words changed: the code still calls the pane `folder` (`sidebar-tab-folder`, `state.nav.shown === 'folder'`).
- **#669, no Smart Rename and no time shift in a view.** `showChrome` (library-view.js) hides both buttons in every kind of view, and
  `hideChrome` shows them again for a folder; 9d-2's Shift Date Taken of a view's selection (`shiftSelectionInView`, the direction field,
  the note) is gone from the page. **The server's bulk time shift is kept** (`op: time_shift`, resume and all, 9d-1): nothing on the
  library page starts one now, and a job of it found running or stopped part-way is still shown in the strip and resumed by it.
- **#670, the strip.** One small line: the view, its count, the status sentence when there is one, and **Refresh view**. When the library
  was last in step is said only when something is wrong (`sync-state.js syncSentence`): the read failed; a sync is running; never in step;
  the newest sync (`last_run.in_step`, now read) left it out of step; last in step more than 48 hours ago (`QUIET_FOR_HOURS`: the daily
  catch-up has not left it in step); or the view's folder holds photos on disk the library does not (the banner's offer: the strip then
  says when it was last in step). *Decided, not copied:* a card marked changed on disk does not make the strip speak -- the card says so,
  and the strip would have to follow every batch of cards (library-source.js has no hook for it). On the live libraries every one's newest
  run was in step and the last whole sync this morning (counted read-only, 2026-10-04): the line is empty. **Back to folder view** is gone.
  Why it did not work: it returned to `state.libraryReturn`, set only when a view opened while a folder was open in Organize (or from a
  `?path` in the address); a view opened from the navigator, a bookmark or the gear with no folder open had nothing to return to, and
  the link closed the view onto an empty page with "No folder was open". `libraryReturn` went with it; Back (the browser's) returns to
  the folder a view was opened from, as it always did. **This folder only** and **Show on disk** went from the strip; `showOnDisk`
  became `openInOrganize(folder)` -- scan first, close the view only when the scan answered (#569), a folder gone or a share away keeps
  the view and says so in the strip -- used by #675's Folders to Organize. Show in File Explorer (the grid's context menu) stays.
- **#675, Folders to Organize.** The tally's answer gains `folders: {count, listed}` (`tagpup.services.selection._folders` over
  `tagpup.store.selection_folders`, a module of its own beside `store.library_view`, which another branch has this week): one
  `SELECT folder_id, COUNT(*) FROM photo_folder WHERE photo_id IN (<the tally's selection>) GROUP BY folder_id`, in the tally's own read
  transaction and over its own `sel` table (`library_view._selected`, private there and used, not copied: the counts and the folders are
  of one set of photos), and the `folders` rows by key only when 10 or fewer (`MAX_FOLDERS_LISTED`). The page never holds the paths of
  the selected photos. Plans on a sandbox copy of photo_index: a list of ids seeks `photo_folder` by key for each (`SEARCH photo_folder
  USING INTEGER PRIMARY KEY`, the ids from `sel`); a Select all scans the covering path index of `photos`, as the tally's own reads do.
  **Measured** (sandbox copy of photo_index, 68,324 photos, its own TAGPUP_HOME, deleted afterwards; the route through Flask's client, 7
  warm rounds): Select all's tally 179 ms median without the folders (172-218), 218 ms with them (209-244); the folder read alone 33 ms
  for 2,672 folders; the reply 34.8 KB. `tally.js` draws them: a button a folder, its name as text (two of one name show the folder
  above), "(N photos)"; a click is `openInOrganize` through `upper` (rapid clicks: one scan, `state.moves.leaving`). More than 10 is a
  sentence with the count. The Date Taken group is hidden in a view's panel (it showed "--": a view's selection holds no records) and
  shown in Organize's, which keeps its range; *decided*, as the finding names the library page's panel.
- **#674, Delete of a view's selection** -- a **bulk job op** (`op: delete` of 9d-1's `tagpup.jobs.bulk_edits`), not batches of the
  one-photo route: it is how a selection across folders and of thousands is named (by id, the source less the excluded), and the job
  gives progress, Cancel, one bulk edit at a time in a library and in another process (`job_runs`), the strip, and the Activity
  page's record, for nothing new. Not resumable: its list of photos is fixed when it starts, and Start again of a cancelled one
  asks the question again (#691, below). The chunk
  (`bulk_edit._delete`) deletes each photo through **the one owner of a delete**, `tagpup.services.photos.delete` (the file to the
  Recycle Bin or, where its place has none, through this PC's, #694; then `forget_photo` -- row, faces -- and the thumbnail), under the lock of changes
  of photo files for the chunk, so no single save of one of them interleaves. **History/journal: as the folder view's delete, none**
  -- a delete of a held photo has never been a journal change, so History neither lists nor undoes it; the Activity page lists the
  job with its counts. **The question is asked of the server first**: `POST /api/library/selection/delete-check`
  (`selection.where_deleted`: the folders of the selection, `recycle_bin.no_bin_reason` once a folder -- 2,672 folders of
  photo_index in 2.1 s on this machine, counted read-only, every one with a Bin), and the question says how many go through this
  PC, how much they take and where they restore to (#694, below). *(It said, until #692/#694, "params.permanent ... a photo found
  with no Bin that the question did not name is left": not so -- the flag was one bool for the whole job, so a photo found binless
  in a folder the question had not named WAS deleted for good. Since #694 nothing is deleted for good, and the flag is gone.)* A file already gone is `skipped_missing` and keeps its row (sync reports it). A share that does not answer is an
  error a photo (`_reachable`, as the other ops). The cap is the other bulk edits' (200,000, refused before any request). When the
  job ends the page reads the view's order again (`library-view.js photosDeleted`, through `upper`), which drops the deleted photos
  from the view, its total and the selection, and closes the open photo onto the grid if it was deleted; the navigator's counts and
  the folder scans are let go as for any job. **What is accepted:** each photo of the job is the one-photo delete's cost --
  `libraries.split`, `refuse_writes`, `forget_photo` with `derived.prune` and the thumbnail, each its own read or write of the
  library -- rather than a per-chunk version of them, so as not to make a second owner of a delete; not measured on the live
  library (it would delete). Deleting the open photo while a job runs is not stopped: its save fails as its file is gone.
- **#691, a delete deletes exactly what its question named.** delete-check resolves the selection (`selection.resolve`) and answers
  its `total` and a `token`, the SHA-256 of the sorted ids (`selection.token_of`; 68,000 ids are about 20 ms). The page's question
  names that total, and the start carries the token; the route resolves the selection again and `bulk_edit.refuse_if_changed`
  refuses, `409`, nothing begun, when the token differs ("The selection changed since you were asked ...: nothing was deleted. Ask
  again"). The job's own list of ids is the one resolved at that start, so nothing that comes into the source afterwards is in it. A
  delete is not resumable (`bulk_edits.resumable` is the time shift's alone); the strip's Start again of a delete goes through
  `deleteSelection` (delete-check, the question, a new token) through `upper`, never `startBulk(request)`. Reproduced on the old
  code by the reviewer (question "3 photos", one photo indexed meanwhile, 4 deleted); `test_bulk_delete` holds it.
- **#693, how long, and what waits.** Measured on a sandbox copy of photo_index (its own TAGPUP_HOME, roots placed at sandbox
  folders, throwaway JPEGs made at 200 and 400 of its rows' places, the real Recycle Bin, the job run through Flask's client; the
  sandbox deleted afterwards): 200 photos in 12.8 s (63.8 ms a photo, 15.7 a second), 400 in 25.0 s (62.6 ms, 16.0 a second).
  The question says the time at 15 a second (`DELETE_PER_SECOND`; 68,000 photos: about 1 hour 16 minutes) and that saving a
  photo elsewhere waits for the photos being deleted at that moment (each chunk of 25 holds the lock of changes of photo files,
  about 1.6 s). A photo copied from a share costs its copy too; that was not measured (no share here).
- **#694, nothing is deleted for good** *(the owner's decision, 2026-10-04)*. A photo in a place with no Recycle Bin (a network share,
  a removable drive, a SUBST drive) goes through this PC: `tagpup.files.recycle_bin.delete_file` -- the one way every delete of a
  photo takes, Organize's (`photos.delete`, `file_only.delete`) and the bulk Delete's -- copies it to
  <Downloads>\TagPup deleted from shares\<server>\<share>\<path> (`mirror_of`; the Downloads known folder by `SHGetKnownFolderPath(FOLDERID_Downloads)`,
  wherever the owner moved it, the profile's Downloads if Windows cannot say, and a test home's own through `TAGPUP_DOWNLOADS`,
  which `tests/own_home.py` sets: no test writes the owner's Downloads), checks the copy (size, then SHA-256 of each), sends the
  COPY to this PC's Recycle Bin (`send_to_recycle_bin`, the existing owner), and only then deletes the original. **Restored, a copy
  goes to that folder under Downloads, not to the share**: the question, the reply and the SPEC say so. Each step that fails
  leaves the original and its row, the photo an error: a copy that fails or differs is taken away; this PC's Bin refusing the copy,
  the copy taken away; a crash or a refusal of the original's delete after the copy is in the Bin leaves the original AND a copy
  in the Bin -- the photo is there twice, and the error says so. A name already in the mirror folder is kept, the copy takes
  `name (2).jpg`. **Free space**: the Downloads folder's drive must have room for the copy and 1 GB spare (`room_for`, before each
  copy); delete-check answers `copy_bytes` (the index's sizes of the photos in places with no Bin) and `no_room`, and the page then
  says the sentence and asks nothing. Nothing is deleted for good any more: only if this PC's own Recycle Bin refuses is the photo
  an error, its original kept. `/api/folder/membership`'s `permanent_delete` keeps its name and now means "this place has no
  Recycle Bin: a delete goes through this PC".
- **#703-#706, a copy is made only where it is kept** (`recycle_bin.can_copy_here`, before every copy and, for the bulk Delete, before
  each chunk's copies, `bulk_edit._cannot_keep`; delete-check asks it with the Bin's size fresh). **#703:** Windows makes room in a
  full Recycle Bin by deleting its OLDEST items for good, so "nothing for good" holds only while the Bin of the Downloads volume can
  keep what it holds plus the copies: its capacity is the volume's `HKCU\...\Explorer\BitBucket\Volume\{guid}` MaxCapacity (no
  key: 5 % of the volume, a guess on the safe side), `NukeOnDelete=1` refuses everything (nothing would be kept), a photo larger
  than the capacity is refused, and `used + copies > capacity * 0.95` refuses with the sizes. `SHQueryRecycleBinW` is slow -- **11.8 s**
  for this PC's Bin (3,353 items), read once 2026-10-04 -- so its answer is kept per volume and counted on with what this process
  sends (`note_binned`), and asked again after 10 minutes (`BIN_FRESH`); a copy sent by another program meanwhile is not seen until
  then (accepted: the 5 % margin). **This PC's Bin was full when read** (49,707 of 49,710 MB): every delete from a share will be
  refused, with the sentence, until the owner empties it. Out of scope, said: the same capacity check for a photo deleted from a LOCAL
  folder with its own Bin -- that is Windows' ordinary behaviour for any file the owner deletes, and the Bin is not TagPup's copy; it
  could share `room_for_copies` if wanted. **#704:** a copy's path (or its `.partial`) of 260 characters or more is refused up front,
  the sentence naming the length and the folder (`too_long`); delete-check counts them and the question says they are left. **#705:**
  the question's time for photos through this PC adds the copies at **200 MB a second** (`COPY_BYTES_PER_SECOND`: copy, two hashes,
  rename, measured on this PC's disk, 100 throwaway files of 3.4 MB: 204 and 210 MB a second) and says "at least", since a share
  reads slower and was not measurable here. **#706:** `.partial` copies a crash left for the same name are taken away on the way in
  (they no longer push the name to "(2)"); a Downloads folder under a OneDrive folder (`OneDrive`, `OneDriveConsumer`,
  `OneDriveCommercial`) is refused. Tests set `TAGPUP_RECYCLE_BIN` (own_home: a large empty Bin) so none reads the owner's.

### The owner's second review of the library views *(2026-10-04; #712-#714 on `arch/library-review-3`)*
- **#712, Folders to Organize switches the sidebar.** Opening a folder of the selection details' Folders to Organize closed the view onto
  it and showed the pane last chosen for a folder -- Library, when the owner had picked it before any view was open. `closeViewOntoFolder`
  now ends with `choosePane('folder')`, the switch's own click handler (navigator.js, through `upper`): the switch, its pane and the
  remembered choice are what a click on Organize leaves. A folder gone or a share away keeps the view and the switch as they were.
- **#713, one floating header.** The strip and the header card sit in one wrapper, `#folder-view-top`; in a view (`.in-library-view`,
  set by `showChrome`) it is the one sticky header: the view's name and total (one line, a long name cut short with the whole in its
  tooltip), Refresh view as an icon button (`aria-label`, tooltip), Sort by, and the card's actions -- Select All, Select None, Delete,
  the count, the thumbnails' size -- the card's own title and count hidden (they said the same and scrolled away). It sticks flush with
  the scroller's top (`top: -20px`, the scroller's padding), so no card shows above it; `grid.js topInset` is its height and the 16 px
  gutter, so a card the keys walk to is below it. Organize is not touched: the wrapper is a plain block there and the card is as it was
  (Organize had no duplicate header). *Measured* in Chromium on a sandbox copy of photo_index (`measure_library_review.py --only d`):
  at a 1,600 px window the header is 65 px tall; at 1,000 px the grid's column is 296 px (the sidebar and Selection Details keep their
  360 and 340) and the header 163 px; at 720 px the column is 40 px and the page scrolls sideways -- it did on the trunk too, the grid's
  cards are 150 px at the least; the header's controls stay inside it at 1,600 and 1,000. A card walked to by the arrow keys (30 rows
  down, 12 up) landed 16 px below the header at every width (on the trunk, at 720, under the strip).
- **#714, Sort by.** A button in the header opens a menu of two sections, as Windows Live Photo Gallery's: the field (Date Taken,
  Caption, File name) and the direction (Ascending, Descending), each a group of `menuitemradio` showing the current choice
  (`web/tagpup/sort-menu.js`; its state `state.sortMenu`). ArrowDown on the button opens it on the field chosen, ArrowUp on the
  direction; ArrowUp/Down, Home and End move through the five items, Enter and Space choose, Escape closes it onto the button, Tab, a
  click or the focus elsewhere close it, and so does the view changing under it (`showSortOrder`, called from `libraryChanged` and
  `hideChrome`). A field keeps the direction and a direction keeps the field; what is chosen already asks for nothing; the order opens as
  a new view (Back returns), and `state.nav.order` follows only when the view has opened, so Cancel on unsaved edits leaves the next
  view's order alone. The sidebar's select is gone. **The address** keeps `order`, its grammar extended: `caption`, `caption-desc` beside
  the first review's four, which read as they did. **The server**: `order=caption | caption-desc` (`store.library_view`): by the
  photo's caption -- the first of `photos.captions`, the one the page shows -- its first 200 characters (`CAPTION_KEY`, which bounds
  what a page token holds), without case, ties by id; the photos with none after the captioned ones in both directions, by id in the
  order's direction, as the undated are by Date Taken. Name and caption are one machinery (`_KEYED`: the index walked in order, each
  entry tested for the source; the keyset page a seek of it). **Migration 23** (additive, index only, touches no table, blocks no undo):
  `idx_photos_caption` on `(<caption> COLLATE NOCASE, id)`, the caption an expression of built-in functions over `captions`
  (`library_view.caption_sql`: `NULLIF(substr(CASE WHEN json_valid(captions) THEN json_extract(captions, '$[0]') END, 1, 200), '')`),
  so SQLite keeps it and no writer of a caption has to know of it, as migration 22's on the path -- *decided over a column* (the owner's
  rule of a column over `json_extract` is about reading JSON on every read; this is read once, by the index): a column would have had
  every writer of captions (the indexer, a save, the bulk edits, an undo) keep it. Text that is not JSON is NULL, never an error that would
  refuse the row's write. Checked by hand against #464: a test holds that 23 adds one index on `photos` and nothing else, every table's
  rows unchanged, and a change journaled at 22 still undoable. The captioned photos are asked as `<caption> COLLATE NOCASE >= ''` -- a
  range of the index -- not `IS NOT NULL`, which read each photo's row to compute the caption again; after a cursor only the cursor's
  bound is given, or SQLite took the first of two lower bounds and walked each page from the start. **Measured** on a sandbox copy of
  photo_index (68,324 photos; 57,775 with a caption, 10,549 without, 8,665 with more than one; the longest 616 characters -- counted
  read-only): migration 23's index 0.18 s, the whole `ensure` 0.3 to 0.7 s; the whole library's id list by caption 24 to 31 ms either
  way (by Date Taken 23, by name 25; with `IS NOT NULL` it was 218), the keyword node with the most photos of its own (14,838) 20 to
  32 ms (its uncaptioned photos a seek of each id and a sort of those ids); 50 keyset pages of 200 in caption order 42 ms for the whole
  library, 153 ms for that keyword (741 and 1,608 with two bounds). photo_index itself, read-only and not yet at 23: `SCAN p` and a
  `TEMP B-TREE`, 394 to 1,255 ms -- what a library opened without its migration (a look) does, the same order. In Chromium (three fresh browsers each, from the click on Sort by to
  the first window painted and idle): the whole library by Caption 332 to 717 ms (median 716; the ids request 172 to 305), by Caption
  descending 215 to 230 ms; the largest keyword by Caption 544 to 595 ms, descending 229 to 253; the other orders 235 to 1,122 ms on
  the same runs (a noisy machine: Date Taken's own first open was 404 to 2,330). Bulk edits, the tally and the selection resolve the
  view's source in Date Taken order whatever the view's: the order is not part of a selection.
- **#722, which sources walk the index.** Name and caption order forced `INDEXED BY` for every source, so a month, a year's Other
  or a folder with its subfolders walked all 68,324 index entries for each page. `_walks` (store.library_view) now walks the index for
  the whole library and for a source given by a list of ids (a keyword, a person, a folder alone, a union holding one of those), and
  leaves a range of another index (`Scope.ranged`: a month, a year, a year's Other, a folder with its subfolders, a union of months and
  years) to SQLite -- the range read and sorted -- unless it holds more than 1/16 of the library (`RANGE_SHARE`; counted at each read,
  one covering count): the top folder of the Folders tab is the whole library through the path range, 114 to 949 ms a page sorted,
  0.2 to 1.8 walked. **Measured** on a sandbox copy of photo_index, caption and name, either way, the first page of 200 and the
  eleventh, then the id list, the walk forced everywhere (before) against the rule: a month (1,201) 1-117 / 44-156 ms against 2-11 /
  7-9; a year's Other (50) 45-149 against 4-10 every time; a folder with subfolders of 3,573, 2-34 / 47-157 against 8-40 / 16-22; the
  top folder 0.2-1.8 / 69-193 against 7-9 / 78-184 (the count, about 7 ms, paid on each read); a year of 5,579 (walked either way)
  0.2-33 / 47-153; a keyword (14,838) 2-4 / 14-24, a person (14,840) 8-16 / 19-29, a folder alone (759) 0.3-7 / 6-7 and unions
  (3,251 to 17,463) 3-29 / 27-175, unchanged. A test asserts the plan for each kind: the range's own index and a sort for a small
  range, the name or caption index and no sort for the library, a keyword, a person, a year holding most of it and the top folder.
- **#723.** A forged caption- or name-order token whose key held a lone surrogate passed `decode` and reached SQLite, which cannot
  bind it: a 500. `decode` refuses a key that does not encode as UTF-8 (400).
- **Found on the way.** The page token was refused over 400 characters, and an order by name's token holds the file name in JSON, six
  characters for each one that is not ASCII: the page after a photo named with some 50 Cyrillic or Greek letters could not be asked for.
  `MAX_TOKEN` is 5,000. No name on the live libraries came near 400 (counted).

### Phase 9e-1: search on the server *(built 2026-10-04; migration 24; branch `arch/phase-9e1-search`)*
Server and store only; the page's search box and picker are 9e-2's, and this is the contract they use.

- **A search is a source** (`library_view.source_of`, kind `search`): `{"kind": "search", "value": {"all_of": [source], "any_of":
  [source], "none_of": [source], "words": "text"}}`, every part optional, another part refused (a misspelt `none_of` would
  otherwise be a search for more). The lists hold sources of the kinds a union holds (`all`, `folder`, `keyword`, `keyword_only`,
  `person`, `year`, `month`, `year_other`), at most 1,000 each. **One statement** over `photos p` (`store._search_scope`), the AND
  -- a balanced tree, as the union's OR is -- of: the `any_of` union's clause (`_union_scope`, unchanged); each `all_of` member's
  own clause (its Scope's where; a folder alone by `photo_folder`); `NOT IFNULL((<none_of union>), 0)`; and the words. So Select
  all, ranges, the tally, every bulk edit and the strip take it unchanged, and Delete's token (#691) refuses a search whose photos
  changed between the question and the start (a test). Keyset paging in every order is the source's: `Scope.ranged` is set, so in
  an order by name or caption the name or caption index is walked when the search holds more than 1/16 of the library (counted,
  #722) and left to SQLite to read and sort when it holds less.
- **Decided: "within" is an `all_of` member.** The navigator's selection is a union; a union in `all_of` is ONE member (its photos
  ANDed with the rest), while a union in `any_of` or `none_of` is its sources (an OR in an OR). "Three family members and not a
  fourth within July of these years" is `all_of: [{any_of: [twelve Julys]}, A, B, C], none_of: [D]`.
- **Decided: a person is by name**, as the person source, the navigator's People and the tally read one in identity stage 1 --
  `photo_people.name` compared without case. By `tag_id` a name no person node is called, one on a branch (#660) or one two nodes
  share has NULL and would be found by nothing (photo_index's `photo_people`: 4, 4 and 1 such names); stage 2 moves every read to
  the id at once. A person on a branch tag is found as the navigator's row of that name finds them (a test). A keyword member is by
  the tree's node, `keyword` with everything under it and `keyword_only` the node alone, as the sources are.
- **Decided: a search that says no more than a source is that source**, as a union of one is: no part is `all`, `any_of` alone its
  union, one `all_of` member alone that member; the reply's `source` says which. `none_of` keeps a photo with no date (NOT of a
  month's comparison is NULL, which would have dropped it: the IFNULL; a test). A member naming nothing (a keyword with no node, a
  person nobody is) makes `all_of` or `any_of` hold nothing and takes nothing from `none_of`; `none_of: [all]` holds nothing.
- **Words: two contentless FTS5 tables, migration 24** (`tagpup.store.search_index`; docs/DATABASE.md 25 and 26):
  `search_words(tags, captions, people)`, `unicode61 remove_diacritics 2`, prefixes 2 and 3 -- each keyword as the row holds it
  (path and leaf: "People/Élodie Marchetti" gives people, elodie, marchetti; a keyword with no node is found by its words), the
  captions and titles, and the names `photo_people` lists (faces too, by the leaf rule) --; and `search_names(name, folders)`,
  `trigram remove_diacritics 1`, the file name and its folders: **every folder below the root of a root-relative row, the root's
  name never a word; of a native row only its own folder** (*decided*: above it are the machine's layout -- drive, "Users", the
  profile, "Pictures" -- whose words every photo would match: renton_parkrun's 1,150 rows are all native and five folders
  deep, counted; photo_index's and kr-track's are all root-relative). `content=''`,
  `contentless_delete=1`, rowid the photo's id: a row is replaced by INSERT OR REPLACE and taken by its id; a photo deleted on any
  connection takes its rows by the trigger `search_goes_with_its_photo`. **Each typed term** (split at blanks, at most 20, at most
  500 characters in all) is quoted, its quotes doubled -- AND, OR, NOT, NEAR, `*`, `^`, `"`, `:`, `(`, `)`, `-` are text -- and is
  found when the words table matches it as a prefix phrase (`"term"*`: every term a prefix, decided, so "beach" finds "beaches"
  and a word being typed finds as it is typed) or, from three characters, the names table holds it (`"term"`: "0412" inside
  "20190412_1430.jpg"); the terms are ANDed with each other and with the sources, a set, never ranked. A term of no letter or digit
  and under three characters says nothing and is dropped. A library below migration 24 answers a search of words `503` with
  `Retry-After: 5` and "The word index is being made now; try again in a few seconds." (`WordIndexComing`, #753): the server
  brings every library it serves up to date as it starts and as a request first names it, so such a library's migration is under
  way (the startup thread, about 4 s on photo_index; another program holding it) or due. One at 24 without the tables (dropped by
  hand) is `400` (`NoWordIndex`).
- **Kept by the store's writes, in their transactions**: `derived._put` refreshes the word rows of every photo it is asked about
  (the index's record, the bulk tag writes, sync, moves and renames, `ensure_row`, `follow_nodes`, the journal's `_derive`);
  `people.rebuild` of the photos whose people it wrote (a face named or unnamed, a tree edit that changes who is a person, a rename
  of a person); `derived.rebuild_all` and the adoption's `rebuild_folders` rebuild them whole. Captions joined the columns the
  derived data comes from: `tests/test_derived_writers.py` now fails a writer of `photos.captions` that does not go through
  `derived`, and the journal's undo of a caption refreshes the photo
  (`_touched`). A tree rename changes no word: the words are the keyword as the file holds it, which TagTuner rewrites one
  transaction after the tree's -- until then the old words find the photo and the new do not (a test), as its keyword rows name no
  node meanwhile.
- **The doctor**: `search_index_out_of_date` (in `checks.RULES`): every photo with no row, every row of no photo, and of 500
  photos spread over the ids those whose texts, each column matched as one phrase, are not found in their row (a contentless table
  cannot be read back, so a word left behind is not seen by the sample). The report says so beside the rules, with the remedy
  (`search_index.LIMITS`, #752): an older checkout writing a library at 24 never refreshes the index, and a word it leaves behind
  is found only by `--rebuild-derived --apply`. `tools/doctor.py --rebuild-derived --apply` rebuilds it
  with the derived tables. Migration 24 runs the same check before it commits. On real rows the phrase check needed one fix found
  by running it: 255 of photo_index's photos have captions with no letter or digit (a lone dash), which no phrase matches; with
  it, every one of its 68,324 photos passes (counted on an in-memory copy of its rows).
- **#464, checked by hand.** Migration 24 declares `touches` = the two virtual tables; FTS5 also makes eight shadow tables
  (`_data`, `_idx`, `_docsize`, `_config` of each), and the migration adds the trigger on `photos` (as migration 19's
  `derived_go_with_their_photo`). A test holds that 24 adds exactly those ten tables and that trigger -- no column, no index --
  that every row of every table that was there is unchanged, and that a change journaled at 23 is still undoable after it and is
  undone. All ten are in `schema.UNWATCHED` (a later migration's watch cannot make a trigger on a virtual table) and the two
  virtual ones in `journal.DERIVED`. Interrupted, the migration leaves the library at 23 with none of them, and runs again on the
  next open (a test).
- **#751: a search's members are read once.** `all_of` resolved each person through a pass of `photo_people`'s names and each
  keyword not spelled as a node through a read of the tree; one `_Reads` per statement now serves the `any_of` union, every
  `all_of` member and `none_of` (a test counts the reads). photo_index, read-only, the id list: all_of 100 people 310 -> 75 ms,
  3 people 56 -> 55 ms.
- **Measured** on a sandbox copy of photo_index (68,324 photos, its own TAGPUP_HOME, roots placed in the sandbox, deleted
  afterwards), in-process through `tagpup.services.library_view` (the route's work less Flask and JSON), medians of five:
  **migration 24 through `schema.ensure` 4.3 s** (a copy just written, cold), of which the backfill is 2.0 s warm and its check
  (counts and 500 phrases) 1.1 s; it runs in the server's startup thread, as every migration since #661 does. The file grows
  36.6 MB (the words' index 8.5 MB, the trigrams' 21.4 MB). The writes, with and without the word index (rolled back): a bulk
  chunk of 25 refreshed 2 ms either way; 2,000 photos refreshed (a folder renamed) 54 ms without, 75 ms with; `people.rebuild` of
  5,000 that changes nothing 89 against 100 ms; the 2,000-photo follow of `test_derived_batches` holds the lock 0.70 s without and
  0.89 to 0.92 s with (its limit is 2.5 s). Searches, ids / first page of 200 with its count, ms, the six orders' range:
  all_of 3 tags (6 photos) 12-20 / 16-27; any_of 2 people less a tag (22,373) 81-164 / 91-155; none_of 1 tag (65,099) 57-91 /
  29-79; a common caption word alone (10,433) 49-74 / 52-64; a rarer word (396) 11-21 / 14-28; two words (9,120) 59-107 /
  77-138; a common word within a year (57) 16-43 / 24-49. Select all of the 65,099-photo search: resolved (a bulk edit's list)
  105 ms, tallied 416 ms; of the 10,433-photo word, resolved 50 ms. The structured searches' plans on photo_index itself
  (read-only): a person's photos `SEARCH p USING INTEGER PRIMARY KEY` from the name index and a temp b-tree for the order; all_of
  a tag and a year `SEARCH p USING COVERING INDEX idx_photos_year (year=?)`; none_of a tag the date index walked
  (`idx_photos_taken (taken>?)`), by name `SCAN p USING INDEX idx_photos_name`, nothing sorted; words `SCAN search_words VIRTUAL
  TABLE INDEX 0:M3` (FTS5's own index) as a list, then the photos by key, as `MULTI-INDEX OR` of the two tables for each term.
- **The contract for 9e-2.** The request is the source above: a `GET /api/library/ids?kind=search&value=<the value as JSON
  text>&order=` or, as the page sends every union, a `POST` of `{"kind": "search", "value": {...}, "order"}` (and the same for
  `/view` with `after` and `limit`); a selection's source is `{"kind": "search", "value": {...}}` (an object or its JSON text), the
  tally, the bulk start and delete-check take it unchanged. **The page's address** for a search is
  `?view=search&value=<encodeURIComponent(JSON of the value)>&order=<order>`, as a union's is `?view=any_of&value=...`; the value's
  parts in the order `all_of`, `any_of`, `none_of`, `words`, empty parts left out, so one search has one address (the page's own
  `sameSource` compares them); within what the navigator has selected is `all_of: [state.nav.followed]` (a union, or the one source
  it is). `MAX_ADDRESS` (100,000 characters) refuses a longer one with a sentence, as for a union. The picker sends names and tags
  as the navigator does: a person `{"kind": "person", "value": <name>}`, a tag `{"kind": "keyword", "value": <tag path>}` (with
  everything under it; `keyword_only` for the node alone). The reply's `source` may be another kind (a search that says no more
  than a source); the page keeps the address it asked by. `400` sentences: a part not known, a list over 1,000, more than 20 terms
  or 500 characters of words, a search in a search. **`503` with `Retry-After`** (#753) while the word index is being made: the
  page shows the sentence and asks again after `Retry-After` seconds, a few times (a migration that fails keeps answering it;
  after about a minute the page leaves the sentence and stops asking).
- **Known limits.** A term is matched within one column of one table: "rowan coast" is two terms, each found anywhere, but a
  quoted phrase typed as one term ("rowan_coast") must be in one column. The camera and the lens
  are words since migration 27 (`search_gear`; below); the author is not (the owner's call). The words of a photo's people are the names as `photo_people`
  holds them, so a rename shows in a search as it shows in the navigator. CLIP (semantic) search is not 9e.

### Phase 9e-2: the search page *(built 2026-10-04; branch `arch/phase-9e2-search-page`)*
The page's half of 9e, over 9e-1's contract; no server change. `web/tagpup/search.js` (the box, the picker, the chips),
`search-model.js` (what the picker offers and a chip says, as data), and a new kind of view in `library-source.js`.

- **Where the box is** *(decided)*: at the top of the **Library pane**, above the navigator's tabs -- not in the floating header
  (#713). A search is a way to open a view, so it is there with no view open; it sits beside the navigator whose selection it
  can look within; and the header stays one line at 1,600 px (it is 163 px at 1,000 already). Organize's "Filter files" box is
  the folder's list filter and stays Organize's (in a view its tooltip points at the Library pane's box). As WLPG's: words in
  one box and the filters beside it -- a **Filters** toggle opens All of, Any of and None of, each a row of chips and a picker.
  **Within the sidebar's selection** is a checkbox under the box, shown only while the navigator's rows are the view open,
  naming them (#713's header is untouched).
- **A search is a view of kind `search`** (`LIBRARY_KINDS`): `searchValue` keeps its value in the contract's one order (`all_of`,
  `any_of`, `none_of`, `words`, empty parts left out, a source named twice in a list once, words' blanks made single), so
  `sameView` compares one text and the same search asked twice asks once. It is always POSTed (`idsRequest`), like a union. The
  address is `?view=search&value=<JSON>&order=`; `viewSpecFromSearch` refuses with a sentence, no request, a part it does not know,
  a search in a search, a list over 1,000, words over 500, JSON that is not an object, and a search that asks for nothing. The
  header names it ("Search: “beach”; all of Trips/Coast and Rowan Thackeray; none of Places/Home", three of a list and "N more"); an
  empty one says "Nothing in the library matches this search." The selection, the tally, every bulk edit and Delete carry
  `{kind: 'search', value, recursive}` unchanged (a test: Select all's tally names the search). The navigator selects no row of a
  search (`membersOf` is `[]`), so a Ctrl-click in a search starts a selection again rather than make a union holding a search,
  which the server refuses.
- **Words are searched on Enter** (or the Search button), *decided over a pause*: each search is a new view -- the grid rebuilt,
  the selection cleared, the address changed -- and every term is matched as a prefix, so a half-typed word finds a broad set
  (a common word: 16,203 photos, 94 KB of ids); searching on a pause would clear a selection and redraw the grid while the
  person is still typing. The picker completes as it is typed: it asks nothing per key (photo_index's 895 keyword nodes and
  413 people, counted read-only, are filtered in the page).
- **One source of names** *(decided)*: the picker offers what the navigator's rows are -- `readSectionIndex('keywords')` and
  `('people')` (navigator.js; a read already under way is waited for, not repeated) -- because a member is exactly a row's
  source. People by name, by the store's one rule (`person_ids`); every node of the tag tree as its path, but a filed person's
  own node (`<branch>/<name>`), who is offered by name. **A name that is a branch is never a person** (#660): the people answer
  lists "Family" when a photo is tagged People/Family, unfiled; a person not filed whose name is a branch of the answer's
  `groups` is not offered as a person, the branch is offered as the tag (photo_index: 4 such names, each a group of the answer;
  kr-track and renton_parkrun: none -- counted read-only 2026-10-04). Not `/api/tags` and `/api/people`: those are the edit
  fields' lists (tags typed into photos, names on faces). Best first: a name that starts with what is typed, then a word of it,
  then one that holds it; people before tags; then alphabetically; 30 shown, "N more: keep typing".
- **Keys**: the pickers are ARIA comboboxes (`aria-activedescendant`); ArrowDown and ArrowUp move, Enter adds (the first name if
  none is chosen; a name of no tag or person is a sentence, nothing added), Escape closes the list and a second empties the box,
  Backspace on an empty box takes the last chip off, Tab closes; Enter in an empty picker searches. A chip's x is a button named
  "Take Trips/Coast off All of"; the focus goes to the next chip or the box. A mousedown on a name keeps the focus in the box.
- **When it runs, and the history** *(decided)*: a chip added or taken off searches at once, as a navigator click does; ticking
  Within does when there is something to search. A search started from another view is a new place; a change while a search is
  open **replaces** it, so Back from any search returns to the view before it. That place's history state holds `searchBack`
  (1, or one more for each search after it in a new place -- a sort; 0 when not known: a search opened by its address), and
  **Clear** (the x, or a search emptied of its words and its last chip) is `history.go(-searchBack)`, or, for a
  bookmarked search, closes the view as a new place (`closeViewAsNewPlace`). The box mirrors the view (`searchFollows`, called from
  `libraryChanged`, `closeLibraryView` and the stay of "Save changes?" through `upper`): a search opened -- by the box, a bookmark,
  Back, Forward -- puts its words and chips in the box; leaving a search empties them; a move between two views that are not
  searches leaves what is typed alone (type, then click a year and tick Within). Within becomes the first member of All of and
  is shown from then on as a chip "Within July 2021" (a keyword or person selection is its own chip: the same member).
- **How it fails**, each a test (`tests/frontend/search-page.test.mjs`; 23 of its 24 fail on the trunk, the 24th guards
  `VIEW_PARAMS`): rapid Enter (the same search twice asks once; a second search while the first is out wins, the first answer
  dropped by the view's identity); a search with no result; words of only punctuation (a sentence, nothing sent; beside a chip
  they are left out and said so; "..." is three characters and sent, as the server looks for it in file names); the vocabulary
  changing while the picker is in use -- the tag editor renames Trips/Coast, the picker offers Trips/Shore when the focus comes
  back to it and the chip already added is marked (dashed, struck through, its tooltip "the library has no such tag now: this
  finds nothing"); the tag editor's edits now mark the navigator's counts out of date (`gear.js`), which they did not; a bookmark
  naming a tag that is gone (the chip so marked, the search sent, the empty view says why); an address over `MAX_ADDRESS` (a
  union of 375 share folders within, a 400-letter word: the sentence by the box, nothing sent, the view as it was); a bookmark of
  1,000 people in Any of (one request, 50 chips and "and 950 more"; a 1,001st refused with a sentence); five addresses that are no
  search; markup in a tag (text, no element); the server's **400** (its sentence by the box and in the strip); **503** with
  Retry-After (#753: the sentence in the strip, asked again after each Retry-After, at most 30 s each, until a minute of them has
  passed -- twelve asks after the first at 5 s -- then the view fails with the sentence; a view replaced meanwhile asks nothing
  more, its timer cleared by `destroyLibrary`); going to another library drops the search with the view's parameters. Found by
  the tests on the way: the picker took a section read under way for fresh (`loadSection` marks it not stale as it starts) and
  painted the old names.
- **Measured** with `scripts/measure_search.py --run` on a sandbox copy of photo_index (68,324 photos; migration 24 by the served
  code 5.9 s), headless Chromium 1,600 x 1,000, a fresh browser each, three rounds, medians (range), thumbnails a 1-pixel picture;
  from the key or click to the first window painted and the main thread idle; no long task in any:

  | the action | painted and idle | the ids request | photos |
  |---|---|---|---|
  | (a) a common caption word (9,531 captions hold it), Enter | 336 ms (327-517) | 226 ms, 94 KB | 16,203 |
  | (b) All of three tags, the third Enter | 136 ms (136-139) | 29 ms | 5 |
  | (c) Any of two people, None of the largest tag, the last Enter | 253 ms (221-272) | 165 ms, 52 KB | 22,867 |
  | (d) the word within the year with the most photos (5,579), Enter | 136 ms (120-173) | 26 ms | 62 |
  | (e) Select all of None of the largest tag, to the tally's tags and people painted | 879 ms (878-937) | the tally 349 ms, 33 KB | 65,949 |

  (e) is the tally's 250 ms pause, its request and the panel; the search adds nothing to it. There is no trunk to compare: the
  page had no search. An earlier run on the same copy gave (a) 512 ms (ids 115 ms): these move by a factor of two run to run.
- **The review (#768-#772)**, each a test that failed on the code before it:
  - #768: the picker reads its names again on the navigator's own pause after writes (`NAV_COUNTS_MS`, inside its timer, after
    the tab's own read whose answer it shares), once for a run of saves, not once a save.
  - #769: Within and the box's words change only when a search view has opened (`searchFollows`): Cancel on "Save changes?"
    after Enter keeps Within ticked and the words; after Clear keeps the search and its words.
  - #770: every place the page makes in the history carries this load's id and its position (`state.entries`; positions are
    consecutive, as a new place drops those after it); Back or Forward cancelled for unsaved edits goes back to the place the page
    shows (`history.go`), whose own state (`searchBack`) is then right, and that return asks nothing (the address names what is
    shown). When a position is not known (a place another load made, or one a folder's address replaced with `{}`), the place
    moved to takes the view's address and its own state less `searchBack`: Clear then closes the view rather than go to a wrong place.
  - #771: Enter while the picker's names are being read waits for them ("Reading the library's tags and people..."), then adds
    the first offered.
  - #772: a section the library could not answer is asked again only when the picker's box is focused again, not on each key;
    when one of the two fails the list says so ("Could not read the library's tags, so only people are offered: ...").
- **Known limits.** A list shows 50 chips; the rest of a long bookmarked list are counted and can be taken off only by Clear. A
  chip's mark (a tag or person gone) is as the navigator last read the section: a rename made in another tab shows when the
  picker or the navigator reads again. Back steps over the changes made to an open search (decided above).

### Identity by id *(owner, 2026-10-02; `photo_tags` built in 9a-1; stage 1, the id beside the name, built 2026-10-04 on `arch/identity-by-id`, migration 21; stage 2 design)*
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

**Stage 1, built** *(2026-10-04; the owner moved it before 9e)*. Additive: `faces.tag_id` and
`photo_people.tag_id` sit beside `name`, and **every read still reads the name**. One module,
`tagpup.store.person_ids`, owns the conversion and every write of the column, as `paths.to_row` /
`from_row` own a path's (`tests/test_person_ids_single_owner.py`):
- **The rule.** A person is a node with `has_face` set that is not a root (a root holding faces is a
  category, as `PeopleVocabulary` reads it) **and has no node under it: a branch tag cannot be a person**
  *(owner, 2026-10-04; #660)*. A name is that node when exactly one such node is called it, compared as
  names are compared everywhere (`vocabulary.key`: trimmed, without case). A name no person node is
  called -- none at all, or only a branch -- or that two are (#27), gets NULL: never a guess, and the
  name itself is left as it is (stage 1 renames and unnames nothing). The doctor lists those names by
  count, and by name only with `--show` ("names with no person node", "names with several person
  nodes", "names on a branch" with the branches' ids); they are reported, not broken, and settling them
  is the owner's. A person given a tag under them becomes a branch and loses the id, by the tree's
  edit; a leaf and a branch of one name is the leaf. Counted read-only on 2026-10-04, by the leaf rule:
  photo_index's faces hold 284 distinct names (35,803 named of 226,246 faces), 277 of them one person,
  4 none (40 faces), 2 on a branch (7 faces), 1 several (1 face); its `photo_people` 413 names (80,060
  rows), 404 one, 4 none (40 rows), 4 on a branch (65 rows), 1 several (12 rows); it has 9 has_face
  branches. kr-track: faces 73 names, 61 one, 12 none (198 faces); `photo_people` 75, 62 one, 13 none
  (200 rows); no branch. Before the leaf rule, the three rules weighed -- exact name, `vocabulary.key`,
  and `find_person_path`'s (a leaf under a face root) -- gave the same counts on every library; nothing
  on any of them differs only in case or spacing, and every node's `name` is the leaf of its `tag`.
- **The id is derived in stage 1**: what the name gives now, under the tree as it stands. So it is
  kept wherever either changes, **in the same transaction**, with the tree read inside it and never
  cached across transactions (a rename committed by another process between two writes is what the
  second reads): every writer of the faces store, through `faces._rebuilt` (`follow_faces`, the faces
  of the photos it touched); `people.rebuild` (`follow_listed`); `people.rename` (`follow_names`);
  every tree edit, through `people.tree_edit` (`follow_tree`: the tree's people read before and after,
  and every name matched again only when they differ -- two reads of ~400 nodes otherwise); and the
  journal's `_derive` after an apply, an undo or a settle (`follow_faces` for the photos whose faces it
  wrote, `sync` when it wrote the tree). Sync, index reads, Suggest's apply, automatch, clustering and
  file changes reach it through those; `file_only` writes no row.
- **Not a key yet, so a rename has a window.** TagTuner's rename moves the node (one transaction),
  rewrites the files, then renames the faces (another). In between, the faces' old name names no node
  and their id is NULL -- never the renamed node's under a name it no longer has. Stage 2 removes the
  window: the rename is then the node's alone.
- **The journal.** `tag_id` is a derived column of `faces` (`journal.DERIVED_COLUMNS`): an undo never
  holds a row to it, a face put back by an undo -- one recorded before the column existed among them --
  is given its id by `_derive`, and a rehearsal compares it after `_derive` on both sides. Migration 21
  adds only that column to a journaled table (`schema.ADDS_DERIVED_COLUMNS`), so it blocks no undo of a
  change made before it (`journal.schema_gap_blocker`); a test holds the declaration to the columns the
  migration really adds.
- **Migration 21** adds the two columns and `idx_faces_person` (`name`, `tag_id`) first, then fills
  them by `person_ids.sync`: the pairs of name and id held, read from the index (never a face's row or
  vector), one UPDATE per pair that is wrong, by the index. One transaction under the write lock: a
  crash leaves the library at 20, and the next open runs it again. **Measured** on a sandbox copy of
  photo_index (68,466 photos, 226,246 faces), through the runner: **17.5 s** the first time (a copy
  just written, cold); warm, the steps are the index 1.6 s, the backfill 1.8 s (35,762 faces and 80,008
  listed people given an id), and the runner's standard checks on `faces` 6.5 s (quick_check 2.8 s,
  foreign_key_check 3.7 s). Writers in other processes wait (the busy timeout is 30 s). **It runs as the
  server starts** (#661): `tagpup_web` starts a thread, before it serves, that brings every library it
  serves up to date (`libraries.bring_up_to_date_in_background`). The server answers at once --
  `/api/server`, which the supervisor's hand-over waits 60 s for, names no library, and lists the thread
  as busy ("bringing N library(ies) up to date"), and a drain waits for it (#664). A page opened while a
  library migrates gets a blank tab until the migration ends (every URL under the library's name waits on
  its write lock); a page already open shows its own spinner for its requests (#666). Before, the first
  request naming the library ran the migration itself. A library held past the busy timeout is logged and
  left for its first request. The CLI and the MCP still migrate a library when they first open it. A whole re-sync
  afterwards, as a tree edit that changes who the people are does, takes 0.23 s. The plans, checked on
  the copy: the pairs `SEARCH faces USING COVERING INDEX idx_faces_person`; the UPDATE `SEARCH faces USING
  INDEX idx_faces_person (name=? AND tag_id=?)`; a photo's faces `idx_faces_photo_id`; `photo_people`'s
  pairs a scan of its 80,060 small rows by `idx_photo_people_name`. The new index also serves every
  lookup `idx_faces_name` served (the planner now picks it, covering).
- **What stage 1 cannot see.** A version of the app from before 21 still writing to the library writes
  names without ids; the doctor's `face_person_ids_out_of_step` and `listed_person_ids_out_of_step`
  (in `checks.RULES`, so the MCP's checks too; the examples are face ids and photo ids) count them, and
  `tools/doctor.py --rebuild-derived --apply` puts them right with the derived tables, touching no name.
  A snapshot from before 21 restored comes back at 20 and is migrated again by `snapshots.restore`'s
  `schema.ensure`. Two libraries are two trees: the same person has an id in each, and nothing reads an
  id across libraries. Ids are AUTOINCREMENT, never given again, so an id left behind by a node deleted
  by an older writer names nothing rather than someone else.

**Stage 2** *(designed and built 2026-10-09, in three parts; the owner's answers are at the end of the design)*: the id becomes the key and the name a cache of the
tree's, the same name can be two people under two groups, and a picker shows the group when a leaf is shared. The design, the counted
numbers, the phases and the owner's questions are in "People by id, stage 2" below. It replaces this section's earlier plan, whose
decisions it keeps: a branch tag cannot be a person, the two rules are refusals, deleting a person tag that faces use is refused with a
force that unnames them, old history stays undoable, and (#985) a name without a person tag is listed for the owner, never unnamed.

### People by id, stage 2 *(design, 2026-10-09; built 2026-10-09: part A, phases 1-3, part B, phases 4-5, and part C, phases 6-8; the questions at its end were the owner's, answered below)*
Stage 1 put the id beside the name and kept it derived from the name. Stage 2 makes the **id the person** and the name
what the tree calls them. Two things the owner added on 2026-10-09 shape it: the same NAME can be two people when their
tags sit in different groups (two cousins called Sam under `Family/Thackeray` and `Family/Ingersoll`; a dog and a friend
called Max under `Pets` and `Friends`), so identity is the tag's id and the leaf is only a label; and wherever two people
share a leaf, every picker, list, chip and hover shows the group too, and only then.

**What stays.** A person is a leaf `has_face` node that is not a root (stage 1's rule, unchanged). The files and
the keywords in them stay text: a person keyword is a path (`Family/Thackeray/Sam`), and a path names exactly one node,
so reading a file resolves a person without any guess even when the leaf is shared. Ids never go into a file, a
journal row of another library, or a snapshot's meaning; they are per library, as now.

**Counted 2026-10-09, read-only (`db.readonly_uri`), by the leaf rule.**

| | photo_index | kr-track | renton_parkrun |
|---|---|---|---|
| faces / named | 226,208 / 35,796 | 9,850 / 1,640 | 3,328 / 1,211 |
| nodes / face roots / person leaves / face branches | 895 / 3 / 406 / 9 | 73 / 1 / 66 / 0 | 262 / 1 / 257 / 0 |
| **person leaves shared by 2+ person tags** | **1 leaf, 2 tags (under two different roots)** | **0** | **0** |
| faces naming a shared leaf (photos) | 1 (1) | 0 | 0 |
| `photo_people` rows naming it (photos) | 12 (12), all from a keyword, the path names the node exactly | 0 | 0 |
| named faces with `tag_id` NULL, by why_not | none 40 (4 names), several 1 (1 name), branch 0 | none 186 (11 names) | 0 |
| `photo_people` rows with `tag_id` NULL, by why_not | none 40 (4 names), several 12 (1 name), branch 65 (4 names) | none 188 (12 names) | 0 |
| distinct ids on faces | 277 | 64 | 242 |
| face branches (group tags) carried by photos | 4 tags, 65 photos | 0 | 0 |
| tags of any kind with tags under them, carried by photos | 37 tags, 10,864 photos | 0 | 0 |
| `change_rows` on `faces` in the journal | 0 | 0 | 0 |

What the numbers say. (1) The owner's case is real but nearly empty today: one shared leaf, in photo_index only, and the
faces cannot say which of its two tags they mean (that one face and the 12 photos whose keyword path names one of the two tags are all the data the feature touches today). (2) The branch names are no longer on faces (the 7 were unnamed on 2026-10-09); what remains of "branch" is 65
`photo_people` rows made by group tags on photos, which #986 stops listing at all. (3) What is left for the names-to-review
list (#985): photo_index 5 names (4 with no person tag, 40 faces; 1 on two tags, 1 face), kr-track 12 names (186 faces),
renton_parkrun none. (4) **Rule (a), read as "no tag that photos carry gets a child", is already broken by 37 tags carried
by 10,864 photos in photo_index** (ordinary hierarchical keywords: a photo holds both `Places` and `Places/Seattle`); read
as "no PERSON tag that photos or faces carry gets a child" it is broken by the 4 group tags only. That is question 1.
(5) The journal holds no face row in any library, so "history from before ids" has nothing to translate today; the rule
below is for entries made before the build. (6) A lookup of a person's faces by id has no index: `WHERE tag_id = ?` scans
`idx_faces_person` (name, tag_id) in full and `photo_people` has none, so the migration adds them.

#### The id is the key: what moves
- **Writers.** Naming a face is `faces.name_as(conn, face_ids, person_id)`: it writes `tag_id` and, beside it, `name` (below), in
  one UPDATE. Nothing writes a face's person by a name any more except the one door that takes a name from outside
  (`person_ids.resolve`, below), so `tests/test_person_ids_single_owner.py` becomes "one writer of the pair". Face writers
  today, among them: `faces.name`, `name_if_unnamed`, `name_unnamed`, `set_names`, `reinstate`, `revert_automatic`, automatch's and
  clustering's, `identities`, the indexer's `record_detected`; all end in `faces._rebuilt` already.
- **Readers (158 lines that read `faces.name` or `photo_people.name`; 48 of them in `store/faces.py`, 16 in `services/faces.py`).**
  Everything that means "this person" groups, counts and joins by `tag_id`: `count_named`, `person_embeddings`, `person_page`,
  `counts_by_name` (becomes `counts_by_person`), `named_embeddings`, `for_named_matrix`, `named_for_known`, `names_by_photo`,
  `named_elsewhere_in_photo`, `guesses_named`, `identify.named_faces` / `decided_faces` / `representative_faces` / `face_samples`
  (keyed by id, not `{name: ...}`), automatch and clustering's known sets, `library_view`'s person source and counts, the
  navigator's people counts, `people.names`. `photo_people(tag_id, photo_id)` serves the photo side. The naming rules that compare
  "a person already on this photo" compare ids: a photo can hold two faces of two different Sams.
- **What a reader is shown** is a label (see "Showing the group"), never the cache.
- **The wire.** Between the pages and the server a person is `{id, name, tag, group, shared}`; a request names a person by
  `person_id`. The old `person_name` (a leaf) is still accepted by the CLI, the MCP and a page not yet reloaded: it resolves to the
  one person of that name, makes the person when no node has the name (today's `vocabulary.person_tag` rule: under the one
  face root, asked when there are several), and is **refused naming the candidates** when two are called it. A saved or
  bookmarked search keeps its contract (`{kind: 'person', value}`, phase 9e-1) with `value` the person's tag path; a bare name
  still works when it is unambiguous. A stale `person_id` (merged or deleted in another tab) is a 404 that says so and offers a reload; it never
  creates anyone.
- **`faces.name` stays, as a cache of the node's leaf, and identity never reads it.** Reasons to keep it: it is the only place
  an unresolved name lives (below); the CLI, the MCP and the doctor print it without a join; an older reader of a restored
  snapshot still sees names; and nothing is dropped, so nothing is one-way. Its rule: for a face with `tag_id` it is the node's `name`,
  written by `person_ids` alone in the same statement that writes the id, and by the tree edit that renames the node
  (`UPDATE faces SET name = ? WHERE tag_id = ?`, by `idx_faces_tag`); for a face with `tag_id` NULL and a name it is an
  **unresolved name**, left exactly as it is. The doctor's `face_person_ids_out_of_step` is turned round to compare
  `name` with the node's: id to name, the reverse of stage 1. (Dropping the column and joining the tree for every display was
  weighed: it ends the cache's drift for good, but an older app reading the library would see every face unnamed and offer
  them all to Identify, so it is not taken until nothing older is in use.) `photo_people.name` is the same cache.
- **Derived tables.** `photo_people` stays derived and stays written only by `people.rebuild`; its key becomes `(photo_id,
  position)` holding `tag_id` first and `name` as the cache. The one rule is `vocabulary.people_in_photo`, which returns person
  *references* (`id`, `name`, `source`) from three inputs: a keyword (its **path** is looked up in `PeopleVocabulary.by_keyword`,
  now built from `(id, tag, name)` rows, so a path gives its id; a bare-leaf keyword gives an id only when one person has
  that leaf, else an unresolved name), a person field of the metadata (a name: same bare rule), and the photo's faces
  (`tag_id`, exact). Duplicates are removed by id, not by lower-cased name, so a photo can list two Sams; a **group tag (a branch)
  is not a person** and gives nothing (#986). `photos.people` JSON (`PEOPLE_JSON`) keeps listing names; a photo that lists a
  leaf twice shows it twice, and the pages read ids for the label (`details-panel` chips).

#### Tree operations, by id
All of them are one transaction (tree row, `faces` cache, `photo_people` of the affected photos), with the files rewritten after it
by the machinery that exists (`tagging.replace_tag`, the journal's `change_files`, resumable). That closes stage 1's rename window:
no moment has faces with a name that no node has.
- **Rename a leaf** = `move_branch` of one node to a free path under the same parent. The id does not change; the cache follows. A rename
  to a path that is a node is refused ("that person is already filed there: merge them instead"). Changed meaning: today's Rename Person
  renames every node called the old name and silently joins them when the new name exists; stage 2's renames the one person picked
  (`rename_person(person_id, new_name)`) and never merges (question 8).
- **Move a person between groups** (`Family/Thackeray/Sam` to `Family/Ingersoll/Sam`): the node keeps its id; every face and every list
  entry follows by id; `faces` is not written at all (the id and the leaf are the same). The photos' keywords are rewritten to the new path.
  If another person called Sam is already at the destination the path is taken: refused, the owner merges or renames first.
- **Merge A into B** (two tags that are one person): `UPDATE faces SET tag_id = B, name = B.name WHERE tag_id = A`, the same on
  `photo_people`, the files' keywords rewritten, then A's row goes (`move_branch`'s join). The plan (dry run, as `tags.merge` already is by
  default) says how many photos would end up with two faces named the same person (both faces stay named; question 6).
- **Delete a person tag that faces use**: refused, with the count ("12 faces are named Sam; unname them first, or delete with
  force"). `force` unnames the faces in the same transaction (`faces.unname_person`, name_source as `unname` writes it) and journals it as one
  change with the tree row. A trigger `person_tag_not_deleted_while_named` (BEFORE DELETE ON `tag_taxonomy` when a face has that `tag_id`,
  RAISE(ABORT)) is the backstop for any connection; the merge repoints the faces before its DELETE, so the trigger sees nothing.
- **The two rules, as refusals with an explanation** *(owner, 2026-10-04 and 2026-10-07)*. Both are enforced where a person is chosen or
  a node made **by an action of the owner**, never when the indexer reads what a file holds: a file's keywords are the truth, are not
  refused, and make their nodes (a reading that makes a person tag a branch, or puts a person under a group, is shown by the doctor).
  (a) *A person tag that faces or photos carry gets no children.* Checked in `taxonomy` (one function, `refuse_child_of_person`) at
  `tags.create`, `move_branch` into a person, `Filer`/the keyword writer adding a new level under one, and the bulk Tags edit:
  "Sam is a person (12 faces, 40 photos); a person cannot have tags under them. Choose another group." Scope is question 1.
  (b) *A group tag is never put on a photo as a person.* Checked in `person_ids.resolve` (the one door for a person by id or name) so
  naming a face, `face_people.add_people`, the bulk People edit and the MCP all refuse a branch: "Family/Thackeray is a group of
  people, not a person." The pickers never offer a branch as a person. A group tag put on a photo as an ordinary tag stays legitimate.
  Existing violations are not converted: the 4 group tags on 65 photos stay as tags and drop out of `photo_people` by the rebuild.

#### Names to review *(#985; owner, 2026-10-09)*
A name on a face or in a photo's people that is no person's node is **not unnamed, tagged or created by the migration or by any
background step**. It is listed, per library, for the owner to settle one at a time.
- **Where it lives: a derived view, plus a tiny table of what was set aside.** The list is `person_ids.unresolved`'s pairs
  (`GROUP BY name, tag_id` over `idx_faces_person`, which stage 1 measured at 1.6 s on a cold copy of photo_index, faster warm; `photo_people` is a scan of 80,000 small rows), read when the
  page asks and never cached. Reasons: `none` (no person tag), `several` (two or more tags have the leaf; after stage 2 this
  means a *text* that cannot say which), and nothing for `branch` (a group tag is not a name; #986). The one table is
  `name_review_dismissals(name_key PRIMARY KEY, rows_seen, decided)` (migration 28, empty): a dismissed name stays hidden until it holds more
  rows than when it was set aside.
- **An entry** shows the name, the reason, the faces (with a few crops) and the photos that list it from a keyword alone, with a link to
  open them, and the person tags it could mean (every person with that leaf; for a name no tag has, the people with a close spelling).
- **Choices** (each a journaled change of its own, said and counted before it is applied, never run on open):
  1. *Make a person tag*: pick the group (a picker of the face roots and groups; one root offers itself, never silently chosen when
     several) and the node is made as a person under it; the name's faces and list entries take its id.
  2. *Link the name to a person*: pick an existing person (labelled with the group); the name's faces take that id and the person's
     name. For a name that came from another library's keyword the photos' *files* keep their old keyword; the entry says how many
     photos that is and offers the existing tag Merge (`tags.merge`) for them, which writes the files, as the owner's own step.
  3. *Unname the faces*: the faces become unnamed (`faces.unname`, manual source as the face panel's Unname writes it); photo
     keywords are untouched.
  4. *Dismiss*: keep as is, hidden until new rows arrive.
- **Where the owner opens it.** TagTuner, Review People: a first row "Names to review (5)" above the people (shown only when the count
  is above 0), opening a dialog with the entries. Activity shows the count with a link to it; the doctor and the MCP's checks report
  the count (names by `--show` only). A route pair in `tuner_routes` (`GET /api/names-to-review`, `POST /api/names-to-review/resolve`)
  over `tagpup.services.name_review`, which uses `person_ids` and `face_people`/`tags` for the writes and holds no SQL of its own.
- **A second kind, once a read made it: a new person with a name another person has.** A file read that makes a person tag whose leaf
  another person already has (a stale keyword of a moved person; a photo copied from another library) is *not* the same person
  until the owner says so. The list shows it too ("Sam, Family/Ingersoll, no faces: the same person as Sam, Family/Thackeray?
  Merge / keep both"). Later phase.

#### Showing the group when a leaf is shared *(the owner's requirement, 2026-10-09)*
**One decision in Python, one string in JS, one fixture holding them together**, as `person_tag` / `resolveTagOrPerson` are held by
`tests/fixtures/person_filing.json`.
- `tagpup.core.vocabulary.person_labels(nodes)` takes every person leaf of the library as `(id, tag)` and returns, for each, `shared` (another
  person leaf has the same `key`) and `group`: **the shortest tail of the parent path that tells the sharing people apart** (one segment,
  as `Thackeray`, when it is enough; two when two groups end alike; the whole parent when needed), empty when not shared. It is computed over **all** the
  library's people, not over the list being shown, so a filtered list of one Sam still says which Sam. Services answer people as
  `{id, name, tag, group, shared}`; nobody else computes `shared`.
- `web/common/vocabulary.js` gets `personLabel(person)`: `name` when not `shared`, else `name + ' · ' + group` (a middle dot, shown
  `Sam · Thackeray`), and `personTitle(person)` (the full tag, for the hover title and the screen reader). No page builds that
  string, joins `group`, or compares two people by their label; they compare ids. The fixture `tests/fixtures/person_labels.json`
  (libraries of people with the labels expected) is read by `tests/test_person_labels.py` and `tests/frontend/person-labels.test.mjs`.
- **The form** is `Sam · Thackeray` (leaf first, because lists are sorted by leaf and the leaf is what is scanned for; the group is dimmed),
  not `Family/Thackeray/Sam`. A narrow window shortens the group from the left (`Sam · …/Cousins/Thackeray`) and never the leaf; the full
  path is the element's `title` and `aria-label`. Sorting is by leaf, then by group, so two Sams sit together.
- **Surfaces** (each reads a person item, none a bare name): TagPup's face panel (the name box's offered people, the five-nearest
  buttons, "named elsewhere"), the photo's people chips and the selection panel's people, Suggest's chips (a name-only suggestion that
  two people match is offered as two labelled chips, replacing today's "which folder?" question), the tag editor's person suggestions,
  the navigator's people list (a tree: the group is already its parent row; a flat list and a filter result get the label), the search
  chips and results header, the person-faces hover (`attachPersonFaces(button, person)`, keyed by id), TagTuner's Identify Faces
  assign popup and bulk "Name as..." confirmation and undo toast, Review People's list and its header, the new-person dialog (see
  below), the rename dialog, and the CLI's and MCP's revealed output.
- **New person.** Typing a name no person has makes a person, under the group the typed text names (`Family/Thackeray/Sam`) or, for a plain
  name, today's rule (the one face root; asked when several). The dialog gains an optional **Group** box (a picker of the face roots and
  the groups under them). A new person whose leaf another person has is made without a warning screen; it is simply shown as `Sam · Group`
  afterwards, and a dialog line says "another person is called Sam (Family/Ingersoll)" so a typing slip is seen before it is made.

#### The journal, and history from before ids
- `tag_id` leaves `journal.DERIVED_COLUMNS`: a change records a face's `tag_id` and `name` both. A row's precondition ("the rows are what the change
  expects") compares `tag_id`, and `name` **only when the recorded `tag_id` is NULL on both sides** (an unresolved name), so a leaf rename
  between a naming and its undo no longer makes the undo a conflict. The cache refresh a rename makes is a derived write, not a change of the face.
- **Entries recorded before the build stay undoable, translated lazily.** A change whose `schema_version` is below the migration is replayed
  as stage 1 replays it today: the recorded `name` is put back and `person_ids.follow_faces` gives it the id the tree gives that name *now*
  (`_derive`); a name that no longer resolves leaves `tag_id` NULL, an unresolved name that the review list shows. Nothing in a journal is
  rewritten (the journal's tests compare it byte for byte), and the rehearsal compares after `_derive` on both sides, as now.
  Counted: no library holds a `faces` row in its journal, so today this is a rule with nothing to run on.
- Tree rows keep their ids in the journal (686 `change_rows` on photo_index, one change), so the tree's undo needs no translation.

#### Migration and phases
Migration 28 is **additive**: `idx_faces_tag` on `faces(tag_id)`, `idx_photo_people_tag` on `photo_people(tag_id, photo_id)` and the empty
`name_review_dismissals`; the trigger comes with phase 5 as a migration of its own, also additive. No row of the libraries is written; stage 1 already filled both `tag_id` columns, and they are
what the names give today. **Measured before it is built, on a sandbox copy**, as migration 21 was (that one took 17.5 s cold; an index over
226,208 faces is expected in seconds; it runs where 21 does, at the server's start, in the background thread, never on a page's request).
The data that changes is derived and is rebuilt by the owner's command, never on open: `tools/doctor.py --rebuild-derived --apply` after
a rehearsal that counts the photos: photo_index at most 77 (the 12 whose shared-name keyword now gets its exact id, the 65 whose group tag stops being
a person), the others none. Until it runs those photos list their people as before and the doctor's `people_out_of_date` says how many.
**An app from before the migration must not write the library after it** (it would write names without ids and the turned-round doctor
would then overwrite them): `schema.ensure` refuses a library whose version is above `LATEST` (today it does not: it only opens), with a message that says
which app to start. That is a new guard (question 7).

| Phase | What | Size |
|---|---|---|
| 1 | **Built (part A).** The group tag is not a person (#986): `PeopleVocabulary` is given the tree's groups (`taxonomy.group_tags`), `extract_people` drops a group's keyword (path or bare leaf), `people_out_of_date` agrees, `tools/doctor.py --rebuild-derived` rebuilds the photos' people (`people.repair`; it did not before). Not done here, left for phase 4 with the references: `PeopleVocabulary` rows carrying ids, `people_in_photo` returning references and deduping by id. No schema. | M |
| 2 | **Built (part A).** Migration 28 (`idx_faces_tag`, `idx_photo_people_tag`, the empty `name_review_dismissals`), and the newer-library guard (`schema.NewerLibrary`, every entry point), measured on a copy. | S |
| 3 | **Built (part A).** `person_labels` + `personLabel` + the fixture, and a `person` `{id, name, tag, group, shared}` on the answers that name someone (additive; pages unchanged; the list is in SPEC_TAGTUNER.md, `/api/people?records=1`). | M |
| 4 | **Built (part B).** Writers and readers move to ids: `faces.name_as`, `person_ids.resolve`, the 158 reader lines, `counts_by_person`, identify/automatch/clustering known sets keyed by id, the wire's `person_id`; `person_ids` follows turned round; tests with two Sams in the fixture. | L |
| 5 | **Built (part B; with 4).** (Was: until it exists, a tree edit that gives a person a child (a tag under them) silently changes the people of every photo carrying them (they become a group and leave the photos' lists, N photos, by the part A rule); the refusals of rule (a) are this phase.) Tree operations by id: rename of one node, move, merge, delete with refusal and force, the trigger on, the two rules as refusals, journal recording of `tag_id`, the lazy rule for old entries. | M |
| 6 | **Built (part C).** The pickers show the group on every surface listed (`personLabel`; the group's span shortens from its left); the pages hold the people as a `PeopleDirectory` and send `person_id`; a name two people have asks which; the new-person Group box. | M |
| 7 | **Built (part C).** Names to review: service, routes, Review People row and dialog, Activity, doctor and MCP counts, dismissals. *Not built:* the "new person with a shared name" entry (the second kind, "Later phase" above). | M |
| 8 | **Built (part C).** Cleanup and docs: the page's `personExists` (leaf and name lists) is gone, the design marked built, DATABASE and the three SPEC documents updated. The bare-name wire (CLI, MCP, a page not reloaded) is kept, as the design says. Columns are not dropped. | S |
Phases 1 and 3 can start at once and are worth having first (1 is an owner decision already made; 3 changes no behaviour). 4 and 5 must not
be split across a merge: the id is the key only when every writer writes it. Sizes are worker rounds: S about half a day, M one to two days, L three.

**Part A as built** *(2026-10-09; branch worktree-agent-aae3c5e71aecc6882)*, and what was decided in building it:
- **The group tag.** A face node with a node under it is a *group* (`taxonomy.group_tags`, the same reading as `person_ids.People`'s branches).
  `PeopleVocabulary.from_rows` is given them: a group's keyword -- spelled as a path, with a backslash or a bare leaf -- names nobody, and is in
  no `by_keyword`, so `extract_people`, `face_people._person_tags` (faces-from-tags), `people_paths` and `people_filing` (where a name is filed,
  hence what a click on a chip writes) all drop it by the one rule. A bare keyword that equals a group's
  leaf still names a *person* of that name elsewhere. `people._touched` compares the groups too, so a person given a tag under them leaves their
  photos' lists in the same transaction (`tree_edit`). **A face's name and a person field of the metadata are names, not tags, and stay listed**
  even when they spell a group (none exists on photo_index: the 7 faces were unnamed on 2026-10-09; they would be the review list's, #985).
  Counted read-only on 2026-10-09: photo_index 9 face groups, 4 carried by photos, **65 photos** whose people the rule now changes
  (`people.stale`, 10.8 s); kr-track and renton_parkrun none. The 12 photos of the shared-leaf keyword are phase 4's (ids), not 65 + 12 yet.
  *The doctor's `--rebuild-derived` did not rebuild `photo_people` at all*, only the derived tables and the ids: it does now (a dry run counts the
  photos; `--apply` rebuilds those photos only, outside the write lock for the reading).
- **Migration 28** is `idx_faces_tag` on `faces(tag_id)`, `idx_photo_people_tag` on `photo_people(tag_id, photo_id)` and the empty
  `name_review_dismissals`; additive, `touches` is the new table alone (so `schema_gap_blocker` is unchanged and a change made at 27 is undoable
  after 28; no `faces` quick_check is run, which migration 21's cost 6.5 s). **Measured** on a copy of photo_index (2,615 MB, copied by
  `db.copy_database` into a home of its own, then deleted): the copy was at schema 26 (the live library has not had 27 yet), 27 and 28 together
  took 0.8 s of which **28 is 0.6 s**; the planner then answers `WHERE tag_id = ?` as `SEARCH ... USING COVERING INDEX idx_faces_tag` and
  `idx_photo_people_tag`. Interrupted, it leaves 27 and runs again; two processes apply it once (both tested).
- **The newer-library guard.** `schema.ensure` raises `schema.NewerLibrary` (its text names the library by file name, its schema and this app's, and
  says to start the newest TagPup) for a version above `LATEST`, before it writes anything, so the journal, settings, snapshots and every store
  function that opens through it refuse too. Reads through `readonly_uri` do not pass `ensure`, so each entry point also asks `schema.newer_problem`
  (read-only; a file it cannot read is not called newer): the server's library middleware answers **409** with the sentence (JSON `{success, error}`
  under `/api/`, plain text for a page) for a library under its address, `/api/server` -- which a hand-over waits for -- still answers; the picker's
  Create answers 400; the CLI's group refuses before any writing command (exit 1); the MCP's writing tools raise a ToolError; the doctor's
  `--rebuild-derived` exits with the sentence. The sentence names where the backups are (data/backups, the snapshots under it) and how to go back.
  **Recovery is not blocked** *(review round 1)*: an older checkout is how the owner goes back, so ONE owner, `schema.reading_newer()`, says which
  operations may open a newer library, as it is, with a one-line note and nothing migrated, settled or written: the CLI's `history` and
  `snapshots` (list and restore; `schema.RECOVERY_COMMANDS`), the doctor's report, the MCP's read tools (their answers carry a `note`), and the
  server's `/api/server`. Everything that writes through the app stays outside it and refused. **When it first bites:** not for the app
  installed today -- its `_ensure` is `if version >= LATEST: return` and it opens a 28 library like any -- but for an app that has this
  guard, that is from the update after this one is installed. Until then the owner must not start an older checkout against a migrated
  library. **What it cannot do:** the hand-over starts the old version again when the new one does not answer; if the new one had migrated
  the library by then, an old version that has the guard is refused until the new one is started (a rollback of the code needs a snapshot restored).
- **The labels.** `vocabulary.person_labels` is one decision over every person of the library; the same *length* of tail for each person who
  shares a leaf; groups differing only in case fall back to the whole parent. `Directory` (`person_ids`) tells a person to the pages and is read
  from the tree inside the answer, never kept. **Decision:** the fields are one nested `person` on each carrying record, not flat beside `name`, since the
  navigator's people already have a `group` of their own (the branch they are filed in) and the tally's `tag` means a tag. A row keyed by a
  name two people share has `person: {id: null, tag: null, group: "", shared: true}`, since part A cannot say which; part B keys the rows by id.
  The photos' `people` lists of names and the dicts keyed by name get nothing now (a card list of thousands would carry a parallel array for
  every photo): the page keeps `/api/people?records=1`, where a name is looked up. The cached Identify queue and the saved suggestions are
  labelled when they are served, never inside the cache.

**Part B as built** *(2026-10-09; branch worktree-agent-a7c53f38d987029d9; phases 4 and 5 together)*, and what was decided in building it:
- **The id is the key; the name is its cache.** `faces.tag_id` and `photo_people.tag_id` hold the node; `name` is the node's leaf, kept in step by
  every writer and by the tree operations (`person_ids.follow_faces`, `follow_listed`, `follow_tree`; `out_of_step(spelling=)` finds a cache
  that differs; `fill` is migration 21's one-time backfill and nothing else). A row with a name and no id is an *unresolved name*
  (the review list's, phase 7). **One door:** `person_ids.target` / `resolve` turn what a caller sends -- an id, a tag path or a name -- into one
  Person or a refusal (`StalePerson` 404, `AmbiguousPerson` and `GroupNotPerson` 400, the sentence naming the candidates' tags);
  `People.settle` drops the id of a node that is gone and keeps the cache; **it gives a name with no id none**, however unique the name has become
  (round 2). **One owner links an unresolved name: `person_ids.link_added`**, for a name a person was ADDED under (`follow_tree` after an edit
  of the tree; the journal for a node its change inserted or renamed). Otherwise a writer that knows the id writes it, a keyword is resolved by
  its path, and the owner links the rest (the names to review).
- **Writers** (`store/faces.py`): `name`, `name_if_unnamed`, `name_unnamed`, `set_names`, `reinstate`, `revert_automatic`, `unname*`, `exclude`, `insert`,
  `unname_person`, `rename_unresolved` write the id with the name in one statement; a bare name two people have is refused, never guessed
  (a detected face given such a name stays unnamed). The keyword writer no longer drops "a person the file already names by their leaf": two
  people called alike are two keywords (`vocabulary.names_same_person`, by the id of their nodes) -- *before part B the second Sam's tag was
  skipped*. **Readers** return `Ref(id, name)`: `people_in_photo`, `people_by_face`, `counts_by_person`, `face_refs`, the matrices, `KnownFaces.labels`;
  a card, a count or a row of the pages keeps its name and gains `person` / `person_id`. Clustering stays name-based (a shared name's faces are
  compared as one person there: a decision, not a defect; the faces still carry their ids).
- **The wire.** `person_id` is preferred in every request that names a person (`face/match`, `faces/match-bulk`, `person/rename`, `person-faces`,
  `unmatched-faces/person-matches` and `build-status`, a bulk People edit's `add_ids` / `remove_ids`, the plans of the assignment job); a name still works
  when it is one person's (an open old page, the CLI, the MCP). `/api/people-faces` and `people-face-samples` are keyed by name when nobody else
  is called alike and by `id:<id>` for every person with a node; the queue rows carry `person_id`. `inspect.photos(person=)` takes a tag path
  (that person) or a name (everyone called it).
- **Tree operations by id** (`store/taxonomy.py`, `services/tags.py`). *Rename* renames the one node picked, never joins two people and never merges
  into one who is there (refused, "merge them instead"); the faces and lists follow in the same transaction, the photos' files after (the journal's
  resumable change). *Move* between groups keeps the id. *Merge* (`move_branch` onto an existing node) repoints the faces and lists to the target
  before the node goes, and the plan says how many photos would list one person twice and how many have a face of each (owner's question 6: allowed).
  *Delete* of a person faces name is refused with the count; `force` unnames those faces in the same transaction (they become faces to review, not
  decided as nobody) and then deletes. **The two rules.** (a) *no children under a person that faces or photos carry* -- for person tags only (owner's
  answer to question 1): refused for an owner's action (the tree view's add, a move under a person, the keyword writer's new tag); never for what a
  file holds (the indexer reads a path as it finds it). (b) *a group tag is never a person* (part A) is refused where an owner would set it.
- **Final round (2026-10-09).** Rename Person on a name no tag has, into a person's name, is a writer that knows the id and writes it
  (`faces.rename_unresolved`; a name two people have is refused). A name that is exactly one person's but whose rows are linked to nobody (a
  same-named person left) is REPORTED -- `person_ids.unresolved()` bucket `one`, the doctor, `checks.names_without_a_person` -- and linked
  only by the owner: `people link-name <name> [--apply]` (`services.people.link_name`; a dry run; one journaled change of the faces,
  undoable). The refusal of an undo (`_person_gone`) is gone with the fill it guarded against. The page action is the names to review's **Link to this person** (part C).
- **Review round 2 (2026-10-09).** The ownership question of round 1, answered: see "`person_ids.link_added`" above. `settle` filled any
  unresolved name that was unique NOW, so a rename of one of two same-named people and then any rebuild of the photo, face write or journal
  replay linked a hand-decided face to the other; it no longer fills. `person_ids.repair` and the doctor put names and dead ids right and
  link nothing; a journal undo of an entry that recorded only a name leaves an unresolved name; fixtures state who a face is. A tree edit takes
  the write lock BEFORE it reads the tree (`people.tree_edit`: BEGIN IMMEDIATE), so two processes cannot decide from a stale tree
  (`tests/test_people_by_id_at_once.py` holds one transaction open until the other is at its edit). The undo of a force delete or a merge returns the
  faces as unresolved names (the name and the decision back, the dead id dropped), never to another person called alike, because nothing links
  an unresolved name by itself; History says what an undo does not give back (the person, the photos' keywords). Cost, measured on a copy of photo_index: the biggest person (7,468
  faces) takes 8.0 s to force delete and writes 22,404 change_rows (the whole journal held 17,053); the rehearsal that refuses a bad merge
  before any file is written takes 6.6 s for it. The refusal text and `usage` say the rows ("about N rows", `history_rows_if_forced`).
- **Review round 1 (2026-10-09), what changed.** (1) *One rule for linking an unresolved name after a tree edit*
  (`person_ids.follow_tree`): only the rows of a name NO ONE had before the edit and a person has after it (a node made, moved or renamed
  into it) are linked; a name that became one person's because a same-named node LEFT (rename away, merge, force delete) was ambiguous
  before and its rows -- a hand decision too -- stay unresolved for the review list. (2) *Every refusal of a merge or move comes before the
  first file is written* (`tags._tree_refusal` rehearses the tree edit in a rolled-back transaction, so the rule is the tree's own).
  (3) *Reads fall back, writes do not:* `person-faces`, `person-matches`, `build-status` and `people-faces` accept a name two people have
  and answer for all of them (`person_ids.SharedName`, `people.for_reading`; nothing is created or linked); a write with it is still a 400
  naming the candidates, and the page shows that text. (4) *The single owner of an undoable record of a face's identity change is the
  journal* (`journal.record_faces`): a force delete (`taxonomy.delete_branch` / `faces.unname_person`) and a merge (`people.merge_person`)
  write ONE change of the face rows (name, name_source, tag_id; counts only in its summary) in the same transaction as the tree edit, so
  History can put the faces back; an undo that would give a face whose person is gone to ANOTHER person called alike is refused. **The tree
  rows themselves are still not journaled** (as before part B; finding). (5) `faces_renamed` is counted after the write, a group counts the
  faces under it. (6) `removals.removed_people` carries the id the change recorded. (7) The navigator keys a person by id (`p:<id>`) and opens a
  shared name by its tag path. (8) Two real processes editing the tree: `tests/test_people_by_id_at_once.py`.
- **The trigger** `person_tag_not_deleted_while_named` (**migration 29**, additive, triggers only) aborts the DELETE of a node a face names on any
  connection; `generation_faces_update` now also fires on `tag_id`.
- **The journal.** A change records `faces.tag_id` and `photo_people.tag_id` as columns of their own (`journal.cache_columns`; `DERIVED_COLUMNS`
  no longer has `tag_id`); a change recorded before part B has none and is replayed by the name (`_named_by_name`: the person that name is
  now). The undo of a node's insert releases the faces linked to it (`person_ids.release`) and is refused when a decision of the owner's names
  the node. *Not done:* the design said tree edits are "journaled with the tree row"; they are not journaled today (the tree is not a journaled
  table), so there is nothing to record for them (finding below).
- **The stale-keyword case, as built.** A file that still holds a path no node has (a rename interrupted between the tree and the files, a copy from
  another library) is read as the name its path ends in; if that name is one person's now, the settle rule lists the photo under that person
  until its file is rewritten (the journal resumes the rename; tested). It is wrong for the moment and visible in the review list; the former-paths
  record (question 5) was not built.
- **The library views.** A person in a view is the node (`tag_id IN (...)`, `idx_photo_people_tag`); a *name* in a search or a view is every
  spelling the rows hold (a view of "Sam" shows both Sams, as it always showed the name), a tag path is exactly that person. The navigator's
  people are `(Ref, photos)`, two people called alike two entries each filed under its own branch; names with no node merge by spelling. The
  tally counts by node. *Counted on a copy of photo_index (2,615 MB; 895 tree nodes, 406 people, 9 groups, 1 leaf with two nodes):* `people_counts`
  8 ms, `people_groups` 54 ms, `counts_by_person` 3 ms, the whole tree read 2 ms, `out_of_step` 4 ms (faces) and 47 ms (photo_people),
  `person_ids.unresolved` 50 ms (4 names with no person, 1 with several, 4 that are a branch), a whole settle with nothing to change 54 ms,
  migrating 26 to 29 1.4 s, and **renaming a person 1.05 s**. Every lookup by id plans as `SEARCH ... USING COVERING INDEX idx_faces_tag` or
  `idx_photo_people_tag`; the only temp b-trees are the grouping of one pass over `photo_people` (as before) and the distinct unresolved names.
- **Left for part C** (done: see "Part C as built"): the pages' own leaf-based "already has" rules and duplicate checks, the pickers that send ids
  from every surface, the names-to-review service, and the shared-leaf specs of the navigator.

**Part C as built** *(2026-10-09; phases 6, 7 and 8; findings #1074-#1080)*, and what was decided in building it:
- **The names-to-review list** is `person_ids.review_pairs(conn)`: two grouped reads (`faces` by `idx_faces_person`, `SEARCH ... (name>?)`, and
  `photo_people` by `idx_photo_people_name`) of the names that hold NO id, one `Review` per name key (all its spellings together; `why` none,
  several, one or branch; faces, those decided by hand, listed people, those from a keyword), never stored. **Counted read-only on the live
  libraries this task (each read 0.03 s, and none has migration 28 yet, so none has a name set aside):** photo_index 9 names -- 4 with no person
  tag (80 rows: 40 faces, 27 of them decided by hand, and 40 listed), 1 with two (13 rows), 4 that are only a group (65 listed rows, no face) -- so **5 names wait** and
  the 4 group names are `stale_group_rows` (the rebuild drops them, #986); kr-track 12 names with no tag (186 faces, all decided by hand, and 188 listed); renton_parkrun none. The one
  rule of what waits is `store.name_review.split`: a group tag's listed-only rows are not a name to settle; a name set aside is hidden until it
  holds MORE rows than `rows_seen`; the service, the doctor and the MCP all use it.
- **The service** `tagpup.services.name_review` (`entries`, `count`, `resolve`): four choices, each a rehearsal unless `apply`, each ONE write
  that reads the name, the person and the group again under the write lock (`BEGIN IMMEDIATE`) -- a name settled in another window is refused in
  words, a person or group gone is a 404, two windows choosing at once leave one change, a failure in the middle rolls the tree row back with the
  rest (all tested). *Make* is `taxonomy.add_node` under the group (so rule (a) is `refuse_child_of_person`'s), and the tree edit's own
  `follow_tree` links the name -- the one writer of that link is `person_ids._link`, which `link_added` and the owner's `link_to` share. *Link*
  gives the name's faces and listed people the person's id and name (`link_to`; for a person called otherwise, `keywords_kept` says how many photos
  keep the old keyword in their files, and the existing tag Merge rewrites them). *Unname* uses `faces.unname` with **name_source NULL, not
  "manual"** -- *a decision, not the design's text*: "manual" is "this is nobody", which Identify Faces never offers again, and the owner's
  intent is to identify them (a person's forced delete leaves its faces the same way). *Dismiss* / *Show again* write `name_review_dismissals`
  and are not journaled (a preference; nothing of a face or photo changes). Journal operations: `PERSON_MADE`, `PERSON_LINKED` (the CLI's `people
  link-name` writes the same), `NAME_UNNAMED`, each one change of the faces with the undo's note in History.
- **The routes** (`web/name_review_routes.py`, both apps, this PC only): `GET /api/names-to-review` (`?count=1`, `?dismissed=1`) and `POST
  /api/names-to-review/resolve`; 409 while the faces are clustered or named from tags, as every write of faces. The count is on the Activity page's
  attention answer (`names_to_review`, `names_url`), in `tools/doctor.py` ("names to review: N waiting, M set aside") and in the MCP's `checks`
  (`names_to_review`: counts by reason; a name only with `reveal`).
- **The pages.** `web/common/vocabulary.js` holds the one `PeopleDirectory` (`/api/people?records=1`: a tag path names exactly one person, a bare name
  one only when nobody else has it, a label names the person it shows), `sameRecord`, `sameTagPerson`, `sameNamed`, `personFields` (the id when
  there is one) and `photoAlreadyHas(photo, tag, isPersonTag, directory)`; with no people read, a library with no person tags, they are the old
  leaf rules -- the one fallback kept, since a leaf is all such a library has. `person-choice.js` asks "which one?" and never takes the first.
  Surfaces changed: TagTuner's Review People and Identify lists (rows by `person_id`, label, search by label, `person_id` in the address), the
  Assign box / cluster guess / Assign Cluster popup / face card Match / badges (a badge holds the LABEL, which finds its person again), rename,
  the faces strip and details, the New Person Group box; TagPup's face boxes, panel, strip, Suggest chips (a name two people have is two
  chips), the add-person list, the in-page and library tallies and their pill edits (`add_ids` / `remove_ids`), the navigator, the search's
  chips and a view's words, and the person-faces hover (by `id:<id>`). The label as an element (`person-label.js`) shortens the GROUP from its
  left with CSS (`direction: rtl` on the group's span) and never the name.
- **Decisions the design did not state.** (1) New Person with a group makes the tag first through the tag editor's create and names the faces by
  the id it answers with (two requests; if the second fails the dialog says the person was made) -- the assignment job's plan carries a name, and
  teaching it a group would have been a larger change of the job than the owner asked for. (2) A name no tag has but the faces hold is "exists", not
  new, in the pickers (hidden people stay people). (3) The in-page tally keys a person by id and the suggestion tally by tag only for a person who
  shares a name, so an unshared person's bare-leaf write is unchanged. (4) Names to review is Review People's, as decided; the Identify queue has no
  such row.
- **Not done, and why.** The second kind of entry ("a new person with a name another person has", later phase). `other_names` in Identify's "also
  names X" notes and a photo details' `people` (names, no ids) are names the server sends without ids: they cannot say which Sam. The History
  dialog never shows a person's name, so there was nothing to label. A leaf-keyed `/api/people-faces` entry is still sent for a person with a node when
  nobody shares the name, for an open page from before the update.

#### How it fails (each is a test before it is built)
- **Interrupted.** The migration is additive and one transaction: a crash leaves 27 and the next start runs it again. A tree operation is one
  transaction for the database (a crash keeps the old or the new, never faces with a name no node has); its file rewrite is the journal's
  resumable change, so an interrupted rename leaves some photos holding the old path. Those photos are the **stale-keyword case** below.
  The names-to-review writes are one change each; interrupted, they are in the journal as incomplete and settle at start like every change.
- **Two at once.** The always-on server and a CLI run: every operation above runs under the library's write lock inside `db.write_with_connection`,
  reads the tree inside the transaction and never caches it across transactions (stage 1's rule); `resolve` is called inside the writer's
  transaction. Two libraries sharing a folder: ids are per library and a keyword is a path, so a move written to the files by library A reaches
  B as a changed file whose keyword path B's tree does not have (the stale-keyword case, for B).
- **The stale-keyword case** (a file edited elsewhere, a copy from before a move, a rename interrupted, a shared folder). The old path has no node, so
  reading it makes a *new person tag* with a new id and the same leaf: a second Sam. This is correct as far as the data goes (a path is a
  person) and is not silent: the second kind of review entry shows it. Whether to *prevent* it with a record of former paths is question 5.
- **A read that fails.** A library that cannot be read answers an error and decides nothing (`present` and the doctor already do). A stale
  `person_id` is a 404, never a new person. A tree read that fails in the middle of a rename aborts the transaction.
- **The real data's shape.** One shared leaf, 5 + 12 names to review, 77 photos to rebuild, 37 ordinary tags with children (so a rule (a) over
  all tags would be broken at once). The tests use two Sams under two roots, a person and a pet of one name, a group tag on a photo, a
  keyword path of a shared leaf, a name with no node, a name two nodes have, and a leaf rename between a naming and its undo.
- **What the owner sees.** A picker that answers from the page's cache (the people list) with `shared` and `group` already there, so nothing is
  asked per keystroke; the review list reads two pair queries (the cold read of faces' pairs on photo_index was 1.6 s in stage 1; warm and the second table to be measured on a copy) and
  says "Reading the library's names..." while it does.

#### Questions only the owner can answer
1. **Rule (a), how wide?** "A tag that photos carry gets no children" over *all* tags is broken today by 37 tags carried by 10,864 photos
   in photo_index (a photo holds `Places` and `Places/Seattle`) and would refuse ordinary keywording. Recommended: **person tags only** (a
   person that faces or photos carry), which only the 4 group tags (65 photos) already break.
2. **Ids on the wire between the page and the server** (never in a file): recommended yes; the alternative, a tag path in every request, breaks when
   a person is renamed in another tab.
3. **The four names with no person tag** were to be created (2026-10-07) and are, since #985, to be settled by hand from the list. Recommended: all of
   them, and kr-track's 12, by hand; nothing is made or unnamed by the migration or a background step. (The one name on two tags: link to one of them.)
4. **The label**: `Sam · Thackeray` with the shortest distinguishing tail of the group, full path on hover, shown only when a leaf is shared.
   The alternative is the whole path always when shared. Recommended: the short form.
5. **A record of former paths** (`person_former_paths`: a moved or renamed person's old path, so a stale keyword resolves to the person and a
   photo is not given a second Sam; additive, people only). It adds a meaning (a keyword with no node can name a person). Recommended: **not now**; the
   stale keyword case is shown in the review list (phase 7) and built only if it happens, since zero shared leaves are stale today.
6. **A merge of two people who each have a face in one photo** leaves two faces named one person. Recommended: allow, and say the count in the plan.
7. **A guard against an older app writing a migrated library**: refuse to open a library of a newer version (new behaviour of `schema.ensure`, so the
   installed copy and the CLI must be installed before the migration runs). Recommended: yes.
8. **Rename Person means one person** (the one picked), it never joins two people of the same name and never merges into an existing tag; merging is
   its own action. Changes what "Rename Person" does today. Recommended: yes.
9. **A dismissed name** comes back when it gains rows. Recommended: yes.
10. **Where the list opens**: Review People (a first row, shown only when there is something) with the count in Activity. Recommended as written.

**Decided (owner, 2026-10-09): every recommendation above, as written.** The owner answered 1 (person tags only), 4 (the short label), 8
(Rename Person means the one picked) and 7 (refuse a library newer than the app) directly, and accepted the recommended answers to 2, 3, 5, 6, 9
and 10 by not changing them. Build order: part A = phases 1-3 (the group tag is not a person; migration 28 with the newer-library
guard; the labels and their shared fixture), part B = phases 4 and 5 together (writers and readers to ids; tree operations, refusals, trigger and
journal), part C = phases 6-8 (the pickers show the group; the names-to-review list; cleanup). Each part is reviewed and merged before the next.

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

### Faces on the photo *(owner, 2026-10-05; #787, #788, #790, #791; built 2026-10-07 on `feat/faces-on-the-photo`; faces and tags in step, the strip and the zoom: #859, #860, #861, built 2026-10-08 on `feat/faces-tags-in-sync`)*

TagPup's Organize shows the open photo with its faces, WLPG-style, and a face and the photo's person tag are one decision:

- **The box (#787).** A face's box is in the *stored pixels of the full file* (`tagpup.files.images.shown_size`), never in the 512 px copy a pane shows: `/api/photo-details` and `/api/photo-faces` say the pixel size the boxes are in (`size`; `null` when the file cannot be read -- then no box is drawn, rather than one on the wrong face), and both pages place a box by `boxInContainedImage` (`web/common/image-zoom.js`), the geometry of the zoom. Counted on photo_index (2026-10-07): 220,941 of 226,208 faces have a box beyond 512 px, so TagTuner's Face Crop Details box was wrong for 97.7% of them.
- **A person's tag names its face (#788).** `tagpup.store.face_tags`: a photo with exactly one face still to be named (unnamed, not excluded, not unmatched by hand) and exactly one keyword person no face carries (a leaf person of the tree: a branch is never a person) gives that face that person, as an automatic name (`name_source` NULL, so clustering may revise it; the photo's own keyword makes it a decided reference, #640). It runs after the keyword writes (`record_tags`, `record_saved`, `follow_fields`), when the indexer reads a photo again and when a photo's faces are recorded -- never after a write of the faces themselves, so an Unmatch is final (`name_source` 'manual') and an Undo is not undone. Several faces or people are named only by `tagpup_cli.py faces-from-tags`, by comparison with the decided references, when exactly one person's decided faces are as alike as naming unasked allows (`clustering.names_unasked`, the owner's 0.80) and the photo's keywords name that person: no new threshold. Counted read-only (2026-10-07), photos with a face to be named and a keyword person no face carries: kr-track 223 -- 72 named by the tag alone, 114 more by comparison (222 faces in all), 37 left for Identify Faces; photo_index 22,648 -- 6,932 by the tag alone, 4,848 more by comparison (12,656 faces in all, 11.5 s to plan, the decided matrix included), 10,868 left. A keyword person with no id (a branch, or a name filed twice) names nothing: 77 such rows in photo_index, 2 in kr-track. The backfill is a dry run unless `--apply`, one journaled change (`undo` takes it back); it was not run on the owner's libraries.
- **Who a suggested person is (#790).** `web/common/person-faces.js`: wherever a suggested person is a text button (TagTuner's face-card suggestions, the "Looks like" button of a cluster, the resemblances beside a selected face; TagPup's Suggest chips and a detected face's suggestion), hovering or focusing it shows up to four square crops of that person -- the faces a person decided first, the one most like them first (`/api/people-face-samples`, `tagpup.services.identify.face_samples`, cached with the decided stamp). One component, one answer per page (kept 15 s), a popup that takes no pointer and no focus (`aria-describedby` while shown), "no face named yet" for a person with none.
- **The faces on the photo (#791).** A face icon on the photo when faces were found (never while Just look: faces are not recorded in a folder the library does not hold) toggles a box on each face (`web/tagpup/face-boxes.js`). A click on a box opens a panel: who it looks like with how much (`/api/face-matches`, TagTuner's own call over one cache of named faces, now served by both apps from `tagpup.web.face_routes`), each with the #790 faces on hover; a name box that autocompletes from the vocabulary (a new name goes through the page's existing placement question); **Not important** (exclude); **Not this person** when named. Choosing a person **names the face AND adds the person to the photo, the tag first**: through the page's own save of the photo's keywords (its queue, its undo, its journaled file change), then `/api/face/match`. A tag that is not added (a failed save, a placement answered Cancel) names no face; a face that cannot be named afterwards says so and keeps the tag, and the same choice names it. **Decided (2026-10-07, #861, reversing #791's "taking a name off leaves the tag"):** taking a name off a face, or excluding it, takes the person off the photo too unless another face still carries them (below). The writes are TagTuner's own views of one face (`tagpup.web.face_routes.face_match`, `face_unmatch`, `faces_exclude`), registered in TagPup's blueprint (`tagpup.web.photo_face_routes`) which refuses them while the library is being clustered as TagTuner's does; a name that is the face's already, as an automatic one, becomes the person's decision (`services.faces.name_face`).
- **Measured** (`scripts/measure_faces_on_the_photo.py`, headless Chromium on a sandbox copy of photo_index, 2.6 GB copied in 13 s; a photo of three unnamed faces given a 4000 x 3000 file, shown 613 px wide; 2026-10-07): the click on the face icon to three boxes painted and idle 29 ms; the click on a box to the panel with five suggestions painted and idle 397 ms for the first (it builds the matrix of every named face, once per state of the faces table) and 81 and 66 ms for the next two; hovering a suggestion to the popup of that person's faces 763 ms for the first (everyone's faces, one pass, cached) and 166 ms for the next (120 ms of it the pause before it shows); the click on a suggestion to the tag written, the face named and the boxes and the photo's people painted and idle 532 ms (an ExifTool write included). Afterwards the library held the face as a decision with the person's id and the person as the photo's keyword person: the face and the tag agree for TagTuner, which reads the same rows.
- **Measured, faces and tags in step** (`scripts/measure_faces_in_step.py`, headless Chromium on a sandbox copy of photo_index, copied in 15 s; a photo of three faces, one named, given a blank 4000 x 3000 file, so that the page and the file start with no person tag, as in the owner's screenshot; 2026-10-08, other agents' work on the machine): the click on a strip card of an unnamed face with a suggestion to the face named, the person on the photo (an ExifTool write included) and its box painted named and idle **648 ms**, the strip drawn again from the library's answer **1,064 ms**; afterwards the library and the file agreed (the face named, the person a keyword person, one person tag in the file), and the page loaded again showed the same boxes, the same cards and the person's pill. **Not this person** on that face to the face unnamed, the tag out of the file and the box painted **524 ms**. The zoom: the click on the photo to the three boxes drawn over it **22 ms** (all inside the window, none behind it), a click on a box to the panel **42 ms**; Escape took the panel away and left the zoom, a second Escape closed it. TagTuner's routes on the same photo: Match wrote one file and named the face (the person a keyword person), Unmatch and Not important on the face took the tag out of the file, Unmatch All left nothing named. What a read of the faces costs, since every write of the open photo's tags with a face still to be named reads them again (`facesFollowTags`, after the write, not blocking it): **/api/photo-faces 0.30 to 0.36 s** (`panel` reads every named face's vector for the strip's suggestion: #828), as it costs each time a photo is opened. AutoMatch All found no look-alike on the picked photo, so its tags were measured by the tests only.
- **Names from one run are references for the next (#840, decided; accepted).** A face named at 0.70 to 0.80 is a keyword-confirmed reference, so a later run compares with it and can name faces a step further, one 0.70 step at a time: three applies in a row on a copy of photo_index changed 10,589, 2,376 and 698 faces. So after the first `faces-from-tags --apply` a later one needs `--again` (it prints the counts and says why; the dry run says it too, `earlier_apply` in the details; an undo of the earlier change forgives it). The rule on a save has the same property -- each face it names becomes a reference for the next photo -- and nothing stops it: the person's references can drift, and Identify Faces is where a drifted name is taken back. The decided faces of a person are read once for an index pass (`derived.Batch.decided`, kept up to date as the pass names faces; #841), once for a save on its own.
- **A face and the person's tag are one decision (#861, owner 2026-10-07; one owner, `tagpup.services.face_people`).** The owner's screenshot: a face named (the box green, the strip showing the name) and the photo saying "No people tags", because TagTuner's Detected Faces strip (Match, AutoMatch All) named the face and wrote no tag. Now every write of a face of an open photo goes through the one service and the keyword follows:
  - **Naming adds the person to the photo, the tag first.** TagTuner's Match (a typed name, a suggestion, a new person) and AutoMatch All, and TagPup's box and strip, all do: a tag that cannot be written (a file that cannot be read, a share that is away, a person the tree files in two places) names no face and says why (400, nothing changed); a face that cannot be named afterwards (somebody named the person on another face meanwhile) keeps the tag, as always. What naming would refuse is refused before the tag is written (an excluded face, a person already on another face). The tag is written by the machinery of every keyword write (`tagging.change_each`): one journaled change of photo files ("person added for a named face"), under the one lock of changes of photo files, each ExifTool command with a 60 s deadline, TagPup's folder records told. History's Undo of that change takes the person off again and, since #908, unnames the face with it (below). A photo whose keywords already name the person (as the library records them) is not read or written again. The server files a typed name by the rule of a chip and Apply All (`vocabulary.person_tag`, the tree only read -- no node is made); a person the tree files in two places, or a tree with several people roots, is refused with the sentence that says where to add them (TagPup's Organize asks the placement question). TagPup's page writes the photo's tag itself (its queue, its undo, its placement), then calls the same `/api/face/match`, which finds the tag there and writes nothing.
  - **A rename is both**: a face that carried another person's name and is named a new one takes the old person's tags off the photo unless another face is them (the same untag as below, after the new person is on).
  - **Taking a name off takes the person off, the face first.** `Not this person`, Unmatch, Unmatch All and `Not important` (exclude) on a face named that person remove every keyword that names the person from the photo (the tag at their path and a bare one: `vocabulary.same_person`, under a people root), **unless another face of the photo still carries them** (then the tag stays). **The tag goes even when the owner typed it by hand** (the owner's rule, #900): a face that is not them is not a person of the photo, whoever wrote the keyword (naming found the tag already there and wrote nothing, so the journal has nothing of it to undo). A selection ruled out in bulk (TagTuner's Exclude selected, Ignore cluster, Unmatch selected) does it too, as a job (below, #907). The order is the reverse of naming on purpose: the face changes first, so a tag that cannot be taken off (said: `warning`) leaves the state the app has always allowed, a person tag with no face; the other way round it would leave a named face on a photo without the person, the very drift. One journaled change ("person taken off for an unnamed face"), which History's Undo takes back -- leaving the face unnamed with the person back, a state the app allows. TagPup's page writes the tags itself here too (`page_writes_tags` in the request; the answer's `untag` says which, by the server's rule) through its own save of the photo.
  - **Automatch names under the faces' write lock**, so its tags follow it (the people it named, one change): a tag that cannot be written takes the names back (`faces.revert_automatic`: only a face still carrying that name as a guess; a face a person confirmed or renamed meanwhile is theirs).
  - **Many photos at once is one job (#907, owner 2026-10-08; `tagpup.jobs.face_assignments`, the plan and steps in `tagpup.services.face_assignment`).** Identify Faces' Assign (`/api/faces/match-bulk`, so also "new person" from a cluster), Unmatch (`unmatch-bulk`, and the Undo of an assign), Exclude selected and Ignore cluster (`/api/faces/exclude` with `bulk`), and Re-examine this folder (`/api/folder/automatch`, applied) all keep faces and tags in step by the same rule as one face, through `face_people`: naming writes the tag first, then the faces; unnaming and ruling out change the faces first, then take the people off the photos no face of theirs remains in; Re-examine writes its guesses under the faces' write lock, then the tags, and takes the guesses of a photo whose tag could not be written back. `leave_tags` (#901) is gone: ignoring a cluster of named faces now writes the photos of its named faces (a face that carried no name touches no file).
    - **Undo is one undo of the job (#952, #953).** The state keeps the journal changes the job wrote and the plan the names and deciders its faces had; `POST /api/faces/job/undo` unnames an assignment's faces (unreviewed) and undoes exactly those changes (a tag the photo already held was never written, so is never taken off), or undoes the tag changes of a removal and puts its faces back (ruled-in again, named by the name and decider they had). TagTuner's Undo bar uses it and the Ignore dialog says so. The undo is state-based and can be pressed again: tags first for a removal (the journal's undo, then the person added where the file lacks them), a face named again only on a photo whose file really went back; an assignment's faces first, then its tags taken off the photos no face carries; the job is marked undone only when nothing remains, else the reply says how many photos remain (the share is away) and the bar says "Undo incomplete". It runs synchronously, held like a running job (`long_work()`, `refuse_if_running`, the library's run), and the bar shows "Undoing...". An undone job is not resumable (`resume` refuses, the banner offers Let go only; `let_go` refuses a job running in another process by the state's run). The record (plan with the people's names, state) is kept 30 days after the last step, a stopped job's resume window too (`job_files.sweep`).
    - **Seen and held to (#955).** `GET /api/faces/job/current` is read on opening either page (`web/common/face-job-banner.js`, wired in each `main.js`): "N of M faces of an assignment remain: Resume / Let go", and a job running in another window is followed. A running job is in `lifecycle.long_work()`, so an update waits for it, and the resume route forgets the grid caches as a start does.
    - **Shape.** The plan is made and refused as a whole before anything is written (two faces of one photo, the person on another face, a person the tree files twice or a branch of the tree -- never a person -- a reason that is none), written to the library's cache folder (`<job>.plan.json` and `<job>.state.json`, `tagpup.files.job_files`; swept after a week, like a bulk edit's), then run in steps of 25 photos (faces that carry no name: 500 to a step, touching no file), each step the journaled change of photo files a single write makes (History lists "person added for a named face" / "person taken off for an unnamed face" per step, each undoable; the faces' names are not journaled, as before). The route waits for the job's end and answers what the job did (`matched_ids`, `not_named`, `tags_written`, `tags_removed`, `warning`); a photo whose file cannot be written is an error entry and its face is left as it was, the job goes on.
    - **Interrupted.** A crash, restart or closed tab leaves the plan, the state and a `running` run, which the next claim marks `abandoned`; `GET /api/faces/job/current` offers it, `POST /api/faces/job/resume` carries on from the first step not recorded done. Every step is idempotent -- a face named already is not named, a tag the photo holds is not written, a person already off is not taken off (the names a removal had are kept in the plan, because a step run again finds the faces unnamed) -- so the step under way is simply run again and no file is written twice (tested by killing the job inside a step). Cancel stops after the step under way.
    - **Two at once.** One assignment runs in a library: the claim is the library's `job_runs`, so the CLI's process and the always-on server refuse each other (409 naming it); the photo-file lock is held for a step only, so another change of photo files interleaves; the faces' writes of other clicks go on and each step decides again under the faces' write lock (a face renamed or ruled out meanwhile is left as it is). The job does not hold TagTuner's clustering flag.
    - **The owner sees** the button say "Assigning..." for as long as it takes (25 files to a step; on a network share every file is a round trip), then the grid as before, with the faces that could not be named still in it and a sentence saying why. Re-examine's question now says it writes the people into the photo files.
  - **A person's tag taken off a photo takes the name off its faces (#908, owner 2026-10-08, reversing #834).** The pill in the pages, a tag taken off a selection (`bulk-tags` and the bulk edit job) and History's Undo of an add (the "person added for a named face" change, an Apply All, a carry-forward) unname every face of that photo that carried the person -- `tagpup.services.face_people.unname_for_removed_tags`, called by `tagging.save_photo`, `tagging.change_tags` and `services.journal.undo` after the photo's file is written and its row follows. It reads what was ACTUALLY written (a photo whose file could not be written keeps its faces), unnames only when the photo's keywords no longer name the person under any spelling, never for a branch of the tree, and leaves a face somebody renamed meanwhile (skippable edit). It is one journaled change of the faces, "face unnamed for a person taken off the photo", so History's Undo names them again, name and who decided it as they were; the pill's reply says `unnamed_faces` and the page reads the photo's faces again. A re-read of a photo whose file was changed outside (the indexer) does not unname: only the owner's own removals do.
    - **One place decides what a change did (review of a1821cf, #950).** `face_people.follow_change(library, change_id, undone=)` reads the journaled change's OWN file rows -- only files it really wrote and left done (or that its Undo really put back), from what each held before to what it holds after -- and from that unnames the faces of persons whose tag really went and names again the face of a person whose tag really came. The pill's save, `change_tags` (bulk-tags and the bulk edit job) and History's Undo all end there; none reads `written`, which also lists photos whose file already read the same and were never written (606 hand-named faces of photo_index sit on photos that lack the tag a selection might remove).
    - **A tag put back names no face; only History's Undo of the removal does (#951, superseded by #959).** A decision made since a removal (a name given by hand) is not in the journal, so no rule names a face from an old record. A person's tag put back by the pill, TagPup's Ctrl+Z or a selection names NO face. The one-face rule of a save, Re-examine and the naming job (`faces-from-tags`) only ever BLOCK: they do not name another face of the photo as that person while an applied, not-undone removal record for that person on that photo stands (`tagpup.store.removals`: the journal's rows of the change "face unnamed for a person taken off the photo", whose summary says which change of photo files it followed); `tags-from-faces` writes tags, never names. The owner names the right face by hand (TagPup's page writes the tag first, then `face/match` names the chosen face, which the block lets through; TagTuner's server writes the tag, then names). History's Undo of the removal is the exact inverse: it undoes the unnaming change (the journal compares the faces with what that change left, so a name given since refuses it, said) and only when every file of the removal went back.
    - **The record of the removal is the face row itself: `name_source = 'manual'` with `name` NULL**, the "this is nobody" decision `Unmatch` has always recorded (decided over a new table or a new column: the guards already read it). Automatch and Re-examine (`faces.NOT_DECIDED_NOBODY`), the one-face rule of a save (`face_tags`) and clustering (`manual_names`) never name such a face, and `tags-from-faces` writes a tag only for a NAMED face, so none of them puts back the tag the owner took off, and nothing else was needed in `tags-from-faces` or the naming job. Not recorded: a person taken off a photo none of whose faces carried them (the tag was typed by hand): no face to mark, so a later Re-examine could name a face as them and write the tag again -- an open question for the owner. A tag put back (the pill again, Ctrl+Z, History's Undo of the removal) names the face the removal unnamed again (below, #923).
  - **Existing data: `tagpup_cli.py tags-from-faces`.** A dry run (counts only, never a name) of the photos listing a person from a face alone (`photo_people` source 'face'), and with `--apply` the keyword written into each photo's FILE -- it changes photo files -- 25 photos to a journaled change ("person tags from faces"; `history` lists, `undo` takes a chunk back; no copy of the library, the journal holds each file's fields before and after). A person the tree files in two places is left, counted. **One rule for "the photo already names the person"**: the keyword writer's (`vocabulary.same_person`: the leaf under any root), applied by the plan too, so a photo whose keywords already name the person under a root the tree does not file people under (`Parkrunner/<name>`, a tag the index does not read as a person) is skipped and counted -- a tree question for the owner (make that root a people root, or retag) -- the dry run's count to write is what `--apply` writes and a second run finds none (#898). **Guesses are left unless asked** (#899): a person no face of that name named by hand (name_source 'manual') backs, only clustering's or automatch's guess, is skipped and counted ("from a guess only"); `--guesses` writes them with a printed warning, since the keyword makes each guess a decided reference (#640). A file that cannot be written is counted and the others are written; a file changed meanwhile is a conflict, never overwritten (run it again); interrupted, the chunks written stay and the one under way is settled at the next start. The doctor reports the same count (`photos listing a person from a face alone`, reported, not broken). Counted read-only 2026-10-08: **photo_index 491 photos, 606 people: 385 photo files (474 people) by default; left: 40 filed in two places, 42 named in the file under another root, 50 from a guess only (`--guesses`: 416 files, 524 people); kr-track 677 photos, 1,052 people: 662 files (1,030 people) by default, 22 from a guess only (`--guesses`: all 677).** Not run on either.
- **The strip and the boxes are one answer (#860).** The cause of "I named it from the strip and the box stayed red, even after a reload": TagPup's Detected Faces card **added the person's tag only**, and the server names a face from a tag only when it is the photo's one face to be named (#788), so with two or more faces nothing named the face, in the library either, and with one the page's copy of the faces (`/api/photo-faces`, which the boxes and the strip are drawn from) was not read again until the photo was reopened. Now the card names the face through the box's own function (`nameFaceAs`: the tag first, then the face), is clickable whenever it would change something (the face unnamed, or the person missing from the photo), and **every write of the open photo's tags** -- the save, a chip, Apply All, an Undo -- reads the faces again when the photo has a face still to be named (`facesFollowTags`, the one hook; not while a naming of the page's own is under way, which draws them itself). The strip and the boxes read one route (`/api/photo-faces`, `tagpup.services.faces.panel`) and are drawn from one copy; the colour of a box and the label of a card are the same field (`name`). The strip's suggestion (`panel`'s nearest named face) and the panel's (`/api/face-matches`, the shared matrix) are still two sources for "who it looks like" (#828, open); they never decide whether a face is named.
- **In the full-window zoom (#859).** The boxes and the panel are drawn over the zoomed picture too (`web/common/image-zoom.js` says where the picture sits, `zoomedPicture`, gives a page a layer to draw in and asks it before a click or Escape closes the zoom; `web/tagpup/face-boxes.js` draws into it, by the zoom's own `boxInContainedImage` and the pixel size the server gave): every face's box, named and not, the same icon (the toggle is shared with the photo in the panel), the same panel with its suggestions, naming and Not this person. One surface at a time -- the photo in the panel is emptied while the zoom is open, so there is never a second copy of a box -- and a panel open when the zoom opens goes with it. **Escape, or a click on the backdrop, takes the panel away first and the zoom after**; a click in the panel or on a box does not close the zoom. The suggestion hover popup is above the zoom (z-index 3500 over 3000). A sideways photo keeps the documented limit.
- **Sideways photos: the known limit.** A photo stored with an EXIF Orientation of 2 to 8 is shown turned (`/api/photo-file` as TagPup serves it) and its boxes are in the stored pixels: they are drawn where the stored pixels put them, which is not where the faces are. `/api/photo-faces` says `turned` and the panel says "it may not sit on the face"; the faces are still named by box, not by position. The orientation redesign (boxes stored as the photo displays) is a later project; tested in `tests/test_organize_faces.py` and `tests/frontend/organize-faces.test.mjs`.
- **Failing, and at once.** Two tabs, or TagPup and TagTuner on one library: a face named in one is read by the other from the faces table (the fingerprint moves, the caches are read again); a person already on another face of the photo is refused (400); an excluded face is refused (409); clustering in progress refuses the writes (409). An interrupted naming leaves the tag written and the face unnamed, the state the app had before: the same choice completes it; an interrupted removal leaves the face unnamed and the tag, which the person's pill takes off. The strip and a box racing (a card and a box clicked together) name once: one function, one `busy` flag. The tag part is journaled where the existing owners journal it (the photo's file change); face names are not (as automatch's are not). A photo's file that cannot be read (a missing file, a share that is away) refuses naming with the reason instead of naming a face the photo does not carry; Just look has no faces to name. A tag written first can look to the one-face rule (#788) like "one face to be named and one person no face carries" and name the OTHER face before the chosen one is named (reproduced when renaming a named face on a photo with one nameless face: the choice was refused, the wrong face green): the server takes that guess back (`face_people._give_back_to`: only a face that was nameless before the tag and still carries the name as a guess) before it names the choice. TagPup's page writes the tag before it calls the server, so there the server cannot tell the guess from an earlier one by when it was made: with `page_writes_tags` a face carrying the person only as a guess (name_source NULL) yields to the person's choice and is given back (`faces.guesses_named`, `revert_automatic`), while a name somebody decided on another face still refuses (#897).

### Name faces from tags *(owner, 2026-10-05; #789, #844; built 2026-10-07 on `feat/name-faces-button`)*

The two ways to name faces from a photo's person tag that already existed -- `tagpup_cli.py faces-from-tags` (the plan and journaled change of "Faces on the photo", #788) and identity clustering (`identities.resolve`, the CLI's `cluster-faces`, the Tk runner's button) -- had no button in the apps. One button, **Name faces from tags**, now runs both as ONE background job (`tagpup.jobs.naming_faces`; routes `tagpup.web.name_faces_routes`, `/api/name-faces/{start,status,scope,current,confirm,cancel}`, both apps, this PC only): TagTuner's **Folder Matches** header and gear, and TagPup's Organize folder view. The dialog is one module, `web/common/name-faces.js`; each page adds a module of its own (`web/tuner/name-faces.js`, `web/tagpup/name-faces.js`: the open folder, what to refresh).

- **A dry run first, and a question.** The job's first step reads the plan of `faces-from-tags` (`faces_from_tags.plan`; nothing is written) with a progress bar and Cancel, and the dialog asks: "Name N faces in M photos (X by their tag, Y by looking like the person's confirmed faces)? Z photos are left for you." (counts only; the plan's bands stay in `faces-from-tags`). **Yes** applies exactly that plan -- not one read again, which would cost the plan's time twice and could name other faces than the question said -- by the same code the CLI's `--apply` uses (`faces_from_tags.faces_from_tags(..., planned=)`, one journaled change, `undo` and History take it back). The plan stays in memory 15 minutes; the job holds no claim and no thread while the question waits (`asking`), so a question left open blocks nothing and an update's drain does not wait for it. **Applied before** (#840): the question says so and why a second time can name more; Yes is then the CLI's `--again`.
- **Grouping, a second box, off.** "Also group the rest of the faces by who they look like and name those groups from the tags" runs `identities.resolve` after the change of the names, through the job, with its steps reported (`on_step`) and a Cancel that stops it before its names are written. It **re-derives the automatic names of the whole library** (names given by hand are kept; they are its anchors), is **not journaled** (History cannot undo it), uses **no model and no graphics card** (`PhotoIndex(model=None)`: it reads the faces' own vectors), and takes long: on a copy of photo_index (225,000 faces) 53 minutes -- reading 2 s, DBSCAN 220 s, voting 2 s, propagating 112 s, **matching the rest 2,807 s (89%)**, writing 19 s -- with a peak of 2.47 GB in the process (506 MB after). The box says all of this.
- **What Cancel does** (the change of the names is one SQLite transaction, `journal.apply`: never half a change). In the plan or at the question: nothing was changed. Before the write: it is not made. During the write: it cannot be stopped; it finishes whole, the grouping is not run, and the result says that the names were written (History). In grouping: stopped before the names are written (`resolve` announces `saving`, then writes in one commit; a Cancel before the announcement writes nothing, after it the commit finishes); the status says `can_cancel` false from then on.
- **Refused, and one at a time.** A library has at most one of these jobs (a click while one plans, writes or groups is told so, with that job's status, 409 `job`; a click while the question waits shows the question). Also refused, 409 with the reason: while the library is indexed, synced, verified, or being worked on by Suggest (the same list as moving a root: `tuner_routes._busy_in`; **decided**: Suggest refuses it too although neither step needs the card -- the rows it reads are being written), edited in bulk (here or in another process), or its faces are being clustered (TagTuner's flag). The claim is a run of the library's `job_runs` (here and in another process, per phase: the plan, then the write), so the Activity page lists each with counts and no name. From the Yes to the end the job holds the same flag, so TagTuner's assignments and TagPup's face writes are refused (409 "Server is currently clustering faces") while names are given: they would write against a plan.
- **Stale plan (#869).** The one transaction of the change holds it to what the plan read, of each planned photo as well as of each face: the face unnamed and not ruled out (as ever), the photo's tags as stored (a person's tag taken off while the question was open), and the names of its other faces (one named meanwhile, which would put the person on two faces). The last two are carried as expectations with no write (`journal.update(..., values={})`, `face_tags.guards`, read in the plan's own read transaction), so the owner of the rule is the journal's apply, and the CLI's `--apply` has them too (its re-plan makes the window seconds, not nothing). Any one differing refuses the whole change: `failed`, "Something changed while the question was open: nothing was written. Ask again." Any change to a planned photo's tags refuses, not only a person's. When another window applied first, it says so (a second apply is refused without `--again`).
- **Grouping and History (#870).** Grouping that rewrites a name the change wrote makes History refuse the undo of that change (confirmed on a copy); the result says so only when a rehearsal of the undo, run after grouping (`naming_faces.Job._undo_still_works`), is refused, else it says Undo takes the names back and that History cannot undo grouping. A name its photo's own keywords bear out is kept, whatever its likeness, by the propagation loop, and the final matching pass names a face unnamed by it from 0.70 (`clustering.is_offered`) when its best match is a person its photo's keywords name, not only from 0.80 (#855 and #902: on a copy of kr-track the loop alone still took 24 names away, 2 of them keyword-confirmed at 0.7331 and 0.7784; with the final pass 22 are taken away and 0 are keyword-confirmed, 278 faces given; hand-given names unchanged) (owner 2026-10-08; the 0.70 to 0.80 names `faces-from-tags` writes were taken away before: 2,639 fewer named faces than before grouping ran). With the box ticked the box, the question and the result say so; unticked, the result says Undo takes it back.
- **The other way (#871, #872).** While names are written or faces grouped (`naming_faces.writing`) the library's other writers are refused with 409 "Names are being given from tags; face changes wait until it finishes." -- `tuner_routes.clustering_refusal`, the one owner, for TagTuner's own writes and, by `name_faces_routes.refuse_writes_while_naming`, for index start, adding a folder, including a folder to review, a sync's apply, bulk edits (start, resume), a photo's keywords (`save-metadata`), the bulk tag writes, auto-apply and deleting photos. Reads are never refused. The dialog says so while it works. Not covered: the folder watcher's own sync in the runtime's background (it has no request to answer). A page opened while the job works shows its progress (`/api/name-faces/current`).
- **A restart.** The thread dies with its process: the job is `abandoned` (the status, read from its run, says what that means: the change of the names either committed whole -- History lists it -- or not at all; grouping either wrote its names in its one commit or none). The question of a plan a restart took answers 404 "nothing was changed". Nothing is orphaned: grouping runs in the server's process, not a child.
- **Two apps, one library.** The registry is the process's, so TagPup sees the job TagTuner started (`/api/name-faces/current`); a name given in one is read from the faces table by the other after it refreshes (TagTuner's lists read again when names were written; TagPup draws the open photo's faces again). **Two libraries**: each has its own job, claim and flag.
- **Only this folder (owner request 2026-10-09; #994).** With a folder open (TagPup's Organize view's, TagTuner's the folder of the selected photo) the dialog's first step offers **only this folder and its subfolders** (the default) or the whole library, each with the count of its unnamed faces (`GET /api/name-faces/scope`, `naming_faces.scope`), and `start` takes `only_folder`. The scope is a filter on which photos the plan reads (`tagpup.services.folder_scope`, `faces_from_tags.plan(folder=)`: the photo ids under the folder, one range of `idx_photos_path_nocase`, handed to `face_tags.plan(photo_ids=)`; faces by `idx_faces_photo_id`, no BLOB), never another rule: the people the tags name, the decided faces (`identify.decided_faces`) and the gate of a person with no decided face (#839) stay the library's, so **a folder's plan is the whole-library plan restricted to the folder's photos** (a test on fixtures made as the indexer makes them). The apply is the plan the owner said Yes to, under the same guards. The question says which scope ("In the folder "Run" and its subfolders: name N faces ..."). **Grouping is not offered for a folder** (`identities.resolve` re-derives the whole library's automatic names): the dialog says why and `confirm` refuses `group` (`GROUP_SAYS`). A plan waiting for another scope is not handed back by `start` (409 with that plan). A folder the library holds no photo under is refused before any claim is taken (400). The same filter is the CLI's `faces-from-tags --folder` and `tags-from-faces --folder`. A second apply needs `--again` whichever folder the first was for (#995). The change's journal summary keeps `scope` and, for a folder, `folder` (`maintenance.Plan.summary`); `journal.history` drops `folder` unless `reveal`, and the refusal, the dialog's "applied before" and the CLI say where the earlier apply was (#1001).
- **Reports what changed in the open folder:** the page names its folder at the start (TagPup's open folder; TagTuner's selected photo's folder, if any), and the result gives that folder's named and unnamed faces before and after (`faces.named_counts`, by the photos' own index; 7 ms for 2,475 faces on photo_index).
- **The Tk runner is kept** (`runner.py`, **Run Identity Resolution Clustering**): unchanged, and the apps' messages after adding a folder now name this button instead (#22).
- **Measured** (`scripts/measure_name_faces.py`, headless Chromium on a sandbox copy of photo_index, 2.6 GB copied in 13 to 24 s; 2026-10-07; a machine with other work on it): the click on the button to the question painted and idle **13.5 s** in TagTuner and **18.4 s** in TagPup (the plan is read again each time; the spread is the machine's), with the server answering a probe twice a second meanwhile -- 27 answers, the slowest in 0.11 s: **no request waits for the plan**; the click on Yes to the result painted (10,477 face names written, one change) **4.2 s**.
- **Grouping, measured in the job** (`scripts/measure_name_faces.py --group`, same copy, 2026-10-07, nothing else on the machine): the click on Yes with the box ticked to the result painted **47.3 minutes** (the 10,477 names written first, then grouping), while a probe asked the server for `/api/server` twice a second -- **5,434 answers, the slowest in 0.36 s**: the server stays usable for an hour of grouping in its own process. On a quiet machine the click to the question was **11.3 s** in both apps. **Ticking the box after the names are written gave 2,639 fewer named faces than before it ran**: the copy ended with 43,641 named, exactly what grouping alone gives it (35,803 + 7,838), because grouping re-derived every automatic name, and kept a name it gave only while the face reached 0.80 of the person (`names_unasked`), so the 0.70 to 0.80 names `faces-from-tags` writes were among those it took away (not attributed face by face). **Since #855 (owner, 2026-10-08) grouping keeps a name its photo's own keywords confirm, whatever the likeness** (`identities.resolve`: the 0.80 test applies to a name the photo's keywords do not bear out); the box's text says so, and this measurement is of the version before; it has not been repeated. Grouping is not run first because the plan the question showed would then be refused whole (its faces would no longer be what it read). The matching of the rest of the faces (`identities.resolve`'s last loop, one `KnownFaces.most_like` per unnamed face) is 89% of the 53 minutes; a block-wise comparison like `face_tags._reached` would be minutes (not done: it is the same rule computed another way, and the owner's results must not move).
- **#844, where the 26 s went.** Profiled on the copy: of 27.5 s, **15.1 s were the per-person count** `_photos_to_be_named` that #839 added (113 people with no decided face, 134 ms each): SQLite drove the join from the library's 190,000 unnamed faces (`idx_faces_identify`), each probing `photo_people`, instead of from the person's few rows. It now goes from the person's photos to their faces by `idx_faces_photo_id` (`CROSS JOIN ... INDEXED BY`, the join of `store.faces.UNDER_FROM_PHOTOS`, #644), as the live save's count does: **the plan takes 13.6 s** (the test counts the virtual machine's instructions, 4,650 with 400 other faces against 950 with 30 on the old query). The 13.6 s that remain are the comparison of the faces of photos with several faces or people (117 blocks of 512) against the 35,740 decided faces (`_reached`: 9.0 s -- 4.5 the matrix products, 3.7 `nonzero` over the 2 billion likenesses, 0.6 the threshold), the photos with one face and one person (1.9 s), the matrix of the decided faces (0.8 s) and the faces of the photos (0.6 s).

### Backlog: face regions and the expensive results in the photo file *(owner, 2026-10-04; after phase 9)*
The owner's idea: write the face rectangles into the image as WLPG did, and keep what indexing
computes so that a model change does not lose earlier results, and a folder added to another
library, or a library that is lost, can read the expensive parts back from the photo (the
source of truth) instead of computing them again.

What is there today (counted 2026-10-04, read-only, photo_index): 68,472 photos, 57,609 with a
face, 226,246 faces, 35,803 named. No recorded metadata holds a region field (`RegionName`,
MWG `RegionAreaX`, Microsoft `RegionRectangle`: 0 rows each) -- but the indexer may not read
those fields at all, so "no file holds regions" is not established; reading a sample of files
with ExifTool comes first. `faces.box` is `[x1, y1, x2, y2]` in stored pixels, ignoring EXIF
Orientation (the tabled orientation redesign); `faces` has no model column, while `embeddings`
already keeps one CLIP vector per model key.

Analysis, in the order it would be decided:
- **The names and rectangles are the valuable part.** A named face is a person's decision; the
  boxes and vectors are machine time. Writing the regions is the cheap, standard half and
  covers "a library is lost" for the human work.
- **Which standard.** MWG regions (`XMP-mwg-rs`: `Regions/RegionList` with `Name`, `Type=Face`,
  `Area` centre x/y, w/h normalised 0-1, and `AppliedToDimensions`) are read by Lightroom,
  digiKam, Picasa's successors and Windows; WLPG wrote Microsoft's
  (`XMP-MP:RegionInfo`, `RegionPersonDisplayName`, `RegionRectangle` "x, y, w, h" top-left).
  ExifTool writes both; writing MWG, and reading both, is the usual choice. Names written are the
  leaf (identity), matching `PersonInImage`; the hierarchical keyword stays as now.
- **Orientation must be decided first.** Standards express the area in the image as displayed
  (orientation applied, `AppliedToDimensions`); our boxes are in stored pixels. Writing them is
  the moment the tabled orientation redesign stops being optional: convert at the file boundary
  (one owner, like `paths.to_row`), or move `faces.box` to displayed coordinates.
- **Which faces.** Named faces only, or every detection? Unnamed and excluded faces in a file
  mean little to other programs; a "not this person" (manual unmatch) has no standard field.
  Likely: named, not excluded. Each naming, unnaming, rename or merge then writes the file
  (through `write_keyword_fields`'s owner, journaled as tag writes are, overwrite-protected by
  stamp and base) -- a bulk rename touches every photo of that person.
- **Reading them back.** The indexer reads regions and makes `faces` rows named `manual` from a
  file's regions when the library has none for that photo (a new library, a folder added to a
  second library), matching by box overlap when it has its own detections. WLPG-written MP
  regions, if a sample finds any, become an import for free.
- **Vectors in the file?** A face vector is 512 float32 (2 KB, about 2.7 KB base64); at 3.9 faces
  per photo plus a CLIP vector it is about 13 KB of XMP per photo. JPEG's XMP segment holds
  64 KB (extended XMP beyond that, which some programs drop), and every vector rewrite changes the
  file. A private namespace (`tagpup:`), keyed by model key so a model change adds and never
  replaces, would let a lost library come back without a GPU pass. Weigh against: file churn on
  the owner's master (a NAS copy, backups re-copying 68,000 files), other programs carrying
  unknown XMP, and that vectors are recomputable. A sidecar-free middle way: write regions now,
  keep vectors in the database with a model column on `faces` (as `embeddings` has), and export
  the expensive tables to a per-folder cache file beside the library, not into the photo.
- **Model changes without loss** is a database change either way: `faces` gains a model key
  (detector + embedder), so a new model's faces are added beside the old ones and the old ones
  stay until the owner retires them. Naming moves to the new model's faces by box overlap.
- **Tests before it is built:** a sideways photo (Orientation 5-8) round-trips its region; a
  rename of a person rewrites only that person's regions; a file with regions from another
  program (MP and MWG) is read; regions edited in another program after TagPup wrote them; a
  folder in two libraries; a photo whose file is replaced by an older copy.

### The library view's selection details navigate, and a photo has a way back *(owner, 2026-10-04; built in wave 3A, #780, #781)*
The right pane of a library view is two halves (`web/tagpup/selection-panel.js`). **Navigation**, above: Folders to Organize (as
before), **People jump** and **Keyword jump** -- each person, and each keyword, the selected photos carry (the server's tally, so the
whole selection and not the cards the page holds) a link that opens the People view of that person or the Keywords view of that tag
(the tag and everything under it), as a click on the navigator's row does: the address names the view, the navigator shows it
selected, Back returns, and the selection is left behind as it is whenever a view opens. Keyword jump leaves out the tags that are
the people already listed and a folder offered to Organize; a list shows 12 and "and N more". A link has an address (`viewSearch`), so
a Ctrl-click opens a new tab. **Tagging**, below, is a collapsible section that starts closed -- the choice is kept in this
browser, `tagpup.selectionTagging` -- holding the people and tags with their arrows and x, and the bulk add of 9d-2, as they
were; Suggest's auto-apply is not in a library view. Organize's panel is not split. *Decision:* the person's link is the tally's
`people` (the server's own list: a person is a leaf name, a branch is never one); a tag is a person's tag when its leaf is a listed
person's name; no query was added and no route changed.

A photo opened over a view (the magnifying glass, Enter, the arrow keys with no photo open) is shown in the details panel, over a grid
the browser hides and so forgets the scroll of. `web/tagpup/view-left.js` is the one owner of **the view as left**: when the photo is
shown it keeps the grid's place (`state.viewLeft`: the scroll offset and the photo at the top of the view, which holds the place if
the order changes) and makes the photo **one place in the history** (a state with `photo`, however many photos are stepped through;
`history-entries.js` is the owner of the places the page makes, moved out of `library-view.js`), so the page's **Back** button,
**Escape** and the browser's **Back** all return to the grid as it was left and Forward opens the photo again. The selection (ids, or a
whole source less the ones left out), the order, the address, the navigator's open rows and the search box are not copied: they are
the page's and are not touched while a photo is open. The grid comes back through `openFolderView`, which asks `restoreViewAsLeft`,
whichever way the photo was closed (a delete of the open photo, a bulk delete that took it). Another view opened over the photo
forgets the place (it was the first view's); a search's `searchBack` goes with the photo's place, one place further
(`searchPlacesBack`). After a reload the view comes from its address and a Back finds the place the view's own history entry kept,
best-effort; the selection is not kept.

The grid keeps *a place*, not an offset (`vgrid.js`: `placeOfView`, `showPlace`; `relayout` reads the row at the top from the cards where
they are). A real browser showed why: a card's height is measured when the grid is laid out and can differ by a pixel or two when it is
shown again (the width moved), and an offset 1,669 rows down is then 3,300 pixels -- 16 rows -- from where it was; the row was read from
the offset and the stride of a layout that was no longer the rows' own. Measured in headless Chromium on a copy of photo_index (a search
of 20,995 photos, Select all less one, scrolled to row 1,677; `scripts/measure_photo_back.py`): before, no Back button, Escape left the
photo open and the browser's Back left the page; after, the button 43 ms, Escape 31 ms, the browser's Back 28 ms (medians of 3) to the
painted grid, the same row at the top, the same selection and address in 3 of 3. A People jump, click to the People view painted: 130 to
171 ms.

### Camera and lens words *(built 2026-10-09; migration 27)*
A term typed in the search is also looked for as a prefix in the photo's camera and lens (`search_index`'s third table,
`search_gear(camera, lens)`; docs/DATABASE.md): "canon", "r6", "eos r6", "pixel" and "24-70", "ef24", "f/2.8" (a letter-digit
run is also indexed split, so a lens spelled `EF24-70mm` is found by `24-70`). **Search only**: the views' navigator has no
place for a camera or a lens (Folders, dates, Keywords, People), so there is no facet and `photo_meta` gains no column.
The words are made from the photo's raw metadata by the one extractor (`photo_meta.gear`) in the transaction of every write of
it (`derived._put`, the index's record, bulk tag writes, sync, the journal's undo) and by `derived.rebuild_all`, the
doctor's `--rebuild-derived`. **Migration 27 reads no photo's JSON**: it makes the camera from `photo_meta`'s make and model
(photo_index copy: 1.0 s with its checks; the fill 0.5 s), and the lens comes with the rebuild (whole rebuild of a photo_index
copy: 21 s, one transaction, nothing changed if interrupted). **The lens fields are asked for now** (2026-10-09, owner's
decision): `fields.METADATA_FIELDS` holds `LensModel`, `LensMake` and `LensID` (and `EXIF:`/`XMP:`/`Composite:` forms), so every
read records them -- 0 of 68,324 rows of photo_index held one before. **A row says which read it comes from**:
`TagPup:ReadGeneration` in its `raw_metadata` (`fields.READ_GENERATION`, 2 = the lens fields are read; findings #1019), written
by every full read (the indexer's, sync's, refresh_rows', a save's read back) and absent from a row read before: a photo with no
lens has no lens key either way, and only this tells "read, holds none" from "read before". No migration, no stamp on existing
rows. **The re-read** of the rows read before is `tagpup_cli.py reread-fields` (`tagpup.services.reread_fields`, on the
refresh's own reading and edits): metadata only (ExifTool; no picture decoded, no model, no graphics card, no file written;
faces, names, embeddings untouched), a dry run by default that counts and times a 100-file sample, `--apply` writing 2,000
photos to a change of the journal (so it is resumable and undoable chunk by chunk; the derived tables, `photo_meta` and the
camera and lens words, are rebuilt for exactly those photos by the journal's own write, so no doctor run is needed),
`--folder` for one folder first. **It writes a row's `raw_metadata` only** (and a `document_id` where the row has none): never
tags, captions, people, modified time or size, and prints per chunk how many rows differ from their file in each field. A row
whose tags, captions or people differ from the file's is "disagrees" and left for sync or refresh_rows (findings #1024). Left
alone and counted: rows never read, rows found damaged before (`ExifTool:Error` in their raw_metadata: not tried on every run),
files missing or unreadable, files changed since indexed (sync reads those with the same fields and sees the size change
`library.reread_resized_pictures` is about; this never writes a new stamp over it), photos on a network share nobody named
(`--folder` or `--shares` names it; every look at a share is bounded, and after the first that does not answer in 10 s the rest
of its rows are left), a row the app saved meanwhile (read by the next run). **Stops** over the whole run, not per chunk: at a
chunk whose every file is gone (a drive unplugged, said once, not 34 times), and when a run that has read none of 10 or more
files finds ExifTool cannot be started; merely unreadable files are counted and the run ends as it began (findings #1023). Two
runs at once, or one beside the app, are held apart by the journal's expected values: the second to write finds the row changed
and skips it. The camera's placeholder lens `24.0-70.0 mm` (167 of 1,499 kr-track photos) is kept as the lens and found by
`24-70` (the words also hold a spelling without a pointless `.0`; findings #1027). Raise `READ_GENERATION` when a field is added that rows
already read should gain, and the same command reads them.
**One name for a camera** (`photo_meta.camera_name`; findings #1003): `fields.camera_of`, by which Shift Date Taken's list
and the photos a shift takes are chosen, is the same rule, and the page takes the name from the record's `camera` and keeps no
copy of it. Image Details shows `camera · lens` on one line (`record.camera`, `record.lens` from `photos.page_record`; hidden for a photo
that names neither). A search of a term costs one more FTS5 probe: "canon" (48,824 photos) 67 ms, "r6" 17 ms on a copy.

### Backlog: which photo fields are searchable *(owner, 2026-10-04; after phase 9)*
A later review of which metadata fields search offers, as members (all of / any of / none of) and as words. Named
so far: **camera make and model** (already columns of `photo_meta`, phase 9a-1), so a time shift can be aimed at
one camera's photos (a camera whose clock was wrong); **lens** (not in `photo_meta` yet: a column made from the
file's metadata as the others are, read from `raw_metadata` by the derived-table rebuild, no file re-read if the
indexer already keeps the lens tag -- check first). Author is not used and stays out. Not part of 9e. *Camera make and model, and the lens, are searchable words since
2026-10-09 (above); a members filter (all of / any of / none of) by camera is not built, and a time shift aimed at one camera
still takes its cameras from the folder's own photos, by the same name.*

### The owner's decisions for the three big projects *(2026-10-07; after waves 1-5)*
**AI pipeline** (reports/TagPup AI pipeline optimization.md):
- The plumbing changes that need no re-embedding come first (decode once, downscale before MTCNN, batching,
  prefetch) -- recommended, not contested.
- **Replace the face models** (insightface detector/recogniser): the non-commercial licence is accepted for this
  personal library. Re-computing face vectors is accepted **as long as the old vectors are kept** beside the new
  ones (a model key per face vector, as `embeddings` has per photo), so nothing done is lost and the two can be
  compared.
- **Replace CLIP** (SigLIP 2): yes, with the photo embeddings versioned by model in the database (the existing
  `embeddings.model` key); the old vectors stay until retired on purpose.
- **Search by what is in the picture**: yes.
- **Bake the expensive results into the photo** (the owner's 1.e, and the backlog's question 5): yes -- the face
  regions (MWG) and the embeddings, each tagged with the model that made it, in the file's XMP (a private
  namespace for vectors), so a photo moved or held by several libraries is read, not recomputed. Size: per model
  a CLIP vector (1024 floats, float16 about 2.7 KB base64) and about 3.9 face vectors (512 floats each); JPEG's
  standard XMP holds 64 KB -- keep only the vectors of the models in use, float16. *Owner, 2026-10-07: baking
  only the CURRENT model's vectors into a photo is fine*; older models' vectors stay in the database only, and a
  model change rewrites the photos' baked vectors in the same pass as the re-embed.
**People by id, stage 2** *(the full design, with the counted numbers and the open questions, is "People by id, stage 2" under "Identity by id")*:
- The 4 names with no person tag: **create the tag**. The name that matches two tags (1 face): **remove the
  tags** -- to be confirmed with the owner by name before anything is written (the tags also sit on photos'
  people lists). The names that sit on a group tag (counted 2026-10-09: 4 names, 65 photos in photo_index):
  *owner, 2026-10-09 (replaces "unmatch those faces")*: **do not unname them on their own** -- note them in
  a list of names to review (a name with no person tag or only a group tag, per library), so the owner
  decides each later, possibly linking the name to a person in the library the photos belong to (a tag that came
  from another library). Until then they are left as they are.
- Deleting a person tag that faces still use: **refuse with a message, with a "force" that unnames the faces**.
- History from before ids: **translate old entries so they stay undoable**.
- The two rules (a tag used on photos gets no children; a group tag is never put on a photo as a person):
  **enforced** as refusals with an explanation.
**Face regions in the photo files:**
- **The sideways-photo (EXIF orientation 5-8) redesign is un-tabled**: boxes stored as the photo displays.
- **Rotation done outside TagPup is detected**: beside what depends on the pixels (regions, embeddings), keep a
  small fingerprint of the picture (the owner suggested a few bytes of the top-left corner; a tiny downscaled
  thumbnail hash plus the Orientation value catches a rotation and an edit more surely -- decide when built), and
  treat a mismatch as "re-detect".
- **Every detected face is written** (not only named ones); an excluded face is removed from the file's regions.
- **No full dry run**: write a small subset first (one folder of a few dozen photos), the owner checks it in other
  programs, then the rest.
- **Write MWG regions; read MWG and Microsoft (WLPG) regions.**

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
| 2026-09-25 | Recurring operations (snapshots, sync, pruning the journal; not compacting, which needs the file to itself and is run by hand, #326) are registered in `tagpup.jobs.recurring` with their periods and run by one runner inside any TagPup process; their runs are recorded in the library. No Windows Task Scheduler *(owner)*. |
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

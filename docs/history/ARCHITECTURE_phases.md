# TagPup architecture, history: The first phases

Moved verbatim from `docs/ARCHITECTURE.md` on 2026-10-09 (trunk 17afc1b), when that file became a map.
Nothing in the sections below was edited, so a number, a date or a branch name in them is as it was on
the day it was written: read it as a log, not as the current design (the map, [../ARCHITECTURE.md](../ARCHITECTURE.md),
and [../INVARIANTS.md](../INVARIANTS.md) are current). Index: [README.md](README.md).

Contents: The first phases (1 to 7), the original runtime, layout and data-model text, the decisions table and progress table as they stood, and phase 10.

Sections in this file:

- Runtime
- Data model
- Frontend
- Testing
- Where everything goes
- Phases
- Phase 1: Foundations
- Phase 2: Services
- Phase 3: Store
- Phase 4: Data model
- Phase 4.5: One owner for each rule
- Phase 5: One server
- Phase 5.5: Models in the package, one composition root
- Phase 6: Pages
- Phase 6.5: No shims
- Phase 7: MCP
- Ideas taken from Windows Live Photo Gallery's database *(2026-10-02)*
- Phase 10: Family albums from many sources (idea, after phase 9)
- After the re-architecture
- Decisions
- Progress

---

### Original opening of ARCHITECTURE.md
# TagPup architecture

---
[◀ Back to README](../../README.md) | [📖 Tutorial](../TUTORIAL.md) | [💡 CLI Examples](../EXAMPLE.md) | [🖥️ TagPup GUI Spec](../SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](../SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](../SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](../DATABASE.md)
---

Where the code is going, why, and in what order. Agreed 2026-09-23. Known problems and review findings are tracked in [findings.md](../findings.md).

[DEVELOPMENT.md](../DEVELOPMENT.md) covers how to work in the code as it is today. This covers the shape it is moving to. For new code, this document wins.

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

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
| Every SQL statement | `tagpup/store/` | `tests/test_layers.py` |
| Schema and migrations (each declares additive or destructive) | `tagpup/store/schema.py` | `tests/test_migrations.py`, `tests/test_schema.py` |
| Bulk edits and migrations recorded, rehearsed, undoable | `tagpup/store/journal.py`, `tagpup/store/file_journal.py`, `tagpup/services/journal.py`, `tagpup/services/file_changes.py` | `tests/test_journal*.py`, `tests/test_file_journal.py` |
| Whether a change may be undone now (the History list, the rehearsal, the undo) | `tagpup/services/journal.py` (`why_not` over `UNDO_CHECKS`, every kind of change by its operation's name) | `tests/test_every_change_kind_has_an_undo_check.py`, `tests/test_journal_undo_refusals.py` |
| A photo path's spelling, comparison and SQL form; the root-relative form; a photo's row id by its path (`store.roots.photo_ids_at`) | `tagpup/core/paths.py`, `tagpup/store/roots.py` | `tests/test_rules_have_one_owner.py` |
| Tags and people taken apart; which of a photo's keywords are tags (`extract_tags`); the person tag | `tagpup/core/vocabulary.py`, `tagpup/store/taxonomy.py` | `tests/test_vocabulary.py` |
| Which copy a keyword, title or caption is read from (XMP's when it holds one, else a clip's QuickTime Title, else IPTC's; a keyword by its group's name alone, never a clip's QuickTime Subject: `KEYWORD_COPIES`, `TITLE_COPIES`, `DESCRIPTION_COPIES`, `keywords_held`); which text is a photo's title (`title_of`: the Title, else the Description), beside the fields a write sets | `tagpup/core/fields.py` | `tests/test_keyword_fields.py`, `tests/test_metadata_extraction.py` |
| A person by the id of their tag-tree node | `tagpup/store/person_ids.py` | `tests/test_person_ids.py` |
| ExifTool | `tagpup/files/exiftool_session.py` | `tests/test_rules_have_one_owner.py` |
| Writing a photo file's fields | `tagpup/files/field_values.py`, `tagpup/services/file_changes.py` | `tests/test_file_journal.py` |
| A patch in place: a JPEG's EXIF, IPTC and XMP segments rewritten at their old lengths when a journaled write's fields fit, the segments made by the own writer (`in_place.patch_jpeg`, from `field_values.write` only; `PATCH_IN_PLACE`), its record saved before its first byte (`file_journal.save_patch`), a file it was writing settled by its bytes (`in_place.held`, `put_back`; `file_changes._by_bytes`) and verified before done by the bytes it changed alone (`_verified`) | `tagpup/files/in_place.py`, `tagpup/store/file_journal.py`, `tagpup/services/file_changes.py` | `tests/test_a_crash_part_way_is_settled_at_the_next_start.py`, `tests/test_a_photo_patched_in_place.py` |
| The bake the index writes after it records a folder (each photo `baking.plan_photo` marks ToBake, the folder one internal change of `write_fields`, listed in no History and erased once finished, but for a file that ended a conflict the doctor counts (`file_journal.NOTES`), kept for it; the index's rows win) | `tagpup/services/bake_after_index.py` | `tests/test_the_index_writes_the_bake.py` |
| An XMP packet's owned properties read and edited as text, every other byte kept, paid for out of its padding; what it does not wholly understand refused (`xmp_packet.read`, `edit`; no caller until M4) | `tagpup/files/xmp_packet.py` | `tests/test_xmp_packet_edits_what_it_owns.py` |
| An EXIF block's nine owned fields read and edited in place, every other byte and offset kept, a longer value paid for out of the slack after the last byte an IFD points at, grown only at its end when asked; what it does not wholly understand refused (`exif_block.read`, `edit`; its layout `containers.exif_tiff`, `maker_note`; no caller until M4) | `tagpup/files/exif_block.py` | `tests/test_exif_block_edits_what_it_owns.py` |
| An APP13 body's owned IPTC datasets (title, keywords, caption) read and edited, every other dataset and resource kept (the IPTCDigest left stale), the dummy resource keeping the length when the change fits; what it does not wholly understand refused (`iptc_block.read`, `edit`; its layout `containers.photoshop_resources`, `iptc_records`, `pad_resource`; no caller until M4) | `tagpup/files/iptc_block.py` | `tests/test_iptc_block_edits_what_it_owns.py` |
| A journaled write's values made into a JPEG header's new EXIF, XMP and IPTC bodies, each field where `fields.EDITED_AS` puts it, a keyword, title or caption save in XMP alone while XMP keeps a value (the other copies answered stale, `fields.XMP_ONLY`); else None, the write a rewrite (`segment_edits.new_header`; the one caller of the three editors; no caller of it until merge 4b) | `tagpup/files/segment_edits.py` | `tests/test_segment_edits_choose_what_to_write.py`, `tests/test_the_block_editors_have_one_caller.py` |
| Child processes | `tagpup/core/processes.py` | `tests/test_processes_single_owner.py` |
| The indexer's progress on its pipe: typed facts (`kind`, a sentence, whole-number counts), one `@tagpup ` JSON line each in ASCII, said only when the server asks (`TAGPUP_PROGRESS_LINES`; by hand the CLI prints a person's words), read as values, never scraped from text | `tagpup/core/progress_line.py` (said by `tagpup_cli.py`, heard by `tagpup/services/indexing.py`) | `tests/test_index_progress_end_to_end.py`, `tests/test_an_index_reads_a_bake.py` |
| ffmpeg: where it is (`config.locate_ffmpeg`), the prerequisite check (`config.ffmpeg_problem`: the server, `tagpup_web.py --check`, the launcher's `starts`, the CLI's `index`, `thumbs warm` and `sync --apply` refuse to start without it; an install says so), a video's frames and poster, and its streams' codecs; what a clip needs before a browser plays it (`convertible.plan_for`: NOTHING or REENCODE, `/api/clip/convert-plan`) | `tagpup/config.py`, `tagpup/files/video_frames.py`, `tagpup/files/convertible.py` | `tests/test_frames_of_a_video.py`, `tests/test_ffmpeg_is_a_prerequisite.py` (I11), `tests/test_what_a_clip_needs_to_play.py` (C8) |
| A clip's thumbnail: its poster frame (the middle frame) through ffmpeg, kept in the thumbnail cache as a photo's is, by id through `/api/photo-thumb` and ahead by `thumbs warm`; ffmpeg found by the entry point and passed down (`find_ffmpeg`; a prerequisite, so missing only when uninstalled while the server runs: a placeholder), at most `thumbs.POSTER_RUNS` runs at once in a process, each within `thumbs.POSTER_SECONDS` (`thumbs.WARM_POSTER_SECONDS` in `thumbs warm`) | `tagpup/services/thumbnails.py`, `tagpup/files/thumbs.py` (`poster`) | `tests/test_a_clip_shows_its_poster.py` (C9) |
| A clip matched on its best frame by a search and Suggest's neighbours: its frames 1 to 4 (`embedding_frames`, read by `store.photos.frame_vectors`) are vectors of the index under the clip's id, FAISS is asked for k x `search.CLIP_PICTURES`, and each photo is answered once at its nearest vector, ties by id; `indexed` counts photos, `index.ntotal` vectors | `tagpup/core/suggesting.py` (`best_frame`), `tagpup/services/search.py` (`PhotoIndex.search`) | `tests/test_a_clip_is_matched_on_its_best_frame.py` (V3) |
| The graphics card, one process at a time | `tagpup/ml/gpu.py` (turns through `tagpup/runtime.py`) | `tests/test_gpu_single_owner.py` |
| Models built once, from a library's settings | `tagpup/runtime.py` | `tests/test_gpu_single_owner.py` |
| The photo models TagPup knows: each one's zero-shot word cut and whether a library may force an image size (`tagpup/ml/photo_models.py`, no torch); SigLIP 2's tokenizer (`tagpup.ml.clip.HubTokenizer`, `tokenizers` alone from the model's `tokenizer.json`, the same ids as open_clip's on 5,086 prompts); the readiness gate on a photo-model switch (`settings.photo_switch_on`, also asked of an undo: refused while more than `UNPASSED_SHARE_ALLOWED`, 5%, of the photos hold no vector under the new key; the faces switch's rule until 2026-10-28, when a face-model switch stopped counting: FaceNet is refused, `settings.NOT_FACENET`) | `tagpup/ml/photo_models.py`, `tagpup/ml/clip.py`, `tagpup/services/settings.py` | `tests/test_switching_a_librarys_photo_model.py` (V12) |
| Another photo model's vectors made beside the library's (`embed-photos`: resumable by what `embeddings` holds under the new key, never read until the switch, a photo removed meanwhile given no row back) and the switch itself (`set-photo-model`: the settings' one journaled change, through the gate above) | `tagpup/services/photo_reembed.py` | `tests/test_making_a_new_photo_models_vectors.py` (V13) |
| The vectors and Suggest's words of the photo models a library is no longer on, retired after the switch (`retire-photo-vectors`: refused while the own key does not cover the photos, one rowless change a key whose undo is refused, the walk by rowid `walk_vectors`) | `tagpup/services/retire_photo_vectors.py` | `tests/test_retiring_the_old_photo_models_vectors.py` (V14) |
| A library's settings; where the libraries are | `tagpup/services/settings.py`; `tagpup/config.py` | `tests/test_config_single_owner.py` |
| What may be set (one rule per kind of input) | `tagpup/core/validation.py`, `web/common/validate.js` | `tests/test_validation.py` |
| A face detection's name (the settings it is told apart by: `validation.face_detector`; `services.faces.detector_of` for a model made, `detector_named` from the settings with no model made) | `tagpup/core/validation.py`, `tagpup/services/faces.py` | `tests/test_service_faces.py` |
| Whether a photo's vector is still its picture's (the stamp trusted; else the decode's fingerprint against its row: the same picture keeps and restamps, no model; else worked out again), asked by sync's pictures to check, the index and a bake's plan; the row goes with every vector written (`store.embeddings.put`/`put_by_id` record the fingerprint they are given, or forget the row) | `tagpup/store/photo_fingerprints.py` (`same_picture`, `follows`) | `tests/test_sync.py`, `tests/test_a_bake_is_planned_from_the_library.py`, `tests/test_an_index_reads_a_bake.py` |
| Which photos the index owes work, and why (no vector under the library's photo key, a vector whose stamp does not describe its file, a current vector with no fingerprint row (its file the bake's to write), never examined for faces by the library's detector, named with no model made by `services.faces.detector_named`): one answer, asked by sync's look (what it queues and counts, `to_check` and `owed_*`; while any is owed the library is not in step) and by the index's start (`search.PhotoIndex.owed`, then `current`); the library's settings on the caller's connection are `services.settings.on` | `tagpup/store/photo_fingerprints.py` (`owed`) | `tests/test_sync.py` (APictureToCheck), `tests/test_index_startup_reads.py` (TheWorkTheIndexOwes) |
| Derived tables (`photo_tags`, `folders`, `photo_folder`, the search words) | `tagpup/store/derived.py`, `tagpup/store/search_index.py` | `tests/test_derived_writers.py` |
| Who is who (clustering, a face's name looking wrong; the one gallery of who a face looks like, `clustering.FaceRows.likeness` / `likeness_of`, each face's photo and year from `store.faces.eras_of`) | `tagpup/core/clustering.py`, `tagpup/services/identities.py` | `tests/test_rules_have_one_owner.py`, `tests/test_face_values_have_one_owner.py` |
| Keeping a library in step with its folders | `tagpup/services/sync.py`, `tagpup/jobs/watching.py` | `tests/test_sync.py` |
| A folder renamed outside the apps | `tagpup/services/sync.py` (pairs the files by name, size and modified time, a file renamed in its folder by folder, size and modified time, then DocumentID; what is left unpaired is removed by a whole sync whose root is confirmed there and whose second walk finds it gone too, undoably: DECISIONS 2026-10-28) | `tests/test_sync.py` |
| Roots (where a library's folders are, per machine) | `tagpup/services/roots*.py`, `tagpup/store/roots_check.py`, `tagpup/config.py` | `tests/test_roots*.py` |
| The library views (grid, navigator, search) | `tagpup/services/library_view.py`, `tagpup/store/library_view.py`, `web/tagpup/` | `tests/test_library_view.py`, `tests/frontend/` |
| A page's request, its DOM output | `web/common/api.js`, `web/common/dom.js` | `tests/frontend/` |
| Updating a running server | `tagpup/launcher.py`, `tagpup/web/lifecycle.py` | `tests/test_launcher_hands_over.py`, `tests/test_install_hands_over.py` |
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
| config | `tagpup.config` | `TAGPUP_HOME` and where the libraries are (its `data/`), which ExifTool and which ffmpeg the machine has (and whether it runs: `ffmpeg_problem`, the prerequisite the server and the CLI's index runs check), where this machine keeps each root (`machine_roots.json`, read, and written only by Change location and the sandbox; it registers its reader with `tagpup.core.machine` when imported, and `core.machine` imports it itself when asked with none registered, so no entry point has to remember to), and nothing of an old `config.ini`, which no code reads (phase 7.6). A library's settings are its own (`tagpup.services.settings`). Never written by the app (#100) | `core` (to spell and check a root's locations) |
| logs | `tagpup.logs` | Each program's log file in `data/logs/`, each line carrying the runs under way (`tagpup.core.runs`), and reading them back, bounded, for the Activity page. Set up by entry points | `core`, `config` |
| launcher | `tagpup.launcher` | Replacing a running server with the version being launched (#726): each server's record of where it answers -- its ports, version, and a token, the launcher's authority to drain it -- in the user's own folder (`%LOCALAPPDATA%\TagPup\servers`); and `make_way`, a launcher's: ask the version, drain another, end it once drained or hung -- or idle but not letting go: drains not ended for `IDLE_PATIENCE` while `/api/server` says requests 0 and busy [], never while work runs (DECISIONS 2026-11-19) -- serve; and `hand_over`, an install's: the same `make_way`, opening no page, then the installed version started on the old one's ports (#795); and the few small helpers they share with the installer and the server (json records written whole, `read_current`, `console_python`, the exit code for ports another holds) | `core`, `config`, `logs` |
| store | `tagpup.store` | The library database: connections and locks, schema and migrations, generations, one repository per table, caches keyed by generation, backups | `core` |
| files | `tagpup.files` | The photo files: ExifTool sessions, reading metadata, writing keyword, caption and orientation fields, identities, opening images (upright or as stored), crops and thumbnails; a video's frames, through ffmpeg (`video_frames`) | `core` |
| ml | `tagpup.ml` | Models: CLIP embeddings, face detection and embeddings, the vector index; and the graphics card, one process at a time (`tagpup.ml.gpu`) | `core`, `files` |
| services | `tagpup.services` | One function per user action. The only code that writes. Returns a `Result` | all of the above |
| jobs | `tagpup.jobs` | Background work: queue, status, cancel, persistence, worker processes for GPU work | `core`, `services` |
| runtime | `tagpup.runtime` | The composition root: turns the settings an entry point read into the process's long-lived objects -- CLIP and the face models, the per-library job runners -- builds each once, warms the models on a thread, and hands them down as arguments | every layer above but the entry points |
| entry points | `tagpup.web`, `tagpup.cli`, `tagpup.mcp`, `scripts/`, `tools/` | HTTP, the command line, the MCP server Claude works through, maintenance and development tools. Each reads the settings, builds one `Runtime`, and passes it on | `config`, `logs`, `runtime`, `services`, `jobs` (and `core` for formatting); `tagpup.web` also `launcher`, for the names it shares |

Guard tests, each of which fails the build. The ones marked *exists* are in place; the rest arrive with their phase.

- Imports inside `tagpup/` go down the layers, and package code never imports an old module from `scripts/` by name. *Exists:* `tests/test_layers.py`.
- `sqlite3.connect` only in `tagpup.store.db`. *Exists:* `tests/test_db_access.py`.
- pyexiftool's classes constructed only in `tagpup.files.exiftool_session`. *Exists:* `tests/test_rules_have_one_owner.py`.
- Photo paths spelled and compared only by `tagpup.core.paths`. *Exists:* `tests/test_rules_have_one_owner.py`.
- Tags taken apart only by `tagpup.core.vocabulary`. *Exists:* `tests/test_vocabulary.py`.
- SQL only inside `tagpup.store`. *Exists:* `tests/test_layers.py`.
- `config.ini` read by nothing (an old file in a home is ignored, never written or deleted); inside `tagpup/`, only `tagpup.config` finds folders from `__file__`. *Exists:* `tests/test_config_single_owner.py`.
- A model (CLIP, the face models) built only by `tagpup.runtime`, and what it is made of only in its own `tagpup.ml` module. *Exists:* `tests/test_gpu_single_owner.py`.
- The graphics card's turns taken only through `tagpup.runtime` (and each model's own check before it loads), its lock made only by `tagpup.ml.gpu`, and every module that loads weights checks for a turn first. *Exists:* `tests/test_gpu_single_owner.py`.
- ExifTool and `Image.open` only inside `tagpup.files`.
- Entry points import services and jobs, never store, files or ml directly.
- Every POST route returns a `Result`.
- Derived tables written only by their rebuild functions. *Exists:* `tests/test_derived_writers.py`, for `photo_tags`, `folders` and `photo_folder`: a function that writes a photo's tags, path or metadata, or the tag tree, goes through `tagpup.store.derived`.
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
tagpup/                config (where the libraries are), logs, runtime (composition root), launcher
  core/                pure rules: paths, vocabulary, validation, clustering, dates, fields, renaming, library,
                       machine, result, per_library, processes, byte_lock, idle, runs, gpu_turns, photo_meta, read_ahead,
                       progress_line (the indexer's facts on its pipe)
  store/               the only SQL. db (connections, locks), schema (migrations), one module per table or
                       family: photos, faces, people, person_ids, taxonomy, embeddings, suggestions, journal,
                       file_journal, face_batches, settings, roots + roots_check + root_rows, derived, search_index, folders,
                       library_view, snapshots, generations, locks, job_runs,
                       added_folders, faces_detected, photo_fingerprints, checks
  files/               the photo files: exiftool_session, metadata, field_values, keywords, identity, images,
                       thumbs, names, shares, lock_owners, recycle_bin, containers (a clip's boxes and a
                       JPEG's segments: where the XMP sits and the room beside it, read from an open file), video_frames,
                       in_place (a JPEG's patch of its metadata segments, the patch's record and what a file
                       holds against it), xmp_packet (an XMP packet's owned properties edited as text),
                       segment_edits (a write's values made into a header's three segments), convertible (what
                       a clip needs before a browser plays it, from its codecs; pure)
  ml/                  models: clip, scrfd_adaface (the face model: SCRFD and AdaFace; MTCNN and FaceNet deleted 2026-10-28), vector_index, grouping, and gpu (the card's turn)
  services/            one function per user action; the only code that writes: tagging, tags, people, faces,
                       identities, identify, indexing, sync, refresh_rows, relink_photos,
                       roots*, settings, journal, file_changes, batches, bulk_edit, selection,
                       library_view, search, suggester, suggestions, snapshots, thumbnails,
                       faces_from_tags, tags_from_faces, face_people, face_assignment + face_batch, inspect, activity,
                       maintenance, metadata_check (the own JPEG writer against ExifTool's on sandbox
                       copies; writes nothing of a library)
  jobs/                background work: indexing queue, suggestions,
                       naming_faces, identify, watching, recurring
  web/                 one Flask app per page from one factory (app.py), *_routes.py by concern, security,
                       lifecycle, libraries, state, responses
  cli.py (tagpup_cli.py at the root is its launcher)    mcp/ (the MCP server, one tool per read; it writes nothing)
web/                   the pages (ES modules, no build step): common/ (api, paths, vocabulary, validate, dom,
                       dialog, press, tag-editor, settings, history), tagpup/, tuner/, activity/
scripts/               programs to run (install_app, startup, measure_*, maintenance scripts); nothing imports from it
tools/                 development tools: run_tests, affected_tests, doctor, add_findings, stale_findings, retro_metrics, patch, watch_processes
tests/                 Python (unittest) and tests/frontend (node --test)
```

`tests/test_layers.py` says what may import what, where SQL may sit, and that `scripts/` holds
programs only. A moved module takes every importer with it and leaves nothing at its old name.

## Processes, servers and apps

- **One server, two apps.** One Flask app per page from one factory (`tagpup.web.app.create_app`), served by
  one Waitress process: TagPup (browse, tag, the library views, search) on port 8090 and TagTuner (Identify
  Faces, the tag tree, settings, roots, history) on 8080. The library comes from the URL. It listens on this
  PC only (127.0.0.1 and ::1) until phase 10 adds logins. Also served: the Activity page (`/activity/`, the
  background work of every library, loopback only; every run's lifecycle -- its claim, progress, failure and end -- is
  `services/job_runs.run`; why a run failed is one Failure, a kind and its sentence, of
  `services/job_runs.failure`, written in its row, and Needs you, `services/activity.needs_you`, reads the rows). The **CLI**
  (`tagpup_cli.py`: index, sync, history, undo, jobs, roots, embed-photos, ...) and the **MCP
  server** (`python -m tagpup.mcp`, for Claude: reads only, counts and ids, names and paths only with
  `reveal=true`) are the other entry points. All build one `Runtime` and call services.
- **One composition root** (`tagpup.runtime.Runtime`): models built once from the library's own settings (the
  one other: `embed-photos`'s new photo model, `photo_reembed.run`, writing under a key the library does not read
  until its switch),
  warmed on a thread, released after an idle period; each library's job runners, its index queue among them
  (`Runtime.index_queues`, one per library file, two runtimes sharing none; `Runtime.sync` and `include` queue on
  it); `begin`/`gpu_turn` take the
  card's turn. Services and jobs are given models; they never build one.
- **Background jobs** run through one runner per library (`tagpup.jobs`): the index queue (an indexing child
  process, via the CLI's `index`, asked for its progress as typed facts on its stdout, `tagpup.core.progress_line`, and
  its log to its run's own file; each run its `index` row of `job_runs`, which the Activity page reads -- the queue
  keeps no history), Suggest runs, Name faces from tags, Identify caches. **Recurring jobs**
  (`tagpup.jobs.recurring`: snapshots, sync) are run by the web server only; the
  folder **watcher** syncs a folder when it changes -- not when its notifications named only photos the library's rows
  already describe, TagPup's own writes from any process (`sync.followed`) -- and its whole sync is a run of the `sync` job
  (`recurring.run_claimed`, through `job_runs.run`), so one walker per library; how far its walk got is that run's row (`job_runs.tell`, counts
  only), the folder it is at alone kept in the walking process's memory (`sync.walking_at_into`, Now's live line).
  Event-driven first: a schedule is only for safety.
- **The graphics card**: one process at a time on the machine has a model on it (`tagpup.ml.gpu.Card`, a byte
  lock in the user's own folder, waiters queued and named on the Activity page).
- **The installed copy.** The owner runs `%LOCALAPPDATA%\TagPup\*.cmd`, made by `scripts/install_app.py`, with
  `TAGPUP_HOME` naming the folder that holds `data/` (the libraries, with their backups and locks). Editing
  the working tree changes nothing they run. Each launcher installs a newer clean commit, then replaces a
  server of another version (`tagpup.launcher.hand_over`: drain, wait for work in progress, end, serve);
  `install_app.py --apply` does the same with no window. (An always-on supervisor process ran
  once, 2026-09-26 to 10-02, and was deleted 2026-10-11.)
- **Logs** go to `data/logs/`, one rotating file per program, each line carrying the runs under way.
- The full text of the old Runtime section (the card's rules, the hand-over, the supervisor) is in
  [history/ARCHITECTURE_phases.md](history/ARCHITECTURE_phases.md), "Runtime".

## Data model on one page

One SQLite file per library (`data/<name>.db`, WAL, the baseline (schema 32: a new library is made whole; one below it is refused, DATABASE.md, section 10) and the
steps after it, from 33 (each face's vector moved to `face_embeddings`, keyed by the face model, and the empty `faces_pending` and `idx_faces_name` dropped), in `tagpup.store.schema`; each runs in one transaction with its checks, and only a destructive one takes a backup). Columns are in
[DATABASE.md](DATABASE.md). Every table has one kind and one writer.

**Ids.** A photo is `photos.id` (an integer kept across renames and moves; `path` is an attribute, and
`document_id` is the XMP identity the file may carry). A face is `faces.id`, pointing at `photos.id`. **A
person is the id of a node in the tag tree** (`tag_taxonomy.id`): `faces.tag_id` and `photo_people.tag_id`
are the key and `name` a cache of the node's leaf. **Every tag has an id** (owner, 2026-10-10): a keyword first seen with no
node is given one by `taxonomy.ensure_node`, its own path; a new person typed to name a face is made by
`face_people.made` (the tag first, at the filer's path), the one maker of a person, and the store's face writers refuse a name no
node has; a name two people have, a group's and a new library's empty People are refused -- so no row holds a name and no id: a name alone is nobody (owner, 2026-10-15), and `person_ids.People.settle` makes one it meets nobody (the
doctor counts it among the rows whose name is not their person's). A person is a face node with no node under it, wherever it sits (a top-level one is filed by its own tag until
the owner merges it into a branch); a branch is a group, never a person. A batch of reads makes its nodes in one tree edit
and none when it would make more than 500 (`derived.MAX_NEW_NODES_PER_BATCH`): a folder from another program with thousands
of keywords would make thousands of tags to merge or delete by hand, each embedded by Suggest; the doctor counts them. The navigator files a top-level person under "Top level".
A folder is the derived `folders` row (rebuilt, its integer ids change). The `folder_ids` and `library_identity`
tables the folder markers wrote were dropped (owner, 2026-10-10: the marker code is deleted; the
`.tagpup` files stay on disk and nothing reads them).

**Roots.** `roots` (name, the share's address) holds the roots the library was adopted by (all three live libraries
were; the adoption command is deleted, DECISIONS 2026-10-11, so no code adds one). A library with roots
holds a path under one as `@<root>/<relative>` (`paths.to_row`/`from_row`, converted at the database
boundary by `store/roots.py`), so a library follows a share that is mapped differently on each machine
(`machine_roots.json`, read by `tagpup.config`); a library with none holds native absolute paths.

| Kind | Tables | Written by |
|---|---|---|
| File copy (the file is the truth) | `photos` (`tags`, `captions`, `raw_metadata`, `taken`, `year`, `document_id`, `mtime`, `size`, `width` and `height`: the picture's size in its stored pixels, the frame of its face boxes, read from the header by the index (`images.picture_size`), NULL until read and on a clip (migration 39), `kind`: `photo` or `video`, from the extension, `images.kind_of`: .mp4, .m4v and .mov are indexed as a `video` row with no vector, dated by its XMP dates first (`dates.CLIP_XMP_DATE_FIELDS`), then `dates.CLIP_DATE_FIELDS`; .wmv, .mpg, .mpeg, .avi and .mod are indexed as a `video` row too, undated when they carry no date, and never written, converted only when the owner clicks Convert: DECISIONS 2026-11-09) | only after a file is read or written: indexer, sync (`refresh_rows.reread`, `edits_for`), `record_tags_in_index` (`store/photos.py`) |
| Decision (exists only here) | `faces` (`name`, `name_source`, `excluded`, `tag_id`), `tag_taxonomy` (the tree), `roots`, `settings`, `added_folders` | services, through a journaled change where bulk; the tree through `people.tree_edit` |
| Derived (rebuilt or computed; feature code never writes) | `photo_people`, `photo_tags`, `folders`, `photo_folder`, `search_words`/`search_names` (FTS5), `embeddings`, `embedding_frames`, `tag_embeddings`, `face_crops`, `face_embeddings` (a face's vector under the model that made it, read under the library's `faces.model`: `store.faces.model_key`), `suggestions`, `faces_detected`, `photo_fingerprints` (the hash of a photo's upright decode; not journaled, a rotation forgets it) | `store/derived.py` (the four view tables), `store/search_index.py`, `store/people.py`, `store/embeddings.py`, `store/faces.py`, `store/suggestions.py`, `store/photo_fingerprints.py` |
| Infrastructure | `schema_version`, `generations` (triggers bump a counter when a cache's data changes, from any process), `job_runs` (the recurring jobs' runs, the syncs: `sync-whole`, `sync-folder`, and each index the queue makes: `index`, a record that gates nothing, one row per process indexing, holding its tag, its folders' count and the syncs it is part of) | `store/schema.py`, `store/generations.py`, `store/job_runs.py` |
| The journal | `changes`, `change_rows` (one row per changed column), `change_files` (each photo file a bulk edit writes, with its state; an in-place patch's byte ranges and the bytes they held, kept only while it is `writing`) | `store/journal.py`, `store/file_journal.py` |

**The journal** (7.5). Every bulk edit and maintenance operation is one `changes`
row: what it found and what it left, applied only where the rows are still what the plan read, rehearsed by
a dry run that applies and undoes inside a rolled-back transaction, undoable on the same terms. A bulk edit that writes photo files marks each file `writing`, then `done` in the same transaction
that records its row; a crash is settled at the next start by what the file holds (`file_changes.settle_once`, from
`runtime.library_settings`: in the web server on a thread of its own, its photos refused to a save until it reaches them,
each change a batch, listed on Activity and stopped by a hand-over, one it cannot finish put off for the next start; in the CLI inline;
DECISIONS 2026-11-07). Keys the journal can
restore are AUTOINCREMENT (never reused). `History`/`undo` are in the CLI and the pages' gear; the MCP server has `history`.

**One batch model** (`tagpup/services/batches.py`, `file_changes.write_fields`; owner, 2026-10-11). A batch of photo files -- more
than 25 -- is the journaled change `write_fields` plans: its files' states in `change_files` are its progress, there is no table and
no thread, and the request stays synchronous -- but for two the server carries out on a thread it owns, the request
returning at once: a settle at a library's first open, and a clip's conversion (the panel's Convert and Make smaller (MP4),
`convert.start`: a batch of one, minutes of ffmpeg that a closed tab must not lose; its ticket on the Activity strip, its end
read at `/api/clip/converted`; a bulk edit asked meanwhile waits behind it, the strip showing its place). It takes a claim (one batch at a time in a process, whichever library; eight may wait,
the next is refused saying how many are ahead; a waiting batch is lost by a restart, having written nothing), is refused above 5,000
photos (`batches.MAX_BATCH`) unless it is the tag tree's rewrite (rename, delete, merge, a person's rename: no cap), and is written 25
files a chunk: between chunks a writer that waits for the lock of changes of photo files is let in, and a batch asked to stop (Cancel
on the Activity page, or an update's hand-over) withdraws the files it did not reach and applies what it wrote, undoable. A caller that
already holds the lock (Smart Rename, the Organize page's bulk tags) takes its claim before the lock and does not yield it.
A tag or person rename (no cap) rewrites every photo first and moves the tree after (`tags._retag`, the one place that orders them): a batch stopped half way leaves the tree as it was, says so with its change, and making the same edit again finishes the rest.
`batches.listing` and `/api/activity/now` / `/api/activity/batches` show the batches running (read from the journal, so another
process's too) and waiting. `tests/test_batches_go_through_the_model.py` names every route that reaches `write_fields`.
A rotation is one of these changes too (`photos.rotate_photos`, field `EXIF:Orientation`): the face boxes a TIFF turns, and the vectors, fingerprint, face crops and `faces_detected` record it makes stale, are written by `services/rotation.py` in the transaction that marks the file `done`, whether forward, settled after a crash, or in an undo, so file and boxes cannot disagree.
A bulk assignment of faces is one of these changes too (owner, 2026-10-11): Assign, Unmatch, Exclude selected, Ignore cluster and Re-examine
(`services/face_assignment.py`) are decided and refused as a whole, then written as ONE change whose file step is the person's tag written
or taken off, and whose photos' faces follow it. Each face is recorded in the plan's own transaction as a row of the journal's `change_rows`
(its decision columns as the plan read them and as they are to be: `store/face_batches.py`), and `services/face_batch.py` writes them in
the transaction that marks the photo's file `done` (or `undone`, from History): forward, settled after a crash, or undone, so a photo and its
faces never disagree. A face is written only where it is still what the plan read (a face somebody named or ruled out since is left and
said) and is put back only where it is still what the change left. A photo that holds the person already is in the change all the same (only
its faces are written). What follows a file is done by one list of followers (`file_changes._followers`, a rotation's boxes and the faces), when the
file is marked done, when it is undone, and when it is taken out of its change after it was done (a write the file did not keep). The people are
checked again under the lock before a file is read: one merged away refuses the whole. Faces that carry no name write no photo, are written by the faces' own write after the batch, and are not in the
change. No job of its own: no plan file, no resume, no Undo of the whole job; the claim, queue, cap (5,000 photos that need their files looked
at, whatever the faces), Cancel, the strip, the hand-over and History's undo are the batch's.
**One strip shows them on both pages** (`web/common/batch-strip.js`; `api.js` announces a page's write requests as `tagpup:writes`): it asks
`/api/activity/batches` once when a page opens (so a reload shows a batch still going) and every 1.5 s while the page has a write out or a
batch is listed, and draws this page's library's rows: the operation, the files handled of the total from the journal's counts, the time left
from when it started, Cancel for a batch of this server, a waiting one with how many are ahead. The page's own request still awaits its reply
and hands the strip its result sentence (`showEnded`: counts, the first 50 errors, History, Dismiss; a stopped batch is the server's "Stopped as
asked after N of M ... change K in History"); a batch that leaves the list with no such sentence ends in one made from its last row. A list
that cannot be read is said ("cannot tell how far the write has got"), never taken for a finished write. The Activity page keeps its own rows.
The photo write queue (`web/tagpup/write-queue.js`: a single save's Retry and Dismiss) is not a batch and stays.
Leaving a photo waits for its own writes (`edits.js` `leavePhotoThen`): one move waits at a time, the last asked (`state.heldMove`), so presses made meanwhile move once; a wait past 300 ms says so over the photo (`web/tagpup/move-waits.js`), and Escape or its Stay here lets the move go.

**A photo's tags and people** flow one way: the file's keywords are read into `photos.keywords`; `photo_tags`
and `photo_people` are derived from them, the faces and the tree whenever any of the three changes, in the
same transaction (`store/derived.py`, `people.tree_edit`).

## Frontend

- `web/common/` holds ES modules shared by the pages: `api.js` (library-aware URLs and JSON, with a read
  retried across a server update), `batch-strip.js` (the strip of the batches of photo files), `paths.js`, `vocabulary.js`, `library.js`, `validate.js` (the rules
  `/api/rules` publishes), `dom.js` (elements built from text), `dialog.js`, `press.js` (a pill, chip, card or row the keyboard presses
  as a click: Tab reaches it, Enter or Space clicks it), `file-addresses.js` (the one spelling of a photo's file and a
  clip's address, `/api/photo-file` and `/api/video-file`, versioned by the record's or card's `mtime`), `photo-write.js`
  (both pages' write of one photo's title, tags and Date Taken through `/api/photos/write`, op `set`, named by its id,
  and the stamp and base of the file as read), and the gear's tag editor, settings and history dialogs.
- `web/activity/` is the Activity page; `web/tagpup/` and `web/tuner/` have a `main.js` that only wires the
  page, plus one module per feature, and their mutable state in one `state` object.
- `web/common/theme.css` is the one place the dark theme's tokens live (colours, fonts and the card
  shadow, as `:root` variables): TagPup's and TagTuner's `style.css` and the phone's `mobile.css` each import it, none holds a copy.
  The phone's look is built on the tokens it adds (docs/plans/phone-redesign.md): `--surface-3` above the slate steps
  `--bg-secondary` and `--bg-tertiary`, `--scrim`, `--on-accent`, `--danger`, `--photo-canvas`, the corners (`--radius-*`), the
  five type steps in rem (`--text-*`, `--weight-*`), the tap target (`--tap`, `--tab-bar-height`) and the motion
  (`--motion-*`, `--ease-out`); the desktop pages may take them up later.
- The phone's page is `web/tagpup/mobile.html` beside TagPup's `index.html`, at `/<library>/mobile.html`
  (docs/plans/phone-viewer.md): `mobile.js` only wires it, `mobile-state.js` holds its state, `mobile-screens.js` makes
  each screen a history step (Back goes up a screen), `mobile-library.js` is its list of libraries; it imports
  `api.js`, `library.js` and `roots-banner.js` as they are, so another library is another page and an update shows
  the same banner. `tagpup/web/app.py` `PHONE_FILES` serves its html, css, manifest and icons (TagPup's alone);
  its modules are served as every page module is. It has no gear, no link to TagTuner and no `/api/apps` (through
  Tailscale Serve the other app's address cannot be built from the request). `mobile-browse.js` walks a section a level
  at a time, `mobile-grid.js` shows a view through library-source.js's card windows and `vgrid.js`, `mobile-photo.js`
  shows one photo large, swiped through with `web/common/swipe.js`, the swipe the desktop page uses too, and zoomed with
  `web/common/image-zoom.js`. `mobile-tabs.js` is its bar of tabs at the foot of the screen (Library, Photos, Filter,
  Libraries): a tab is a screen of `mobile-screens.js` and a press is `openScreen`, so Back goes to the screen it was pressed
  on. Browse rows and the People strip at its top show covers from `GET /api/library/covers`
  (`tagpup.services.library_view.covers`: each row's first photo by the views' order and its thumbnail's URL, read from the
  index alone, nothing stored), asked by `mobile-browse.js` for the rows near the screen, at most 24 at a time.
  `mobile-edit.js` is its Edit sheet, a history step of its own over the photo: a tag added
  or taken off (behind an Undo) through `/api/photos/write` (op `tags`, by id), the title and Date Taken through `photo-write.js`, each
  write of the page in turn after the one before it.
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
| Which program holds a file (lock owners; the Activity File access probes were deleted 2026-10-11) | [history/ARCHITECTURE_jobs_sync_journal.md](history/ARCHITECTURE_jobs_sync_journal.md) |
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
| Camera and lens words (the FTS5 table of them was dropped; a camera is now found in the photos' metadata, `search_index.cameras_with`) | [history/ARCHITECTURE_library_views_and_search.md](history/ARCHITECTURE_library_views_and_search.md) |
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

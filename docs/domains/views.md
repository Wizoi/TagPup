# Views, search and AI: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-views-ai.md` (its Scope, its contract, its guards, the scenario list
V1 to V9, V11 to V14, V17; V10, MTCNN's detector cap, retired with the old models; V15 and V16, a video played and a
clip's poster, are Media's C8 and C9 since 2026-11-13, docs/domains/media.md). This page states what the domain is today,
each of the seven conditions of health (docs/OWNERS.md) with its evidence and date, and what is so by design. A condition
not met is the domain's queue.

## What the domain is (counted 2026-11-08, read-only, `db.readonly_uri`)

Recounted 2026-11-13 (the other cells are not recounted): photo_index 62,229 photos, 2,910 clips, 11,632 rows in
`embedding_frames`; kr-track 2,186 photos, 5 clips, 20 frame rows; renton_parkrun 1,309 photos, 0 clips. The libraries
are three (the fourth `.db` in `data/` is a backup of kr-track, #2932).

Which photos and clips, in what order and how many, for every way the owner looks at a library (a folder, a keyword, a
person, a date, a union of them, a typed search), the navigator's counts, the photos' CLIP vectors (a clip's five frames),
Suggest (the video the browser plays and the poster it shows are Media's), and the models on the one graphics card. What it reads, in
the three live libraries:

| | photo_index | kr-track | renton_parkrun |
|---|---|---|---|
| photos (clips among them, `kind = 'video'`) | 61,797 (2,179) | 2,070 (5) | 1,309 (0) |
| clips with a vector / rows in `embedding_frames` | 2,179 / 8,708 | 5 / 20 | 0 / 0 |
| faces | 183,669 | 12,664 | 3,315 |
| photo_tags / photo_people rows | 150,380 / 79,482 | 4,292 / 2,825 | 1,076 / 1,076 |
| folders | 2,618 | 35 | 9 |
| saved suggestions / candidate-word vectors | 111 / 978 | 546 / 102 | 1,164 / 0 |
| `faces_detected` rows | 10,580 | 707 | 31 |
| model keys holding vectors (each with one vector length) | 1 | 1 | 1 |

All three libraries name the same models (ViT-SO400M-14-SigLIP2/webli and scrfd-adaface): 1 set. Schema 38 in each.

## The contract other domains rely on

| Contract | Guard |
|---|---|
| Order: Date Taken, file name, caption key or file size, either way, then the photo id in the order's direction; photos with no key after, by id | `tests/test_library_views_scenarios.py` (V1: every kind of source, both ways, across pages), `tests/test_library_union_and_order.py`, `tests/test_caption_order.py`; by size: `test_library_views_scenarios.py` (TheLargestFilesFirst) |
| The list: one order, no photo twice, none skipped; `total` counts a photo once | V1; `tests/test_selection_scenarios.py` (V5, V7) |
| Reads only; ids and cards separate reads; caps `MAX_IDS`, `MAX_CARDS`, `MAX_MEMBERS` | V1, `tests/test_library_view.py`, `tests/test_library_view_plans.py` |
| A search ANDs its terms and never ranks; nothing typed is FTS5 syntax | `tests/test_library_search.py` (V2), `tests/test_camera_words.py` |
| One process on the card at a time, taken through `Runtime.begin` / `gpu_turn`; a waiter told who holds it; one set of models in a process (#761), the startup warm-up included (#1423) | `tests/test_one_gpu_at_a_time.py`, `tests/test_one_gpu_jobs.py` (V4), `tests/test_gpu_single_owner.py` |
| Models built once per settings by the runtime, released when idle; the page names what holds them; the detector cap is each detection's argument, not a model setting | `tests/test_models_released_when_idle.py` (V6), `tests/test_the_runtime_keeps_models_by_settings.py` |
| The photo model is a table entry, not a scatter of constants: `tagpup/ml/photo_models.py` holds each model's zero-shot word cut (0.11 SO400M; ViT-H-14's row was deleted 2026-10-28; Suggest reads it by `word_cut`) and whether a library may force an image size; SigLIP 2 reads its tokenizer with `tokenizers` alone (identical to open_clip's on 5,086 prompts); a library cannot switch photo model until its new vectors cover all but `UNPASSED_SHARE_ALLOWED` (5%) of its photos, an undo held to the same rule (`settings.photo_switch_on`) | the photo-model tests (`tests/test_suggest_reads_people_once.py`, `tests/test_embeddings_follow_their_photo.py`, the settings tests) |
| A new photo model's vectors are made beside the old (`embed-photos`, `tagpup/services/photo_reembed.py`, the second photo-model builder: `Runtime.clip(None, settings)`, a model that is no library's; Integration contract rows 17 and 64): the library reads only its own model's key, so a pass part-way is never read; the switch is the settings' one journaled change through the gate (`set-photo-model`, or the gear), undone by `undo`, held to the same gate | V13 (`tests/test_making_a_new_photo_models_vectors.py`), V12 (`tests/test_switching_a_librarys_photo_model.py`) |
| After the switch, the vectors under every key but the library's own, and Suggest's words under those models (not the own model's), are retired by `retire-photo-vectors` (`tagpup/services/retire_photo_vectors.py`): refused while more than the switch's share of the photos hold no vector under the own key; the library's key read again in each chunk; one rowless change a key (`VECTORS_OPERATION`), its undo refused (`vectors_check`), and the switch back refused by the switch's own gate | V14 (`tests/test_retiring_the_old_photo_models_vectors.py`) |
| Vectors and suggestions follow their photo; a looking run keeps nothing | `tests/test_suggest_then_apply_all.py` (V3, V9), `tests/test_analyse_only_suggest.py` (V3: a looking run forced to remember fails 16 of 24) |
| A view kind moves in every layer: store `KINDS`, `_source_of`, `library-source.js`, `navigator-model.js`, `search-model.js` | `tests/test_view_rules_follow_the_server.py`, `tests/test_chip_keys_follow_the_server.py`, `tests/frontend/search-page.test.mjs` (every member kind has a section, a chip-key pair and labels of its own) |
| Filter mode's marks are a `kind=search` view, no kind of their own: `navigator-model.js` `specFromRowStates` makes it (a union per tab of its `+` rows in `all_of`, each `&` row its own member, `-` rows in `none_of`) and `rowStatesFromSpec` reads it back, null for a search made elsewhere (words, an Any-of list, Within) | V28 (`tests/frontend/scenario-filtering-the-library-by-rows.test.mjs`, the values the page sends against the server: `tests/fixtures/filter_searches.json`, `tests/test_filtering_the_library_by_rows.py`) |
| A search is left one way: the search box's Clear, a filter emptied (its last mark, a chip, Clear all) and Filter mode turned off with a filter open all call `library-view.js` `leaveSearch` (Back `searchBack` places, or a bookmarked search closed as a new place), and `state.nav.filter.leaving` holds from that call until the next view is followed (`navigatorFollows`) or the person stays on the photo, so a second click before Back arrives goes back nowhere. Filter mode's marks and chips are read from the view open, never kept (`filter-bar.js`); a filter's lists are capped as the server caps them (`library-source.js` `listOverCap`) | V29 (`tests/frontend/scenario-filtering-the-library-in-filter-mode.test.mjs`) |
| A clip keeps its frame 0 as its row in `embeddings` and frames 1 to 4 in `embedding_frames` (migration 38; DECISIONS 2026-11-06), written with the vector and taken with it; a search answers each photo once, at its nearest vector (`suggesting.best_frame`, `PhotoIndex.search`), a clip among the `k` once; a folder's clips are Suggest's subjects beside its photos, queried by frame 0 and offered tags only, the face model never shown one (`store.photos.FACE_KINDS`; DECISIONS 2026-11-13) | V3 (`tests/test_a_clip_is_matched_on_its_best_frame.py`, `tests/test_suggest_then_apply_all.py`) |
| Suggest's neighbour cut for clips is measured on a copy, not set: clips keep 0.35 until the owner sets it from the measurement (DECISIONS 2026-11-07, 2026-11-08) | V17 (`tests/test_measuring_the_neighbour_cut_for_clips.py`) |

## The seven conditions (2026-11-08)

1. **No reach-ins.** Met. The suggester and the runtime are reached through `Runtime.begin` and `suggester.model_for_run`
   alone; tests reach the suggester the way a run does (`tests/stand_in_models.py`). A clip's bytes and poster are asked of
   `services.photos` and `services.thumbnails`; the page never looks at the file (`tests/test_photo_looks_have_one_owner.py`).
2. **No rule written twice.** Met. "A run of another set is under way" is `Runtime._another_set`; a lease's set and settings
   are built by `_Lease` alone. A clip's best frame is `suggesting.best_frame`, asked by `PhotoIndex.search` alone.
3. **No hidden coupling.** Met. A view kind added to the server and not to the pages fails the page test over
   `view_rules.json` `member_kinds`. A clip's frames follow their photo by the migration's trigger
   (`embedding_frames_go_with_their_photo`) and by `embeddings.put` / `put_by_id` replacing them with the vector.
4. **No concept that could collapse.** Met. Models given to the runtime collapsed into models built from settings; the
   warm-up is a run's `_Lease`; a clip's frame 0 is the vector itself, not a second kind of row.
5. **No branch without an instance.** Met. Deleted in earlier passes for 0 instances: the suggester's arms for no model
   and no index (#1420), the vector-length check (#1089), the unreadable-setting arm, the seven catch-alls. Kept for an
   instance counted 2026-11-08: a clip with frame 0 alone (2,179 clips hold a vector and 8,708 frame rows; kr-track's 5
   hold 20), and a library with no clip (renton_parkrun, 0). The warm-up's refusal beside a run of another set has a user
   action: the server starts and the owner clicks Suggest in a library on another set.
6. **Scenario tests, each file owned and mapped.** Met: V1 to V9, V11 to V14 and V17 each have a file in `tests/owners.py`
   (`tests/test_the_test_map.py` passes 2026-11-08). Unit tests that restated a scenario were lifted and deleted (V1, V7,
   V9). The unit files that stay hold a rule no scenario holds (below).
7. **No open data or wrong finding, and every low fixed or by design.** Met 2026-11-08: `tools/add_findings.py --open`
   lists none of severity data or wrong; this page was #2729.

## By design

- **A photo with no `faces_detected` row is detected again by the first Suggest that looks at it** (#1573; owner,
  2026-10-16). That Suggest records the detection (with the library's detector name) and the photo is settled from then
  on. Counted 2026-11-08: 10,580 rows in photo_index against 61,797 photos, 707 of 2,070 in kr-track, 31 of 1,309 in
  renton_parkrun; the cost is one detection each (about a second).
- **Suggest alternating between two libraries on different model sets reloads the models at each switch.** The process
  keeps one set at a time (#761; DECISIONS 2026-10-15): two ViT-H-14-sized sets do not share the owner's card with
  the owner's other programs. Cost today: 0 (1 set across the 3 libraries). Keeping two sets is the owner's call.
- **The startup warm-up refuses Suggest in a library on another set until its load ends**, with a sentence naming it
  ("the warm-up of Suggest's models (TagPup server) is using ..."): the warm-up is a run while it loads (#1423). Cost
  today: 0 (no library on another set).
- **A model load that hangs refuses Suggest on another set until the server restarts**: the run (or the warm-up) holding
  the hung load keeps its lease until it returns or the process ends. Cost today: 0 libraries on another set.
- **Suggest of a folder just added waits for its index** (`jobs/suggestions._get_ready` waits while
  `IndexQueue.indexing(folder)` answers, to the end of the CLI's process; 2026-10-16): started before it ends, the run
  would read the folder's faces half written. Since 2026-10-23 the process ends with a read-only matching of the faces
  it made (DECISIONS 2026-10-23), no longer a clustering that rewrites names. The wait says what it waits for, and
  Cancel stops it.
- **A program under `scripts/` may not import store, files or ml, nor hold SQL** (`tests/test_layers.py`; #1527): a
  census reads through services; a stage it cannot reach needs a store read or an ml helper of its own.
- **The census strata count a photo's faces in play, not every face found** (#1528): named, or unnamed and not excluded
  (`services.identify.photos_waiting`); a library with many excluded faces reads low, and the census output says so.
- **The whole-library view reads clips from `idx_photos_clips`** (#2644; migration 39, DECISIONS 2026-11-09): the partial
  index `ON photos(id) WHERE kind = 'video'` holds the clips' ids alone; on a copy of photo_index the clip statement took 47
  ms as a scan of photos and 1 ms by the index. The plan test asks every source's clip statement to scan no photos. Under
  the Videos filter the whole library ordered by caption still walks `idx_photos_caption` past every photo (#2775).
- **The order by file size reads its source's rows and sorts them** (the owner, 2026-11-12: no index of `photos.size`,
  no migration): measured warm, read-only on photo_index (62,451 rows, 2026-11-12), the whole library 150 ms against 33
  by Date Taken -- the one source that scans `photos` -- its clips alone 12 ms (`idx_photos_clips`), the largest top folder
  with its subfolders 245 ms as by Date Taken, a 15,397-photo keyword or person 80-100 ms, a year 6 ms. An index
  `ON photos(size, id)` would make the whole library a walk; it is the owner's to ask for.

- **A Suggest offers a name by the cuts of the face model its run holds** (2026-10-20, phase 3 merge 3;
  `clustering.cuts_of(self.faces.key)`, `suggester`): a library switched mid-run ends on the model it began with, and
  the cuts with it, and the vectors too (the photo's faces and the known faces are read under `self.faces.key`); the
  next run has the new model's.

## Unit files that stay, and the rule each holds

- `test_library_view`: what each kind of source holds (folders alone and with subfolders, keywords alone and below,
  people by id and path, months' bounds, `%`, `_` and quotes as text), the cards, the navigator's counts, rooted
  libraries, the rules of what is a date (file time ignored, text order of zoned and old dates, an unread row last).
- `test_library_union_and_order`: union semantics (a photo in two members once, a folder before its recursive parent,
  1,000 months in one statement), case-blind name order, a dashed date sorting as text, the plans.
- `test_caption_order`: the caption key and its plans.
- `test_library_view_plans`: a view page seeks an index, never scans `photos`.
- `test_camera_words`: camera and lens naming; V2 holds no camera.
- `test_selection`: request shapes, the caps' sentences, lists of ids, another library's ids, one moment's list.
- `test_selection_tally`: the tally's order, cap and plans.
- `test_embeddings_follow_their_photo`: one vector per model, the stamp of the file as opened, rotation, a swap, a
  deletion across models.
- `test_suggester_scoring`: the scoring constants (the half-life, the path boost, the consensus bands, the zero-shot
  floor).
- `test_a_search_keeps_no_thread_buffers`, `test_vector_index`, `test_photo_index_keeps_only_vectors`,
  `test_index_reload_reuses_its_connection`: FAISS threads, memory and connections.
- `test_models_in_use_survive_an_unload`: the unload racing a run.
- `test_the_runtime_keeps_models_by_settings`, `test_the_runtime_reaches_suggest`, `test_suggest_index_stays_current`:
  models and indexes per library's settings.
- `test_suggest_reads_people_once`, `test_suggest_respects_face_decisions`, `test_zero_shot_skips_people`,
  `test_suggestions_keep_hierarchy`, `test_one_value_for_a_suggested_tag`, `test_person_tag_edges`: what a suggestion
  offers for a person, a decided face and a tag's path.

## History

- 2026-10-15 domain pass (one lease for the warm-up, one way to take models, the deleted arms): DECISIONS 2026-10-15 and
  docs/history/ARCHITECTURE_phases.md.
- 2026-10-17 AI pipeline 1b (a processor half and a card half, read-ahead): docs/AI_PIPELINE.md, phase 1b.
- 2026-11-06 to 2026-11-08 clip frames, best-frame matching, the video route, posters, the neighbour-cut measure:
  DECISIONS 2026-11-06, 2026-11-07, 2026-11-08.

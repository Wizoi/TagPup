# Views, search and AI: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-views-ai.md` (its Scope, its contract, its guards, the scenario list
V1 to V9). This page states what the domain is today, each of the seven conditions of health (docs/OWNERS.md) with its
evidence and date, and what is so by design. A condition not met is the domain's queue.

## What the domain is (counted 2026-10-15, read-only)

Which photos, in what order and how many, for every way the owner looks at a library (a folder, a keyword, a person, a
date, a union of them, a typed search), the navigator's counts, the photos' CLIP vectors, Suggest, and the models on the
one graphics card. What it reads, in the three live libraries:

| | photo_index | kr-track | renton_parkrun |
|---|---|---|---|
| photos (each with a vector and a row of search words) | 59,519 | 1,499 | 1,309 |
| faces | 187,784 | 9,850 | 3,328 |
| photo_tags / photo_people rows | 150,053 / 79,586 | 1,999 / 1,981 | 1,075 / 1,264 |
| folders | 2,617 | 21 | 9 |
| saved suggestions / candidate-word vectors | 13 / 1,080 | 485 / 270 | 1,164 / 112 |
| model keys holding vectors (each with one vector length) | 1 | 1 | 1 |

All three libraries name the same set of models (CLIP and face settings): 1 set.

## The contract other domains rely on

| Contract | Guard |
|---|---|
| Order: Date Taken, file name or caption key, either way, then the photo id in the order's direction; photos with no key after, by id | `tests/test_library_views_scenarios.py` (V1: every kind of source, both ways, across pages), `tests/test_library_union_and_order.py`, `tests/test_caption_order.py` |
| The list: one order, no photo twice, none skipped; `total` counts a photo once | V1; `tests/test_selection_scenarios.py` (V5, V7) |
| Reads only; ids and cards separate reads; caps `MAX_IDS`, `MAX_CARDS`, `MAX_MEMBERS` | V1, `tests/test_library_view.py`, `tests/test_library_view_plans.py` |
| A search ANDs its terms and never ranks; nothing typed is FTS5 syntax | `tests/test_library_search.py` (V2), `tests/test_camera_words.py` |
| One process on the card at a time, taken through `Runtime.begin` / `gpu_turn`; a waiter told who holds it; one set of models in a process (#761), the startup warm-up included (#1423) | `tests/test_one_gpu_at_a_time.py`, `tests/test_one_gpu_jobs.py` (V4), `tests/test_gpu_single_owner.py` |
| Models built once per settings by the runtime, released when idle; the page names what holds them | `tests/test_models_released_when_idle.py` (V6), `tests/test_the_runtime_keeps_models_by_settings.py` |
| Vectors and suggestions follow their photo; a looking run keeps nothing | `tests/test_suggest_then_apply_all.py` (V3, V9), `tests/test_analyse_only_suggest.py` (V3: a looking run forced to remember fails 16 of 24) |
| A view kind moves in every layer: store `KINDS`, `_source_of`, `library-source.js`, `navigator-model.js`, `search-model.js` | `tests/test_view_rules_follow_the_server.py`, `tests/test_chip_keys_follow_the_server.py`, `tests/frontend/search-page.test.mjs` (every member kind has a section, a chip-key pair and labels of its own) |

## The seven conditions (2026-10-15)

1. **No reach-ins.** Met for what this pass swept. The suggester and the runtime are reached through `Runtime.begin`
   and `suggester.model_for_run` alone; tests reach the suggester the way a run does (`tests/stand_in_models.py`).
   `tagpup_cli.py index` (Files) makes the CLIP model only once a photo is to be embedded (#1089).
2. **No rule written twice.** Met. The runtime took test models two ways (given, or built); now only built
   (`build_clip`/`build_faces`). `begin` called `model_for_run` twice for `remember`; now once. "A run of another set is
   under way" is `Runtime._another_set`, asked by `begin` and by the warm-up; a lease's set and settings are built by
   `_Lease` alone.
3. **No hidden coupling.** Met. A view kind added to the server and not to the pages fails the page test over
   `view_rules.json` `member_kinds` (mutation: `year_other` dropped from `sectionOf` or its labels fails it).
   `model_for_run` no longer passes `remember` "only when it differs" so a stand-in need not know of it.
4. **No concept that could collapse.** Met: models given to the runtime collapsed into models built from settings; the
   warm-up is a run's `_Lease` (with no index), not a second kind of hold.
5. **No branch without an instance.** Met. Deleted for 0 live instances: the suggester's arms for no CLIP model, no face
   model and no index (#1420); `runtime._part`'s unreadable-setting arm (0 of 3 libraries hold one; the settings
   validator refuses one); the navigator's person with no id (the route always sends one); the index's vector-length
   check and all that only it used (`stored_mismatch`, `stored_dim`, `vector_dim`, `clear_clip_embeddings`,
   `output_dim`; 1 key and 1 length per library), whose arm cleared every model's vectors. The seven catch-alls of the
   suggester that turned a failed read or write into "nothing" are gone: a run's read that fails fails the run with its
   sentence, a photo's fails the photo (V3). The warm-up's refusal beside a run of another set has a user action: the
   server starts and the owner clicks Suggest in a library on another set before the warm-up runs.
6. **Scenario tests, each file owned and mapped.** Met: V1 to V9 each have a file in tests/owners.py. Unit tests that
   restated a scenario were lifted and deleted, each with a mutation that fails the scenario: `test_library_view`'s
   EqualDates and dated-then-undated order (V1), `test_selection`'s source-is-the-ids-route and excluded tests (V7),
   `test_embeddings_follow_their_photo`'s rename and folder removal (V9). The unit files that stay hold a rule no
   scenario holds (below).
7. **No open data or wrong finding, and every low fixed or by design.** Met: #1420, #1423, #1424, #1425, #1089 and #1303
   are fixed; the pass's own findings (a vector-length check with no instance, a swallowed read of the named faces) are
   fixed; the third (a stand-in arm in `services/faces.detector_of`) is People's.

## By design

- **Suggest alternating between two libraries on different model sets reloads the models at each switch.** The process
  keeps one set at a time (#761; DECISIONS 2026-10-15): two ViT-H-14-sized sets do not share the owner's card with
  the owner's other programs. Cost today: 0 (1 set across the 3 libraries). Keeping two sets is the owner's call.
- **The startup warm-up refuses Suggest in a library on another set until its load ends**, with a sentence naming it
  ("the warm-up of Suggest's models (TagPup server) is using ..."): the warm-up is a run while it loads (#1423). Cost
  today: 0 (no library on another set).
- **A model load that hangs refuses Suggest on another set until the server restarts**: the run (or the warm-up) holding
  the hung load keeps its lease until it returns or the process ends. Cost today: 0 libraries on another set.

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

- 2026-10-15, the domain pass: the warm-up holds a run's lease, and `begin` drops another set only once its own are made
  (#1423, #1424); the runtime takes test models one way; the suggester is always given both models (#1420, #1425); the
  unreadable-setting and no-id-person arms gone; a page test over every member kind; an index with nothing to do imports
  no model library (3.2 s to 1.0 s in a sandbox, #1089) and the vector-length check is deleted; the suggester's
  catch-alls gone; the Activity page names what holds the models; V1, V7 and V9 lifted over the unit tests they
  restated; `test_settings.py` named for what it tests (#1303).

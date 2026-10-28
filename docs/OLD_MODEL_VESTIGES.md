# What is left of the old models (2026-10-25)

The old models are MTCNN + FaceNet (`mtcnn-facenet`) and OpenCLIP ViT-H-14 `laion2b_s32b_b79k`; the new ones are
`scrfd-adaface` and SigLIP 2 (`ViT-SO400M-14-SigLIP2`, `webli`). Owner, 2026-10-25: everything uses the new ones. A model
change means re-embedding everything and naming again, and that is taken as given: there is no mixed-model arm.

**Live counts** (read-only through `db.readonly_uri`, 2026-10-25; photo_index / kr-track / renton_parkrun):

- The settings hold SigLIP 2 / webli, full frame true, aspect ratio 1.4, an empty image size (forcing one is refused for
  SigLIP 2), and `scrfd-adaface`. All three still hold MTCNN's rows (20, 0.85, "0.6, 0.7, 0.7"). None holds
  `faces.detect_max_side`.
- `face_embeddings` holds `scrfd-adaface` only: 182,803 / 10,884 / 3,315. No FaceNet vector is left.
- `embeddings` holds the SigLIP 2 key only (`...|full-frame|1.4|native`): 59,523 / 1,634 / 1,309. `tag_embeddings`
  holds 0 rows in each.
- `faces_detected` still holds rows under MTCNN's detector name: 0 / 175 / 139. Under the new model it holds 10,189 / 156 / 31.
- Each library's switch INSERTED its `faces.model` row: changes 3244 / 342 / 1221. Undone, the row goes, and the
  library reads the default. That undo is refused (since 2026-10-28 by `settings.NOT_FACENET`: no library is put back on FaceNet; until then NONE_HELD, no FaceNet vector being left). Each library's photo
  switch (3249 / 343 / 1222) updated rows the stamp had written.
- The weights on this machine: SCRFD 640 and 1280 and AdaFace, in torch's checkpoints folder, and SigLIP 2 SO400M in the
  Hugging Face cache. FaceNet's `20180402-114759-vggface2.pt` and four other CLIP checkpoints sit there unused.

## The table

Actions: CONVERT (make it use the new models), DELETE (dead: no live instance), KEEP (say why). "next cut" means it is
planned below and not built in this branch.

| # | Where | What it does | Live instances | Action |
|---|---|---|---|---|
| 1 | `core/validation.py` SETTINGS defaults: `model.name` ViT-H-14, `model.pretrained` laion2b, `model.force_image_size` 512, `faces.model` mtcnn-facenet | The values a new library is stamped with (#1974) and the values read for a missing row | Every new library | CONVERT. Designed and tried, NOT committed: 46 test files fail (see "Cut A") |
| 2 | `services/settings.py` `undo_check` (`DEFAULTS[FACE_MODEL]` for an inserted row), `_filled`, `store/settings.py` `face_model()` | Gives a missing faces.model row the default | 3 (changes 3244/342/1221, if undone) | CONVERT with 1: a missing row in a library that holds settings reads `BEFORE_FACE_MODELS = mtcnn-facenet`; only the stamp changes |
| 3 | `FACE_MODELS = (MTCNN_FACENET, SCRFD_ADAFACE)`, the `one_of` rule, the gear's info text, `SETTING_GROUPS["face-model"]` consequences ("set back to mtcnn-facenet" until 2026-10-28; now "A library is not switched to mtcnn-facenet") | The old model is still a choice of the rule, a switch to it refused since 2026-10-28 (`settings.NOT_FACENET`) | 0 libraries on it, 0 FaceNet vectors | CONVERT the order and text now (Cut A); DELETE MTCNN_FACENET as a choice in Cut B |
| 4 | `faces.min_face_size`, `faces.confidence_threshold`, `faces.mtcnn_thresholds`, `faces.detect_max_side`, the "Face detection" group, `LibrarySettings.faces` MTCNN arm, `detect_max_side` property | MTCNN's settings; scrfd-adaface takes none (`model_alone`) | 3 libraries hold 3 rows each that nothing reads; 0 hold detect_max_side | DELETE in Cut B. The rows left in the live libraries (3 / 3 / 3, counted 2026-10-26) are deleted by `delete-old-detector-rows`, one journaled change, undone from History (owner, 2026-10-26; DECISIONS 2026-10-26) |
| 5 | `tagpup/ml/faces.py` (265 lines): FaceModel, `facenet_pytorch` import; `runtime._build_faces` FaceModel arm | The old detector and embedder | 0 | DONE (2026-10-28, Cut B build 1): `SMALLEST_FACE` and `NotDetected` moved into `ml/scrfd_adaface.py` (`detection_picture` was the old model's alone), `ml/faces.py` deleted, `runtime._build_faces` builds ScrfdAdaFace alone (0 libraries on mtcnn-facenet, 0 FaceNet vectors; a switch to it refused, `settings.NOT_FACENET`); the new model imports without facenet-pytorch (`tests/test_scrfd_adaface_faces.py`) |
| 6 | `requirements.txt` facenet-pytorch; `scripts/sandbox.py` version list | The old model's package | 0 | DONE with 5 (2026-10-28): the line and the entry deleted, and `scripts/measure_pipeline_stages.py`'s import of it (its MTCNN hooks are row 22's). `open-clip-torch` stays: SigLIP 2 loads through open_clip |
| 7 | `settings.switch_on` FaceNet "back" arm, `NONE_HELD`, `holds_vectors_under` | Allows a switch back to FaceNet, or refuses it | 0 (all three refuse it) | DONE (2026-10-28, owner's choice (b); DECISIONS 2026-10-28): the back arm, `NONE_HELD` and `holds_vectors_under` deleted; a switch to FaceNet is refused for every library, empty or not, with one sentence (`settings.NOT_FACENET`, `face_model_refusal`), and the undo of 3244/342/1221 by the same rule and sentence (`undo_check`), in D2's matrix |
| 8 | `core/clustering.py` `FACENET_CUTS`, `CUTS[MTCNN_FACENET]`; `scripts/verify_workflow.py`'s FaceNet cut | FaceNet's naming, offer and group cuts | 0 libraries | DONE (2026-10-28, Cut B build 2): `FACENET_CUTS` and its `CUTS` entry deleted (CUTS holds the models a library may be put on, `settings.face_model_refusal`); verify_workflow reads the library's model's cuts; the tests asking FaceNet's cuts ported to AdaFace's, and P10 (`tests/test_a_library_indexes_and_suggests_with_its_face_model.py`) works in a library on its model, its switch-from-FaceNet tests deleted with the cuts they stood on |
| 9 | `ml/photo_models.py` row `("ViT-H-14", "laion2b_s32b_b79k")`, listed first | ViT-H's word cut 0.23 and its forced size | 0 vectors, 0 libraries | Order now (Cut A); DELETE the row in Cut B. The undo of 3249/343/1222 stays refused (UNKNOWN_PHOTO_MODEL instead of TOO_FEW) |
| 10 | `services/face_gate.py` (235), `scripts/measure_face_gate.py` (192), the two-model tests of scenario P9 (deleted 2026-10-26: `tests/test_the_gate_reads_by_year_gap.py` keeps `--alone`'s by-year reading on the new model), the gate section of `store/faces.py` (`pass_counts`' gate half), `core/holdout.py` for its gate use | Phase 3's gate: old against new on faces holding both vectors | 0 faces hold both | DELETE (Cut B; a deletion; scenario P9 leaves `tests/owners.py`) |
| 11 | `retire-faces`, `retire-vectors` (CLI), `services/retire_faces.py` (531), `OLD = MTCNN_FACENET`, and its tests (895 lines) | Retires the old detector's faces and FaceNet's vectors | Applied once in each library; 0 left to retire | DONE (cut B1, 2026-10-25): the commands, service and tests deleted; `retire faces (%s)` is in `journal.RETIRED` until the baseline migration, `retire vectors (%s)` refused by `retire_photo_vectors.vectors_check`, and a face pass recorded before a standing `retire faces` change refused by `journal.pass_check` (2026-10-26), until the same migration. The generic "retire the old model's data" step is the seam's (Cut C) |
| 12 | `face-pass` (`services/face_pass.py`, 260), `pass_counts(conn, MTCNN_FACENET, model)` in `switch_on` | Re-embeds faces and carries names by overlap from the old faces to the new ones | 0 libraries need it now | DONE in part (2026-10-27, docs/PLAN_KEEP_IN_STEP.md cut 3): `face-pass` and `services/face_pass.py` deleted; a face-model switch re-embeds through the index (the photos never examined under the new model are its work). DONE (2026-10-28, DECISIONS 2026-10-28): `pass_counts` left `switch_on`, whose refusal no command could pass; `pass_counts` stays only for the gate (row 10) |
| 13 | `embed-photos`, `set-photo-model`, `retire-photo-vectors`, `services/photo_reembed.py`, `retire_photo_vectors.py`, `settings.photo_switch_on` / `covers` | Re-embed, declare, retire, for any photo key | Generic | KEEP as the photo half of the flow. The 5% gate (`covers`) is the check that the re-embed was done. Nothing in it is ViT-H's |
| 14 | `cluster-faces`, `tags-from-faces`, automatch | Naming again after a new model | Generic | KEEP: the flow's naming step |
| 15 | `faces_detected` rows under MTCNN's detector name | A record of "looked at" that no library on the new model reads | 0 / 175 / 139 (2026-10-26) | DELETE: `delete-old-detector-rows`, with row 4's settings, one journaled change (`faces_detected` in `journal.KEYS`), undone from History (owner, 2026-10-26; DECISIONS 2026-10-26) |
| 16 | `store/schema.py` migration 33 (`FACENET_KEY`, `FACENET_BYTES`) | Frozen migration | Read by an older library or snapshot | KEEP (frozen) |
| 17 | `services/bake_reading.py` `OTHER_MODELS` | A bake under a key that is not the library's: that side is worked out | Generic (the next swap) | KEEP. A new library on the new models reads a bake from the live libraries without a model once 1 is in |
| 18 | `--skip-faces` | Index without faces | Generic | KEEP. Only its SPEC text names MTCNN |
| 19 | Tests: `stand_in_models.FaceModelStandIn.key = MTCNN_FACENET`, `face_rows.FACENET` (the helpers' default), `people_by_id`, `journal_library`, `face_rows.configured_model()` (from DEFAULTS) | Seed faces and vectors under FaceNet | Fixtures only | CONVERT with 1 (Cut A): the new library's model is the default; FaceNet only where a test is about the old model |
| 20 | Docs: README 52/74; SPEC_TAGPUP_CLI 16/31/33/47/64/194/222; EXAMPLE 80; DATABASE 81/343/355/356; SPEC_TAGTUNER 189/580/611; domains/people 103/227, views 34/78; AI_PIPELINE (default mentions); INVARIANTS 21 (a history line: KEEP) | Name the old models as the default or the engine | - | CONVERT in Cut A (the default) and Cut B (the engine) |
| 21 | UI: the gear is built from validation (no model literal in `web/`); the group title "The CLIP model", `web/tuner/index.html`'s menu title | Calls the photo model "CLIP" | - | CONVERT the wording (low), Cut A |
| 22 | `scripts/measure_pipeline_stages.py` (MTCNN hooks), `measure_pipeline_holdouts.py` (ViT-H candidates and cut), `prepare_test_environment.py` (seeds `MTCNN_FACENET`) | Measurement and sandbox programs on the old models | 0 | CONVERT or DELETE in Cut B, with 5 |

Counts by action: CONVERT 11 (1, 2, 3, 9 in part, 12, 19, 20, 21, 22 in part), DELETE 8 (3 in part, 4, 5, 6, 7, 8, 10, 11),
KEEP 7 (13, 14, 15, 16, 17, 18, INVARIANTS' history line).

## The switch machinery against the one flow

The flow is: declare the new model, re-embed, name again, retire the old model's data. **The flow itself:**
embed-photos, set-photo-model, retire-photo-vectors, the index after a face-model switch (re-embed; face-pass deleted 2026-10-27), cluster-faces / tags-from-faces
/ automatch (name again), the photo switch's coverage gate. **Extra**, kept only so FaceNet could stay a choice: the switch back
and NONE_HELD (7, deleted 2026-10-28), FaceNet's cuts (8), MTCNN's settings (4), the gate between models (10), retire-faces and retire-vectors
as written for FaceNet alone (11), and face-pass's FaceNet-as-old (12, the command deleted 2026-10-27, its count in the switch 2026-10-28).

## The cuts (none of them writes the live libraries)

- **Cut A** (next, about 700 to 900 lines): rows 1 to 3, 9's order, 19, 20's defaults, 21. The patch is designed:
  validation's defaults become SigLIP 2 / webli / an empty size / scrfd-adaface, and `BEFORE_FACE_MODELS` is what a
  library stamped before faces.model was a setting reads (`store.settings.face_model`, `settings._filled` when
  `held` is non-empty, `undo_check`). That keeps the three undos refused as they are today. Tried on the trunk: 46
  test files fail, mostly People tests that seed FaceNet vectors through `face_rows` into a library that now reads
  `scrfd-adaface`. Port `face_rows` / `stand_in_models` to the library's default model. Port the tests that check the
  cuts to AdaFace's numbers, or, for the ones about the old model (switching, retiring), stamp FaceNet explicitly
  (`settings.stamp(..., {"faces.model": MTCNN_FACENET}, FROM_REPLACED)`, as `tests/test_an_index_reads_a_bake.py` does
  for the new model). Add a scenario: a library made by Create alone indexes a folder baked under the live libraries'
  keys with no model asked (fails on the old code).
- **Cut B** (a deletion; about +300 lines, more than 2,500 deleted): rows 4 to 8, 9's row, 10, 11, 22. Rows 5
  and 6 done first (2026-10-28: `NotDetected` / `SMALLEST_FACE` moved, `ml/faces.py` deleted). Move retire's undo checks into
  `journal.RETIRED`. The Data owner gives the refusal of an undo of an inserted faces.model row.
- **Cut C, the seam** (about 500 to 700 lines): one module declares each model kind (photo: key spelling, loader,
  dimensions, word cut, sizes; face: key, loader, vector dimension, the detector record's spelling, the floor). Runtime,
  settings (`embedder`, `faces`), validation's choices, bake (`PHOTO_DIM`, `FACE_DIM`), clustering's cuts and the switch
  ask it. The re-embed (the index, face-pass being deleted) and retire are one flow with "old" = the library's current model. `photo_models.py` folds into it.
- **The owner's**: deleting the unread MTCNN settings rows and `faces_detected` rows from the live libraries (a
  journaled write).

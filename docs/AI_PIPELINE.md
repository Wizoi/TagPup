# The AI pipeline: design and plan

The one place for how TagPup's models are to be run and replaced, in phases. It replaces the three places the design
was spread over (the research report, the owner's decisions of 2026-10-07 and the health review's section 9.1); those
stay as the record, this is what is followed. Owner for the domain: Views, search and AI (models, GPU turn, Suggest,
vectors), with People for faces and Data for the schema ([OWNERS.md](OWNERS.md)).

Status (2026-10-17): **phase 0 done** (measured, section 7); the plan below is reordered by what it showed (owner
accepted, 2026-10-16). **Merged:** 1f (`repair-vectors`, applied on photo_index 2026-10-16: 223 vectors made again) and
2a (`cluster-faces` 2,113 s to 202 s, names unchanged). **Built, off:** 1a (the detector downscale, the setting
`faces.detect_max_side`, measured below; turning it on is the owner's). **Built:** 1b (the index reads the next photos
ahead: 1.94 to 1.68 s a photo on an event folder, measured below). **Measured, not built:** 1c (after 1b the card waits for its next photo 1.6% of the index on the many-face sample, 0.2% on the event folder). **Built:** 2b (a name on likeness alone needs a margin over the next person, 0.04 to name and 0.02 to offer; names given from now on only, DECISIONS 2026-10-17). **Built:** 2c (density measured, no change; the cluster-of-one vote gated). **Measured:** phase 3's speed check (2026-10-18,
section 7: SCRFD 10g in torch and AdaFace IR-101, about 1.8 hours for photo_index against 9.3, no second runtime;
chosen by the owner, DECISIONS 2026-10-18). **Built (2026-10-18):** the model module (merge 2a), the
`faces.model` setting with its one refusal and `Runtime.look_at_faces` (merge 2b), and migration 33 (pulled forward:
each face's vector in `face_embeddings` under its model's key, every reader keyed by the library's `faces.model`);
**installed on all three libraries on 2026-10-18** (schema 33; vectors checked byte for byte against the backups,
named counts unchanged). **Merge 3, the pass:** slice A1 merged (one pairing rule, Suggest and a face with no vector,
the undo of a pass's face); slice A2 (the store layer and `tagpup/services/face_pass.py`, the model injected) is built
and reviewed but NOT merged: two blockers to fix first (a photo removed while the pass runs must not be re-made as
a ghost row; the change's name must not carry the folder) and two owner decisions taken (named old faces are
paired first; new faces stay unnamed). **Slice B** (the `face-pass` command) is built. **Slice C** (the gate's scoring,
`scripts/measure_face_gate.py`, read-only) is built on `feature/gate-scoring`, as built below; the owner picks the naming
precision from its numbers. **Next:** merge slice C, a run on a copy, a small real folder the owner checks. Update a phase's status
here when it merges.

## 1. Where this comes from

| Source | What it holds | Standing |
|---|---|---|
| [reports/TagPup AI pipeline optimization.md](../reports/TagPup%20AI%20pipeline%20optimization.md) and its `research_notes/` | The research: what the stack does, the ranked changes, the model comparisons, the sources. Written against trunk `04bb0e6`; its counts (68,460 photos, 226k faces), line numbers and some estimates are stale (sections 3 and 7). | Archive. Read for the evidence; do not plan from its list or its numbers. |
| [DECISIONS.md](DECISIONS.md) 2026-10-07 (row 47) and [history/ARCHITECTURE_faces_and_naming.md](history/ARCHITECTURE_faces_and_naming.md), "The owner's decisions for the three big projects" | The owner's choices: plumbing first, replace the face models (licence accepted), replace CLIP with SigLIP 2, search by what is in the picture, bake the current model's vectors and face regions into the file. | Decisions. Not reopened without a new fact. |
| [reports/Project health review 2026-10-09.md](../reports/Project%20health%20review%202026-10-09.md), 9.1 and 9.2 | Start only the plumbing slice; the model swap waits; baking vectors into files is its own project. | Advice, adopted below. |
| [DECISIONS.md](DECISIONS.md) 2026-10-16, "THE FACE VECTOR GETS A MODEL KEY" | The schema: a `face_embeddings` table, migration 33, not before about 2026-11-15. | Decision. |
| The phase 0 measurements (`scripts/measure_pipeline_stages.py`, `measure_pipeline_holdouts.py`; findings #1526 to #1533) | The numbers of section 7. | Evidence. |

## 2. Goals, and what is not a goal

1. **Indexing and Suggest are fast enough that a re-embed is an afternoon.** Measured 2026-10-16: an index is 1.87 s a
   photo on an event folder and 1.24 s on the many-face sample, 69% of it the face detector; the photo vector alone
   (decode, pad, CLIP) is about 0.2 s, so a photo re-embed of photo_index's 59,519 photos is about 3.3 hours today.
2. **Faces are found and recognised better**, above all small, side-on and growing children, without any named face
   losing its name.
3. **Models are a per-library setting and their vectors are versioned**, so a change adds and never replaces, and the
   old result stays until the owner retires it.
4. **No claim of "faster" or "better" without a number** measured on a user action in a sandbox, baseline first
   ([DEVELOPMENT.md](DEVELOPMENT.md), "Performance work").

Not goals: ONNX/TensorRT export, `torch.compile`, GPU JPEG decode (each costs more to install on Windows than it
returns; research report, "Ten changes"), approximate search (Flat is exact and 10 ms at this size), a second
runtime for the face models.

## 3. The pipeline today

### 3.1 What phase 0 showed (2026-10-16, details in section 7)

- **The face detector is the cost.** MTCNN takes 0.78 s of a photo's 1.14 s (69%); the 759-face photo took 9 s (7 in
  MTCNN) and a 193 MP photo 31 s (27 in MTCNN). CLIP's forward pass is 0.058 s. The two decodes and the resize are
  about 0.2 s (18%).
- **The card is not saturated, and not free.** It averaged 20 to 31% busy during an index (idle at exactly 0% in only
  14% of samples): MTCNN mixes processor work with small kernels. The indexing process used about three cores.
- **Card memory is nearly full on large photos.** The sample peaked at 9,982 of 10,240 MiB, from MTCNN on the full-size
  picture. Alongside the desktop apps' 6.3 GB seen earlier in the day, an index would have run out of memory.
- **Photo vectors are already at 512**, and 224 lost nothing on the one library tested (kr-track, coarse tags).
- **Batching is safe** (cosine 0.999998 or more against one at a time).
- **`cluster-faces` takes 34 minutes**, of which 2 are the pair pass and 32 are "Resolving multi-face photo
  conflicts": processor work, card idle (#1530). Phase 2a found the 32 minutes in the last matching pass, which logged
  no line of its own (the rounds take 76 s); after it, 202 s.
- **A defect:** the stored vectors of photos turned by their Orientation are stale (4 of 4 in the sample, cosine 0.81
  to 0.94; photo_index holds 223 turned photos; #1529).
- **A margin over the second-best person separates right from wrong far better than likeness**: with FaceNet today,
  precision 98.4% at 0.70 likeness against 99.8% at a margin of 0.04, on 28,359 of 35,214 faces (#1532 for the bias
  of this curve).

### 3.2 The research report's list, recounted against HEAD and the numbers

| # | Change | State and worth |
|---|---|---|
| 4 | Downscale before MTCNN | **Open, now first.** The largest single saving (MTCNN is 69%) and the fix for the memory squeeze. Changes which faces are found: behind a setting, judged by counts. |
| 5 | Prefetch (threads decode ahead of the GPU) | **Open.** The serial loop leaves the card 70 to 80% idle; the processor work (decode, MTCNN's host side) overlaps with it. |
| 6 | Batch CLIP images and tag prompts | **Open.** Safe (cosine 0.999998). Small: CLIP is 0.058 s a photo. |
| 1 | Decode each photo once | **Open, smaller than the report thought.** `images.opened` reads the file once into memory but CLIP decodes it upright and faces decode it again as stored. Worth about 0.07 s of 1.14. |
| 2, 3 | `Image.draft`; resize before padding | **Open, small.** Decode plus pad and resize is 0.14 s a photo; `draft` helps most on the very large photos. |
| 7 | `inference_mode`, `cudnn.benchmark` | **Half done.** `inference_mode` is in `clip.py` and `faces.py`; `cudnn.benchmark` is not. |
| 8 | Skip the vector-index load when indexing nothing; remember failed files | **Half done.** #1089 stops an index that finds nothing from importing a model's library. Remembering failures only if counted (1d). |
| 9 | Hand small indexes to the warm models, or keep a warm indexer | **Open, deferred** (1e). A run's fixed cost is about 15 s (imports 3.4, CLIP load 9.8) plus the metadata read. It touches the one-process GPU rule. |
| 10 | Crop encode off the GPU loop | **Drop.** The crop JPEG is 0.004 s and the crop and tensor 0.011 s a photo. |

Two things the plumbing must respect: `images.opened` detects a damaged or zero-tailed file and raises `Unreadable` (a
`draft()` read must still reach the full decode that proves the file whole), and the index no longer loads the model
library for a run that has nothing to do.

## 4. Principles for every phase

- **Never two re-embeds.** The model key of a photo vector includes the model, the weights, the framing and the image
  size, so any of them re-embeds all photos. The resolution question (224 or 512) is decided with the model in
  phase 5; one re-embed serves both. A re-embed of faces is a single pass too (phase 3).
- **The photo files are written once** (owner, 2026-10-16): phases 1 to 5 change the database only and never touch a
  photo file for the sake of a model. The XMP work (phase 6) starts after the face and photo models are final and
  implemented, and bakes the final model's vectors and the face regions in one pass.
- **Old vectors stay until the owner retires them**, in `embeddings` (photos) and `face_embeddings` (faces).
- **A model change is a library setting**, read through `tagpup.services.settings`, changed by a journaled change,
  never read from `config.ini`.
- **Thresholds belong to a model.** `OFFER_A_NAME` 0.70, `NAME_WITHOUT_ASKING` 0.80, the grouping cut 0.885 and
  Suggest's neighbour cut 0.35 are measured per model on the named faces (35,425 in photo_index, counted
  2026-10-16; recount in each brief), never carried over. Read a curve on the unnamed faces too, not only on the faces
  already matched (#1532): a recogniser's gate is read on the margin curve (over the second-best person) and on a
  hand-checked sample of unnamed faces, not on the named-face curve alone (98.3% at every likeness cut, because
  those faces are the ones already matched).
- **Every bulk write on photos or rows is a dry run first**, `--apply` to write, a backup before, run on a copy of the
  library first, reporting what changed and not what was attempted.
- **GPU work takes its turn** through `tagpup.runtime` (`begin`, `gpu_turn`); no phase loads a model on the card
  itself (`tagpup/ml/gpu.py`). Plan for 10 GB of card with the owner's desktop apps holding up to 6 GB of it.
- **A phase ends with the findings it raised closed** (owner, 2026-10-17): each is fixed, or closed `decided:` by
  design with its reason written in a document, before the next phase starts; the open count in `docs/findings.md` is
  counted from the file and said in the merge report. A defect gets a row; a method caveat or a risk goes in the
  phase's report or section 8, not in `findings.md`. Running the real library finds old defects too: budget for them.
- **A phase is a branch of about 1,200 added lines or fewer, one concern**; a larger piece is cut into merges that each
  leave the trunk whole. At most two review rounds, the ownership question in the second.
- **Real data shapes:** every brief names the scenarios the owner works in: an index interrupted half way, two indexes
  of two libraries at once, a library switched mid-folder, a folder held by two libraries, an update while an index
  runs, a damaged file, a 759-face photo, a 193 MP photo, a turned photo (223 in photo_index).

## 5. The phases

```
 0 Baseline (done) ──► 1 Plumbing, detector first ──┐
                  └──► 2 Clustering and margin rule ─┴──► 3 Faces: detector and recogniser, one pass ──► 4 Matching ──► 5 Photo model ──► 6 Bake into files
                          (no schema)                          (migration 33)
```

Phases 1 and 2 need no schema work and may start now, in separate worktrees (never two GPU test runs at once).
Phase 3 waits for the schema review (about 2026-11-15) and for phase 1 merged.

### Phase 0: Baseline (done 2026-10-16)

Built and run in a sandbox (`scripts/measure_pipeline_stages.py`, `scripts/measure_pipeline_holdouts.py`, sharing
`scripts/sandbox.py`); results in section 7; findings #1526 to #1533. The sample was weighted to many-face photos
(299 photos, 37% at 10 or more faces; photo_index strata none 10,493, 1 to 3 34,804, 4 to 9 10,798, 10 or more
3,423; max 759). Left open by it: no Suggest cold/warm number (the script is broken by the Add folder dialog, #1531),
and the many-face hold-out for the 224 test rests on one library.

### Phase 1: Plumbing, the detector first (no schema, no re-embed)

Every step keeps every stored vector valid. Verified against the stored vectors (cosine 0.999 or more, except the
turned photos, which 1f repairs) on a sandbox copy of 200 photos, and timed against the section 7 baseline with the
same scripts.

- **1a Downscale before the detector, behind a library setting.** Cap the long side before MTCNN and scale the boxes
  back. Judged by counts on the phase 0 sample: faces found per stratum, and which named faces would be lost
  (expected none). It also lowers the card's peak memory (9,982 MiB today). Off by default; turned on only on the
  owner's word, once the counts are shown.
  *Built 2026-10-16, off.* The setting `faces.detect_max_side` (Face detection, "Look at most"; empty is off): the
  detector looks at the picture scaled to the cap, the boxes are scaled back, crops and vectors come from the full
  picture. The cap is an argument of each detection (`LibrarySettings.detect_max_side`), not a setting of the face
  models: libraries differing only in it share one set, and a Suggest in one is not refused beside a run in the other.
  The cap enters the `faces_detected` detector name only when set, so off nothing is detected again; on, only
  photos with no face whose detection was recorded are detected again by Suggest (kr-track 1, renton_parkrun 9,
  photo_index 0; a photo with faces keeps them, and a stored box moves only under a forced re-index). Measured on the
  phase 0 sample (299 photos, 5,724 faces found without a cap, 206 of them named):

  | Cap | Faces found | Lost: named / unnamed (median side px) | Gained | MTCNN s a photo | Index s a photo (sample / event) | Peak card MiB (others before) |
  |---|---|---|---|---|---|---|
  | off | 5,724 | - | - | 0.891 | 1.40 / 1.84 | 9,927 (1,971) |
  | 3,000 | 4,796 | 0 / 1,184 (34) | 256 | 0.424 | 0.94 / 1.44 | 8,575 (1,419) |
  | 2,048 | 3,608 | 0 / 2,251 (34) | 135 | 0.282 | 0.77 / 1.08 | 7,437 (1,529) |
  | 1,600 | 2,966 | 0 / 2,859 (36) | 101 | 0.206 | 0.69 / 0.96 | 6,969 (1,528) |

  Most of the loss is in the 10 most crowded photos (887, 1,818 and 2,337 of their 3,234 faces). A cap is a larger
  smallest face on a large photo: MTCNN's smallest face becomes `min_face_size` x long side / cap at full size, so the
  faces lost are the small ones (90% under 75 px). Over the whole of photo_index, the named faces under that size are
  5, 8 and 23 of 35,425 at 3,000, 2,048 and 1,600 by the box's longer side, 5, 27 and 53 by its shorter (counted from
  each photo's header, read-only, 2026-10-16); unnamed 10,777, 24,584 and 35,491 of 152,325.
  The resize adds 0.04 to 0.06 s a photo to the decode for faces.
- **1b Overlap.** A thread pool decodes and prepares the next photos while the card runs the current one (Pillow
  releases the GIL; a Windows `DataLoader` would spawn processes). Batch CLIP images and the tag prompts Suggest
  already knows up front. `cudnn.benchmark` for the fixed-size recogniser batch.
  *Built 2026-10-17: the read-ahead only.* Each photo's model work is split into its processor half and its card
  half (`PhotoEmbeddings.prepared` / `made`, `FaceModel.picture` / `detect_and_embed`), and `tagpup.core.read_ahead`
  runs the processor halves of the next photos on two threads, three ahead; the card, the identity write and every
  library write stay on the CLI's thread in the old order. The pictures read ahead are reserved within an estimate of
  1 GiB, each half saying what it holds (`ClipModel.input_bytes`, `FaceModel.picture_bytes`), and a photo larger than
  that is read alone; process memory is not capped (the file's bytes during each decode, the card thread's crops and
  tensors are outside the estimate). Measured the same day on the same samples, other programs holding 1.0 to 1.7 GB
  of the card, before to after: the event folder (61 photos) 1.94 to 1.68 s a photo, card busy 20.9 to 23.8%, peak
  card memory 8,920 to 8,920 MiB; the many-face sample (299) 1.22 to 1.05 s a photo, card busy 31.2 to 37.4%, peak
  card memory 9,937 to 9,902 MiB; the 299 photos' rows identical (5,724 faces, every box the same, photo and face
  vectors at cosine 1.000000). It adds more named things than it takes away (the read-ahead, `Prepared`,
  `DetectionPicture`, each model's two halves and their sizes, against `embed_image`, `embed_picture`, the unused
  `embed=` and `shown_size`): the split of each photo's work into a processor half and a card half is what the phase
  is, and each half is named where its model is. Not built, because they cannot pay: batching CLIP's images (its
  forward pass is 0.058 s in all) and `cudnn.benchmark` (the recogniser is 0.032 s, and the flag is process-wide, so
  MTCNN would re-tune for every photo size). Suggest's tag prompts are embedded one at a time
  (`suggester._precompute_candidates`), but each is kept after its first use: left as it is.
- **1c Decode once, `draft`, resize before padding. Measured 2026-10-17, not built.** After 1b the card's thread waits for
  its next photo 5.4 s of 333.6 s on the many-face sample (1.6%; 3.7 s of it is the one 193 MP photo, read alone by design,
  so about 0.5% without it) and 0.2 s of 102.1 s on the event folder (0.2%); decode, pad and resize are off the critical
  path, so the 5% rule stops the phase. Same-day index alone: 1.12 s a photo (sample) and 1.67 (event). What decoding once
  could still buy is less competition with MTCNN's host side, which only building step 1 and an A/B run would show;
  reopen only if a later phase makes the processor side the bottleneck. The plan as written: One decode per photo, the upright copy for CLIP and the
  stored-orientation copy for faces (face boxes stay in stored-orientation coordinates); `draft()` at twice the target
  for JPEG. The damage check must still run: a truncated or zero-tailed file raises `Unreadable` as before, tested
  with the three damaged shapes of the 2026-10-15 decision. Worth about 0.15 s of 1.14 a photo; do it after 1a and 1b
  show what is left.
- **1d Wasted runs. Decided 2026-10-17, nothing to build:** the index already skips a file that does not decode, logs it, lists it at the end and tries it again next run; the owner wants no skip-list (DECISIONS 2026-10-17). As planned: Counted first: does a failed file still cost a run (14 truncated files cost 7 runs in
  2026-09-26 to 28, before #1089)? If yes, record what failed beside the file's stamp in an existing place; a new table
  waits for phase 3's migration.
- **1e Warm indexer. Decided 2026-10-17, not built (the gain is small, owner).** As planned:** Small indexes (1 to 50 photos) use a long-lived worker holding the models
  instead of a 15 s start. Touches the GPU turn: the Views-AI and Integration owners both review. Skipped if the
  fixed cost is small next to the work after 1a to 1c.
- **1f Repair the turned photos' vectors (#1529, `wrong`).** The vectors of photos turned by their Orientation were
  made before CLIP was shown the picture upright, and their stamps still match so they are never redone. Count first
  in each library (photo_index 223, kr-track 0, renton_parkrun 0, counted 2026-10-16), then recompute those vectors
  through the one path that owns them (`PhotoEmbeddings`; a rotation deletes the vector for this reason): about 223
  photos at 0.2 s, built as `repair-vectors` (dry run by default). No doctor check can find one: the row holds neither
  the Orientation (0 of 59,519 rows of photo_index, counted 2026-10-16) nor the vector's age, so `repair-vectors`
  reads each file's header and tells a vector stale by making it again (#1529). It cannot come back by the same road
  once phase 6 puts how a photo is read into the key a vector is kept under; until then, a change in how CLIP is shown
  a photo is reviewed as a change of model key. `repair-vectors` is deleted at phase 5's re-embed (delete:
  embedding_repair.py, the CLI command, its tests and V9 entry, the SPEC heading, the DATABASE.md sentence, the 1f
  note).

**How it fails:** an index killed between the decode thread and the write (nothing half written: the existing
transaction is unchanged); a decode thread raising on a damaged file (that photo is reported, the run goes on); two
libraries indexing (one GPU turn at a time, as now); a 759-face photo and a 193 MP photo (batched crops and the
downscale stay under memory); a turned photo (its boxes stay as stored).

**Exit.** The section 7 user actions re-timed, faster by a measured amount; vectors within 0.999; face counts with
1a off unchanged and with 1a on reported by stratum; peak card memory reported. Two or three merges, each under 1,200
added lines. Owner: Views-AI, with Files for the indexer loop.

### Phase 2: Clustering and the margin rule (no schema)

**Why.** `cluster-faces` took 34 minutes, 32 of them in the last matching pass, on the processor, not in the pair pass
(#1530; fixed by 2a, 202 s now). It runs after every index that recorded a photo, from every entry point (the CLI's
`index` decides; DECISIONS 2026-10-16, #1544). Until then the apps never ran it, though DECISIONS 2026-10-15 said the
indexer did, so the live photo_index has had no run since at least 2026-09-23, and one run would name 21,225 faces,
unname 1,434 and rename 669, counted on a copy. And the baseline shows the margin over the second-best person
is the strong signal (precision 99.8% at margin 0.04, with FaceNet's vectors today).

- **2a The conflict stage.** *Done 2026-10-16: the last pass asked a likeness a face and a person at a time; 2,113 s
  to 202 s on one copy, the names unchanged.* Find what the 32 minutes do (a per-photo loop with a query each time? one query that
  could sit outside it? counted on the real data first, `EXPLAIN QUERY PLAN` on a copy) and make it per-run, not
  per-item. The pair pass (about 2 minutes) is left alone unless 2a leaves it the largest part.
- **2b The margin rule.** A suggested or automatic name requires a margin over the second-best person, with the
  thresholds read off the phase 0 curve on today's vectors (and checked on unnamed faces by the owner's review of a
  sample, #1532). Interim for FaceNet; phase 4 recalibrates it for the new model. *Built 2026-10-17: margin 0.04 to
  name unasked, 0.02 to offer, beside the likeness cuts (owner); worked out once (`clustering.margins`) over everyone
  named. It gates names given from now on: a face that carries the person is asked none. On copies of the three
  libraries a run on today's names writes what trunk's writes (photo_index 0 either way; kr-track one face fewer of 302,
  renton_parkrun the same 12). From scratch (every automatic name taken off a copy of photo_index) trunk names 55,123
  and the margin 51,059: 4,377 of trunk's not given (1,539 in the last pass), 313 given that trunk does not (70 of them
  another person than trunk's; 1,327 of the 51,059 reach the margin only by leaving out the people the photo's other faces
  hold, 32 of the 313 and 11 of the 70), for the owner's sample review (#1532). New Person gathers at the offer's margin.*
- **2c Density.** *Measured 2026-10-17, no change (owner): `min_samples` stays 1.* At 1 no face is noise: a face like no
  other is a cluster of one (kr-track 79.3% of its faces, photo_index 67.1%, renton_parkrun 76.7%), and a larger value makes
  more noise, not fewer (photo_index 67.1% at 2, 75.0% at 5, 80.2% at 20). On a copy of photo_index every value from 2 takes
  4,644 to 6,457 names the pass keeps today (a face turned noise loses its own anchor's and its cluster's vote), changes 248
  to 1,512, and over a third of the names it gives are more like another person than the one given; precision on the faces
  named by hand, held out, is no better (0.986 to 0.993 against 0.987 at 1, on 152 faces; kr-track 0.996 either way), and
  the run is 5% quicker (DBSCAN's neighbour search is about 130 s at any value). The chain is the density problem, and no
  value breaks it: one cluster holds 38,456 faces at 1 and 21,843 at 20, and the vote of a cluster held out by folder is
  right for 17,615 named faces and wrong for 16,051; a voted name stands only where its photo's keywords name the person.
  That is a question for the new recogniser's grouping cut (phase 4), not for `min_samples`. *Built from it (owner):* the
  vote of a cluster of one in a photo of two or more faces is its photo's keywords alone, so a name it gives from now on asks
  the tag's gate (`names_from_the_tag`: 0.70 and the margin 0.04) in the first round, past the photo's conflicts; a name the
  face carries is never asked. photo_index held 2,389 names given so, 865 of them more like another person. On copies of the
  three libraries a run on today's names writes what trunk's writes, face by face. From scratch (every name not given
  by hand taken off), each on its own copy of the live photo_index made minutes apart (today's plans agree face by face, so
  the library had not changed), trunk gives 51,059 names, 2b's count, and the branch 50,654 (each with the 175 by hand:
  51,234 and 50,829). The 405 between them: 1,112 that trunk alone names (1,110 votes of a cluster of one, which the last
  pass refuses too, and 2 the last pass named), less 707 that the branch alone names (693 by the rounds, the other face of
  the photo paired once the refused one no longer holds the person, and 14 by the last pass); 1 face is named by both, a
  person apart (the last pass in trunk, the rounds in the branch). The process of elimination asks no likeness and stays so
  (docs/domains/people.md, By design).
  **Suggest's `search(k=15)` stays one a photo** (owner: not built): a flat search of photo_index's 59,519 vectors is 9.25 ms
  a photo one at a time and 0.27 ms batched (records of the neighbours 0.5 ms), about 1% of a photo Suggest detects again
  (0.78 s), under the 5% a change asks; no timed run was made.

**Gate.** Same named faces, same or better precision on the hold-out; never a name given or taken that the old pass
would not; `cluster-faces` re-timed on the real command. Owner: People, with Views-AI for Suggest.
Size: one or two branches.

### Phase 3: Faces, one pass: the detector and the recogniser (the schema, migration 33)

The report planned a recogniser-only swap first. Phase 0 showed why that is not cheap: the alignment the new
recognisers need takes the five landmarks, which are not stored, so a "keep every box" swap runs the detector over
every photo anyway (about 0.78 s a photo with MTCNN, about 13 hours for photo_index). So the detector and the
recogniser are replaced in one pass over the photos (owner, 2026-10-16): one decode, the new detector (landmarks
included), alignment, the new recogniser, names carried over by box overlap.

**Schema (DECISIONS 2026-10-16).** `face_embeddings(face_id, model, vector)` with a trigger deleting a face's vectors
with the face; `faces` keeps identity only; the migration copies `faces.embedding` into the table under the FaceNet key
and drops the column. Destructive (full backup first, no undo); batched with the parked drops of `faces_pending` and
`idx_faces_name`; no other migration in flight; not before about 2026-11-15. Readers to move: `store/faces.py`,
`store/face_tags.py`, `services/identify.py`, `services/suggester.py`, and every `faces.embedding` read the sweep finds.

**First, a speed check (a spike, in a sandbox, before the design is fixed; measured 2026-10-18, section 7, "The
phase 3 spike").** The detector candidates (SCRFD, YuNet,
YOLOv8-face) and the recognisers (AdaFace, an ArcFace R50) timed per photo on the phase 0 sample, with their peak
card memory, and faces found per stratum beside MTCNN's. The estimate for a pass is `photos x (decode + detector +
recogniser)`; it is measured, not assumed. AdaFace's code is MIT, its weights carry their training data's
non-commercial terms like insightface's (section 7); insightface's weights are accepted by the owner for this personal library. The recogniser runs through torch where it can, with no second
runtime unless the detector needs one and the owner agrees.

**The pass.**
- Each old named box takes the new box with the highest IoU of 0.5 or more in the same photo, before any unnamed box is
  paired (owner, 2026-10-18: a named face beside an unnamed duplicate of it takes the detection though the duplicate
  overlaps it more), keeps its person, and gets its new vector; then each unnamed box the same; unmatched named faces are listed for review and never dropped; new unmatched detections
  become new `faces` rows; excluded faces stay excluded. A turned photo keeps the "turned" rule of DECISIONS
  2026-10-16: boxes are not converted here.
- One journaled change per folder, dry run by default, a small folder first and the owner checks it, backup first, run
  on a copy first, resumable by what `face_embeddings` already holds, never two models' vectors mixed in one read.
- The embedding norm is a quality score: low-quality faces are held out of clustering.
- The face model is a per-library setting; a library switches to the new model only when the gate below is met, and
  only once its pass has reached its faces (items 1 and 2 below).

**How it fails:** interrupted half way (each photo's faces change in one transaction; the run resumes); a photo
unreadable now (its faces keep their old vectors, reported); two libraries holding one folder (each library has its
own vectors); a library switched mid-run (a setting change journaled, the run finishes under the key it began with);
the card shared with Suggest or an index (one turn at a time).

**Gate.** On the named-face hold-out (read on unnamed faces too), the new model's precision at the owner's chosen
precision beats FaceNet's curve from section 7, at new thresholds read off the curve; every previously named face
still named or on the review list; the counts printed: matched, unmatched, new, skipped, failed, by library.

Owners: Data (migration), People (identity), Views-AI (detector, recogniser, card), Files for nothing written to a
file. Size: three merges (migration and readers; detector and recogniser behind the setting; the pass and calibration).

**Merge 2b, as built (2026-10-18).** The setting `faces.model` (`mtcnn-facenet`, the default, or `scrfd-adaface`;
locked, in its own group "face-model", "The face model", whose consequences say what switching does -- the face
detection group's are MTCNN's; the dialog offers it as a list of the rule's choices; a library holding no value reads as
the default). A library set to a model whose vectors it
cannot keep (`tagpup.services.settings.KEPT_FACE_MODELS`, FaceNet's alone) is refused every write of faces with one
sentence (`FACE_MODEL_NOT_KEPT`): Add in TagPup and TagTuner (`tagpup.services.libraries.add`) before the folder is
recorded or queued; the CLI's index before it writes or imports anything, asked of both its reads of the settings
(exit status 78; the server's queue says "Nothing was indexed: ..."); Suggest and Just look at `Runtime.begin`
(`FaceModelNotReady`, nothing built); and the warm-up, which builds nothing for it and says so at INFO, a refusal as
Suggest's are (`tagpup.jobs.suggestions.REFUSALS`). Identify reads the faces the library holds (since migration 33
it compares them by the library's model's vectors alone, so none until the pass gives them the new model's). A look that
writes nothing builds the new model, and only it, by `Runtime.look_at_faces`: held as a run is (a Suggest on the other set is refused
beside it, and it beside one), the card taken once at its first use and let go, the model with it, at `end()`.
Scenario V11, `tests/test_the_new_face_model_writes_nothing.py`.

**Merge 3 brief (the pass; after migration 33).**
- The crop is cut from the picture as stored (`ScrfdPicture.stored`) by the face's box, as every face's crop is
  (`images.face_crop_of`), by the model's `new_faces`: wherever a detection makes a new face (the index, Suggest, the
  pass), as built in item 3 below. (`ScrfdAdaFace.faces_in`, which gave no crop, is gone.)
- The name a detection is recorded under (`tagpup.services.faces.detector_of`, `faces_detected`) is the model's own
  (`detector(max_side)`): `{"model": "scrfd-adaface"}` for the new model, and MTCNN's settings spelled as before, so the
  names every stored MTCNN detection was written under stand (item 3 below).
- Read-ahead: the pass hands the model the bytes it has read ahead (as the index's read-ahead does for MTCNN), or it
  reads one photo at a time; the model does not read ahead of its own.
- `faces.detect_max_side` does not apply to the new model: it fits each photo into its two squares itself.
- The new model's vectors go only to `face_embeddings`, under its model key; never to `faces.embedding`.
- The pass extends `KEPT_FACE_MODELS` (and with it what `faces.model` can be written under), not migration 33
  (DECISIONS 2026-10-18, "AFTER MIGRATION 33"): until the pass, the refusal above stands.
- The pass runs while the library is still on FaceNet (the setting switches only at the gate), so every reader keyed by the
  library reads FaceNet's while it writes the new model's. It needs, in `tagpup.store.faces` as their one owner and named
  before they are built: a reader with an EXPLICIT key (what the new model's vectors already hold, to resume and to compare
  at the gate) and a journaled add-vector writer, which adds a vector row under the new key to an existing face
  (`journal.KEYS` has `face_embeddings` already). `tests/face_rows.add_vector_of_face` (INSERT OR REPLACE) is a
  fixture's, not that writer.
- The pass is not in `tagpup.web.lifecycle.under_way`, the one list a drain, Activity and the roots' moves read: run
  in the server, an update would not wait for it. So the pass is a process of its own (the CLI) with its own
  `Runtime`, the graphics card shared through `gpu.Card` as the CLI's index shares it, or it is listed in `under_way`
  before it runs in the server.

**Merge 3, slice A2, as built (2026-10-18): the pass's store and service, the model handed in.** `tagpup.services.face_pass.run`
(People's; the model Views-AI's), scenario P8 (`tests/test_the_face_pass.py`, the model stood in for by
`tests/stand_in_models.NewFaces`). The store's part is `tagpup.store.faces`: `vectors_under(conn, key, photo_ids)` (the reader
with an explicit key), `boxes_of`, `pass_photos` and the writer `give_vectors`, which records every row it adds as stored
(`journal.record_inserted`). `KEPT_FACE_MODELS` is not extended here; no CLI yet (slice B).
- The pass writes no `faces_detected` row: a photo is done when one of its faces holds a vector under the model's key, so
  MTCNN's found-none rows stand, and a photo the new model finds no face in writes nothing and is looked at again each run
  (at most 10,492 photos of photo_index, about 50 minutes a run, writing nothing: accepted).
- No new face under `face_pass.NEW_FACE_FLOOR` = 40 px on its shorter side (owner, 2026-10-18, DECISIONS): the detector's own
  floor is 15 px and one real folder gave 31 smudges under 40 px among 40 new faces. Only the making of an unmatched
  detection as a new face is gated; an old face a detection of any size matches still gets the vector. The detections left
  unmade are counted in `Folder.too_small` (the CLI's per-folder line). The done rule is unchanged: a photo whose
  detections are all under the floor and take no old face holds no vector, so it is looked at again each run like one with no
  face found (a count of detections, nothing written; a marker would be schema work, which DECISIONS 2026-10-15 holds). The
  floor is tuned in slice C against held-out named faces.
- What the journal holds: each vector about 2 KB of `change_rows`, a new face's crop about 6 KB more; about 0.4 to 0.5 GB for
  photo_index's vectors (accepted).
- A folder's change is called "face pass (<key>)": History and the MCP `history` show the operation without reveal, so it
  names no path; the folder is the summary's `folder`, in the library's row form, behind reveal, and is what `Folder.held`
  reads. A folder interrupted and resumed is two changes of one folder, each undone alone, each
  summary what was committed (added to in each photo's transaction). A change undone while the run goes on is not added to:
  the folder's next photo opens a new one.
- A dry run (the default) reads and counts the same and names the changes the folder already holds (`Folder.held`).
- The pass names nobody (owner, 2026-10-18; DECISIONS 2026-10-18 "THE FACE PASS NAMES NOBODY"): a new face is made
  unnamed, and a matched face keeps the name it had. Naming the new faces is Name faces from tags' job, once the library is
  switched to the new model after the gate. Named old faces are paired first (owner, 2026-10-18; "The pass" above).
- A face the pass matched holds one row of its change, its vector: naming it since does not refuse the undo (the name
  stays, the vector goes). A new face named since does refuse it ("faces N is not what change C left").
- Slice B: the CLI, its `undo` output naming the first ten reasons a folder's undo is refused, the card's turn, read-ahead.

**Merge 3, slice B, as built (2026-10-18): the `face-pass` command.** `tagpup_cli.py face-pass DIRECTORY... [--apply]
[--reveal]` (docs/SPEC_TAGPUP_CLI.md), scenario P8 from the command line (`tests/test_a_face_pass_from_the_command_line.py`).
- A process of its own: the model from `Runtime.look_at_faces`, the card taken at the first photo the model is asked about
  and let go at `end()`; not in `tagpup.web.lifecycle.under_way`, as the brief above chose.
- Read-ahead: `face_pass.run(..., reading=...)` reads the photos ahead with `tagpup.core.read_ahead`, the CLI giving it the
  index's threads, depth and bytes; the model's `picture` on those threads, `detect_and_embed` and every write on the
  caller's. What a picture holds is the model's to say (`ScrfdAdaFace.picture_bytes`). A service run given no `reading`
  reads one photo at a time through the same code.
- Ctrl+C: `run(..., stop=...)` is asked before each photo, so the photo under way is committed whole or not begun; the
  CLI's handler sets it (a second Ctrl+C stops at once, the photo's transaction rolled back). `Folder.looked` and
  `Folder.stopped` say how far it went.
- One snapshot a run, just before its first write (`run(..., before_first_write=...)`, called before each photo's write;
  the CLI's acts once a run, across its folders): a daily, unless one from today stands
  (`tagpup.services.snapshots.before_a_long_write`), and none for a run that writes nothing, so a no-op `--apply` rotates
  no daily out; that snapshot is the run's backup, and each folder's change is undone alone from the journal.
- The undo's ten reasons were already the journal's (`tagpup.store.journal.Refusal`: the first ten, "and N more"); slice B
  adds the scenario that shows it from the command line, nothing to the code.
- `KEPT_FACE_MODELS` is not extended: the pass writes the new model's vectors without the library being set to it, and
  extending it lets a library be switched to the new model, which is the gate's (slice C).
- A dry run asks the model about every photo not done, the card held, as long as the run but for the writes: it counts
  what --apply would write (slice A2, "A dry run ... reads and counts the same"); by design.
- The weight files are not in the checkpoints folder of this machine yet (counted 2026-10-18): the command refuses with
  the sentence of `missing_weights` before anything is done.

**Merge 3, slice C, as built (2026-10-20): the gate's scoring, read-only.** What it is: `tagpup/core/holdout.py` (the
hold-out and the curve, moved out of the phase 0 script), `tagpup.store.faces.under_both` (the named and unnamed faces
holding both models' vectors) and `pass_counts`, `tagpup/services/face_gate.py` (the scoring), `scripts/measure_face_gate.py`
(the program), scenario P9 (`tests/test_the_gate_reads_both_models.py`). It opens each library read-only, loads no model and
takes no turn of the card.
- Run: `.venv/Scripts/python.exe scripts/measure_face_gate.py --source data/kr-track.db --source data/renton_parkrun.db
  [--precision 0.95 ...] [--out <folder outside the repo>]`; about 1 s.
- Method (as section 7): the named faces, held out by folder, each person by their closest face; both models on the same
  faces, the ones holding both vectors; the cut read on a 0.005 grid of likeness and of margin, each 0 to 1, from the top.
  A row is "too thin" when fewer than 100 faces were evaluated or fewer than 4p/(1-p) named at the cut; a size band too.
- Two readings at each precision, said apart in the output. **Closed set**: the faces whose person has a face in another
  folder, precision = right / named among them; it compares the models (the verdict reads it) but is not what the owner
  would live with. **Open set**: every named face, a face alone in its folder too: its person is not in the gallery by
  construction, so every name given it is wrong; precision = right / (named of both), its own cut read on that curve,
  recall the named right of the faces that could be. A cut at the grid's lowest is said as "no cut: it names every face".
- Counted 2026-10-20 on kr-track and renton_parkrun (photo_index has not had the pass): 2,849 named faces hold both vectors
  (pooled), 2,498 evaluated on the closed set, 351 alone in their folder; the closed-set verdict "yes" in every row. The
  sizes use the old MTCNN box.

| Pooled precision | Closed set, by margin: cut FaceNet / AdaFace | Recall FaceNet / AdaFace | Its open-set precision FaceNet / AdaFace | Open set, by margin: cut FaceNet / AdaFace | Recall FaceNet / AdaFace |
|---|---|---|---|---|---|
| 0.95 | 0 / 0 (no cut: names every face) | 0.9616 / 0.9740 | 0.8431 / 0.8540 | 0.060 / 0.040 | 0.9195 / 0.9684 |
| 0.98 | 0.030 / 0.015 | 0.9452 / 0.9732 | 0.9084 / 0.9051 | 0.100 / 0.090 | 0.8683 / 0.9568 |
| 0.99 | 0.060 / 0.060 | 0.9195 / 0.9628 | 0.9531 / 0.9709 | 0.140 / 0.205 | 0.7874 / 0.9171 |

- On the open set at 0.99 FaceNet names 1,984 (17 wrong, 14 of them alone in their folder) and leaves 865 for review;
  AdaFace names 2,314 (23 wrong, 10 alone) and leaves 535. On the closed set at 0.99, 178 and 70 left for review, 23
  wrong each, and beside them 90 and 49 faces alone in their folder named, all wrong. By likeness at 0.99 the cut is
  0.755 / 0.435 on the closed set, 0.795 / 0.520 on the open set (FaceNet / AdaFace).
- Size bands (AdaFace, closed set, margin cut at 0.99; "thin" by the module's rule on what the band names):

| Shorter side | Faces evaluated | Recall |
|---|---|---|
| under 30 px | 5 | 1.0 (thin) |
| 30 to 39 px | 31 | 0.9032 (thin) |
| 40 to 49 px | 52 | 0.9615 (thin) |
| 50 px and over | 2,410 | 0.9635 |

- Recall if new faces under a floor were never made (AdaFace, closed set, 0.99), read two ways. Counting an old face under
  the floor as missed: floor 30 gives 0.9608, 40 gives 0.9496, 50 gives 0.9295. Among the faces at or above the floor:
  0.9627, 0.9634, 0.9635. The floor applies only to a detection no old face took (face_pass.NEW_FACE_FLOOR), so an old
  face under it is kept: the first is a bound, the second the recall of the faces the floor keeps.
- Unnamed faces holding both vectors: 3,721, after leaving out 36 a person called nobody by hand (3 on kr-track, 33 on
  renton_parkrun). They are held out by photo (no person, so no folder of theirs to leave out) while the cut is read by
  folder. At pooled 0.99 by margin FaceNet would name 1,557 on the closed-set cut and 704 on the open-set cut, AdaFace
  1,425 and 1,017.
- The margin grid runs to 1.0: 1,354 of AdaFace's 2,849 named faces have a margin above 0.5 (the highest 0.744), against
  13 of FaceNet's; extending it from 0.5 changed no reading at 0.95, 0.98 or 0.99.

**Merge 3, items 1 and 2, as built (2026-10-20): the cuts are the face model's, and a library may switch.**
- The likeness and margin a name is given and offered at are the face model's (`tagpup.core.clustering.Cuts`; `CUTS`,
  `cuts_of(model)`, asked by the key the vectors were read under, `store.faces.model_key`): two models' cosines are not on
  one scale. Every reader that named or offered by the old constants asks the cuts of its run's model.

| Face model | Name: likeness / margin | Offer: likeness / margin | From |
|---|---|---|---|
| `mtcnn-facenet` | 0.80 / 0.04 | 0.70 / 0.02 | the owner's choices of 2026-09-24 and 2026-10-17, unchanged |
| `scrfd-adaface` | 0.520 / 0.205 | 0.310 / 0.090 | slice C's pooled open-set reading at precision 0.99 (name) and 0.98 (offer), 2026-10-20 |

- Name faces from tags' counts are named for what they are, not for FaceNet's numbers: `like_them_enough_to_name` and
  `like_them_enough_to_offer` (were `like_them_from_0.80`, `like_them_from_0.70_to_0.80`).
- `KEPT_FACE_MODELS` holds both models. `settings.change` refuses a switch of `faces.model` to the new model while more
  than `UNPASSED_SHARE_ALLOWED` (5%, the owner's to change) of the library's faces are in photos the pass has not
  reached, before the consequences are asked; refused or not it says how many faces hold a vector under the model, how
  many named faces hold none (they leave the gallery of named faces until the pass reaches them) and how many are in
  photos not passed (`details["face_vectors"]`, from `store.faces.vector_counts` and `pass_counts`, read-only). A library
  with no faces switches; switching back to FaceNet is never refused. Counted 2026-10-20, read-only: kr-track 11,457
  faces, 10,246 with the new vector, 3 named without, 16 in photos not passed (0.14%: allowed); renton_parkrun 3,644,
  3,315, 0, 7 (0.19%: allowed); photo_index 187,784, none, 55,298, all (refused). The unmatched old faces of a passed
  photo (1,195 on kr-track) keep FaceNet's vector alone, by design: they are not counted against the switch.
- Left for items 3 and 4: the index and Suggest writing faces with the new model (`ScrfdAdaFace` answers neither the
  index's `picture(path, max_side=...)` nor Suggest's `detect_and_embed_faces` yet), and the switch's own scenario end
  to end. Scenario V11, `tests/test_the_new_face_model_writes_nothing.py`, is the switch now.

**Merge 3, item 3, as built (2026-10-20): the index, Suggest and the pass write faces with the library's model, whichever.**
- One surface, answered by `tagpup.ml.faces.FaceModel` and `tagpup.ml.scrfd_adaface.ScrfdAdaFace` alike, built by
  `tagpup.runtime` from the library's `faces.model`: `key` (what its vectors are filed under), `picture(path, max_side=)`
  (the processor's half, on a read-ahead thread), `picture_bytes(width, height, max_side=)` (what the read-ahead
  reserves), `detect_and_embed(picture)` (the card's half), `new_faces(detected, picture)` (of what it found, the faces a
  detection makes new where no old face takes them, each with its crop), `detect_and_embed_faces(path, max_side=)` (the
  three in one, Suggest's) and `detector(max_side)` (the name its detections are recorded under). The library's cap
  (`faces.detect_max_side`) is MTCNN's: the new model takes it and does not use it, its two squares being its own.
- The floor of a new face has one owner, `scrfd_adaface.NEW_FACE_FLOOR` (40 px on the shorter side), applied by the
  model's `new_faces` wherever a detection makes a face: the index, Suggest, and the pass for a detection no old face
  takes. FaceModel's `new_faces` makes every face it found, as before.
- Scenario P10, `tests/test_a_switched_library_indexes_and_suggests_with_its_model.py`: a library its pass reached is
  switched; Name faces from tags reads the new vectors by AdaFace's cuts; a new photo is indexed through the CLI (the face
  under the floor not made, the one made holding the new model's vector alone, a crop, and `{"model": "scrfd-adaface"}`
  as its detector name); Suggest detects with the new model and offers a person at 0.45, which FaceNet's cuts would not;
  switched back, FaceNet reads its own vectors again and neither switch wrote one.
- Open: the panel shows a suggestion from `suggesting.OFFER_A_TAG` (0.60), read on FaceNet's likenesses, and a face's
  offer is scored by its likeness, so under the new model an offer between 0.310 and 0.60 is made and not shown (a
  finding of this branch).

### Phase 4: Matching, with the new model

- **4a Time-nearest matching.** A new face is matched against the time-nearest named faces by Date Taken, not one
  centroid, and needs a margin over the second-best person (2b, recalibrated for the new model). This is the answer
  to children changing across years, independent of model.
- **4b Incremental assignment.** A folder that arrives has its faces matched against known faces by neighbour query and
  joins a person within threshold, or waits; a full re-cluster is the exception, not the response to every event.

**Gate.** The phase 3 curve, read per age gap where Date Taken allows, on the margin curve and a hand-checked sample
of unnamed faces too (#1532); the owner's check of a sample folder. Owner:
People, with Views-AI for the vectors.

### Phase 5: The photo model (one re-embed)

**Cost, measured:** about 0.2 s a photo for decode, pad and CLIP forward at 512 (0.118 s at 224 including the decode), so
about 3.3 hours for photo_index with today's loop and less after phase 1. The faces are not part of it.

**Decide, in this order, with the phase 0 numbers:** resolution (224 against 512: no loss on kr-track in phase 0, to
be repeated on the many-face photos of photo_index, where small faces make 224 most likely to lose something), then
the model (the candidates, cheapest change first: DFN5B ViT-H-14 same code and dimension; SigLIP 2 B/16 or L/16 best
quality per cost; dimensions per model, so nothing may assume 1024).

**Build.** The model and its settings are a library setting; vectors under a new `embeddings.model` key beside the old;
the tag-prompt cache (`tag_embeddings`) recomputed under the new model; Suggest's neighbour cut and any thresholds
re-derived on the held-out set; a SigLIP sigmoid score tried for multi-label tags only if the phase shows it is better.
The text tower alone stays resident in the server between runs; the image tower loads for an index. The re-embed is a
CLI run (long indexes go through the CLI), resumable by what `embeddings` holds under the new key, dry-run first, on a
copy first.

**How it fails:** a re-embed killed at 40,000 photos (Suggest keeps using the old key until the library switches; a
partial new key is never read); the card shared with Suggest (one turn at a time); disk (a second vector set: about
240 MB at float32 for 1024 dimensions for photo_index, counted before the run).

**Gate.** Suggest on the held-out photos is better than today's by an agreed margin, at the new thresholds; the old
vectors are kept until the owner retires them. "Search by what is in the picture" (decided 2026-10-07): ask the Views-AI
owner what exists today before scoping it, and plan it only as the reading of these vectors.

Owner: Views-AI. Size: two merges (setting and model loader; re-embed and recalibration).

### Phase 6: Bake into the photo files (its own project, last, one pass)

Decided 2026-10-07 (the current model's vectors and every detected face's region into the file's XMP), advised against
starting in October (health review 9.2). **Owner, 2026-10-16: do it after the final design and research are all
implemented, so the files are updated only once.** That fixes the order: the face and photo models are final (phases 3
to 5 run on the live libraries, old models retired or settled) before a file is written, so the baked vectors are the
final model's and never rewritten for a model change.

**Its purpose is transport** (owner, 2026-10-16): the XMP carries the finished GPU work, so a folder can be analysed on
the local desktop (the card is there), copied to the server, and added to the library there without the card, the
library reading the vectors and regions instead of computing them. This sets the shape of the project:
- **Two sides, one format, every machine both** (owner, 2026-10-16: the server can also write). Any machine that has
  the card analyses a photo and bakes the final model's vectors and every detected face's region into its XMP (the
  *write side*). An index on any machine first looks at the file: if its baked model key is compatible (equal to the
  library's current model key) it takes the vectors and regions and the card is never asked (the *read side*, the
  streamlined path); if the bake is missing, stale or from another model, the machine computes the photo itself and
  bakes it, or, with no card, queues it. So the bake is a shortcut and never a requirement: the desktop-then-server flow
  is the fast one, the server alone is the slow one, and both end with the same file. Build and test the read side
  first, against hand-made files.
- **Write before the copy, not after.** A new folder is baked on the desktop before it goes to the server, so a master
  file is written once, as it arrives, and an existing master file is only ever rewritten by the one backfill pass over
  the library.
- **A baked vector is trusted only while it is the picture's.** It is keyed by model key (so a model change adds and
  never replaces), carries the fingerprint of the picture (a rotation or edit made elsewhere reads as "compute again";
  #1529 is the lesson of a vector kept past a change in how the picture is read), and survives the file's own metadata
  writes (the existing `restamp` rule for an app write; a write by another program changes the stamp and the bake is
  checked by its fingerprint, not its mtime).
- **Names are not carried by the bake.** A region and a vector say where a face is and what it looks like; who it is
  comes from the person tags in the photo's keywords through the existing naming-from-tags path, and "a name alone is
  nobody" (DECISIONS 2026-10-15) still holds on import.
- **Compatible means one key.** The same model key a library's `embeddings` and `face_embeddings` use decides it, so
  there is no second notion of compatibility. A machine with no card reads compatible bakes and queues the rest (its
  row says so; the owner sees the count).

**Not designed here.** It writes to the master copy of about 60,000 irreplaceable files for the backfill, depends on
the orientation and fingerprint work, and carries about 13 KB of XMP a photo (JPEG's 64 KB XMP segment is the limit).
It begins only when: phases 3 to 5 have run on the live libraries; the read-only census of regions already in the files
is done; and the owner opens it. Its own design document, planned by the Files and People owners (with Integration for
the desktop-to-server flow), comes first. Whatever it writes goes through `file_changes.write_fields`.

## 6. Decisions the plan needs, and who makes them

| Decision | When | Who |
|---|---|---|
| Turn the detector downscale on (1a), and at which cap? | DECIDED 2026-10-16 (owner): keep it OFF until the phase 3 detector; revisit only if a full re-index is needed sooner (at 3,000 it saves about 0.4 s a photo and costs 1,184 small unnamed faces in the sample). The counts are in phase 1a. On, a forced re-index (`--force-reembed`, which replaces a photo's faces in any case) does not find again a named face smaller than the cap's smallest face: on photo_index 5, 8 and 23 of 35,425 named faces at 3,000, 2,048 and 1,600 (5, 27 and 53 by the box's shorter side). photo_index holds no `faces_detected` row at all, so each of its photos with no face is detected again by the first Suggest that looks at it (which records it), with a cap or without (pre-existing, a finding of its own) | Owner (the person) |
| Does a failed file still cost a run (1d), and is a skip-list needed? | Phase 1, counted | Owner of Files |
| Is the warm indexer worth it (1e)? | After 1b is timed | Views-AI and Integration owners |
| How the 223 turned photos' vectors are repaired (1f) and how a stale one is found | Phase 1 | Views-AI and Data owners |
| The margin and likeness thresholds for naming, interim (2b) | Phase 2, read off the curve | Owner (the person) |
| Which detector and recogniser (3, after the speed check) | DECIDED 2026-10-18 (owner): SCRFD 10g in torch at 1280 and 640, AdaFace IR-101, no second runtime (DECISIONS 2026-10-18) | Owner (the person) |
| The owner's chosen precision for naming a face | Phase 3 gate | Owner (the person) |
| Resolution, then photo model | Phase 5, with phase 0 numbers | Owner (the person), on the owners' evidence |
| When to retire old vectors | After a model has run for a while | Owner (the person) |
| When phase 6 opens | After phases 3 to 5 are final (owner: files written once) | Owner (the person) |
| Phase 6 design questions: where the desktop-to-server copy happens, whether the server's library indexes the server path or a desktop copy (the master is on the server today), the fingerprint, the backfill order | Phase 6's own design | Files, People and Integration owners; the owner (the person) decides |

## 7. Measured numbers (phase 0, 2026-10-16)

Measured in sandboxes on copies of the libraries, on the owner's RTX 3080 (10 GB) with other programs holding 0.9 to
1.2 GB of it, 2.4 to 7.4% processor busy. Raw JSON (counts and numbers only) is kept outside the repository by the
worker that ran it. The column "After" is filled as each phase merges.

**The index rows are the index alone.** Since 2026-10-16 `index` clusters the whole library's faces after an index that
recorded a photo (DECISIONS 2026-10-16, #1544; 202 s on photo_index), so a whole run's time carries those seconds
whatever the folder's size. `scripts/measure_pipeline_stages.py` times the index to the clustering's line
(`tagpup.services.indexing.CLUSTERING_STARTS`; in `stages` a timer on the CLI's `_cluster_after_index`) and gives the
clustering's seconds apart, so the rows below are measured again like for like; the clustering's own row is
`cluster-faces`.

| Measure | Today | After | Notes |
|---|---|---|---|
| Index, an event folder, seconds a photo | **1.87** (kr-track folder of 61 photos, 114.1 s) | **1.68** (1b; 1.94 before on the same day, the index alone) | Matches 1.9 of 2026-09-27; that log is gone, so the folder was the one nearest 60 photos. |
| Index, many-face sample, seconds a photo | **1.24** whole CLI run (299 photos, 369.5 s) | **1.05** (1b; 1.22 before on the same day, the index alone) | By stratum (mean s): none 0.60, 1 to 3 0.74, 4 to 9 0.91, 10 or more 1.55, worst 10 photos 3.79. |
| Per stage, mean s a photo | decode for CLIP 0.071; pad and resize 0.072; CLIP forward 0.058; identity write 0.037; decode for faces 0.061; **MTCNN 0.784**; crop and tensor 0.011; crop JPEG 0.004; recogniser 0.032; faces write 0.021; photo rows 0.003 | | MTCNN is 69%. The 759-face photo 8.97 s (7.0 MTCNN); a 193 MP photo 30.7 s (27.4 MTCNN). |
| Fixed cost of an index run | **about 15 s** plus the metadata read (10.1 s for 299 photos) | | Process start 0.14, imports 4.7, CLIP load 9.8, face models 0.36. First CLIP forward 0.26 s against 0.058 steady. Suggest's vector load is a separate 8.2 s. |
| `cluster-faces`, whole library | **2,060 s (34.3 min)** | **202 s** (2a; 2,113 s before on the same copy, the same day); **171 s** (2b, 197 s before on the same copy; from scratch 172 s against 209 s: a round's pairing reads its faces from one table a block of photos) | Before: 132 s grouping (DBSCAN, every core), 76 s the five rounds, about 1,896 s the last pass, card idle (#1530). After: 132 s grouping, 48 s the rounds, 12 s the last pass, 4 s reading and saving; the names identical face by face on the three libraries. |
| Suggest, a folder, cold / warm | **not measured** | | The script is stale (#1531). |
| Identify Faces / New Person | Ignore Cluster: first faces on screen 36.5 s, then click to usable median 0.09 s. New Person: first faces 35.5 s, first click 2.46 s, then median 0.51 s | 2b, measured again the same day, trunk first, 3 runs each: Unknown Faces grid first faces 61.1 s trunk, 58.0 s 2b (10,902 cards); one person's grid 10.3 s both (its 'likely' tab 521 cards on trunk, 112 with the margin) | Headless browser, sandbox copy; the first-faces wait is a start-up cost the owner meets on opening the page. |
| Card use during an index | many-face sample: 13.6% of samples at 0%, 23.4% under 10%, mean 30.9%. Event run: 13.8%, 21.8%, mean 20.3% | | `nvidia-smi` every 500 ms. The indexing process used 18.9% of 16 logical processors (about 3 cores). |
| Card memory | peak **9,982 of 10,240 MiB** (many-face sample), 8,159 MiB (event) | | MTCNN at full resolution on the largest photos (#1533). |
| Photo vector, batched against one at a time | cosine 0.999998 or more at batches 8 and 16 | | 201 photos. |
| Photo vector, stored against re-run | 197 of 201 at 0.999996 or more; **the 4 turned photos 0.81 to 0.94** | | #1529. |
| Suggest, 224 against 512 (kr-track, 117 tagged photos) | 224: precision 0.911 on 224 offered, 204 right; 512: 0.895 on 228 offered, 204 right; recall 0.50 both; top 5 0.887 and 0.879 | | One library, coarse tags. Embedding at 224 takes 0.118 s a photo with the decode. |
| Named-face precision, FaceNet vectors, photo_index (35,425 named, 281 people), held out by folder | By likeness: 0.9830 at 0.40, 0.9837 at 0.70, 0.9846 at 0.80, 0.9858 at 0.85, 0.9909 at 0.90, 0.9960 at 0.95 (faces 35,214 down to 4,807). By margin: 0.983 at 0, 0.9954 at 0.02, 0.9983 at 0.04, 0.9991 at 0.06, 0.9992 at 0.10, 1.0 at 0.20 (faces 35,214 down to 9,986) | | Read on faces already matched; the hard faces are among about 152,000 unnamed (#1532). |
| photo_index counts | 59,519 photos (59,518 readable, 1 gone), 187,784 faces, 223 turned photos | | Megapixels: median 10.0, p10 3.1, p90 20.0, max 192.9; 418 photos at 30 MP or more. |

### The phase 3 spike: face detectors and recognisers (2026-10-18)

The script `measure_face_models.py` (removed after the run: it built models and took the card's turn outside the runtime, which the guards forbid; it is in git at commit 33deed9d) on phase 0's sample drawn again from photo_index (299 photos, the 10 with the most
faces among them as "worst"), each candidate in a process of its own in a sandbox venv (onnxruntime-gpu 1.22, OpenCV,
onnx2torch beside the main venv's torch 2.5.1), one at a time, holding the card's turn; the same card, other programs
holding 0.6 to 1.6 GB of it. Seconds are means a photo, the card synchronised, after an untimed warm-up photo; "card"
is the run's peak (nvidia-smi every 250 ms) over what the card held before it. Faces are kept by the indexer's rule
(15 pixels or more each side). The baselines: MTCNN as the indexer runs it (the library's settings, full size), and
the photo's stored faces that are named and not excluded (339). The pass is the library's mix, not the sample's:
photo_index's 59,518 readable photos by stratum (none 10,493; 1 to 3 34,804; 4 to 9 10,798; 10 or more 3,413; worst
10) times each stratum's decode and detection seconds.

| Detector (long side) | Runtime | Detect s | Card MiB | Faces found: none / 1-3 / 4-9 / 10+ / worst | MTCNN's 5,667 found again, IoU 0.5 / 0.3 | Named found, of 339 | Pass, decode + detect |
|---|---|---|---|---|---|---|---|
| MTCNN (full size) | torch | 0.679 | 8,580 | 0 / 117 / 401 / 1,969 / 3,180 | (the baseline) | 339 | 8.81 h |
| SCRFD 10g, 640 | onnxruntime | 0.039 | 339 | 11 / 101 / 342 / 1,471 / 369 | 2,090 / 2,106 | 317 | 1.67 h |
| SCRFD 10g, 1280 | onnxruntime | 0.065 | 868 | 16 / 107 / 426 / 1,955 / 2,134 | 3,831 / 3,864 | 323 | 2.02 h |
| **SCRFD 10g, 1280** | **torch (onnx2torch)** | **0.036** | **567** | 16 / 107 / 426 / 1,955 / 2,135 | 3,832 / 3,865 | **323** | **1.53 h** |
| SCRFD 10g, 1920 | onnxruntime | 0.092 | 1,369 | 16 / 93 / 424 / 2,084 / 3,817 | 4,786 / 4,808 | 313 | 2.43 h |
| SCRFD 2.5g, 640 | onnxruntime | 0.027 | 276 | 10 / 101 / 322 / 1,370 / 256 | 1,885 / 1,900 | 312 | 1.36 h |
| SCRFD 2.5g, 1280 | onnxruntime | 0.054 | 487 | 11 / 104 / 408 / 1,793 / 1,741 | 3,502 / 3,528 | 321 | 1.85 h |
| YuNet, 640 | OpenCV, processor | 0.012 | 0 | 2 / 52 / 132 / 300 / 0 | 484 / 484 | 196 | 1.11 h |
| YuNet, 1280 | OpenCV, processor | 0.032 | 0 | 2 / 67 / 188 / 682 / 24 | 959 / 959 | 269 | 1.43 h |
| YOLOv8n-face, 640 | onnxruntime | 0.027 | 275 | 13 / 102 / 326 / 1,379 / 375 | 2,020 / 2,039 | 315 | 1.39 h |

Decoding (the indexer's `images.opened`) is 0.067 to 0.075 s a photo for every candidate (MTCNN's run 0.097), so
after the change the decode is the larger half of the detector's pass. Detection time hardly moves with the faces in a
photo for SCRFD (0.034 to 0.048 s in torch from none to worst) where MTCNN's goes from 0.31 to 2.59 s.

What the numbers say:
- **The torch conversion is the same detector, faster.** SCRFD 10g at 1280 through onnx2torch finds the same faces as
  in onnxruntime (4,639 against 4,638; the same 323 named) at 0.036 s against 0.065, in 567 MiB against 868. **No
  second runtime is needed**: the detector runs in torch like everything else.
- **Every miss of a named face is a face not found, not a box drawn to other edges**: each detector finds the same
  named faces at IoU 0.3 as at 0.5.
- **SCRFD has a blind spot at each end of its scale.** At 640 the named faces it misses are small (22 missed, median
  side 42 px, largest 89); at 1280 and 1920 it also misses faces that fill the frame (16 missed at 1280, the largest
  1,690 px; 26 at 1920, median 842 px), which is why 1-3 faces a photo is found less at 1280 (59 of 64 named) than at
  640 (62 of 64). The two scales together (640 and 1280, merged by non-maximum suppression, counted from the runs
  above) find 326 of the 339 named, the 13 missed all small (median 42 px, largest 89), and 3,840 of MTCNN's faces.
- **MTCNN's "worst" photos are mostly not faces SCRFD agrees with**: 3,180 MTCNN faces in 10 photos against 2,135 for
  SCRFD at 1280 (1,771 shared), and 8 named among them. The faces MTCNN finds that no SCRFD finds are where the
  pass will make unnamed rows disappear; the pass's rule (named faces never dropped, listed for review) covers the 8.
- **YuNet** misses too much (196 and 269 of 339 named) and **SCRFD 2.5g** and **YOLOv8n-face** are no faster than
  SCRFD 10g in torch while finding fewer.

| Recogniser | Faces from | Align s | Recognise s | Card MiB (torch's own peak) | Mirror cosine | Two photos' cosine | Pass with its detector |
|---|---|---|---|---|---|---|---|
| FaceNet (today) | MTCNN | 0.014 (crop) | 0.025 | 4,439 (1,767) | 0.945 | 0.123 | 9.27 h |
| ArcFace R50 (w600k_r50), onnxruntime | SCRFD 10g 1280 | 0.004 | 0.045 | 9,009 (onnxruntime's arena) | 0.924 | 0.041 | 2.56 h; 2.07 h with the torch detector |
| **AdaFace IR-101 (WebFace12M), torch, half precision** | SCRFD 10g 1280 | 0.004 | **0.025** | 6,648 (2,268) | 0.930 | 0.040 | 2.31 h; **1.81 h with the torch detector** |

The sanity checks say each recogniser is read right: a face and its mirror image are alike (0.92 to 0.95) and the
first faces of two different photos are not (0.04 to 0.12; FaceNet's vectors sit closer together than the two newer
models'). How well each tells people apart is not measured here: that is the gate, on the named-face hold-out.
The recognisers' card memory is the whole photo's faces in one batch (the worst photo has hundreds); the build
batches faces in fixed chunks, so that peak is not the model's.

For photo_index, decode, detection, alignment and recognition together: **about 1.8 hours (0.110 s a photo)** for
SCRFD 10g at 1280 in torch with AdaFace, **at most about 2.5 hours** with the 640 scale added (its 0.039 s in onnxruntime,
not timed in torch), against **9.3 hours (0.561 s a photo)** for MTCNN and FaceNet measured the same way. Not counted:
writing the rows and the vectors (phase 0's faces write: 0.021 s a photo).

**Licences.** facenet-pytorch (MTCNN, FaceNet): MIT code, weights trained on VGGFace2. insightface's model packs
(SCRFD 10g and 2.5g, ArcFace R50 `w600k_r50`): MIT code, the weights for non-commercial research only (accepted by the
owner for this personal library, above). YuNet (OpenCV Zoo): MIT. YOLOv8n-face (lindevs): AGPL-3.0, Ultralytics'
terms. AdaFace: MIT code, but the IR-101 weights are trained on WebFace12M and its model card says to follow the
training data's licence, which is non-commercial research too: the "MIT licence" in the plan above is the code's, and
AdaFace's weights are in the same class as insightface's.

**Recommendation (for the owner's choice, section 6).** Detector: **SCRFD 10g, run in torch** (converted from
insightface's file by onnx2torch), at 1280 with a second look at 640 merged by non-maximum suppression, so that both
the small and the frame-filling faces are found; landmarks come with it, for the alignment. Recogniser: **AdaFace
IR-101** in torch, half precision: the fastest of the two candidates here, torch only, and the plan's preferred model; ArcFace R50 needs onnxruntime and is slower. **No second runtime**: everything runs in torch, and
onnxruntime stays in the sandbox. Left for the build: whether onnx2torch is needed at run time or the converted
detector is saved once as a torch file; the 640 scale timed in torch; the faces batched in chunks.

**As built (2026-10-18, `tagpup/ml/scrfd_adaface.py`, built by nothing yet).** The converted detector is traced once
for each square (it cannot be traced for every size at once) and saved as TorchScript by
`scripts/make_scrfd_torchscript.py`, so the app needs torch alone and onnx2torch is needed only to make the files. Timed
in torch on the card (the turn held, nothing else on it) on the sample's 10 worst photos, found again read-only in
photo_index by their faces and size, after a warm-up round: **detection at 640 is 0.0064 s a photo and at 1280
0.0148 s** (the model alone, the square on the card to the decoded boxes), torch's own peak 388 MiB; on the processor,
decoding is 0.074 s and fitting both squares 0.053 s (the 640 square scaled from the 1280 one, which halved it). The
faces found against the spike's on those photos: 2,070 at 1280 against 2,135, 336 at 640 against 369 (Pillow's scaling
here, OpenCV's area scaling in the spike), 2,081 merged. The whole of a worst photo (both squares, the merge, the
alignment and AdaFace in chunks of 32 for about 210 faces) is 0.25 s. So the second scale costs about 0.006 s on the
card and 0.02 s on the processor a photo, not the 0.039 s the onnxruntime run suggested.
Measured by a throwaway script in the worker's scratch folder (not kept, so not repeatable from the tree): it took the card's turn itself and read the weights from its own folder, converted by `scripts/make_scrfd_torchscript.py` from the spike's `det_10g.onnx`; the three weight files are not in the owner's checkpoints folder.

## 8. Risks

| Risk | Phase | Handled by |
|---|---|---|
| A faster path changes a vector slightly | 1 | Cosine 0.999 on 200 photos before any merge; `draft()` only at 2 times the target |
| A faster path hides a damaged file | 1 | The damage check kept and tested on the three shapes |
| The detector downscale loses faces | 1a | Off by default; counts by stratum and named faces lost reported before the owner turns it on |
| The card runs out of memory beside the desktop apps | 1, 3 | Peak memory reported in every exit; the downscale; batches sized to the free card |
| A name lost in a migration or a swap | 3 | Old vectors kept; boxes carried by IoU; unmatched named faces listed, never dropped; backup; copy first |
| The new detector is no faster than MTCNN | 3 | The speed check comes before the design is fixed; the plan is re-read if it fails |
| A threshold carried to a model it does not fit | 2, 3, 4, 5 | Thresholds read off the hold-out curve per model, on unnamed faces too |
| A stale vector kept past a change in how photos are read | 1f, 5, 6 | `repair-vectors` now; the fingerprint in the model key at phase 6 |
| Peak card memory 9,982 of 10,240 MiB on the phase 0 sample, MTCNN at full resolution on the largest photos, with the desktop apps holding up to 6 GB (#1533): by design until phase 3 | 1, 3 | The 1a cap exists and stays OFF (owner, DECISIONS 2026-10-16); 1b's prefetch bounds the bytes it holds; phase 3 replaces MTCNN. Until then the owner closes the desktop apps for a large index |
| Two heavy jobs on one card | all | The GPU turn; no two test runs at once |
| The migration is judged by feel | 3 | Phase 0 timings and counts before and after |
| Scope creep into the files | 6 | Phase 6 opens only by the owner, with its own design |

## 9. Keeping this current

When a phase merges: set its status here, fill the "After" column of section 7 with the same scripts, and add the
measured result to the domain document ([domains/views.md](domains/views.md), and [domains/people.md](domains/people.md)
for faces). A decision the plan needs goes into [DECISIONS.md](DECISIONS.md) as a dated row, with this document linked,
not repeated.

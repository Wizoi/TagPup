# The AI pipeline: design and plan

The one place for how TagPup's models are to be run and replaced, in phases. It replaces the three places the design
was spread over (the research report, the owner's decisions of 2026-10-07 and the health review's section 9.1); those
stay as the record, this is what is followed. Owner for the domain: Views, search and AI (models, GPU turn, Suggest,
vectors), with People for faces and Data for the schema ([OWNERS.md](OWNERS.md)).

Status (2026-10-17): **phase 0 done** (measured, section 7); the plan below is reordered by what it showed (owner
accepted, 2026-10-16). **Merged:** 1f (`repair-vectors`, applied on photo_index 2026-10-16: 223 vectors made again) and
2a (`cluster-faces` 2,113 s to 202 s, names unchanged). **Built, off:** 1a (the detector downscale, the setting
`faces.detect_max_side`, measured below; turning it on is the owner's). **Built:** 1b (the index reads the next photos
ahead: 1.94 to 1.68 s a photo on an event folder, measured below). **Measured, not built:** 1c (after 1b the card waits for its next photo 1.6% of the index on the many-face sample, 0.2% on the event folder). **Built:** 2b (a name on likeness alone needs a margin over the next person, 0.04 to name and 0.02 to offer; names given from now on only, DECISIONS 2026-10-17). **Next:** 2c. Update a phase's status
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
- **1d Wasted runs.** Counted first: does a failed file still cost a run (14 truncated files cost 7 runs in
  2026-09-26 to 28, before #1089)? If yes, record what failed beside the file's stamp in an existing place; a new table
  waits for phase 3's migration.
- **1e Warm indexer (decide after 1b).** Small indexes (1 to 50 photos) use a long-lived worker holding the models
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
- **2c Density.** Try `min_samples` 3 to 5 against the named faces (79% of faces are noise at today's cut). Suggest's
  one `search(k=15)` a photo becomes a batch.

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

**First, a speed check (a spike, in a sandbox, before the design is fixed).** The detector candidates (SCRFD, YuNet,
YOLOv8-face) and the recognisers (AdaFace, an ArcFace R50) timed per photo on the phase 0 sample, with their peak
card memory, and faces found per stratum beside MTCNN's. The estimate for a pass is `photos x (decode + detector +
recogniser)`; it is measured, not assumed. AdaFace (MIT licence) is the cleaner choice; insightface's weights are
accepted by the owner for this personal library. The recogniser runs through torch where it can, with no second
runtime unless the detector needs one and the owner agrees.

**The pass.**
- Each old named box takes the new box with the highest IoU of 0.5 or more in the same photo, keeps its person, and
  gets its new vector; unmatched named faces are listed for review and never dropped; new unmatched detections
  become new `faces` rows; excluded faces stay excluded. A turned photo keeps the "turned" rule of DECISIONS
  2026-10-16: boxes are not converted here.
- One journaled change per folder, dry run by default, a small folder first and the owner checks it, backup first, run
  on a copy first, resumable by what `face_embeddings` already holds, never two models' vectors mixed in one read.
- The embedding norm is a quality score: low-quality faces are held out of clustering.
- The face model is a per-library setting; a library switches to the new model only when the gate below is met.

**How it fails:** interrupted half way (each photo's faces change in one transaction; the run resumes); a photo
unreadable now (its faces keep their old vectors, reported); two libraries holding one folder (each library has its
own vectors); a library switched mid-run (a setting change journaled, the run finishes under the key it began with);
the card shared with Suggest or an index (one turn at a time).

**Gate.** On the named-face hold-out (read on unnamed faces too), the new model's precision at the owner's chosen
precision beats FaceNet's curve from section 7, at new thresholds read off the curve; every previously named face
still named or on the review list; the counts printed: matched, unmatched, new, skipped, failed, by library.

Owners: Data (migration), People (identity), Views-AI (detector, recogniser, card), Files for nothing written to a
file. Size: three merges (migration and readers; detector and recogniser behind the setting; the pass and calibration).

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
| Which detector and recogniser (3, after the speed check) | Phase 3 spike | Owner (the person), on the owners' evidence |
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

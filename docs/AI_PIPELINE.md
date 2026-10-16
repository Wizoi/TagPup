# The AI pipeline: design and plan

The one place for how TagPup's models are to be run and replaced, in phases. It replaces the three places the design
was spread over (the research report, the owner's decisions of 2026-10-07 and the health review's section 9.1); those
stay as the record, this is what is followed. Owner for the domain: Views, search and AI (models, GPU turn, Suggest,
vectors), with People for faces and Data for the schema ([OWNERS.md](OWNERS.md)).

Status: **designed 2026-10-16, nothing built.** Update the status line of a phase when it merges.

## 1. Where this comes from

| Source | What it holds | Standing |
|---|---|---|
| [reports/TagPup AI pipeline optimization.md](../reports/TagPup%20AI%20pipeline%20optimization.md) and its `research_notes/` | The research: what the stack does, the ranked changes, the model comparisons, the sources. Written against trunk `04bb0e6`; line numbers and two items are stale (section 3). | Archive. Read for the evidence; do not plan from its list. |
| [DECISIONS.md](DECISIONS.md) 2026-10-07 (row 47) and [history/ARCHITECTURE_faces_and_naming.md](history/ARCHITECTURE_faces_and_naming.md), "The owner's decisions for the three big projects" | The owner's choices: plumbing first, replace the face models (licence accepted), replace CLIP with SigLIP 2, search by what is in the picture, bake the current model's vectors and face regions into the file. | Decisions. Not reopened without a new fact. |
| [reports/Project health review 2026-10-09.md](../reports/Project%20health%20review%202026-10-09.md), 9.1 and 9.2 | Start only the plumbing slice; the model swap waits; baking vectors into files is its own project. | Advice, adopted below. |
| [DECISIONS.md](DECISIONS.md) 2026-10-16, "THE FACE VECTOR GETS A MODEL KEY" | The schema: a `face_embeddings` table, migration 33, not before about 2026-11-15. | Decision. |

## 2. Goals, and what is not a goal

1. **Indexing and Suggest are fast enough that a re-embed is an afternoon, not a day and a half.** At the one measured
   rate (1.9 s a photo, 60-photo run, 2026-09-27) the 68,460 photos take about 36 hours.
2. **Faces are found and recognised better**, above all small, side-on and growing children, without any named face
   losing its name.
3. **Models are a per-library setting and their vectors are versioned**, so a change adds and never replaces, and the
   old result stays until the owner retires it.
4. **No claim of "faster" or "better" without a number** measured on a user action in a sandbox, baseline first
   ([DEVELOPMENT.md](DEVELOPMENT.md), "Performance work").

Not goals: ONNX/TensorRT export, `torch.compile`, GPU JPEG decode (each costs more to install on Windows than it
returns; research report, "Ten changes"), approximate search (Flat is exact and 10 ms at this size), a second
runtime for the face models.

## 3. The pipeline today, recounted against HEAD (2026-10-16)

The research report's ranked list, with what has changed since:

| # | Change | State on HEAD |
|---|---|---|
| 1 | Decode each photo once | **Open.** `images.opened` reads the file once into memory, but CLIP decodes it upright (`ml/clip.py`) and faces decode it again as stored (`ml/faces.py`): two decodes. |
| 2 | `Image.draft` before the first pixel read | **Open.** No `draft` anywhere in `images.py`. |
| 3 | Resize before padding to a square | **Open.** `images.pad_to_square` takes the full-resolution picture. |
| 4 | Downscale before MTCNN | **Open.** Changes which faces are found: behind a setting. |
| 5 | Prefetch (threads decode ahead of the GPU) | **Open.** The CLI indexer is a serial loop. |
| 6 | Batch CLIP images and tag prompts | **Open.** |
| 7 | `inference_mode`, `cudnn.benchmark` | **Half done.** `inference_mode` is in `clip.py` and `faces.py`; `cudnn.benchmark` is not. |
| 8 | Skip the vector-index load when indexing nothing; remember files that failed to decode | **Half done.** #1089 stops an index that finds nothing from importing a model's library. Remembering failures is open and may need a place to keep them (see 1c). |
| 9 | Hand small indexes to the warm models, or keep a warm indexer | **Open, deferred** (phase 1d). It touches the one-process GPU rule. |
| 10 | Crop encode off the GPU loop | **Open.** |

Two things the report did not know and the plumbing must respect: `images.opened` detects a damaged or zero-tailed file
and raises `Unreadable` (a `draft()` read must still reach the full decode that proves the file whole), and the index
no longer loads the model library for a run that has nothing to do, so a skip-list is only worth building if the
baseline shows failed files still cost a run.

## 4. Principles for every phase

- **Never two re-embeds.** The model key of a photo vector includes the model, the weights, the framing and the image
  size, so any of them re-embeds all photos. The resolution question (224 or 512) is measured in phase 0 and decided
  with the model in phase 5; one re-embed serves both.
- **The photo files are written once** (owner, 2026-10-16): phases 1 to 5 change the database only and never touch a
  photo file for the sake of a model. The XMP work (phase 6) starts after the face and photo models are final and
  implemented, and bakes the final model's vectors and the face regions in one pass.
- **Old vectors stay until the owner retires them**, in `embeddings` (photos) and `face_embeddings` (faces).
- **A model change is a library setting**, read through `tagpup.services.settings`, changed by a journaled change,
  never read from `config.ini`.
- **Thresholds belong to a model.** `OFFER_A_NAME` 0.70, `NAME_WITHOUT_ASKING` 0.80, the grouping cut 0.885 and
  Suggest's neighbour cut 0.35 are measured per model on the named faces (35,803, counted by the report; recount in
  each brief), never carried over.
- **Every bulk write on photos or rows is a dry run first**, `--apply` to write, a backup before, run on a copy of the
  library first, reporting what changed and not what was attempted.
- **GPU work takes its turn** through `tagpup.runtime` (`begin`, `gpu_turn`); no phase loads a model on the card
  itself (`tagpup/ml/gpu.py`).
- **A phase is a branch of about 1,200 added lines or fewer, one concern**; a larger piece is cut into merges that each
  leave the trunk whole. At most two review rounds, the ownership question in the second.
- **Real data shapes:** every brief names the scenarios the owner works in: an index interrupted half way, two indexes
  of two libraries at once, a library switched mid-folder, a folder held by two libraries, an update while an index
  runs, a damaged file, a 759-face photo, a sideways photo (about 1 in 600).

## 5. The phases

```
 0 Baseline ──► 1 Plumbing ──► 2 Clustering ─┐
                     │                        ├──► 3 Face recogniser ──► 4 Detector + matching ──► 5 Photo model ──► 6 Bake into files
                     └── (no schema) ─────────┘     (migration 33)
```

Phases 0 to 2 need no schema work and may start now. Phase 3 waits for the schema review (about 2026-11-15) and for
phase 1 merged, so the migration is judged against real timings.

### Phase 0: Baseline (no product change)

**Why first.** Nothing is measured per stage on this machine. Every plumbing claim rests on the hypothesis that the card
is idle most of the time; the baseline proves or kills it.

**What.** A sandbox (a copy of a library, its own `TAGPUP_HOME`, a free port, a separate process, `TAGPUP_NO_JOBS=1`),
deleted after. Record versions (torch, open_clip, facenet-pytorch, faiss, scikit-learn, Pillow, numpy) and:
- **Which library** (owner, 2026-10-16): indexing is timed on an event folder of kr-track (the library behind the 1.9 s
  figure); clustering and the named-face hold-out come from a copy of photo_index. Its image files must be readable
  from the desktop, checked first. The card must be quiet (installed apps and any indexer closed): the owner names the
  window.
- **Which photos** (owner, 2026-10-16): the sample is weighted to photos with many faces detected, since that is where
  detection, per-face work and recognition are tested. Draw it in strata by the faces already found (none, 1 to 3,
  4 to 9, 10 or more, plus the worst photos, up to 759), with at least a third at 10 or more, and record the faces per
  photo of each stratum and the resolution of each photo. The face counts are counted this session, read-only.
- **Per stage**, for 200 to 500 photos (the sample above, across the resolution distribution, itself never measured), with
  `torch.cuda.synchronize()` around each GPU stage: decode for CLIP, pad and resize, CLIP forward, decode for faces,
  MTCNN, per-face crop and tensor, recogniser forward, crop encode, database writes; the per-run fixed costs (process
  start, imports, vector load, CLIP load, warm-up); sampled `nvidia-smi` and CPU use.
- **The user actions:** an event folder indexed through the CLI; `scripts/measure_suggest_folder.py` cold and warm;
  `scripts/measure_identify_faces.py`; one timed `cluster-faces`.
- **Three gating checks:** (a) cosine between stored vectors and a re-run (fp16, batched) of the same photos, expecting
  0.999 or more; (b) a held-out set of the owner's tagged photos scored at 224 and at 512 (Suggest quality), drawn from
  the same many-face weighting, where small faces make 224 most likely to lose something; (c) a
  hold-out over the named faces with today's FaceNet vectors: precision of the top suggestion against similarity and
  margin, the curve a new recogniser must beat.

**Exit.** Numbers in this document's section 7, the idle-card hypothesis confirmed or not, and the plumbing list
re-ordered by what the baseline shows. Owner: Views-AI. Size: scripts only, under `scripts/`, no change to `tagpup/`.

### Phase 1: Plumbing (no schema, no re-embed)

Every step keeps every stored vector valid. Verified against the stored vectors (cosine 0.999 or more) on a sandbox copy
of 200 photos, and timed against the phase 0 baseline.

- **1a Decode once.** One decode per photo, the upright copy for CLIP and the stored-orientation copy for faces
  (transpose one from the other; face boxes stay in stored-orientation coordinates). `draft()` at twice the target for
  JPEG, resize before padding to a square. The damage check must still run: a truncated or zero-tailed file raises
  `Unreadable` as before, tested with the three damaged shapes the 2026-10-15 decision used.
- **1b Overlap.** A thread pool decodes and prepares the next photos while the GPU runs the current one (Pillow
  releases the GIL; a Windows `DataLoader` would spawn processes). Batch CLIP images and the tag prompts Suggest already
  knows up front. Per-face crop and JPEG encode moves off the GPU loop. `cudnn.benchmark` for the fixed-size recogniser
  batch.
- **1c Wasted runs.** Counted first: does a failed file still cost a run (the 14 truncated files cost 7 runs in
  2026-09-26 to 28, before #1089)? If yes, record what failed beside the file's stamp in an existing place; a new table
  is schema work and waits for phase 3's migration.
- **1d Warm indexer (decide after 1b).** Small indexes (1 to 50 photos) use a long-lived worker thread holding the
  models, or the server's warm ones, instead of a 7 to 13 s subprocess start. Touches the GPU turn: the Views-AI and
  Integration owners both review. Skipped if 1a and 1b leave the fixed cost small next to the work.
- **1e Downscale before the detector, behind a library setting, off by default.** It changes which faces are found, so
  it is judged by the count of faces found on a sample (and by which named faces would be lost: expected none), not by
  time. Turned on only on the owner's word.

**How it fails:** an index killed between the decode thread and the write (nothing half written: the existing
transaction is unchanged); a decode thread raising on a damaged file (that photo is reported, the run goes on); two
libraries indexing (one GPU turn at a time, as now); a 759-face photo (batched crops stay under memory).

**Exit.** The baseline's user actions re-timed, faster by a measured amount, vectors within 0.999, and the face counts
unchanged with 1e off. Two merges, each under 1,200 added lines. Owner: Views-AI, with Files for the indexer loop.

### Phase 2: Clustering on the card (no schema)

**Why.** `cluster_candidates` is the N-squared pass (25.6 s for 79,662 faces; about 3.5 minutes extrapolated to 226k)
and `cluster-faces` runs after every index. No faiss-gpu exists for Windows, so the route is torch.

**What.** A blocked k-nearest-neighbour graph by `torch.mm` and `topk` on the card, taking its turn; components or
density clustering on that sparse graph; the all-pairs pass replaced. Try `min_samples` 3 to 5 against the named faces
(79% of faces are noise at today's cut). Suggest's one `search(k=15)` a photo becomes a batch.

**Gate.** Same named faces, same or better precision on the phase 0 hold-out; never a name given or taken that the old
pass would not; timed on the real `cluster-faces`. **Not here:** incremental assignment (phase 4), because its
thresholds are the new recogniser's.

Owner: People, with Views-AI for the card. Size: one branch. Can run in parallel with phase 1 in a separate worktree
(never two GPU test runs at once).

### Phase 3: Face recogniser, and the schema (migration 33)

**Schema (DECISIONS 2026-10-16).** `face_embeddings(face_id, model, vector)` with a trigger deleting a face's vectors
with the face; `faces` keeps identity only; the migration copies `faces.embedding` into the table under the FaceNet key
and drops the column. Destructive (full backup first, no undo); batched with the parked drops of `faces_pending` and
`idx_faces_name`; no other migration in flight; not before about 2026-11-15. Readers to move: `store/faces.py`,
`store/face_tags.py`, `services/identify.py`, `services/suggester.py`, and every `faces.embedding` read the sweep finds.

**The swap.** Keep every existing box. Ask the detector for the five landmarks it already computes, align each crop
(112 by 112 for the ArcFace family) and embed all faces with an ArcFace or AdaFace recogniser (AdaFace, MIT licence, is
the cleaner choice; insightface's weights are accepted by the owner for this personal library). The embedding norm is a
quality score: low-quality faces are held out of clustering. The recogniser runs through torch, with no second runtime.
The face model is a per-library setting; the library switches to the new model only when the gate below is met.

**How it fails:** a re-embed interrupted half way (a face has the old vector and, for some, the new one; the reader
uses one model key and never mixes; the run resumes by what `face_embeddings` already holds); a face whose photo is
unreadable now (left with its old vector, reported); two libraries holding one folder (each library has its own
vectors); a library switched mid-run (a setting change journaled, the run finishes under the key it began with).

**Gate.** On the named-face hold-out, the new model's precision at the owner's chosen precision beats FaceNet's curve
from phase 0, at new thresholds read off the curve. The counts printed: embedded, skipped, failed, by library.

Owners: Data (migration), People (identity), Views-AI (readers). Size: probably two merges (migration and readers;
swap and calibration).

### Phase 4: Detector, and better matching

- **4a Detector swap (SCRFD).** The new detector runs per photo; each old named box takes the new box with the highest
  IoU of 0.5 or more in the same photo, keeps its person, and gets its new vector; unmatched named faces are listed for
  review and never dropped; new unmatched detections become new `faces` rows. One journaled change per folder, dry run
  by default, a small folder first and the owner checks it, backup first, run on a copy first. Excluded faces stay
  excluded. A sideways photo keeps the "turned" rule of DECISIONS 2026-10-16: boxes are not converted here.
- **4b Matching.** A new face is matched against the time-nearest named faces by Date Taken, not one centroid, and needs
  a margin over the second-best person; the two thresholds come from the phase 3 curve. This is the answer to children
  changing across years, independent of model.
- **4c Incremental assignment.** A folder that arrives has its faces matched against known faces by neighbour query and
  joins a person within threshold, or waits; a full re-cluster is the exception, not the response to every event.

**Gate (4a).** Matched, unmatched and new counts printed; every previously named face still named or on the review
list; the owner's check of the sample folder. Owner: People, with Files for any file touched (none are here).

### Phase 5: The photo model (one re-embed)

**Decide, in this order, with the phase 0 numbers:** resolution (224 against 512 on the held-out tagged photos), then
the model (the candidates, cheapest change first: DFN5B ViT-H-14 same code and dimension; SigLIP 2 B/16 or L/16 best
quality per cost; dimensions per model, so nothing may assume 1024).

**Build.** The model and its settings are a library setting; vectors under a new `embeddings.model` key beside the old;
the tag-prompt cache (`tag_embeddings`) recomputed under the new model; Suggest's neighbour cut and any thresholds
re-derived on the held-out set; a SigLIP sigmoid score tried for multi-label tags only if the phase shows it is better.
The text tower alone stays resident in the server between runs; the image tower loads for an index. The re-embed is a
CLI run (long indexes go through the CLI), resumable by what `embeddings` holds under the new key, dry-run first, on a
copy first.

**How it fails:** a re-embed killed at 40,000 photos (Suggest keeps using the old key until the library switches; a
partial new key is never read); the card shared with Suggest (one turn at a time); disk (a second vector set: 280 MB
at float32 for 1024 dimensions, counted before the run).

**Gate.** Suggest on the held-out photos is better than today's by an agreed margin, at the new thresholds; the old
vectors are kept until the owner retires them. "Search by what is in the picture" (decided 2026-10-07): ask the Views-AI
owner what exists today before scoping it, and plan it only as the reading of these vectors.

Owner: Views-AI. Size: two merges (setting and model loader; re-embed and recalibration).

### Phase 6: Bake into the photo files (its own project, last, one pass)

Decided 2026-10-07 (the current model's vectors and every detected face's region into the file's XMP), advised against
starting in October (health review 9.2). **Owner, 2026-10-16: do it after the final design and research are all
implemented, so the files are updated only once.** That fixes the order: the face recogniser, detector and photo model
are final (phases 3 to 5 run on the live libraries, old models retired or settled) before a file is written, so the
baked vectors are the final model's and never rewritten for a model change.

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
  never replaces), carries the fingerprint of the picture (a rotation or edit made elsewhere reads as "compute again"),
  and survives the file's own metadata writes (the existing `restamp` rule for an app write; a write by another
  program changes the stamp and the bake is checked by its fingerprint, not its mtime).
- **Names are not carried by the bake.** A region and a vector say where a face is and what it looks like; who it is
  comes from the person tags in the photo's keywords through the existing naming-from-tags path, and "a name alone is
  nobody" (DECISIONS 2026-10-15) still holds on import.
- **Compatible means one key.** The same model key a library's `embeddings` and `face_embeddings` use decides it, so
  there is no second notion of compatibility. A machine with no card reads compatible bakes and queues the rest (its
  row says so; the owner sees the count).

**Not designed here.** It writes to the master copy of about 68,000 irreplaceable files for the backfill, depends on
the orientation and fingerprint work, and carries about 13 KB of XMP a photo (JPEG's 64 KB XMP segment is the limit).
It begins only when: phases 3 to 5 have run on the live libraries; the read-only census of regions already in the files
is done; and the owner opens it. Its own design document, planned by the Files and People owners (with Integration for
the desktop-to-server flow), comes first. Whatever it writes goes through `file_changes.write_fields`.

## 6. Decisions the plan needs, and who makes them

| Decision | When | Who |
|---|---|---|
| Does a failed file still cost a run (1c), and is a skip-list needed? | Phase 1, counted | Owner of Files |
| Turn the detector downscale on (1e)? | After the sample's face counts | Owner (the person) |
| Is the warm indexer worth it (1d)? | After 1b is timed | Views-AI and Integration owners |
| The owner's chosen precision for naming a face | Phase 3 gate | Owner (the person) |
| Resolution, then photo model | Phase 5, with phase 0 numbers | Owner (the person), on the owners' evidence |
| When to retire old vectors | After a model has run for a while | Owner (the person) |
| When phase 6 opens | After phases 3 to 5 are final (owner: files written once) | Owner (the person) |
| Phase 6 design questions: where the desktop-to-server copy happens, whether the server's library indexes the server path or a desktop copy (the master is on the server today), the fingerprint, the backfill order | Phase 6's own design | Files, People and Integration owners; the owner (the person) decides |

## 7. Baseline numbers (filled in by phase 0)

| Measure | Today | After phase 1 | Notes |
|---|---|---|---|
| Index, an event folder, seconds a photo | 1.9 (60 photos, 2026-09-27) | | |
| Fixed cost of an index run | 10 to 20 s (the report) | | |
| `cluster-faces`, whole library | | | extrapolated 3.5 minutes for the pairs alone |
| Suggest, a folder, cold / warm | not measured | | |
| Identify Faces / New Person | not measured | | |
| Card idle share during an index | not measured | | the hypothesis |
| Named-face precision at the chosen cut | not measured | | the curve phase 3 must beat |

## 8. Risks

| Risk | Phase | Handled by |
|---|---|---|
| A faster path changes a vector slightly | 1 | Cosine 0.999 on 200 photos before any merge; `draft()` only at 2 times the target |
| A faster path hides a damaged file | 1 | The damage check kept and tested on the three shapes |
| A name lost in a migration or a swap | 3, 4 | Old vectors kept; boxes carried by IoU; unmatched named faces listed, never dropped; backup; copy first |
| A threshold carried to a model it does not fit | 3, 5 | Thresholds read off the hold-out curve per model |
| Two heavy jobs on one card | all | The GPU turn; no two test runs at once |
| The migration is judged by feel | 3 | Phase 0 timings and counts before and after |
| Scope creep into the files | 6 | Phase 6 opens only by the owner, with its own design |

## 9. Keeping this current

When a phase merges: set its status here, fill section 7, and add the measured result to the domain document
([domains/views.md](domains/views.md), and [domains/people.md](domains/people.md) for faces). A decision the plan needs
goes into [DECISIONS.md](DECISIONS.md) as a dated row, with this document linked, not repeated.

# TagpupCLI — System Specification

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

AI-powered local tag inference for photo libraries. Runs entirely on your local Windows PC — offline, private, and requiring no cloud API keys.

---

## 1. System Architecture & Components

The tool is built as a modular Python application with script wrappers. It relies on the following key components:

```
[Untagged Image] ---> [ClipEmbedder (ViT-H-14)] ---> 1024-dim Vector
                                                              |
                                                              v
[Tagged Library] ---> [MetadataExtractor]       ---> [PhotoIndex (SQLite + FAISS)]
                               |                              | (Query k-NN)
                               v                              v
                       [TagTaxonomy]             ---> [TagSuggester] (Face Match)
                               |                              | (Aggregate & Score)
                               v                              v
                       [photo_index.db: tree]   ---> [suggestions kept in the library]
```

- **`tagpup_cli.py` (CLI entry point)**: Unified Command Line Interface using `click` and `rich`.
- **`tagpup/files/metadata.py` (Metadata Extraction)**: Interfaces with `exiftool` to read standard metadata fields in batches of 500. Handles bare and namespaced tag keys. What the fields mean -- tags, people, captions -- is `tagpup/core/vocabulary.py`; the library's people are read by `tagpup/store/taxonomy.py` and handed to each batch.
- **`tagpup/runtime.py` (The models)**: Builds the CLIP model and the face models once for each set of settings, from the settings of the library the command is given (`--db`; `tagpup.services.settings`), and hands them to what needs them. Nothing else builds a model.
- **`tagpup/ml/clip.py` (Visual Embeddings)**: CLIP (`ViT-H-14` by default, customizable resolution up to $512 \times 512$) using PyTorch (supporting GPU/CUDA acceleration if available with FP16 half-precision, or falling back to CPU). Generates normalized embeddings. `tagpup/services/search.py` (`PhotoEmbeddings`) keeps each photo's in the library, so an unchanged file is not embedded again.
- **`tagpup/services/search.py` (SQLite & FAISS Vector Index)**: `PhotoIndex` loads a library's photo records and their vectors from the store (`tagpup/store/`) and builds an in-memory `faiss.IndexFlatIP` flat index (`tagpup/ml/vector_index.py`) for rapid cosine similarity queries.
- **`tagpup/ml/faces.py` (Face Recognition)**: Detects face bounding boxes using **MTCNN** and generates 512-dimensional face vectors using **InceptionResnetV1** (supporting GPU/CUDA and FP16 acceleration).
- **`tagpup/services/identities.py` (Identity Clustering)**: Performs density-based clustering (**DBSCAN**) of a library's faces to resolve and assign names to visual identities based on co-occurrence tagging patterns, by the rules in `tagpup/core/clustering.py`.
- **`tagpup/store/taxonomy.py` (Hierarchical Tag Taxonomy)**: Builds and updates a tree of all known hierarchical paths (e.g. `Family/Immediate/John Doe`). Resolves leaf tags to their ancestors.
- **`tagpup/services/suggester.py` (Tag Suggestion Engine)**: Scores tags using cosine similarity of nearest visual neighbors and boosts matched tags if specific face embeddings are recognized in the target image. It is given its models.

---

## 2. Requirements & Preconditions

| Requirement | Details |
|-------------|---------|
| **Operating System** | Windows 10 or 11 |
| **Python** | Python 3.10+ added to system PATH |
| **ExifTool** | Installed on path or resolved dynamically in `tagpup_cli.py` (defaults to `%USERPROFILE%\AppData\Local\Programs\ExifTool\exiftool.exe`) |
| **Disk Space** | ~3.8 GB one-time download for the default `ViT-H-14` CLIP model weights. Face models require ~112 MB. Cache consumes ~4 KB per photo indexed. |
| **Hardware Acceleration** | NVIDIA GPU with CUDA support (e.g. CUDA 12.1 runtime) enables FP16 hardware acceleration, offering 2x-3x speedup. Falls back to CPU if CUDA is unavailable. |

---

## 3. Python Dependencies

Configured in `requirements.txt`:
- **`torch --index-url https://download.pytorch.org/whl/cu121`**: PyTorch GPU runtime (CUDA 12.1 build).
- **`torchvision --index-url https://download.pytorch.org/whl/cu121`**: PyTorch vision library for image processing.
- **`open-clip-torch`**: Open-source implementation of CLIP for embedding generation.
- **`faiss-cpu`**: Facebook AI Similarity Search engine.
- **`pyexiftool`**: Python wrapper interface to ExifTool.
- **`rich`**: Beautiful formatting and rendering of CLI tables and statistics.
- **`click`**: CLI creation library.
- **`tqdm`**: Command-line progress bars.
- **`Pillow`**: Standard Python Imaging Library for image preprocessing.
- **`facenet-pytorch`**: GPU/CPU-compatible MTCNN face detector and InceptionResnetV1 face embedder.
- **`scikit-learn`**: Machine learning library for DBSCAN clustering algorithms.

---

## 4. Metadata Fields Processed

The system reads and writes metadata using the following tags:

### Read Fields
- **Keywords / Tags**: `IPTC:Keywords`, `XMP:Subject`, `XMP:HierarchicalSubject`
- **People / Faces**: the leaf of a hierarchical tag under a root the library's tree marks as holding faces (e.g., `John Doe` from `Family/Immediate/John Doe`). `XMP:PersonInImage` and `XMP:RegionName` are not read at all (owner, 2026-10-15).
- **Captions / Descriptions**: `IPTC:Caption-Abstract`, `XMP:Description`
- **Title / Name**: `XMP:Title`, `IPTC:ObjectName`
- **Date Taken**: `EXIF:DateTimeOriginal`, `XMP:DateTimeOriginal`, `EXIF:CreateDate`
- **Location**: `XMP:City`, `XMP:State`, `XMP:Country`, and IPTC equivalents
- **GPS Coordinates**: `Composite:GPSLatitude`, `Composite:GPSLongitude`
- **Camera hardware**: `EXIF:Make`, `EXIF:Model`
- **Rating**: `XMP:Rating`

### Write Fields
Tags and captions are written back using ExifTool:
- **Flat Tags & People names**: Written to `XMP:Subject`, `IPTC:Keywords` (in append `+=` mode) and `EXIF:XPKeywords` (semicolon-separated string).
- **Hierarchical Paths**: Paths (containing `/`) are written to `XMP:HierarchicalSubject`.
- **Captions**: Written to `XMP:Description`, `IPTC:Caption-Abstract`, `EXIF:ImageDescription` (which maps to `System.Title` in Windows), and `EXIF:XPComment` (which maps to `System.Comment`/Caption in Windows).

---

## 5. Algorithmic Rules

### A. Indexing Rule
- **Every photo is indexed.** Indexing once required a photo to carry at least one flat tag, person tag or caption, which made the index a record of what had already been organised rather than of the library. An untagged photo could not be searched for, suggested against, or have its faces identified -- precisely the photos that needed the tool most.
- **Smart Skipping (Incremental Indexing)**: On subsequent indexing runs, the system compares each scanned file's modification time (`mtime`) and file size (`size`) with the values stored in the database. If both match, the file is skipped entirely from metadata parsing and embedding generation, drastically speeding up catalog updates.
- **Single-Pass Face Indexing**: Unless `--skip-faces` is passed, the indexer automatically triggers face detection and embeddings extraction in the same loop, writing results to the `faces` table after the parent photo row has been committed.
- **A Tag Is Written Whole**: keyword fields carry the full path -- `Family/Immediate/
  Cora Ingersoll` -- and never its fragments. Writing used to emit the path *and* each of
  its segments, so one three-level person tag became four keywords: the path plus
  `Family`, `Immediate` and `Cora Ingersoll`. That buries a deliberate hierarchy under its
  own pieces, and the bare leaf is exactly the form that gave one person two entries in
  the Add Person list. The convention is read from the library rather than assumed: of
  18,502 keyword values in the main gallery, 18,364 are full paths separated by `/` and
  none are bare leaves. Levels are preserved on every write; a photo's existing depth is
  never flattened.
- **A Name Or Keyword Seen For The First Time Gets A Node (every tag has an id, owner 2026-10-10)**:
  `extract_people()` returns *leaf* names, and indexing used to file a new person under the
  library's people root by the depth it used. Now the index records a batch of photos and
  makes the node of each keyword and name the tree lacks in one place, `taxonomy.ensure_node`
  (`derived.make_nodes` for the batch's keywords, the face and photo writers for names): a keyword
  its own path (a bare one is a tag of its own and names nobody), a name a node directly under the
  tree's root marked as a person (a name two people have, or a group's leaf, is another path: it
  gets its own node), to be merged into the right branch in the tag view. Nothing is made for the
  name of a group at the top, or for a keyword that would go under a person that faces or photos
  use (a file never turns a person into a group; the doctor counts the keywords). A person is listed
  for a photo by the id of a node and no other way (owner, 2026-10-11): a name alone is nobody
  (owner, 2026-10-15), and the doctor counts a row with a name and no id among the rows whose name
  is not their person's; `--rebuild-derived --apply` unnames such a face (no name, no id, no
  decider) and takes such a listed person off the photo's list.
  **A folder from another program with thousands of distinct keywords makes thousands of
  nodes**, each a tag the owner must merge or delete by hand (there is no bulk delete) and
  that Suggest embeds. So a batch of reads that would make more than 500 new nodes
  (`derived.MAX_NEW_NODES_PER_BATCH`) makes none and leaves its keywords without a node:
  the doctor counts them. A
  `sync` re-read of a changed file does not make nodes.

  `add_tag()` additionally refusesto create a bare root that an existing **people**
  path already claims: photo files carry both forms in the wild, and a file saying
  `Cora Ingersoll` beside a taxonomy saying `People/Cora Ingersoll` gave that person two homes.
  Only people are folded this way -- `Kentridge` beside `School/Kentridge` is left
  alone, since tidying that for every tag is a different question.

  This matters because indexing runs repeatedly: the kr-track taxonomy had been cleaned
  from 51 such duplicates to zero once before, and the next index run recreated them
  all. The MCP tool `merge_duplicate_person_tags` merges any that already exist (a rehearsal
  unless `apply`); it removed 53 across 129 photos here.
- **Per-Photo Locking**:A photo is locked for the duration of its processing by creating a
  file in `data/locks/`, named for the MD5 of its absolute path -- exclusive creation makes this
  atomic between processes. The lock records the holding process's pid, host and acquisition
  time. A lock whose holder is demonstrably gone (dead pid on this host, or older than six
  hours regardless) is **taken over**, not obeyed. Locks are released once per batch of 100
  photos, so a live holder never approaches the age limit.

  This matters because release happens in a `finally`, which a hard kill skips, and nothing
  else cleans up another process's lock. Before takeover existed, every killed run left its
  in-flight photos permanently unindexable, skipped in silence by every later run; 348 such
  locks had accumulated over three months. Two deliberate refusals: a lock belonging to
  another host is never stolen on a pid comparison (pids are not comparable across machines,
  and libraries may live on a network share), and no lock is stolen because a liveness check
  failed to answer. A photo skipped because a live process holds its lock is reported at the
  end of the run with its path -- never dropped silently.
- **Metadata Batch Resilience**: Metadata is read in batches of 500. ExifTool exits non-zero if
  *any* file in a batch is unreadable and pyexiftool raises on that status, so a failed batch is
  re-read one file at a time. Only the file that actually fails is recorded with empty tags,
  people and captions; it keeps its `mtime` and `size` so that change detection does not treat
  it as new on every later run. A clean batch still costs exactly one ExifTool call.
- **Reset Option**: The CLI accepts a `--reset` option which backs up and then deletes the library (`photo_index.db`), its tag tree with it, before indexing afresh.

### B. Similarity & Scoring
- Neighbors are retrieved using Inner Product (equivalent to cosine similarity on L2-normalized CLIP vectors).
- **Time-Decayed Similarity Weighting**: The similarity score of neighbor $i$ ($S_i$) is scaled by an exponential decay factor based on the age gap in years ($\Delta t$) between the target photo and the neighbor:
   $$S'_{i} = S_i \cdot e^{-\lambda \cdot \Delta t}$$
   where $\lambda = 0.1386$, representing a 5-year half-life. This ensures tags from temporally closer photos are weighted higher.
- Target tag confidence score is computed as:
   $$Score(T) = \frac{\sum_{i \text{ has } T} S'_{i}}{\sum_{i=1}^K S'_{i}}$$
   where $S'_i$ is the time-decayed similarity score of neighbor $i$, and $K$ is the number of nearest neighbors (default $15$).

### C. Path Hints Boosting
Folder names along the target image's path are extracted as hints. If a suggested tag (or any of its sub-segments) matches a path hint, the tag's final score is boosted:
$$\text{Score}_{\text{final}} = \min(1.0, \text{Score}_{\text{base}} + 0.20)$$

### D. Taxonomy Ancestry Rule
If a neighbor is tagged with a hierarchical leaf node like `Family/Immediate/John Doe`, the taxonomy expands it to include its ancestors `Family/Immediate` and `Family`, ensuring parent categories receive proportional weight.

### E. Era-Aware Zero-Shot Candidate Prompting
- People names under the `Family/` or `Friends/` folders in the taxonomy are automatically extracted as zero-shot candidate tags.
- The target image's year of capture is parsed from its metadata. If available, prompts are dynamically generated as:
   - For people: `"a photo of {tag} in {year}"`
   - For other tags: `"a photo of a {tag} in {year}"`
- This calibrates CLIP's visual recognition to match styles (clothing, hair, photographic medium) typical of that specific era. If the year is unavailable, standard prompts like `"a photo of a {tag}"` are used. Matches above a threshold of $0.23$ are recommended and mapped back to their full hierarchical paths.

### F. Event-Level Folder Consensus Post-Processing
To resolve individual image noise by leveraging event-level folder context, recommendations are grouped by parent folder and post-processed:
- **Consensus Rate**: The fraction of images in the folder that recommend a tag $T$ at or above a score of $0.20$.
- **High Consensus Boost**: If $ConsensusRate(T) \ge 0.40$, the tag's score is boosted:
   $$\text{Score}_{\text{new}} = \min(1.0, \text{Score} \cdot 1.25)$$
- **Isolated Context Outlier Penalty**: If a contextual tag (under `Activity/`, `School/`, `Trips/`, `Scenic/`, `Location/`, or `Albums/`) is an outlier within a folder, it is penalized:
   - If $ConsensusRate(T) < 0.10$, the score is heavily penalized: $\text{Score}_{\text{new}} = \text{Score} \cdot 0.3$
   - If $ConsensusRate(T) < 0.20$, the score is moderately penalized: $\text{Score}_{\text{new}} = \text{Score} \cdot 0.6$
- Tags with adjusted scores $< 0.15$ are filtered out.

### G. Face-Level Identity Self-Tuning & Recognition
- Bounding boxes are extracted via MTCNN (confidence $\ge 0.85$, dimensions $\ge 15\text{px}$).
- Faces are clustered using DBSCAN at the library's face model's grouping radius (`tagpup.core.clustering.Cuts.group_at`: a cosine similarity of $\ge 0.885$ for `mtcnn-facenet`, Euclidean epsilon `0.48`, and $\ge 0.80$ for `scrfd-adaface`).
- **Cluster Naming**: Visual groups are assigned people identities based on tag voting. If a photo has only one face and one name tag (e.g. `John Doe`), it acts as an anchor vote.
- **Centroid Validation & Refinement**: During iterative propagation, resolved assignments are validated against the visual centroid of their resolved identities. Faces showing a similarity $< 0.80$ (Euclidean distance $\ge 0.63246$) to their respective identity centroid are unassigned/cleared to maintain visual profile purity.
- **Match Suggestion Boost**: Untagged images undergo face detection. Detected faces are matched against resolved database faces. If a face yields a similarity $\ge 0.85$ to a known profile, the matching person's tag is automatically boosted to a confidence score of `1.0`.

---

## 6. CLI Command Reference

The `tagpup_cli.py` engine is accessed via `click` subcommands. 

### Global Options
- **A library from a newer TagPup is not opened.** With `--db` (or `TAGPUP_DB_PATH`) naming a library whose schema is newer than this version knows, every command but `history` and `snapshots` (list and restore: the recovery commands, which open it as it is with a one-line note) stops before it starts: the sentence of `schema.NewerLibrary` (the library's name, its schema, the one this version knows, and to start the newest TagPup), exit status 1, nothing written. `tools/doctor.py` and the MCP's tools say the same.
- `--test`: Use the test library (`test_photo_index.db`) to avoid altering the production one. The tag tree is in the library, so the test tree stays in the test library (findings.md, #61).

---

### Subcommands

#### 1. `index`
Scans and indexes a photo library recursively: one or more directories, in one run (the models are loaded once; sync hands it every folder of new files at once). Indexing a folder adds it to the library: it is recorded as added (`tagpup.services.libraries.record_added`; with `--no-subfolders`, without its subfolders), as TagPup's Add and TagTuner's Add Folder record it, and each photo's row is made as it is read.
**After an index that recorded a photo, it matches the faces it made with the named ones, and writes nothing** (owner, DECISIONS 2026-10-23; `tagpup.services.faces.automatch_new`): the faces whose ids lie between the newest face read as its turn on the graphics card began and the one read before it let the turn go, decided by Re-examine's matcher against the faces a person decided. This command is what both apps' queues and sync's run, so every index does it alike. It prints one line beginning `New faces: `, counted: how many of the new faces have a likely name (open Re-examine on their folder to confirm them), or that none looks enough like anyone named, or that no new face waits for a name; and how many of their photos carry a person's keyword that no face has (Name faces from tags, or `cluster-faces`, may name them). The server's queue ends the folder's message with that sentence, read whole. No face, row or file is written, so nothing is refused meanwhile; a matching that fails says so on the same line and the run still exits 0, the photos indexed. A run that recorded no photo (everything current) says nothing of faces. Stopped while it waits for the card (exit status 76), it indexed and matched nothing.

- **Usage**: `run.bat [global-options] index <DIRECTORY> [<DIRECTORY> ...]`
- **Options**:
  - `--force-reembed`: Force recreation of all CLIP visual embeddings.
  - `--reset`: Delete the existing SQLite database index and taxonomy files to start fresh.
  - `--skip-faces`: Skip MTCNN face detection and FaceNet embedding extraction during indexing.
  - `--no-subfolders`: Only the photos directly in each DIRECTORY (`images.photos_in`): what sync queues for a folder the library holds, whose subfolders may be folders to review or ignored.

#### 2. `cluster-faces`
Run by hand, with no options (an index matches only the faces it made, and writes nothing; DECISIONS 2026-10-23). It clusters the face embeddings and names the faces from their photos' people (`tagpup.services.identities.resolve`). Names a person gave (`name_source = 'manual'`), their "nobody" decisions and exclusions are kept; a face is written only while it is still as the run read it, and a person is written by id. Two or more faces of a photo
whose keywords name as many people no face is known for stay unnamed (nothing tells which is whose; owner, 2026-10-15), and
so does a face whose people tie in its votes; the run says how many faces it left so and how many earlier guesses it took
back.

### `compact [--apply]`
Says how much of the library's file holds nothing: pages left free by deleted rows and dropped columns, which SQLite keeps until the file is rewritten. With `--apply`, backs the library up (`db.backup`, into `backups/`), then rewrites it without them (`VACUUM`, `db.compact`) and says the size before and after. The rewrite needs the file to itself; close the apps first. After phase 4's migrations photo_index holds about 1 GB free of 4 GB.

### `history [--change ID] [--limit N] [--reveal]`
The library's journal (`tagpup.services.journal`): the changes the maintenance scripts and the MCP server's write tools applied, newest first -- each one's id, operation, status (`applied`, `derived_pending`, `undone`), when it was made and undone, the schema version it was made at, and how many rows of each table it inserted, updated and deleted. `--change ID` shows that change alone with the keys (ids) of every row it wrote; with `--reveal` too, each column's value before and after, a BLOB by its size. Values can name people, so they are shown only when asked.

### `undo ID [--apply]`
Undoes change ID of the journal. Without `--apply` it rehearses: undoes the change and applies it again inside a transaction rolled back, and says whether that restored every row exactly. With `--apply` it writes the old values back, only where every row is still what the change left. Refused, with nothing written and exit status 1, when a row has changed since, when a newer change touched the same rows (named), or when the library's schema has moved on since the change was made -- and for a stamp of the library's settings, its first settings, which are changed rather than undone, and for a change whose undo would put back a face model the library may not be switched to now (`faces.model`: the switch's own check and sentence, `tagpup.services.settings.undo_check`) -- the one account of `tagpup.services.journal.why_not` over `UNDO_CHECKS`, whose every reason SPEC_TAGPUP_GUI.md lists (`/api/history`, `why_not`).

### `sync [--folder FOLDER] [--apply]`
Brings the library in step with its folders (`tagpup.runtime.sync`, over `tagpup.services.sync`; ARCHITECTURE.md, phase 8): walks the library's root folders (`library.roots`) and every folder it holds photos in, each once from the topmost, or FOLDER, and compares each file with its row by path, size and modified time. Says how many files are new (and in how many folders), changed, moved and missing, and how many folders are wholly gone -- among them a folder the library holds (a root, a folder its rows are in, a folder added) that is not there at all, as on an unplugged drive. A dry run unless `--apply`, reading only the changed files (and, where a row is missing, the DocumentIDs of the new files its name, size and modified time did not match); nothing is written. With `--apply`: the changed rows are read again from their files, as `refresh_rows` does, and the rows of moved files follow them with their faces, as one change of the journal (`sync`), which `undo` takes back; the folders the library holds that hold new files are indexed, each without its subfolders, one at a time, and the command waits for them; a folder under a root holding no indexed photo is counted as a folder to review, never indexed on its own (TagTuner's gear, Folders to review), and one under an ignored folder (`library.ignored`) is passed over; missing files are never removed. Each applied run is recorded in the library (`sync_runs`), which the pages show as "last in step". Counts only; exit status 1 when refused -- FOLDER not a full path, or holding no photo of the library and under none of its root folders -- or something failed. **A renamed folder** is not followed by anything but the pairing above (name, size and modified time): one renamed and saved again before the next sync is reported as missing folders (`folders gone`) with its rows left as they were, and the owner renames it back by hand. The hidden `.tagpup` files that `folder-ids mark` once wrote are ignored (nothing reads or deletes them; the command is gone, owner 2026-10-10).

### `tags-from-faces [--apply] [--guesses] [--folder FOLDER]`
**`--folder FOLDER`** (#994) counts and writes only the photos under that folder and its subfolders (the folder is spelled by its root's first place, as in `tagpup.services.folder_scope`); the rule for each photo is the same, so the folder's plan is the whole-library plan restricted to its photos. A folder the library holds no photo under is refused (exit status 1).

Puts on a photo the people its faces name and its keywords do not (`tagpup.services.tags_from_faces`, over `tagpup.services.face_people`; #861). A face named and the photo's person tag are one decision; faces named before that (TagTuner's strip, Match, AutoMatch All, Identify Faces, clustering) left photos listing a person from a face alone (`photo_people` source `face`), "a green box and No people tags". A dry run unless `--apply`: counts only, never a name or a path -- the photos, the people, how many photo files would be written, and how many people are left: filed by the tag tree in two places (or under several people roots), for you to place; named in the photo's keywords already under a root the tree does not file people under (`Parkrunner/<name>`: the same rule as the keyword writer, the leaf under any root; a tree question -- make that root a people root, or retag), so the count to write is what `--apply` writes and a second run finds none; and "from a guess only" -- no face of that name was named by hand, only by clustering or automatch. `--guesses` writes those too, with a printed warning: the keyword makes each guess a decided reference for the faces compared with it afterwards (#640). **`--apply` changes photo files**: it writes each person's keyword into the photo's file with ExifTool, one change of the journal (operation `person tags from faces`; `history` lists it, `undo` takes it back, recording every file's fields before and after -- no copy of the library is taken), a batch of photo files written 25 at a time, its progress on the Activity page. Ctrl+C or Ctrl+Break stops it: the files written stay written, applied and undoable, the photos not reached are taken out of the change (not written by the next program to open the library), and it says how many it wrote. A file ExifTool cannot read or write is counted and the others are written (exit status 1, run it again); a file changed since it was read is a conflict, reported and never overwritten; TagPup and TagTuner may stay open. The doctor (`tools/doctor.py`) reports the same count as "photos listing a person from a face alone" (reported, not broken).

### `face-pass DIRECTORY... [--apply] [--reveal]`
The new face model's pass over the photos under each DIRECTORY, at any depth (`tagpup.services.face_pass`; docs/AI_PIPELINE.md, phase 3, "The pass"): each old face, named or not, given the new model's vector by box overlap (the named first), its name, box and crop kept; each detection no old face takes a new face, unnamed; each old face no detection takes listed for review, untouched. The library stays on its face model (`faces.model`): every reader keyed by it reads FaceNet's vectors as before. Its own process with its own `Runtime`: the model is the runtime's (`Runtime.look_at_faces`), the graphics card taken at its first photo (it says who holds it while it waits) and let go at the end; the photos are read ahead as the index reads them. A dry run unless `--apply`: it reads and counts all the same, and writes nothing, no snapshot either. For each folder holding photos it says, in counts: the photos looked at, done before (resumed by what the new model's vectors already hold), turned (skipped: their boxes are not converted), with no face found (looked at again next run), and failed, by reason (a picture that does not decode, or a photo whose row or faces changed while it was read); the old faces given the new vector, the new faces, the detections too small to make a new face (under 40 px on the shorter side, `NEW_FACE_FLOOR`; one an old face takes still gets the vector), the old faces unmatched; the changes of the journal the folder already holds, and those written. `--reveal` adds each folder's path and the ids of the faces unmatched and the photos failed. **`--apply`** takes one snapshot of the library (`tagpup.services.snapshots.before_a_long_write`) just before its first write, unless a daily from today stands, never one per folder, and none for a run that writes nothing (every photo done, or no face found); it writes each photo in one transaction, one change of the journal per folder ("face pass (<model>)", `undo` takes it back; a folder whose new faces were named since is refused, naming the first ten reasons and how many more). Ctrl+C or Ctrl+Break stops it after the photo under way (a second stops it at once, the photo's transaction rolled back): what was written stays written and undoable (a run that had written nothing, a dry run among them, says so), and a run again goes on (exit status 1). Refused, with nothing done: the weight files not all there (it names what to make or fetch), a folder given twice or inside another (exit status 1).

### `retire-faces [--apply] [--clear-manual-names] [--reveal]`
Retires the old face detector's faces of a library already on the new face model, after its pass (`face-pass`) and the switch (`tagpup.services.retire_faces`; docs/AI_PIPELINE.md, phase 3, "Retiring the old detector's faces"; DECISIONS 2026-10-22). Every face holding no vector under the library's model -- one the old detector alone found, which no reader keyed by the model compares -- is deleted, with its crop and its vectors under every key; every decision of the faces kept is cleared: the person (named by hand or automatically), "nobody", an exclusion and its reason. No keyword in a photo file and no tag of the tree is touched: the people a photo's keywords name stay in its people, and Name faces from tags and `cluster-faces` name the faces kept again. Names the library first. A dry run unless `--apply`: counts only, never a name or a path -- the faces deleted (of them named automatically, and decided by hand or excluded), the decisions cleared by kind, the photos walked, the names given by hand that would go, and the photos left with no face at all (every face of theirs deleted: the new model's pass found none in them, or has not reached them); a library with nothing to retire says "Nothing to retire". `--reveal` adds the ids of the photos it walks. Refused, with nothing written (exit status 1): a library not on the new model, or with more of its faces in photos the pass has not reached than a switch allows (the switch's own check and sentence, `settings.switch_to`); with `--apply`, names given by hand (of the faces kept, or of the faces deleted) without `--clear-manual-names`, and any running app (`launcher.running`: a running app keeps faces it read in memory), asked again before each chunk: an app started during the run stops it there, the chunks written standing, and it says so. An MCP server keeps no record of itself (`launcher.say_where` is the web server's) and is not seen: close it first. **`--apply`** takes one snapshot of the library (`snapshots.before_a_long_write`) unless a daily from today stands, then walks the photos 500 at a time, each chunk one transaction in which its faces are read again (a photo removed or done since is left alone, counted) and its deleted rows and cleared decisions recorded in the journal before they are written, as one change ("retire faces (<model>)"), its progress a line a chunk. It says what was committed, never what was planned. Ctrl+C or Ctrl+Break stops it after the chunk under way (a second stops it at once, that chunk rolled back): a run again continues the same change while it is the journal's newest (exit status 1). `undo <change> --apply` puts every face back with its crop and vectors and every decision as it was, refused once a later change wrote the same faces (a face named since); and the pass's own change ("face pass (<model>)") is not undone while a retiring after it stands, nor the retiring while a photo it took faces from holds a face made since (by a pass, or by Suggest).

### `retire-vectors [--apply]`
Retires the old face model's vectors of a library on the new face model, after `retire-faces` (`tagpup.services.retire_faces`, `plan_vectors` and `apply_vectors`; docs/AI_PIPELINE.md, phase 3, "Retiring the old model's vectors"; DECISIONS 2026-10-23): every vector under FaceNet's key (`mtcnn-facenet`) in `face_embeddings` is deleted, which no reader keyed by the library's model reads. No face, crop, decision, keyword or tag is touched. Names the library first. A dry run unless `--apply`: counts only -- the vectors that go and their megabytes -- and nothing written, no snapshot either; a library holding none says "Nothing to retire". Refused, with nothing written (exit status 1): a library not on the new model, and one any face of which holds FaceNet's vector and none of the new model's (its last vector would go: run `retire-faces` first); with `--apply`, any running app (`launcher.running`), asked again before each chunk as `retire-faces` does. **`--apply`** takes a snapshot of the library first, always (`snapshots.take(force=True)`, even when a daily from today stands: the oldest daily may go), since it is the way back -- while it stands: it is a daily, three are kept (`snapshots.KEEP`), and the app's own daily snapshots push it out in about three days, which the dry run, a continued run and the last line each say (a run continued after it went names it all the same: no check of it, no instance); then deletes 5,000 vectors a transaction (`VECTOR_CHUNK`), walked by rowid, the library's model read again in each and the faces holding FaceNet's vector alone counted again (a retiring of the old detector's faces undone beside the run brings them back: that chunk is refused, nothing of it written, exit status 1), as ONE change of the journal holding no rows ("retire vectors (mtcnn-facenet)"), its summary the vectors and bytes deleted, so `history` and `snapshots list` say a restore loses it. It says what was committed, never what was planned. Ctrl+C or Ctrl+Break stops it after the chunk under way: a run again continues the same change, the newest kept of its name, also after a change recorded since (a setting), and takes no second snapshot (the first holds every vector). The change is not undone: `undo` of it is refused, naming the snapshot, and so are the undo of the retiring of the old detector's faces (its faces would come back holding only a vector no reader reads), the undo of the switch to the new model, and a switch back to FaceNet (`settings.switch_to`: no face holds its vector). After it, `compact`, with the apps closed, gives the space back to the disk.

### `thumbs warm [--folder FOLDER] [--limit N] [--apply]`
Makes the library's thumbnails ahead of the first time each is asked for (`tagpup.services.thumbnails.warm`; ARCHITECTURE.md, phase 9a-2): a 300 px JPEG of each photo, kept under `cache/<library>/thumbs` beside the library, which the library views show instead of decoding every photo's file again. A dry run unless `--apply`: it reads the library and each file's size and modified time -- never the picture -- and says how many photos already have a thumbnail for their file as it is now, how many need one and about how much they will take (the average entry already kept, or 30 KB when none is), how many have no file there, how many are damaged (recorded so, or found not to decode: they are not decoded again until their file changes, and nothing is kept for them), and how many could not be reached (a share away). It writes nothing, not even the cache folder. With `--apply` it makes them, four at a time, and prints its progress after each 500 photos; a library behind this version's schema is brought up to date first, and the thumbnails of photos the library no longer holds (a restore, a delete outside TagPup) are deleted. It can be stopped (Ctrl+C) and run again: every thumbnail is written whole or not at all, what was made is found there, and the run goes on from it. `--limit N` stops after making N, so a large library can be filled in parts. `--folder` limits it to the photos under that folder. The cache is not bounded (the owner's decision: 1.5 to 3 GB for photo_index) and every entry can be made again; a photo that leaves the library takes its thumbnail with it. Exit status 1 when the library holds a root this machine does not place, or when the cache folder cannot be written (the thumbnails were made and not kept: it says so).

### `repair-vectors [--apply]`

Makes the vectors of turned photos (an EXIF Orientation of 2 to 8) describe the photo as it is shown (`tagpup.services.embedding_repair`; docs/AI_PIPELINE.md, phase 1f; #1529). Vectors made before CLIP was shown photos upright were kept, since their file's stamp still matches. A row holds no Orientation and a vector no age, so the turned photos are found by each file's header (never the picture), and a vector is told stale by making it again. A dry run unless `--apply`: it prints its progress, then counts the photos looked at, the turned ones, those that hold a vector for their file as it is, those that hold none or one for an earlier file (the next look at those makes one upright), and those with no file, that could not be read or whose header does not read. A network share that does not answer is asked once, named, and its photos counted as left (run it again when it is back), never as unreadable. With `--apply` each is embedded again by `PhotoEmbeddings`, the one writer of vectors, on a turn of the graphics card taken through the runtime (one process at a time; it says who holds the card while it waits); a file turned or edited since the look is embedded as it now is and stamped so. The run says how many were replaced (different, and read back as written), how many already matched, how many were not kept (the file changed meanwhile: the next look makes them), how many were damaged, and how many could not be made, by the name of the failure (out of memory, say); none of those ends the run. Run again it finds each already matching and keeps what it made; stopped (Ctrl+C) it keeps what was made and the next run resumes by what the table holds. Face vectors and boxes are not touched. The write is not a journaled change (the table is derived). A running TagPup keeps the vectors it loaded until a photo is edited or the app is restarted: restart it to search by the new ones. Exit status 1 when the library holds a root this machine does not place.

### `roots`
The library's roots (`tagpup.services.roots.listing`): for each, its name, address, when it was added and where this machine keeps it, or "NOT PLACED on this machine" with the map's file when it does not. A library with no roots says so: every path in it is this machine's own. Reads only.

### `roots check`
Whether every rooted row converts back: its root is the library's and this machine places it (`tagpup.services.roots.check`). Says the library's roots are in order, or one line for each problem and exit status 1. Reads only; changes nothing in the library or the map.

### `snapshots list`
The library's snapshots (`tagpup.services.snapshots`), its backup: three dailies, one weekly and one monthly in `data/backups/<library>/daily|weekly|monthly`, and the last two `before-restore` copies. Each by name (`daily/20260926_090000`), when it was taken, its size and how many of the journal's changes were made since -- what restoring it would lose -- and the disk they take together. A daily is taken when the newest is a day old, the weekly and the monthly from that same copy when theirs are 7 and 30 days old; each is copied in one step of SQLite's backup API, one read transaction, while writers go on, checked (`PRAGMA quick_check`) under a temporary name and only then renamed into place, and an old one is removed only after its replacement passed. photo_index (2.5 GB) takes about 12 s to copy and about 13 GB for five.

### `snapshots restore NAME [--apply]`
Says which of the journal's changes the library holds that snapshot NAME does not, by id, time and operation: what restoring it would lose; and what it costs: about three times the library free on the disk (the library as it is, snapshotted first, and the copy back through its WAL) against what the disk has, and about half a minute for photo_index. Refused, with nothing written, on a disk without the room. With `--apply`, snapshots the library as it is first (`before-restore/<time>`, which `snapshots restore` puts back), then copies NAME into the library through SQLite's backup API, into the file the apps have open, and checkpoints the WAL without waiting for readers (one a reader held up is logged, and finished by the next checkpoint). Photo files written since keep what was written; the rows go back. Refused, exit status 1, for a snapshot made by a newer TagPup.

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)

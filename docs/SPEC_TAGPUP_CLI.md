# TagpupCLI — System Specification

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

AI-powered local tag inference for photo libraries. Runs entirely on your local Windows PC — offline, private, and requiring no cloud API keys.

---

## 1. System Architecture & Components

The tool is built as a modular Python application with script wrappers. It relies on the following key components:

```
[Untagged Image] ---> [ClipEmbedder (SigLIP 2)] ---> 1152-dim Vector
                                                              |
                                                              v
[Tagged Library] ---> [field_values.Fields]     ---> [PhotoIndex (SQLite + FAISS)]
                               |                              | (Query k-NN)
                               v                              v
                       [TagTaxonomy]             ---> [TagSuggester] (Face Match)
                               |                              | (Aggregate & Score)
                               v                              v
                       [photo_index.db: tree]   ---> [suggestions kept in the library]
```

- **`tagpup_cli.py` (CLI entry point)**: Unified Command Line Interface using `click` and `rich`.
- **`tagpup/files/field_values.py` (`Fields.whole`) and `tagpup/files/metadata.py` (the record)**: one ExifTool, opened through `runtime.fields`, reads standard metadata fields in batches of 500, and metadata.py builds each record. Handles bare and namespaced tag keys. What the fields mean -- tags, people, captions -- is `tagpup/core/vocabulary.py`; the library's people are read by `tagpup/store/taxonomy.py` and handed to each batch.
- **`tagpup/runtime.py` (The models)**: Builds the CLIP model and the face models once for each set of settings, from the settings of the library the command is given (`--db`; `tagpup.services.settings`), and hands them to what needs them. Nothing else builds a model.
- **`tagpup/ml/clip.py` (Visual Embeddings)**: The photo model (SigLIP 2 SO400M, `ViT-SO400M-14-SigLIP2` / `webli`, at its native 224 pixels, every library's) using PyTorch (supporting GPU/CUDA acceleration if available with FP16 half-precision, or falling back to CPU). Generates normalized embeddings. `tagpup/services/search.py` (`PhotoEmbeddings`) keeps each photo's in the library, so an unchanged file is not embedded again.
- **`tagpup/services/search.py` (SQLite & FAISS Vector Index)**: `PhotoIndex` loads a library's photo records and their vectors from the store (`tagpup/store/`) and builds an in-memory `faiss.IndexFlatIP` flat index (`tagpup/ml/vector_index.py`) for rapid cosine similarity queries.
- **`tagpup/ml/scrfd_adaface.py` (Face Recognition)**: Detects face bounding boxes and generates face vectors with the face model, **SCRFD + AdaFace** (`scrfd-adaface`, every library's; MTCNN and FaceNet were deleted 2026-10-28), on the GPU in half precision or on the processor.
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
| **Disk Space** | One-time download of the SigLIP 2 SO400M photo model's weights (about 4 GB in the Hugging Face cache). Face models require ~112 MB. Cache consumes ~4 KB per photo indexed. |
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
- **`scikit-learn`**: Machine learning library for DBSCAN clustering algorithms.

---

## 4. Metadata Fields Processed

The system reads and writes metadata using the following tags:

### Read Fields
- **Keywords / Tags**: `IPTC:Keywords`, `XMP:Subject`, `XMP:HierarchicalSubject`
- **People / Faces**: the leaf of a hierarchical tag under a root the library's tree marks as holding faces (e.g., `John Doe` from `Family/Immediate/John Doe`). `XMP:PersonInImage` and `XMP:RegionName` are not read at all (owner, 2026-10-15).
- **Titles**: `XMP:Title`, `IPTC:ObjectName` (the title shown is the Title, else the Description: `fields.title_of`)
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
- **Titles**: Written to `XMP:Title` and `IPTC:ObjectName` by the photo panel's save (`fields.title_fields`).
- **Captions**: Held in `XMP:Description`, `IPTC:Caption-Abstract`, `EXIF:ImageDescription` (which maps to `System.Title` in Windows), and `EXIF:XPComment` (which maps to `System.Comment`/Caption in Windows); no writer sets them, and a save clears only those holding the text it moves into the title (DECISIONS 2026-11-06).

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
  all. A merge (`merge_duplicate_person_tags`, the MCP server's tool, deleted 2026-10-28) removed
  the 53 that already existed, across 129 photos here; its change is listed in History and cannot be undone: the library is past the schema it was made at.
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
- Bounding boxes are found by SCRFD (score $\ge 0.5$, at least $15\text{px}$ a side; a new face at least $40\text{px}$ on its shorter side, `scrfd_adaface.NEW_FACE_FLOOR`) and each face's vector made by AdaFace.
- Faces are clustered using DBSCAN at the library's face model's grouping radius (`tagpup.core.clustering.Cuts.group_at`: a cosine similarity of $\ge 0.80$ for `scrfd-adaface`, the one face model, Euclidean epsilon `0.632`).
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
Scans and indexes a photo library recursively: one or more directories, in one run (the models are loaded once; sync hands it every folder of new files at once). ffmpeg is a prerequisite (`tagpup.config.ffmpeg_problem`: found, and `-version` runs, for ffmpeg and ffprobe): without it the run is refused before it opens anything, "Not run: ffmpeg not found; install it with winget install Gyan.FFmpeg" (or, when it is found and does not run, "... reinstall it: winget uninstall Gyan.FFmpeg, then winget install Gyan.FFmpeg"), exit status 1, nothing written. Indexing a folder adds it to the library: it is recorded as added (`tagpup.services.libraries.record_added`; with `--no-subfolders`, without its subfolders), as TagPup's Add and TagTuner's Add Folder record it, and each photo's row is made as it is read.
**After an index that recorded a photo, it matches the faces it made with the named ones, and writes nothing** (owner, DECISIONS 2026-10-23; `tagpup.services.faces.automatch_new`): the faces whose ids lie between the newest face read as its turn on the graphics card began and the one read before it let the turn go, decided by Re-examine's matcher against the faces a person decided. This command is what both apps' queues and sync's run, so every index does it alike. It prints one line beginning `New faces: `, counted: how many of the new faces have a likely name (open Re-examine on their folder to confirm them), or that none looks enough like anyone named, or that no new face waits for a name; and how many of their photos carry a person's keyword that no face has (Name faces from tags, or `cluster-faces`, may name them). The server's queue ends the folder's message with that sentence, read whole. No face, row or file is written, so nothing is refused meanwhile; a matching that fails says so on the same line and the run still exits 0, the photos indexed. A run that recorded no photo (everything current) says nothing of faces. Stopped while it waits for the card (exit status 76), it indexed and matched nothing.
**What it prints is a person's, unless the server asks** (`tagpup.core.progress_line`): run by hand it prints the lines this section names (`New faces: `, the bake lines, `Photos that failed: `) and draws its bars, and the bake's folder lines are in the log alone. Started by the server's index queue (`tagpup.services.indexing.index_folder`, which sets `TAGPUP_PROGRESS_LINES=1` in its environment), it prints instead one typed fact a line, `@tagpup ` and the fact as JSON in ASCII -- each phase's counts and its percent, the wait for the graphics card, each bake folder as it begins and as each chunk of its write ends, the bake's line, what it read from the bakes, the new faces and the photos that failed -- draws no bar and logs to its run's own file alone; the server reads the facts as values and shows any other line (a traceback) as it is. The first line that cannot be said (the server gone: a pipe with no reader) is logged once to the run's file, and nothing more of that index is said -- no fact, no person's line, no library's print: `tagpup_cli._Pipe` is the one writer of the pipe, stdout and stderr -- and the index goes on to its end (its faces matched, its folders baked) and exits as it would have.
**Which photos it works out, and the line that counts them** (owner, DECISIONS 2026-10-27; `photo_fingerprints.same_picture`, the one rule; which photos are owed work is `photo_fingerprints.owed`, the answer `sync` gives too: no vector under the library's photo model, a vector whose stamp does not describe its file, a current vector with no fingerprint row, or never examined for faces, the last not asked with `--skip-faces`): a photo owed only its fingerprint row is not read again: its folder goes to the bake after the index, which writes its file from the library with no model (the row follows the write) or records the decode's row for a file holding its bake or one marked read-only (any other failed write, a conflict, a photo not answered or not reached stays owed: the bake takes away the index's rows of this run's photos before it plans, so one it did not write, find holding its bake or find read-only -- however the run ended -- is owed at the next sync), said first in one line (`tagpup_cli.BAKE_ONLY_LINE`, counts only) and in the bake line (`N fingerprint(s) recorded with no write`). A photo whose row and vector's stamp describe its file is current and not read, unless it was never examined for faces by the library's face model (no face row, no `faces_detected` record under it; asked by the model's name, `services.faces.detector_named`, no model made): then its faces are detected on the picture as shown and its vector kept. A photo whose vector's stamp no longer describes its file (another program wrote its metadata, or its picture) is decoded: when the decode's fingerprint equals its `photo_fingerprints` row the vector is kept and stamped again with the file, no model run and no bake read, its faces kept; otherwise (a different picture, or no row) its vector and faces are worked out again, or read from a current bake. **Whether a model is needed is decided before one is built** (`tagpup_cli._kept_with_no_model`): when every photo read is kept, by its stamp or by its fingerprint (none never examined for faces, none holding a bake, none with no vector or a different picture), the run builds no model, imports no model's library (open_clip, torch) and takes no turn on the graphics card -- it never waits behind Suggest or another index -- and still stamps each photo again with its fingerprint row, reads its row again and ends with the line below; each stale photo is decoded once, its decode kept for the index's read. Otherwise the models are built and the turn taken once, for the run, as before. `--force-reembed` works every photo out again. After the bake line, every run that read a photo ends with one line of counts: `Photo vectors: N kept (the file as it was), N kept with the picture unchanged (only the file's metadata changed: stamped again, no model run), N read from a bake, N worked out; N photo(s) never examined for faces looked at.` (`tagpup_cli.KEPT_LINE`): the first four add up to the photos the run indexed (the current ones it did not read among the first); a never-examined photo is counted as looked at only when its detection ran (one that failed records nothing and is owed still); counted as each batch is recorded: a run stopped before a batch is recorded counts none of it, and the stamps of that batch stay stale until the next run. The bake line says `worked out on the graphics card` and `needed no graphics card` when a model of the run is on the card, `worked out on the processor` and `needed no model` when none is (`runtime.on_the_card`).

- **Usage**: `run.bat [global-options] index <DIRECTORY> [<DIRECTORY> ...]`, or `index --folders-file <FILE>`
- **Options**:
  - `--folders-file`: A file of folders to index as well as any DIRECTORY, UTF-8, one a line (each line a folder to its last character; blank lines skipped), checked as a DIRECTORY is. How the server hands its indexer the folders of a job (`tagpup.services.indexing.index_folder`, always; the file is its own and removed when the run ends): a sync's few hundred folders on the command line passed Windows's 32,767 characters and the indexer never started (#2149). A file that cannot be read, or is not UTF-8, is a sentence and exit status 2, and nothing is indexed; neither a DIRECTORY nor this option is refused as a missing argument.
  - `--force-reembed`: Force recreation of all CLIP visual embeddings.
  - `--reset`: Delete the existing SQLite database index and taxonomy files to start fresh.
  - `--skip-faces`: Skip face detection and face vectors (SCRFD and AdaFace) during indexing.
  - `--no-subfolders`: Only the photos directly in each DIRECTORY (`images.photos_in`): what sync queues for a folder the library holds, whose subfolders may be folders to review or ignored.

#### 2. `cluster-faces`
Run by hand, with no options (an index matches only the faces it made, and writes nothing; DECISIONS 2026-10-23). It clusters the face embeddings and names the faces from their photos' people (`tagpup.services.identities.resolve`). Names a person gave (`name_source = 'manual'`), their "nobody" decisions and exclusions are kept; a face is written only while it is still as the run read it, and a person is written by id. Two or more faces of a photo
whose keywords name as many people no face is known for stay unnamed (nothing tells which is whose; owner, 2026-10-15), and
so does a face whose people tie in its votes; the run says how many faces it left so and how many earlier guesses it took
back.

### `compact [--apply]`
Says how much of the library's file holds nothing: pages left free by deleted rows and dropped columns, which SQLite keeps until the file is rewritten. With `--apply`, backs the library up (`db.backup`, into `backups/`), then rewrites it without them (`VACUUM`, `db.compact`) and says the size before and after. The rewrite needs the file to itself; close the apps first. After phase 4's migrations photo_index holds about 1 GB free of 4 GB.

### `history [--change ID] [--limit N] [--reveal]`
The library's journal (`tagpup.services.journal`): the changes the maintenance operations, the pages and the CLI applied, newest first -- each one's id, operation, status (`applied`, `derived_pending`, `undone`), when it was made and undone, the schema version it was made at, and how many rows of each table it inserted, updated and deleted. `--change ID` shows that change alone with the keys (ids) of every row it wrote; with `--reveal` too, each column's value before and after, a BLOB by its size. Values can name people, so they are shown only when asked.

### `undo ID [--apply]`
Undoes change ID of the journal. Without `--apply` it rehearses: undoes the change and applies it again inside a transaction rolled back, and says whether that restored every row exactly. With `--apply` it writes the old values back, only where every row is still what the change left. Refused, with nothing written and exit status 1, when a row has changed since, when a newer change touched the same rows (named), or when the library's schema has moved on since the change was made -- and for a stamp of the library's settings, its first settings, which are changed rather than undone, and for a change whose undo would put back FaceNet (`faces.model` `mtcnn-facenet`, to which no library is switched: the switch's own rule and sentence, `settings.NOT_FACENET`, asked by `tagpup.services.settings.undo_check`; each live library's first switch, which inserted the row, among them) -- the one account of `tagpup.services.journal.why_not` over `UNDO_CHECKS`, whose every reason SPEC_TAGPUP_GUI.md lists (`/api/history`, `why_not`).

### `sync [--folder FOLDER] [--apply]`
With `--apply` (it indexes the new files) ffmpeg is a prerequisite, as for `index`: without it "Not run: ..." and exit status 1, nothing written; the dry run needs none.
Brings the library in step with its folders (`tagpup.runtime.Runtime.sync`, over `tagpup.services.sync`; ARCHITECTURE.md, phase 8): walks the library's root folders (`library.roots`) and every folder it holds photos in, each once from the topmost, or FOLDER, and compares each file with its row by path, size and modified time. Says how many files are new (and in how many folders), changed, moved and missing, how many photos the index owes work and why (`to_check`, `photo_fingerprints.owed`, the answer `index` gives too: a photo with no vector under the library's photo model, one whose vector's stamp no longer describes its file by `photo_fingerprints.same_picture`, one never examined for faces by the library's face model, and one whose current vector's picture was never fingerprinted (Suggest's vector, a re-embedding pass's), whose file the index's bake writes; one line, `tagpup_cli.OWED_LINE`: `N photo(s) in N folder(s) the index owes work: N with no photo vector, N whose file changed since its vector was made (the index keeps a vector whose picture is the same), N never examined for faces, N whose file the index's bake writes (its picture never fingerprinted). Their folders go to the index.`, a photo counted once in its total and under each reason it has, followed by `tagpup_cli.OWED_HELD_BACK`, ` N of those folder(s) are held back: a new file in each may be a copy of a missing photo; settle that first.`, when any is; their folders are queued for the index as a new file's is, but a folder held back, and the index keeps the vector of a picture that did not change and makes one again for an edited picture; sync decodes nothing; while any is owed the library is not in step), and how many folders are wholly gone -- among them a folder the library holds (a root, a folder its rows are in, a folder added) that is not there at all, as on an unplugged drive. A dry run unless `--apply`, reading only the changed files (and, where a row is missing, the DocumentIDs of the new files its name, size and modified time did not match); nothing is written. With `--apply`: the changed rows are read again from their files (`refresh_rows.reread`, `edits_for`), and the rows of moved files follow them with their faces, as one change of the journal (`sync`), which `undo` takes back; the folders the library holds that hold new files, or a file to check, are indexed, each without its subfolders, one at a time, and the command waits for them; a folder under a root holding no indexed photo is counted as a folder to review, never indexed on its own (TagTuner's gear, Folders to review), and one under an ignored folder (`library.ignored`) is passed over; and the rows of missing files are removed with their faces in the same change, by the rule of `/api/sync` (docs/SPEC_TAGTUNER.md; owner, DECISIONS 2026-10-28): only by a sync of the whole library that looked at every folder, for a file not paired as a move nor held back as a possible copy, its root confirmed there, and found gone again by a second walk made only then; the dry run says how many it would remove and their faces and named faces (`N of the missing row(s) would be removed, with N face(s), N of them named (undoably)`), and the applied run says what the change removed (`N of the missing row(s) were removed, ...`, a row another sync took meanwhile not counted) and its `Wrote` line the same. An unplugged drive, a root gone or repointed at an empty folder, and a sync of one FOLDER remove nothing. Each applied run is recorded in the library (`sync_runs`), which the pages show as "last in step". Counts only; exit status 1 when refused -- FOLDER not a full path, or holding no photo of the library and under none of its root folders -- or something failed. **A renamed folder** is not followed by anything but the pairing above (name, size and modified time, or a file renamed in its own folder by its folder, size and modified time, then DocumentID): the files of one renamed and saved again before the next sync that pair are followed, and the rows of those that do not are removed with their faces (`undo` puts them back); its new files are indexed as new. The hidden `.tagpup` files that `folder-ids mark` once wrote are ignored (nothing reads or deletes them; the command is gone, owner 2026-10-10).

### `tags-from-faces [--apply] [--guesses] [--folder FOLDER]`
**`--folder FOLDER`** (#994) counts and writes only the photos under that folder and its subfolders (the folder is spelled by its root's first place, as in `tagpup.services.folder_scope`); the rule for each photo is the same, so the folder's plan is the whole-library plan restricted to its photos. A folder the library holds no photo under is refused (exit status 1).

Puts on a photo the people its faces name and its keywords do not (`tagpup.services.tags_from_faces`, over `tagpup.services.face_people`; #861). A face named and the photo's person tag are one decision; faces named before that (TagTuner's strip, Match, AutoMatch All, Identify Faces, clustering) left photos listing a person from a face alone (`photo_people` source `face`), "a green box and No people tags". A dry run unless `--apply`: counts only, never a name or a path -- the photos, the people, how many photo files would be written, and how many people are left: filed by the tag tree in two places (or under several people roots), for you to place; named in the photo's keywords already under a root the tree does not file people under (`Parkrunner/<name>`: the same rule as the keyword writer, the leaf under any root; a tree question -- make that root a people root, or retag), so the count to write is what `--apply` writes and a second run finds none; and "from a guess only" -- no face of that name was named by hand, only by clustering or automatch. `--guesses` writes those too, with a printed warning: the keyword makes each guess a decided reference for the faces compared with it afterwards (#640). **`--apply` changes photo files**: it writes each person's keyword into the photo's file with ExifTool, one change of the journal (operation `person tags from faces`; `history` lists it, `undo` takes it back, recording every file's fields before and after -- no copy of the library is taken), a batch of photo files written 25 at a time, its progress on the Activity page. Ctrl+C or Ctrl+Break stops it: the files written stay written, applied and undoable, the photos not reached are taken out of the change (not written by the next program to open the library), and it says how many it wrote. A file ExifTool cannot read or write is counted and the others are written (exit status 1, run it again); a file changed since it was read is a conflict, reported and never overwritten; TagPup and TagTuner may stay open. The doctor (`tools/doctor.py`) reports the same count as "photos listing a person from a face alone" (reported, not broken).

### `retire-photo-vectors [--apply]`
Retires the photo vectors of every model a library is no longer on, after `embed-photos` and `set-photo-model` (`tagpup.services.retire_photo_vectors`; docs/AI_PIPELINE.md, phase 5; owner, 2026-10-24): every vector in `embeddings` under a key other than the library's own (`settings.photo_key_on`), which no reader keyed by the library's model reads, and the words Suggest cached in `tag_embeddings` under each such key's model, unless that model and its weights are the library's own (an Image size change makes a key of the same model). No photo, keyword, face or tag is touched. Names the library first, then the library's own key and how many of its photos hold a vector under it (`settings.PHOTOS_HELD`), then for each other key the vectors that go, their megabytes, and the words and rows that go with them. A dry run unless `--apply`: counts only, nothing written, no snapshot; a library holding no vector under another key says "Nothing to retire". Refused, with nothing written (exit status 1): a library more of whose photos than a switch allows (5%) hold no vector under its own key (the vectors that would go are the most complete it holds: switch first); with `--apply`, any running app (`launcher.running`: a running app keeps the vectors it read in memory), asked again before each chunk: an app started during the run stops it there, the chunks written standing, and it says so. **`--apply`** takes a snapshot of the library first, always (`snapshots.take(force=True)`, even when a daily from today stands: the oldest daily may go), since it is the way back -- while it stands: it is a daily, three are kept (`snapshots.KEEP`), and the app's own daily snapshots push it out in about three days; then deletes 5,000 vectors a transaction (`retire_photo_vectors.VECTOR_CHUNK`), walked by rowid, the library's key read again in each (a switch beside the run ends it, nothing of that chunk written, exit status 1), the words in the first chunk of a key, as ONE change of the journal a key, holding no rows ("retire vectors (<key>)"), its summary the vectors, bytes and word rows deleted, so `history` and `snapshots list` say a restore loses it. It says what was committed, never what was planned. Ctrl+C or Ctrl+Break, or an app started during the run, stops it after the chunk under way: a run again continues the same change and takes no second snapshot. The change is not undone: `undo` of it is refused, naming the snapshot, and so is the undo of the switch away from a retired model and a switch back to it (the switch's own gate: no photo holds a vector under it), which the last line of a finished run says. After it, `compact`, with the apps closed, gives the space back to the disk. The live journals also hold one change of the old face model's vectors ("retire vectors (mtcnn-facenet)") and one retiring of the old detector's faces ("retire faces (scrfd-adaface)") each, made by `retire-vectors` and `retire-faces`, deleted once applied in every library (owner, 2026-10-25: the old model goes away completely): `undo` of either is refused, naming the snapshot. They hold one deletion of the old detector's rows each too ("delete old detector rows (mtcnn)", changes 4871 / 371 / 1235), made by `delete-old-detector-rows`, deleted 2026-10-28: its `undo` is refused, the settings it deleted being ones no library has now.

### `thumbs warm [--folder FOLDER] [--limit N] [--apply]`
Makes the library's thumbnails ahead of the first time each is asked for (`tagpup.services.thumbnails.warm`; ARCHITECTURE.md, phase 9a-2): a 300 px JPEG of each photo, and of each video its poster frame (the middle frame, through ffmpeg; "Of them, N are the poster frames of videos."; a poster is given up on after 120 s, and a clip that took longer is counted apart, not as damaged, and tried again by the next run: "N video(s) took too long to make a poster of (not damaged): run it again to try them once more."), kept under `cache/<library>/thumbs` beside the library, which the library views show instead of decoding every photo's file again. A dry run unless `--apply`: it reads the library and each file's size and modified time -- never the picture -- and says how many photos already have a thumbnail for their file as it is now, how many need one and about how much they will take (the average entry already kept, or 30 KB when none is), how many have no file there, how many are damaged (recorded so, or found not to decode: they are not decoded again until their file changes, and nothing is kept for them), and how many could not be reached (a share away). It writes nothing, not even the cache folder. With `--apply` it makes them, four at a time, and prints its progress after each 500 photos; a library behind this version's schema is brought up to date first, and the thumbnails of photos the library no longer holds (a restore, a delete outside TagPup) are deleted. It can be stopped (Ctrl+C) and run again: every thumbnail is written whole or not at all, what was made is found there, and the run goes on from it. `--limit N` stops after making N, so a large library can be filled in parts. `--folder` limits it to the photos under that folder. The cache is not bounded (the owner's decision: 1.5 to 3 GB for photo_index) and every entry can be made again; a photo that leaves the library takes its thumbnail with it. Exit status 1 when the library holds a root this machine does not place, or when the cache folder cannot be written (the thumbnails were made and not kept: it says so). ffmpeg is a prerequisite (`tagpup.config.ffmpeg_problem`: found, and `-version` runs, for ffmpeg and ffprobe): without it the run is refused before it opens anything, "Not run: ffmpeg not found; install it with winget install Gyan.FFmpeg" (or, when it is found and does not run, "... reinstall it: winget uninstall Gyan.FFmpeg, then winget install Gyan.FFmpeg"), exit status 1, nothing written. A dry run too: it is asked before anything.

### `embed-photos --model NAME:WEIGHTS [--apply]`

Makes another photo model's vectors for the library's photos, beside its own model's (`tagpup.services.photo_reembed`; docs/AI_PIPELINE.md, phase 5), so the gear's switch to that model, refused while more than 5% of the photos hold none (`settings.photo_switch_on`), can pass. NAME:WEIGHTS is one of the models TagPup knows (`tagpup.ml.photo_models.MODELS`), never the library's own (refused, exit status 1). The vectors are kept under the model's key at its own image size; the library's model, key and settings are not changed, and nothing reads the new vectors until the switch. A dry run unless `--apply`: counts the photos, those holding a vector under the key already, those holding none and how many of them have no file, and the time `--apply` would take (0.04 s a photo, an estimated 2 s a video). With `--apply` (which needs ffmpeg: without it, "Not run: " and the sentence that says what to install, exit status 1, nothing written, while a dry run needs none; a library behind this version brought up to date first) it waits in order for a turn of the graphics card (saying who holds it; Ctrl+C cancels the wait, nothing written), then reads which photos hold none -- a run that waited behind another finds what it made -- and embeds them, read six ahead, kept 256 at a time at the row each was read from (a photo removed meanwhile is counted as not kept and never given a row back), printing its progress after each batch. A video's vector is made of its five frames, read by ffmpeg, as the index makes it: frame 0's is its vector, frames 1 to 4 are kept beside it in the same write; a video ffmpeg cannot read as it is is not counted as unread but listed after the run (its count, then up to five with ffmpeg's sentence), and tried again by the next run. A file not found is counted as gone, and a file that does not decode or cannot be read is counted by why, each apart and tried again by the next run; when the two together pass 1% of the run (and 20), it stops, keeping what was made, with "N of M photo files were not found or could not be read: is the drive or share connected?" (Windows answers an unmounted drive and a share that stopped answering as files not found; a library's files really gone are a handful). It ends with what it kept and how many of the library's photos now hold a vector under the key. Ctrl+C stops it after the photo under way, keeping what was made; run it again to continue. Faces are not touched; the write is not a journaled change (the table is derived). Exit status: 0 when it ran to the end (a dry run too); 1 for a model refused, a wait for the card cancelled, a pass stopped by too many photo files not found or not read, or a root this machine does not place; 130 when stopped by Ctrl+C after a photo. The next step is `set-photo-model` (below); the run's recipe is docs/DEVELOPMENT.md, "Switching a library's photo model".

### `set-photo-model NAME:WEIGHTS [--apply]`

Switches the library to the photo model NAME:WEIGHTS at its own image size, as the gear's switch does (`tagpup.services.photo_reembed.switch`, over `tagpup.services.settings.change`, acknowledging the `clip` group as the gear's dialog asks the owner to tick it): `model.name`, `model.pretrained` and `model.force_image_size` (emptied) as ONE change of the journal ("change settings"). NAME:WEIGHTS is one of the models TagPup knows (`tagpup.ml.photo_models.MODELS`); another is refused before anything is read ("Not run: ...", exit status 1). It prints the gate's counts (`settings.photo_switch_on`: how many of the library's photos hold a vector under the new key, and how many none), and how many words Suggest holds made under the new model against those it holds under the library's (`tag_embeddings`, read-only): nothing makes them ahead, Suggest makes the rest at its first run after the switch, one text embedding a word and prompt (photo_index holds 540 words under ViT-H, 1,080 rows, counted 2026-10-24; the time was not measured). Refused, with the gate's sentence ("Nothing was written: ...") and exit status 1, nothing written: more than 5% of the photos holding no vector under the new key (`embed-photos --apply` makes them), or a size forced on a model that takes none. A dry run unless `--apply`: the same counts and verdict, "--apply would switch it. Nothing was written." With `--apply` it prints the change's id and `undo <id> --apply`; the undo puts the old model back and is held to the same gate (`settings.undo_check`: refused while more than 5% of the photos hold no vector under the old key, after its vectors were retired). A library already on the model says so and changes nothing. Search and Suggest read the library's settings at each run, so a running TagPup uses the new model's vectors from its next search (`Runtime.photo_index` makes the index again when the key changed). Exit status 1 when the library holds a root this machine does not place.

### `metadata-check [--sample N] [--seed S] [--reveal]`
Compares TagPup's own JPEG writer with ExifTool's on copies (`tagpup.services.metadata_check`; docs/PLAN_OWN_METADATA.md, M4 step 4c): of the library's JPEGs, N (default 100; the bar is 3,000 a library) drawn with seed S (the census's seed by default, `segment_census.drawn`, so both read the same photos) are each read through `shares` (a photo gone, changed while read, unreadable or on a share that does not answer is counted and passed over) into a sandbox folder in this PC's temporary folder. Eight writes are planned as the app plans them from the fields each copy holds -- a keyword added, the last keyword taken off, a person added, a caption set, the caption cleared, Date Taken moved 90 minutes, a turn right, the name it had kept (Smart Rename's PreservedFileName); the keyword, caption and name hold letters outside ASCII -- each skipped when the copy holds it already, and each written twice, on fresh copies in a folder of its own, through the one writer (`field_values.write`): once by ExifTool, once by the own writer. The two copies are compared by what they hold: every segment but EXIF, XMP and IPTC and the picture must be the original's; the fields written must read back as written (a copy the own writer left stale holds what it held); and ExifTool's whole read of the two must agree tag for tag, but for the tags the writers leave differently by design (the XMP toolkit; where the thumbnail lies in the EXIF block), and with what ExifTool says as it reads each (its warning, `-validate`'s counts): the own writer's copy saying other than ExifTool's copy and than the original is a difference, since a copy ExifTool heals as it reads may be refused by another reader; and each side's second write of the same values must change no byte. How each side wrote (patch, rewrite, unchanged, failed) is counted by its direction; a patch on one side and a rewrite on the other is no difference. **It never writes the library or its photos**; the sandbox is removed at the end however the run ends (Ctrl+C too), and one a killed run left is removed by the next. Counts only -- no path, name, tag or value -- unless `--reveal`, which lists each difference's photo, operation and values (paths and values can name people). Exit status: 0 when the writers agreed on every photo compared; 1 when they differ anywhere, or when refused, nothing compared (N under one, a library holding a root this machine does not place, another `metadata-check` running on this PC); 2 when the run stopped early with no difference found (Ctrl+C, or the sandbox's disk without room for the next photo's copies beyond 1 GB, checked before each).

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

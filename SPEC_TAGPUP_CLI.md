# TagpupCLI — System Specification

---
[◀ Back to README](README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
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
                       [photo_taxonomy.json]    ---> [suggestions.json]
                                                              |
                                                              v
                                                      [MetadataWriter (ExifTool)]
                                                              |
                                                              v
                                                      [Tagged Image]
```

- **`tagpup_cli.py` (CLI entry point)**: Unified Command Line Interface using `click` and `rich`.
- **`scripts/metadata.py` (Metadata Extraction)**: Interfaces with `exiftool` to read standard metadata fields in batches of 500. Handles bare and namespaced tag keys.
- **`scripts/embedder.py` (Visual Embeddings)**: Loads CLIP (`ViT-H-14` by default, customizable resolution up to $512 \times 512$) using PyTorch (supporting GPU/CUDA acceleration if available with FP16 half-precision, or falling back to CPU). Generates normalized embeddings and maintains a local cache to avoid re-embedding unchanged files.
- **`scripts/index.py` (SQLite & FAISS Vector Index)**: Manages an SQLite database (`photo_index.db`) containing indexed photo records and parallel face coordinate/embedding entries, and builds an in-memory `faiss.IndexFlatIP` flat index at runtime for rapid cosine similarity queries.
- **`scripts/faces.py` (Face Recognition & Identity Clustering)**: Detects face bounding boxes using **MTCNN** and generates 512-dimensional face vectors using **InceptionResnetV1** (supporting GPU/CUDA and FP16 acceleration). Performs density-based clustering (**DBSCAN**) to resolve and assign names to visual identities based on co-occurrence tagging patterns.
- **`scripts/taxonomy.py` (Hierarchical Tag Taxonomy)**: Builds and updates a tree of all known hierarchical paths (e.g. `Family/Immediate/John Doe`). Resolves leaf tags to their ancestors.
- **`scripts/suggester.py` (Tag Suggestion Engine)**: Scores tags using cosine similarity of nearest visual neighbors and boosts matched tags if specific face embeddings are recognized in the target image.
- **`scripts/writer.py` (Metadata Writer)**: Writes suggested tags and derived captions back to photos using ExifTool. Creates default `_original` backup files.

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
- **People / Faces**: `XMP:PersonInImage`, `XMP:RegionName`, plus leaf nodes extracted from hierarchical tags starting with `Family/` or `Friends/` (e.g., `John Doe` from `Family/Immediate/John Doe`).
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
- **People Enter The Taxonomy Under A People Root**:`extract_people()` returns *leaf*
  names -- `photos.people` is the flattened view used for display and matching -- so
  passing that list to `taxonomy.add_tags()` treated each leaf as a whole path and
  minted a bare root node per person, beside the `People/<name>` the hierarchical
  keyword had already created. Indexing calls `taxonomy.add_people()` instead, which
  files a person under the root the library already uses for people (`People`,
  `Family`, `Friends`, or one marked `has_face`) and does nothing at all when that
  person is already in the taxonomy somewhere.

  A newly seen person is filed at the depth the library already uses: where people live
  at `Family/Immediate/<name>`, a new one joins them there rather than appearing at
  `Family/<name>`, which would be a second and shallower home for people.

  `add_tag()` additionally refusesto create a bare root that an existing **people**
  path already claims: photo files carry both forms in the wild, and a file saying
  `Cora Ingersoll` beside a taxonomy saying `People/Cora Ingersoll` gave that person two homes.
  Only people are folded this way -- `Kentridge` beside `School/Kentridge` is left
  alone, since tidying that for every tag is a different question.

  This matters because indexing runs repeatedly: the kr-track taxonomy had been cleaned
  from 51 such duplicates to zero once before, and the next index run recreated them
  all. `scripts/merge_duplicate_person_tags.py` merges any that already exist (dry-run
  by default, `--apply` to write); it removed 53 across 129 photos here.
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
- **Reset Option**: The CLI accepts a `--reset` option which deletes the existing database file (`photo_index.db`) and taxonomy configuration (`photo_taxonomy.json`), enabling developers and users to start a clean index scan.

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
- Faces are clustered using DBSCAN (Euclidean epsilon = `0.48`, representing a cosine similarity of $\ge 0.885$ on normalized face vectors).
- **Cluster Naming**: Visual groups are assigned people identities based on tag voting. If a photo has only one face and one name tag (e.g. `John Doe`), it acts as an anchor vote.
- **Centroid Validation & Refinement**: During iterative propagation, resolved assignments are validated against the visual centroid of their resolved identities. Faces showing a similarity $< 0.80$ (Euclidean distance $\ge 0.63246$) to their respective identity centroid are unassigned/cleared to maintain visual profile purity.
- **Match Suggestion Boost**: Untagged images undergo face detection. Detected faces are matched against resolved database faces. If a face yields a similarity $\ge 0.85$ to a known profile, the matching person's tag is automatically boosted to a confidence score of `1.0`.

---

## 6. CLI Command Reference

The `tagpup_cli.py` engine is accessed via `click` subcommands. 

### Global Options
- `--test`: Use test database paths (`test_photo_index.db` and `test_photo_taxonomy.json`) to avoid altering the production database.

---

### Subcommands

#### 1. `index`
Scans and indexes a photo library recursively.
- **Usage**: `run.bat [global-options] index <DIRECTORY>`
- **Options**:
  - `--force-reembed`: Force recreation of all CLIP visual embeddings.
  - `--reset`: Delete the existing SQLite database index and taxonomy files to start fresh.
  - `--skip-faces`: Skip MTCNN face detection and FaceNet embedding extraction during indexing.

#### 2. `suggest`
Analyzes untagged photos and generates tag recommendations.
- **Usage**: `run.bat [global-options] suggest <DIRECTORY>`
- **Options**:
  - `--k INTEGER`: Number of nearest neighbors to consider (default: `15`).
  - `--min-sim FLOAT`: Cosine similarity cutoff (default: `0.35`).
  - `--output TEXT`: Path to write the output suggestions JSON file (default: `suggestions.json`).

#### 3. `write`
Writes suggested tags and descriptions back to photo file metadata using ExifTool.
- **Usage**: `run.bat [global-options] write <SUGGESTIONS_FILE>`
- **Options**:
  - `-Live`: Write tags to files for real (actually modifies image files on disk).
  - `-MinScore FLOAT`: Write tags at or above this score threshold (default: `0.50`).
  - `--nobackup`: Avoid creating backup copies (`_original` files) during write operations.

#### 4. `search`
Semantic natural language query against indexed visual vectors.
- **Usage**: `run.bat [global-options] search <QUERY>`
- **Options**:
  - `--k INTEGER`: Number of search results to return (default: `10`).

#### 5. `stats`
Displays overall database metrics, unique tags count, unique people count, top tags, top people, and taxonomy coverage.
- **Usage**: `run.bat [global-options] stats`

#### 6. `inspect`
Debugs and displays parsed metadata and raw read fields for a single photo file.
- **Usage**: `run.bat inspect <PHOTO_PATH>`

#### 7. `list-index`
Lists all photo filenames currently stored in the index.
- **Usage**: `run.bat [global-options] list-index`
- **Options**:
  - `--folder TEXT`: Filter list results to paths under this directory.

#### 8. `remove`
Deletes a photo or folder's indexed vector and metadata from the database.
- **Usage**: `run.bat [global-options] remove`
- **Options**:
  - `--path TEXT`: Remove a specific image path from the index.
  - `--folder TEXT`: Remove all indexed images under this directory recursively.

#### 9. `index-faces`
Manually scans photos and indexes face embeddings into the database.
- **Usage**: `run.bat [global-options] index-faces <DIRECTORY>`
- **Options**:
  - `--force`: Force re-detection of faces on already processed images.

#### 10. `cluster-faces`
Runs self-tuning identity resolution to cluster face embeddings and assign names.
- **Usage**: `run.bat [global-options] cluster-faces`
- **Options**:
  - `--reset`: Reset all face name assignments back to `NULL` before clustering.
  - `--max-iterations INTEGER`: Maximum iterations for propagation loop (default: `5`, set to `0` for anchor-only).

---
[◀ Back to README](README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)


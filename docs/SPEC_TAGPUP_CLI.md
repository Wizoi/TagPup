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
                       [photo_index.db: tree]   ---> [a table of suggested tags (a preview)]
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
- **`tagpup/services/tagging.py` (`write_suggestions`)**: Writes (path, tags, caption) back to photos using ExifTool as one change of photo files (`tagpup/services/file_changes.py`), each file recorded in the index as it is written; `undo <change>` puts the files back. No `_original` copies are made. No CLI command calls it since the JSON suggest/write flow was retired (2026-10-09); TagPup's pages write through the same journal.

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
- Faces are clustered using DBSCAN (Euclidean epsilon = `0.48`, representing a cosine similarity of $\ge 0.885$ on normalized face vectors).
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
- **Usage**: `run.bat [global-options] index <DIRECTORY> [<DIRECTORY> ...]`
- **Options**:
  - `--force-reembed`: Force recreation of all CLIP visual embeddings.
  - `--reset`: Delete the existing SQLite database index and taxonomy files to start fresh.
  - `--skip-faces`: Skip MTCNN face detection and FaceNet embedding extraction during indexing.
  - `--no-subfolders`: Only the photos directly in each DIRECTORY (`images.photos_in`): what sync queues for a folder the library holds, whose subfolders may be folders to review or ignored.

#### 2. `suggest`
Shows the tags TagPup would suggest for the photos of a folder, as a table. Nothing is written: no file, and no suggestions file (`--output` and the `write` command that read one were retired 2026-10-09); TagPup's Suggest keeps a folder's suggestions in the library and applies them.
- **Usage**: `run.bat [global-options] suggest <DIRECTORY>`
- **Options**:
  - `--k INTEGER`: Number of nearest neighbors to consider (default: `15`).
  - `--min-sim FLOAT`: Cosine similarity cutoff (default: `0.35`).
  - `--add`: Add DIRECTORY to the library first when the library does not hold every folder of photos under it: the folder is recorded as added, as TagPup's Add does (`index` then reads its photos). Without it such a folder is refused, exit code 1, with a message naming the folder and the library: Suggest records faces and vectors on each photo's row, and a row makes its folder the library's -- kept in step, watched, its new files indexed (`tagpup.services.libraries.not_in`).

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
  - `--reset`: Clear the names clustering assigned (automatic names) before clustering again. Names a person gave (`name_source = 'manual'`), their "nobody" decisions and exclusions are kept: they are the anchors clustering works from.
  - `--max-iterations INTEGER`: Maximum iterations for propagation loop (default: `5`, set to `0` for anchor-only).
- The apps' **Name faces from tags** button runs `tagpup.services.identities.resolve`, the code behind this command, as its optional second step (off unless ticked), on a thread of the server with progress and a Cancel that stops it before its names are written (`--max-iterations` is the default 5; `--reset` is not used). The command is unchanged. The Tk runner's **Run Identity Resolution Clustering** button is also unchanged.

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)

### `compact [--apply]`
Says how much of the library's file holds nothing: pages left free by deleted rows and dropped columns, which SQLite keeps until the file is rewritten. With `--apply`, backs the library up (`db.backup`, into `backups/`), then rewrites it without them (`VACUUM`, `db.compact`) and says the size before and after. The rewrite needs the file to itself; close the apps first. After phase 4's migrations photo_index holds about 1 GB free of 4 GB.

### `history [--change ID] [--limit N] [--reveal]`
The library's journal (`tagpup.services.journal`): the changes the maintenance scripts and the MCP server's write tools applied, newest first -- each one's id, operation, status (`applied`, `derived_pending`, `undone`), when it was made and undone, the schema version it was made at, and how many rows of each table it inserted, updated and deleted. `--change ID` shows that change alone with the keys (ids) of every row it wrote; with `--reveal` too, each column's value before and after, a BLOB by its size. Values can name people, so they are shown only when asked.

### `undo ID [--apply]`
Undoes change ID of the journal. Without `--apply` it rehearses: undoes the change and applies it again inside a transaction rolled back, and says whether that restored every row exactly. With `--apply` it writes the old values back, only where every row is still what the change left. Refused, with nothing written and exit status 1, when a row has changed since, when a newer change touched the same rows (named), or when the library's schema has moved on since the change was made -- and for a stamp of the library's settings, its first settings, which are changed rather than undone.

### `sync [--folder FOLDER] [--apply]`
Brings the library in step with its folders (`tagpup.runtime.sync`, over `tagpup.services.sync`; ARCHITECTURE.md, phase 8): walks the library's root folders (`library.roots`) and every folder it holds photos in, each once from the topmost, or FOLDER, and compares each file with its row by path, size and modified time. Says how many files are new (and in how many folders), changed, moved and missing, and how many folders are wholly gone -- a whole root among them, as an unplugged drive is. A dry run unless `--apply`, reading only the changed files (and, where a row is missing, the DocumentIDs of the new files its name, size and modified time did not match); nothing is written. With `--apply`: the changed rows are read again from their files, as `refresh_rows` does, and the rows of moved files follow them with their faces, as one change of the journal (`sync`), which `undo` takes back; the folders the library holds that hold new files are indexed, each without its subfolders, one at a time, and the command waits for them; a folder under a root holding no indexed photo is counted as a folder to review, never indexed on its own (TagTuner's gear, Folders to review), and one under an ignored folder (`library.ignored`) is passed over; missing files are never removed. Each applied run is recorded in the library (`sync_runs`), which the pages show as "last in step". Counts only; exit status 1 when refused -- FOLDER not a full path, or holding no photo of the library and under none of its root folders -- or something failed. **Marked folders**: when the walk meets a file missing or moved and the library has marked folders (`folder-ids mark`) -- in a sync of any scope, `--folder` and the watcher's folder syncs included -- a marked folder among the missing or moved rows' folders that is gone and whose marker (this library's line) is found in a folder where files were found new or moved to, or beside the gone folder (one listing of its parent), is followed exactly -- its photo rows with their faces and names, its id, the `library.roots` and `library.ignored` entries and the folders added that name it -- as a journaled change of its own, `follow_folder_markers`, written after the sync's change and before any folder is queued for indexing, so a followed folder's files are never indexed as new; a photo is paired by the file of the same name that has no row, else by DocumentID, or size and Date Taken. A folder queued as new whose marker carries this library's id for a marked folder recorded elsewhere is followed too, whatever this sync's own counts (a folder moved into a folder the library holds). An `undo` of the change is done again by the next sync that finds the old folder gone, since the marker still says so (take the line out of the `.tagpup` first; no command does that yet). `sync` prints how many marked folders it left (their files already have rows) or could not find, and the watcher logs it. A copy of a marked folder beside it never takes the link; two folders for one lost id are left; a folder none of whose rows can go (its files already have rows of their own) is left and counted, its id not moved. A dry run says how many it would follow, and reads the files of a folder whose photos were renamed too through ExifTool to say which photos would follow. A library that has marked nothing is not touched, and `sync` says `folder-ids mark` exists when folders are wholly gone and none is marked.

### `relink-folders [--from OLD --to NEW] [--reveal] [--apply]`
Follows folders renamed outside the apps (`tagpup.runtime.relink_folders`, over `tagpup.services.folder_moves`; ARCHITECTURE.md, phase 8, "A folder renamed outside the apps"). Under a root, `sync` already relinks a renamed folder's unchanged files by name, size and modified time; this is for what that does not reach (files changed or renamed meanwhile, a library without roots, added-folder records). A folder whose rows' folder is gone (the topmost gone folder whose parent is there; one whose drive or share is not there is left as it was) is looked for among the folders beside it whose name begins with the same date; a photo there matches a row by DocumentID, else by size and Date Taken (or, with neither, the same name), one to one. One candidate holding at least 80% of its rows is relinked: rows follow their files with ids, faces and names, and the `library.roots` and `library.ignored` entries under the old folder follow, as one journaled change (`relink_folders`) that `undo` takes back, the added folders under the old folder following after it only on a clean one-to-one rename (the new name not added already, no other folder going into it; never merged) and back with an undo. Several candidates or a thin match is proposed only; `--from OLD --to NEW` (together; either in any place of its root) is the owner's word that it is the folder. An added folder gone with no photo under it (its rows moved already) is only reported -- counted, named with `--reveal` -- and nothing is changed for it. A file that already has a row is no destination; rows that match nothing stay missing, never removed. A dry run unless `--apply`; prints counts and a verdict per folder, the folders named only with `--reveal`. Marked folders (`folder-ids mark`) that are gone are followed first and exactly by their markers (unless `--from/--to` is given), the same journaled change `sync` writes; their counts are printed above the verdicts. Exit status 1 when refused (`--to` not a folder that is there, `--from` still there) or something failed.

### `folder-ids mark [--reveal] [--apply]`
Gives each leaf folder of the library -- a folder the library holds photos in directly; a folder holding only folders gets none -- a hidden `.tagpup` file (`tagpup.runtime.mark_folders`, over `tagpup.services.folder_ids` and `tagpup.files.folder_marker`; ARCHITECTURE.md, "Folder ids"). The file is a list of lines, each a library's identifier and that library's id for the folder (random UUIDs, nothing else), so a folder in several libraries carries one line for each; this library's line is added beside the others, which are kept byte for byte, and the file is written whole to a temporary name and renamed over the old one. **Opt-in, never automatic**: nothing but this command writes a marker or stamps the library's identifier (`library_identity`), which the first `--apply` that records an id does, in the same transaction, and which cannot be taken back (the markers carry it). A dry run unless `--apply`: reads each leaf folder's marker (never writes) and says, as counts, how many folders are already marked, to mark, to write again (the library records an id and the marker lacks it), to adopt (the marker holds this library's line and the library has no row: an interrupted run, a table restored), and which it leaves alone and why -- a copy of a marked folder (its id is recorded for a folder that is still there: reported, never rewritten), one whose id is recorded for a folder that is gone (the next sync follows it), one whose marker and library name different ids, a marker that does not parse (hand-edited, empty, with a byte-order mark, another program's file of that name: never rewritten), one that could not be read (a locked file, a share gone), and a place where nothing can be written (a read-only share or file: counted and left, never made writable); folders gone from disk and folders the library ignores are counted and passed over. Names only with `--reveal`. With `--apply`: every file is staged under a temporary name beside its marker, the ids of the files that could be staged are recorded as ONE journaled change (`mark_folders`: `history` lists it, `undo` takes the ids back and leaves the markers, which a later run adopts), and each file is renamed into place after checking that the marker is still as it was read; a run interrupted between the file and the record, or the record and the file, is finished by the next run, which finds the marker or the recorded id and makes no second id. Two runs at once are serialised by the library's write lock, and the second is refused without having written. A file another program changed meanwhile is left (`changed meanwhile`) and written by the next run. Refused, exit status 1, when another library file in the data folder carries this library's identifier (a copy kept for a trial: two libraries answering to one line would follow each other's folders; `tools/doctor.py` names it). Exit status 1 as well when something failed. `sync` and `relink-folders` follow a marked folder that moved (see `sync`); `relink-folders` does not mark.

### `dedupe-spelled-rows [--apply]`
Finds the files the library holds under more than one photo row (`tagpup.services.duplicate_rows`, #479) -- one file reached by two names, such as a share and its drive, a junction or a hard link -- and merges them. Only rows PROVABLY one file are touched: the same volume and file index (`os.stat`), among rows that share a name and a size. A copy of a file (another file with the same name, size and time, in another folder) has a row of its own and is left alone, counted as `copies`. Of the rows of one file the one under a root is kept, else the one a person curated, else the oldest id; what the others hold is moved onto it first where it lacks it (a face with no counterpart, a name or exclusion given by hand a counterpart lacks, a vector under a model it has none of), then they are deleted. A set whose rows differ in tags or captions, or whose faces carry two names, is left for a person. Without `--apply` a dry run printing counts only (never a name or a path) and a rehearsal; with it one change of the journal, which `undo` takes back.

### `reread-fields [--apply] [--folder FOLDER] [--shares]`
Reads again, for the metadata fields ExifTool is asked for now -- the camera's LENS (`EXIF:LensModel`, `LensMake`, `LensID`) -- the photos read before they were (`tagpup.services.reread_fields`, over `refresh_rows`' reading and edits; findings #1018 to #1022). A row says which read it comes from, `TagPup:ReadGeneration` in its `raw_metadata`; the rows with a lower one are read. **Metadata only**: ExifTool reads the file; no picture is decoded, no model or graphics card is used, no photo file is written, and faces, names and embeddings are untouched. A dry run unless `--apply`: it counts (photos in scope, already read, never read, to read again, and those left: files not found, changed on disk since indexed, on a network share nobody named, on a share that did not answer), reads a sample of 100 files and prints the rate, how many of the sample hold a lens, and an estimate for the rest (kr-track copy, 1,498 photos on a local disk: 32 files a second, 47 s). `--apply` writes a row's `raw_metadata` only (and a `document_id` where it has none) -- never tags, captions, people, modified time or size; a row whose tags, captions or people differ from its file's is counted "disagrees" and left to `sync` or refresh_rows; rows found damaged before are not tried again -- and reads and writes 2,000 photos at a time, each chunk one change of the journal (`history`, `undo`), and prints rows written, files left, rows skipped (saved in the app, or by another run, while their files were read: the next run reads them) and `Still to read`. The camera and lens words of search are rebuilt for exactly the rows written by the same write: no doctor run. Safe to stop and run again; a photo read is not read twice. A chunk whose every file is gone (a drive unplugged) stops the run after that chunk, and so does ExifTool that cannot be started when a run has read none of 10 or more files (exit status 1); merely unreadable files are counted and exit status stays 0.

**`--folder FOLDER`** limits it to the photos under that folder and its subfolders (`tagpup.services.folder_scope`); a folder the library holds no photo under is refused (exit status 1). Naming a folder is also what allows files on a network share to be read: without `--folder` or **`--shares`**, a photo whose path is on a share (a UNC path or a mapped network drive) is counted and never opened or looked at.

### `faces-from-tags [--apply] [--again] [--folder FOLDER]`
**`--folder FOLDER`** (#994) limits the plan and the write to the photos under that folder and its subfolders (`tagpup.services.folder_scope`; the folder is spelled by its root's first place, so a second location of a root works, and its photos are one range of the path index: `Run` never takes in `Run2`). Only the photos read are fewer: the rule, the people their tags name, the decided faces a face is compared with and the gate of a person with no decided face are the whole library's, so the folder's plan is the whole-library plan restricted to the folder's photos. A folder the library holds no photo under (outside its roots, never indexed, mistyped) is refused with a sentence and exit status 1, never read as nothing to do. A second `--apply` needs `--again` whichever folder the first was for. What is left to name after the write is counted in the folder. The journaled change keeps its scope in its summary (`whole library`, or `folder` and the folder as stored): `history --change N` prints the scope and `--reveal` the folder (a path can name people; History's dialog and listings never carry it), and the refusal of a second apply says where the earlier one was.

Names the faces a photo's person tag names (`tagpup.services.faces_from_tags`, over `tagpup.store.face_tags`; #788). A photo with exactly one face still to be named -- unnamed, not excluded, not marked "nobody" by hand -- and exactly one keyword person (a leaf person of the tag tree, never a branch) that no face of it carries, gives the face that person. A photo with several faces or several people is named only by comparison: a face goes to a person when exactly one person's decided faces (named by hand, or on a photo whose keywords name them) are alike enough to be named unasked (`clustering.names_unasked`), that person is one the photo's keywords name and no face of it carries, and no other face of the photo is proposed for them; anything less is left for Identify Faces. Names are written as automatic (`name_source` NULL), which clustering may revise, and carry the person's id beside the name. The same rule runs on its own for a photo when its keywords are saved, it is read again by the indexer, or its faces are recorded; this applies it once to the photos already there. The tag alone is trusted as far as the face looks like the person (#833): a person with decided faces needs the face's best cosine to them to reach 0.70, a person with none is named by the tag alone only when exactly one photo of theirs has a face to be named (several: all left, counted as `not_decidable_yet`), and a face under 2,000 square pixels is never named by it. A dry run unless `--apply`: counts only, never names (photos with a face and a person to place; of those with one face and one person, how many the person has no decided face for, how many are like them at 0.80 or more, from 0.70 to 0.80, and not like them, which are left; those named by comparison; those left), and the rehearsal of the change; with `--apply` the faces are written as one change of the journal (`faces_from_tags`), which `undo` takes back. A library it was applied to before (and not undone) refuses a second `--apply` without `--again`, saying why -- the faces it named then are references for this run, so each apply can name more, one 0.70 step at a time -- and showing what it would have named; the dry run says so too. After `--apply` it says how many faces the rule would still name, counted again after the write (naming one face can leave a photo with one face and one person). A face unmatched by hand or excluded since the plan is not written: the change is refused. The apps' **Name faces from tags** button (TagTuner's header and gear, TagPup's folder view) runs this same plan and this same `--apply` (a plan read once, shown as a question, and applied as it was shown; a second apply is the `--again` its question names), as a job with progress and Cancel (SPEC_TAGTUNER.md, "Name faces from tags").

### `tags-from-faces [--apply] [--guesses] [--folder FOLDER]`
**`--folder FOLDER`** (#994) counts and writes only the photos under that folder and its subfolders (the same folder rules as `faces-from-tags --folder`); the rule for each photo is the same, so the folder's plan is the whole-library plan restricted to its photos. A folder the library holds no photo under is refused (exit status 1).

Puts on a photo the people its faces name and its keywords do not (`tagpup.services.tags_from_faces`, over `tagpup.services.face_people`; #861). A face named and the photo's person tag are one decision; faces named before that (TagTuner's strip, Match, AutoMatch All, Identify Faces, clustering) left photos listing a person from a face alone (`photo_people` source `face`), "a green box and No people tags". A dry run unless `--apply`: counts only, never a name or a path -- the photos, the people, how many photo files would be written, and how many people are left: filed by the tag tree in two places (or under several people roots), for you to place; named in the photo's keywords already under a root the tree does not file people under (`Parkrunner/<name>`: the same rule as the keyword writer, the leaf under any root; a tree question -- make that root a people root, or retag), so the count to write is what `--apply` writes and a second run finds none; and "from a guess only" -- no face of that name was named by hand, only by clustering or automatch. `--guesses` writes those too, with a printed warning: the keyword makes each guess a decided reference for the faces compared with it afterwards (#640). **`--apply` changes photo files**: it writes each person's keyword into the photo's file with ExifTool, 25 photos to a change of the journal (operation `person tags from faces`; `history` lists them, `undo` takes one back, each recording every file's fields before and after -- no copy of the library is taken). A file ExifTool cannot read or write is counted and the others are written (exit status 1, run it again); a file changed since it was read is a conflict, reported and never overwritten; TagPup and TagTuner may stay open. The doctor (`tools/doctor.py`) reports the same count as "photos listing a person from a face alone" (reported, not broken).

### `settings` and `settings set KEY VALUE [--acknowledge GROUP] [--apply]`
`settings` lists the library's settings, key and value, read without stamping it. `settings set` changes one as one journaled change (`tagpup.services.settings.change`), which `history` lists and `undo` takes back -- a dry run unless `--apply`, saying what would change. A value the validator refuses is refused (exit status 1), as is a locked setting (the CLIP model, face detection, ExifTool) unless its group is named with `--acknowledge`. Folders (`library.roots`, `library.ignored`) are one a line or separated by `|`: `tagpup_cli.py --db photo_index settings set library.roots "D:\Training\Pictures" --apply`. Setting the roots adds every folder under a new root that holds photos and no indexed photo to `library.ignored` in the same change, and says how many; a root not on disk now is taken, with a warning. A dry run reads the settings without migrating a library behind this version, and says how far behind it is.

### `thumbs warm [--folder FOLDER] [--limit N] [--apply]`
Makes the library's thumbnails ahead of the first time each is asked for (`tagpup.services.thumbnails.warm`; ARCHITECTURE.md, phase 9a-2): a 300 px JPEG of each photo, kept under `cache/<library>/thumbs` beside the library, which the library views show instead of decoding every photo's file again. A dry run unless `--apply`: it reads the library and each file's size and modified time -- never the picture -- and says how many photos already have a thumbnail for their file as it is now, how many need one and about how much they will take (the average entry already kept, or 30 KB when none is), how many have no file there, how many are damaged (recorded so, or found not to decode: they are not decoded again until their file changes, and nothing is kept for them), and how many could not be reached (a share away). It writes nothing, not even the cache folder. With `--apply` it makes them, four at a time, and prints its progress after each 500 photos; a library behind this version's schema is brought up to date first, and the thumbnails of photos the library no longer holds (a restore, a delete outside TagPup) are deleted. It can be stopped (Ctrl+C) and run again: every thumbnail is written whole or not at all, what was made is found there, and the run goes on from it. `--limit N` stops after making N, so a large library can be filled in parts. `--folder` limits it to the photos under that folder. The cache is not bounded (the owner's decision: 1.5 to 3 GB for photo_index) and every entry can be made again; a photo that leaves the library takes its thumbnail with it. Exit status 1 when the library holds a root this machine does not place, or when the cache folder cannot be written (the thumbnails were made and not kept: it says so).

### `jobs`
Lists each recurring job for the library `--db` names, or for every library in the data folder: its period, why it is scheduled (`safety`, `retention`, `catch-up`: only what no event announces is), when it last ran, how that run ended (`running`, `done`, `failed`, `abandoned`), what it changed as counts, and when it is due next. The runs are recorded in each library (`job_runs`). Only the web server -- the always-on process -- runs what is due, looking every ten minutes; the CLI runs a job only when told to, with `jobs run`, and the MCP server never. A job is due a period after its last run that ended started -- an hour sooner for a period of a day or more, since the server looks every ten minutes; an hour after a failed one -- so a missed period runs once. A library that has not had this version's migrations is left alone (`behind`) until an app opens it: running a job never migrates a library.

### `jobs run NAME`
Runs the recurring job NAME now, for the library `--db` names or every library, whether or not it is due; a job another process is running is left to it (`not run, running`). Exit status 1 when a run failed or was not run. `jobs run snapshots` takes a daily snapshot now.

### `snapshots list`
The library's snapshots (`tagpup.services.snapshots`), its backup: three dailies, one weekly and one monthly in `data/backups/<library>/daily|weekly|monthly`, and the last two `before-restore` copies. Each by name (`daily/20260926_090000`), when it was taken, its size and how many of the journal's changes were made since -- what restoring it would lose -- and the disk they take together. A daily is taken when the newest is a day old, the weekly and the monthly from that same copy when theirs are 7 and 30 days old; each is copied in one step of SQLite's backup API, one read transaction, while writers go on, checked (`PRAGMA quick_check`) under a temporary name and only then renamed into place, and an old one is removed only after its replacement passed. photo_index (2.5 GB) takes about 12 s to copy and about 13 GB for five.

### `snapshots restore NAME [--apply]`
Says which of the journal's changes the library holds that snapshot NAME does not, by id, time and operation: what restoring it would lose; and what it costs: about three times the library free on the disk (the library as it is, snapshotted first, and the copy back through its WAL) against what the disk has, and about half a minute for photo_index. Refused, with nothing written, on a disk without the room. With `--apply`, snapshots the library as it is first (`before-restore/<time>`, which `snapshots restore` puts back), then copies NAME into the library through SQLite's backup API, into the file the apps have open, and checkpoints the WAL without waiting for readers (one a reader held up is logged, and finished by the next checkpoint). Photo files written since keep what was written; the rows go back. Refused, exit status 1, for a snapshot made by a newer TagPup.

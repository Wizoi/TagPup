# TagPup — System Specification

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

- **`tagpup.py` (CLI entry point)**: Unified Command Line Interface using `click` and `rich`.
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
| **ExifTool** | Installed on path or resolved dynamically in `tagpup.py` (defaults to `%USERPROFILE%\AppData\Local\Programs\ExifTool\exiftool.exe`) |
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
- Only photos containing at least one flat tag/keyword, person tag, or caption/description are added to the search index.
- **Smart Skipping (Incremental Indexing)**: On subsequent indexing runs, the system compares each scanned file's modification time (`mtime`) and file size (`size`) with the values stored in the database. If both match, the file is skipped entirely from metadata parsing and embedding generation, drastically speeding up catalog updates.
- **Single-Pass Face Indexing**: Unless `--skip-faces` is passed, the indexer automatically triggers face detection and embeddings extraction in the same loop, writing results to the `faces` table after the parent photo row has been committed.
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
- Faces are clustered using DBSCAN (Euclidean epsilon = `0.55`, representing a cosine similarity of $\ge 0.85$ on normalized face vectors).
- **Cluster Naming**: Visual groups are assigned people identities based on tag voting. If a photo has only one face and one name tag (e.g. `John Doe`), it acts as an anchor vote.
- **Match Suggestion Boost**: Untagged images undergo face detection. Detected faces are matched against resolved database faces. If a face yields a similarity $\ge 0.85$ to a known profile, the matching person's tag is automatically boosted to a confidence score of `1.0`.


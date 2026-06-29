# 🐶 TagPup

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](#preconditions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**TagPup** is a unified, private desktop suite for offline photo tagging, sequential renaming, camera clock time-shifting, and local semantic search. Just like a loyal retriever puppy, TagPup sniffs out your photos' visual features and faces, fetches metadata, and tags your files to keep them organized.

Unlike cloud-dependent services, TagPup operates **100% locally** using PyTorch, CLIP, face recognition models, and ExifTool to manage your physical photo files securely on your hard drive.

---

## 🗺️ System Architecture

TagPup is structured around two equally important pillars, managed by a central desktop dashboard cockpit:

```
                  ┌──────────────────────────────┐
                  │   Desktop Dashboard Cockpit  │ (runner.py)
                  │       (GUI Runner App)       │
                  └──────────────┬───────────────┘
                                 │
         ┌───────────────────────┴───────────────────────┐
         ▼                                               ▼
┌──────────────────┐                            ┌──────────────────┐
│   TagPup GUI     │ (tagpup_gui.py)            │   AI CLI Engine  │ (tagpup_cli.py)
│  (Local Web UI)  │                            │  (Advanced CLI)  │
└────────┬─────────┘                            └────────┬─────────┘
         │                                               │
         ├─ Folder Browser & Multiselect                 ├─ Semantic CLIP Embeddings
         ├─ Batch Keywords & People Tagging              ├─ Vector Search Index (FAISS)
         ├─ Sequential Renaming & Eviction               ├─ Face Clustering (DBSCAN)
         ├─ Camera Time-Shift Highlight                  ├─ Zero-Shot Year Consensus
         └─ Interactive Taxonomy Tree                    └─ ExifTool Metadata Writes
```

---

## 🎛️ The Dashboard Cockpit (Developer GUI Runner)

For developers and advanced users, the **GUI Runner** dashboard (`runner.py`) provides an optional, unified desktop panel to run and monitor multiple processes side-by-side:

*   **Multi-Server Control**: Spin up and stop both web servers simultaneously from a single panel.
*   **Visual CLI Builder**: Graphically configure indexing options, face detection parameters, and search queries instead of using the terminal.
*   **Live Console Log Viewer**: Stream server output and CLI execution logs in real-time.

To launch the dashboard, run:
```cmd
.venv\Scripts\python runner.py
```

---

## 🖥️ 1. TagPup GUI (Visual Metadata Editor)

The **TagPup GUI** is a lightweight, responsive local web interface designed to manage folder visual hierarchies and edit photo file metadata directly.

### Key Visual Features
*   **Folder Tree & Grid Selection**: Browse folder groups sorted by capture year, toggle thumbnail dimensions (Small, Medium, Large), and select photo ranges (Shift-Click support).
*   **Smart Sequential Renaming**: Sequential renaming based on a custom `[Grouping] - [Index] - [Caption]` format. Automatically evicts folder name conflicts to temporary filenames and stores the original filename in image metadata.
*   **Tag Taxonomy Tree Manager**: An interactive collapsible manager tree. Easily create sub-tags, toggle whether categories are autocomplete-hidden, mark folders as custom "People" roots, and safely check image usage before deleting tags. Supports dynamic renaming which cascades to descendant nodes and updates image tags on disk.
*   **Interactive Tag Resolution**: When adding a new tag or person, TagPup displays placement prompts. It prompts which root category the tag belongs to, prevents duplicates, and resolves ambiguous leaf names.
*   **Camera Time-Shifting**: Toggles a clock adjustment panel to offset capture timestamps recursively for specific camera models.

*For details on the interface layout, shortcuts, and mechanics, refer to the [TagPup GUI Specification](SPEC_TAGPUP_GUI.md).*

---

## ⚙️ 2. AI CLI Engine (Advanced Indexing & Semantic Search)

The **AI CLI Engine** is the underlying machine learning backend that indexes visual features, matches face coordinates, and computes predictions.

### Key Scenarios & Capabilities
*   **Incremental Indexing**: Generates 1024-dimensional visual embeddings using **CLIP** (`ViT-H-14`) and face crops using **MTCNN + Facenet**. Incremental indexes scan quickly by skipping files with matching sizes and timestamps.
*   **Semantic natural language search**: Cosine similarity queries against the FAISS vector database index, allowing you to find files by writing natural text prompts (e.g. `run.bat search "hiking trip in the mountains"`).
*   **Automated Tag Suggestion**: Infers keywords using visual similarity, decay-weighted capture timestamps, path hints, and event-level consensus.
*   **Face Profile Self-Tuning**: Clusters face vectors using **DBSCAN** and labels identity centroids automatically by analyzing photo metadata. Recognizes known faces in untagged images to boost tagging predictions.

*For CLI commands references, algorithmic rules, and parameters, refer to the [CLI Specification](SPEC_TAGPUP_CLI.md).*

---

## 🚀 Getting Started

> [!TIP]
> For a conceptual, step-by-step walkthrough of how TagPup works—from importing photos to auto-matching faces and using AI suggestions—check out the [Getting Started Tutorial](TUTORIAL.md).

### 📋 Preconditions
- **OS:** Windows 10 or 11
- **Python:** Python 3.10+ added to your system PATH
- **ExifTool:** Installed on your computer (expected path: `%USERPROFILE%\AppData\Local\Programs\ExifTool\exiftool.exe` or customized in `config.ini`).

### 🔧 Installation & Setup

1. **Clone or download** this repository.
2. **Run the setup script** to initialize the virtual environment (`.venv`), install PyTorch (with GPU/CUDA acceleration if supported), CLIP, FAISS, and MTCNN.

   **Using PowerShell:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   **Using Command Prompt:**
   ```cmd
   setup.bat
   ```

3. **Launch the Application**:
   You can start each interface directly using a single command:

   *   **Start Folder Tagging (TagPup GUI)**:
       ```cmd
       .venv\Scripts\python tagpup_gui.py
       ```
   *   **Start Face Matching (TagTuner)**:
       ```cmd
       .venv\Scripts\python tagtuner.py
       ```
   *   **Start Developer Cockpit Dashboard**:
       ```cmd
       .venv\Scripts\python runner.py
       ```

---

## 📚 Project Documentation Directory

For guides, tutorials, specifications, and schemas:
*   📖 [Getting Started Tutorial](TUTORIAL.md): High-level concept tutorial on building your photo taxonomy and utilizing confidence-based AI suggestions.
*   💡 [Usage & CLI Examples](EXAMPLE.md): Practical commands cheat sheet, setup instructions, indexing, clustering, and searching walkthroughs.
*   🖥️ [TagPup GUI Specification](SPEC_TAGPUP_GUI.md): Detailed design guidelines, folder browser actions, tag taxonomy tree manager, and metadata resolution prompts.
*   🎯 [TagTuner UI Specification](SPEC_TAGTUNER.md): Face tuning grid mechanics, autocompletes, DBSCAN identity matching, and profile workflows.
*   🐶 [AI CLI Engine Specification](SPEC_TAGPUP_CLI.md): Machine learning architecture, CLIP embeddings, consensus scoring formulas, and CLI parameters.
*   🗄️ [Database Specification](DATABASE.md): SQLite schema table structures and visual Entity-Relationship/Data-Flow diagrams.

---

## ⚙️ Configuration (`config.ini`)

The project configurations are managed via [config.ini](config.ini):

```ini
[paths]
exiftool = %USERPROFILE%\AppData\Local\Programs\ExifTool\exiftool.exe
data_dir = data
embedding_cache_dir = data/embedding_cache

[model]
name = ViT-H-14                      # CLIP Model architecture
pretrained = laion2b_s32b_b79k       # Pretrained weights
preserve_full_frame = true           # Whether to preserve original aspect ratio
max_aspect_ratio = 1.4               # Maximum aspect ratio for padding
force_image_size = 512               # Input resolution size

[candidates]
tags = Landscape, Portrait, Nature, Urban, Sunset, Sunrise, Night, Ocean, Mountain, Forest, Animal, Cat, Dog, Food, Indoor, Outdoor, Vehicle, Flower, Architecture, Party, Wedding, Beach, Sports, Concert

[faces]
min_face_size = 20                   # Minimum width/height in pixels for face detection
confidence_threshold = 0.85          # Minimum probability score for face detection
mtcnn_thresholds = 0.6, 0.7, 0.7     # Detection thresholds for MTCNN stages

[renaming]
format = {grouping} - {index} - {caption}  # Custom template pattern for sequential renaming
```

---

## 📝 License
This project is licensed under the MIT License. ExifTool is owned by Phil Harvey and licensed under its own terms.

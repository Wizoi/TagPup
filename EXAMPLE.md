# TagPup — Quick Start & Usage Examples

This guide walks you through setting up the TagPup and running common photo organization workflows.

---

## 1. Initial Setup

### Step 1: Install Dependencies
Open a terminal in your project directory (e.g. `C:\src\TagPup`) and run:
```powershell
# Using PowerShell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```
Or use the batch script:
```cmd
# Using Command Prompt
setup.bat
```
This automatically sets up a Python virtual environment (`.venv`), upgrades pip, installs PyTorch (with NVIDIA GPU/CUDA support), CLIP, FAISS, and ExifTool bindings, and creates the `data` and `data/embedding_cache` directories.

### Step 2: Configure ExifTool
Ensure ExifTool is installed on your machine.
- Default expected path: `%USERPROFILE%\AppData\Local\Programs\ExifTool\exiftool.exe`
- If you have ExifTool installed elsewhere, open the generated [config.ini](config.ini) and modify the `exiftool` path:
  ```ini
  [paths]
  exiftool = C:\Path\To\Your\exiftool.exe
  ```

---

## 1.5 Testing & Validation Mode

If you want to run tests, validate commands, or experiment without cluttering your actual production index database, you can prefix any of the database-access commands (`index`, `suggest`, `search`, `stats`, `list-index`, `remove`, `index-faces`, `cluster-faces`) with the global `--test` flag.

```cmd
# Build a test index using test folders (generates test_photo_index.db)
run.bat --test index "test_library\Tagged"

# Suggest tags using the test index
run.bat --test suggest "test_library\Untagged"

# Inspect and view statistics for the test index
run.bat --test stats
run.bat --test list-index
```
This guarantees your production database files (`photo_index.db` / `photo_taxonomy.json`) remain clean and unaffected by testing.

---

## 2. Common Scenarios & Workflows

### Scenario A: Indexing Your Tagged Library
Before you can tag new photos, you must build the "knowledge base" from your already-tagged photos.

```cmd
# Index the directory recursively (CLIP + Face detection in a single pass)
run.bat index "D:\Photos\Tagged_Archive"

# Index the directory, but skip extracting face embeddings
run.bat index "D:\Photos\Tagged_Archive" --skip-faces

# Force indexing and recreate all embeddings from scratch
run.bat index "D:\Photos\Tagged_Archive" --force-reembed

# Clear the database entirely and start a fresh index
run.bat index "D:\Photos\Tagged_Archive" --reset
```
* **Smart Skipping (Incremental Indexing)**: The tool automatically compares the size and modification time of each file in your folders with what is already saved in the database. If a file hasn't changed, it skips ExifTool metadata reading and CLIP re-embedding entirely, making incremental runs lightning-fast.
* **What happens**: The tool extracts existing keywords/people/captions from new or changed files, runs them through the CLIP model to produce vectors, runs MTCNN to detect and extract face vectors (unless `--skip-faces` is specified), and commits everything to the SQLite database.

---

## Scenario B: Self-Tuning Face Identity Resolution
If you want the system to match face embeddings against specific family members, you must group them and resolve names after indexing.

```cmd
# Retroactively extract faces on already indexed files (optional standalone)
run.bat index-faces "D:\Photos\Tagged_Archive"

# Cluster and assign identities using photo-level tags (Self-Tuning)
run.bat cluster-faces
```
* **What happens**: The clustering command maps similar face vectors into visual groups using DBSCAN and matches them with people tags (e.g. `Family/John Doe`). Future runs of `suggest` will match faces on untagged photos and boost recognized people tags to 1.0 (highest) confidence.

---

### Scenario C: Suggesting Tags for New Photos
Now, point the tool at your directory of newly imported, untagged photos.

```cmd
# Suggest tags for the untagged directory
run.bat suggest "D:\Photos\2026_Imports"
```
- **What happens**: The tool generates a visual embedding for each new image, detects any faces and matches them against the database face index (applying a 1.0 confidence boost on hits), retrieves nearest visual neighbors, and outputs the results to `suggestions.json` in the current folder.

---

### Scenario D: Reviewing and Applying Suggestions
Always preview the suggestions before modifying your images.

#### 1. Dry Run / Preview (Safe)
```cmd
run.bat write suggestions.json
```
Prints a summary table showing which tags will be added to which files. No files are modified yet.

#### 2. Live Write (Apply tags)
Once you are satisfied, apply the tags to your images:
```cmd
run.bat write suggestions.json -Live -MinScore 0.60
```
- **What happens**: 
  - Filters out suggestions with a score lower than `0.60`.
  - Prompts you to type `YES` to confirm.
  - Appends flat tags to `XMP:Subject` and `IPTC:Keywords` (including C# XPKeywords).
  - Appends hierarchical paths to `XMP:HierarchicalSubject`.
  - Generates tags-based captions and writes them to Description/XPComment fields.
  - ExifTool preserves original files by renaming them with a `_original` suffix.

---

### Scenario E: Finding Photos Semantically
You can search through your indexed collection using descriptive text queries:

```cmd
run.bat search "sunset at the beach"
run.bat search "birthday party family gathering"
```
Prints a table of the top 10 most visually similar photos in your index matching that description.

---

### Scenario F: Inspecting a Specific Image's Metadata
If you want to debug what tags or details ExifTool is reading from a single file:

```cmd
run.bat inspect "D:\Photos\Tagged_Archive\sample.jpg"
```
This prints the parsed tags (Tags, People, Captions) and a detailed table of raw fields read by ExifTool.

---

### Scenario G: Inspecting & Cleaning up the Index
You can list what files are currently indexed and remove individual photos or entire folders.

#### 1. List Index Contents
```cmd
# List all indexed photos
run.bat list-index

# List only photos indexed under a specific folder
run.bat list-index --folder "D:\Photos\Tagged_Archive\Family"
```

#### 2. Remove Photos from the Index
```cmd
# Remove a single photo from the index database
run.bat remove --path "D:\Photos\Tagged_Archive\Family\sample.jpg"

# Remove all indexed photos under a specific folder recursively
run.bat remove --folder "D:\Photos\Tagged_Archive\Family"
```
*(Requires typing `YES` to confirm deletion. This deletes the photo's visual vector, tags, and face coordinates from the SQLite database via cascade deletion; it does not delete your physical photo files).*

# TagpupCLI — Quick Start & Usage Examples

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

This guide walks you through setting up TagpupCLI and running common photo organization workflows.

---

## 🚀 Key Commands Cheat Sheet

Here is a quick reference table of the most common commands you will use in TagpupCLI:

| Workflow / Task | Command | Description |
| :--- | :--- | :--- |
| **Index Library** | `run.bat index "path/to/photos"` | Scans photos, extracts CLIP vectors, and detects faces in a single pass. |
| **Start TagTuner UI** | `python tagpup_web.py --open tuner` | Launches the local server and web browser client for manual face matching. |


> [!TIP]
> **Safe Testing Mode:** Prefix any database command with the global `--test` flag (e.g. `run.bat --test index "test_library\Tagged"`) to run operations on a temporary test database index (`test_photo_index.db`) without touching production archives.

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
This automatically sets up a Python virtual environment (`.venv`), upgrades pip, installs PyTorch (with NVIDIA GPU/CUDA support), CLIP, FAISS, and ExifTool bindings, and creates the `data` directory.

### Step 2: Configure ExifTool
Ensure ExifTool is installed on your machine.
- Default expected path: `%USERPROFILE%\AppData\Local\Programs\ExifTool\exiftool.exe`
- Otherwise the one on PATH is used. To use another, open TagTuner on the library, choose **Library settings** in the gear, and under ExifTool choose **Change...** and give its path.

---

## 1.5 Testing & Validation Mode

If you want to run tests, validate commands, or experiment without cluttering your actual production index database, you can prefix any of the database-access commands (`index`) with the global `--test` flag.

```cmd
# Build a test index using test folders (generates test_photo_index.db)
run.bat --test index "test_library\Tagged"

```
This guarantees your production library (`photo_index.db`, with its tag tree) remains clean and unaffected by testing.

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
# Cluster and assign identities using photo-level tags (Self-Tuning)
run.bat cluster-faces
```
* **What happens**: The clustering command maps similar face vectors into visual groups using DBSCAN and matches them with people tags (e.g. `Family/John Doe`). TagPup's Suggest will then match faces on untagged photos and boost recognized people tags to 1.0 (highest) confidence.

---

### Scenario D: Reviewing and Applying Suggestions
Suggest and apply tags in TagPup's pages: open a folder, run **Suggest**, review what it offers, and apply. The write is one change of the library's History; `run.bat undo <change>` shows what undoing it would put back, and `--apply` does.

---

### Scenario G: Cleaning up the Index
You can remove individual photos or entire folders from the index.

#### Remove Photos from the Index
```cmd
# Remove a single photo from the index database
run.bat remove --path "D:\Photos\Tagged_Archive\Family\sample.jpg"

# Remove all indexed photos under a specific folder recursively
run.bat remove --folder "D:\Photos\Tagged_Archive\Family"
```
*(Requires typing `YES` to confirm deletion. This deletes the photo's visual vector, tags, and face coordinates from the SQLite database via cascade deletion; it does not delete your physical photo files).*

---

### Scenario H: Interactive Tuning (TagTuner Web UI)
Start the interactive web interface in your browser to inspect metadata, tune tags, auto-match faces, and manage face identities.

```cmd
# Start TagTuner Web UI (Recommended: runs server inside the virtual environment)
.venv\Scripts\python tagpup_web.py --open tuner
```

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)

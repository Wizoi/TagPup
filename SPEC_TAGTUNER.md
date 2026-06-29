# TagTuner UI Specification

---
[◀ Back to README](README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

This document records the design, specifications, prerequisites, and instructions for the TagTuner User Interface and its matching mechanics.

## Design and Visual Aesthetics
- **Core Principle**: Dark mode, premium styling with Outfit (headings) and Plus Jakarta Sans (body) typography.
- **Glassmorphism**: Backdrop blur headers (`backdrop-filter: blur(12px)`) with subtle gradient borders.
- **Layout**: Two-pane split view. Left sidebar lists photos with unmatched faces (or resolved people in Face Matching mode). Right details panel shows the main image, interactive face cards, and photo metadata tags.
- **Interactive States**: 
  - Face cards transition smoothly when selected (expanding to vertical layout with custom options).
  - Validation styling displays inline errors for duplicate or empty names.

## Features and Mechanics

### 1. Interactive Face Tuning
- Clicking on a face card in the "Detected Faces" grid selects it and expands it to show the editing panel.
- **Deselection/Cancel**: Clicking "Cancel" or selecting another face card deselects the current face and hides the editing panel.
- **Suggestions (Top 5 matches)**: Dynamically fetches and displays the top 5 names of people whose faces are most similar to the selected face embedding (calculated via cosine similarity/dot-product of 512-dimensional embeddings).
- **Match Selection**:
  - Input field with standard HTML5 autocomplete linked to a global `<datalist>` of all known people.
  - Appends the name to the photo's `people` array in the database upon matching.
- **New Person Profile Creator**:
  - Clicking the `👤+` button (available in both unmatched face panel and face matching actions) opens the **Create New Person Profile** modal dialog.
  - Validates in real-time that the entered name is unique (case-insensitive check against `allKnownPeople`) and non-empty.
  - Fetches similar unmatched faces from `/api/face-matches-unmatched?id=<face_id>` (similarity $\ge 0.8$).
  - Allows bulk-tagging the seed face and all selected similar faces under the new name.
- **Unmatching**:
  - Displays an "Unmatch Face" button for resolved faces.
  - Clears the name from the face record (`SET name = NULL`).
  - Removes the person from the photo's `people` array if no other face in the same photo is matched to them.

### 2. Photo List Sorting and Grouping
- **Grouping & Nesting**: Photos are grouped under collapsible Year and physical parent folder headers.
- **Folder Sort**: Folders are sorted by the latest `mtime` of the photos contained within them.
- **Default State**: Folders start collapsed by default on initial page load.
- **Photo Sorting**: Inside each folder group, photos are sorted alphabetically ascending by filename.
- **Arrow Key Navigation**: Users can navigate up/down through visible sidebar entries using the arrow keys.
- **Matched Photos Toggle**: A toggle checkbox controls whether photos with zero unmatched faces are displayed in the list.

### 3. Detected Faces Grid Sorting
- Inside the details panel, the detected faces grid is sorted with **already matched faces at the top**, followed by unmatched faces.
- Within both groups, faces are sorted descending by their computed maximum similarity/correlation to known identities in the database.

### 4. Folder View and Thumbnail Selection Actions
- **Thumbnail Grid Selections**:
  - Supports standard checkbox check toggles and select all/none actions.
  - **Contiguous Range Selection (Shift-Click)**: Holding `Shift` while clicking a card or checkbox selects a continuous range of photos between the current target and the last-clicked path.
- **Date Taken Range**:
  - Displays the Date Taken range for multiple selections in the sidebar, showing the chronological minimum and maximum timestamps. Smart Date-Time formatting simplifies display.
- **Smart Renaming & Cycle Eviction**:
  - Automatically renames selected photos sequentially based on a `[Grouping] - [Index] - [Caption].[Ext]` pattern.
  - Pads sequence indices dynamically based on the selection size: 1 digit for <10 items, 2 for <100, up to 4 for 1000+ items.
  - If a renaming destination is occupied by an external file (not in the selection), it is automatically evicted to a unique conflict filename (`{Name}_conflict_{Counter}.ext`).
  - Utilizes a cycle-safe two-pass renaming sequence (using temp paths) to avoid self-overwrite overlaps.
  - Stamps the original filename in `XMP-xmpMM:PreservedFileName` metadata.
  - Auto-renames files on disk when their Title is edited if they contain a preserved original filename.
- **Inline Title Renaming**:
  - Allows renaming photo captions directly inside the grid by clicking on the filename (marked by dotted underlines). Pressing `Enter` or blurring saves the title, and `Escape` cancels editing.
- **Thumbnail Sizes**:
  - Segment buttons toggle grid sizes between **Small**, **Medium** (Default), and **Large** (preferences are saved in `localStorage`).
- **Camera Time Shift Highlights**:
  - Toggles a clock panel (`⏰`) to shift timestamps on camera models recursively. Toggles visual dashed highlights on cards matching the selected model.

## Backend APIs

### `GET` Endpoints
- `/api/photos?mode=unmatched&show_matched=<bool>`: Returns JSON array of photo records with unmatched face counts, file metadata, and folder paths.
- `/api/photo-details?path=<photo_path>`: Returns metadata details (path, filename, caption, people, tags, faces list with `max_similarity` scores).
- `/api/photo-file?path=<photo_path>`: Serves the original image file (supports dynamic resizing via `size=<int>` parameter).
- `/api/face-crop?id=<face_id>`: Dynamically crops the face from the original photo and returns it as a JPEG (caches the JPEG crop binary in the database).
- `/api/people`: Returns a sorted list of all unique people names in the database.
- `/api/people-with-counts`: Returns unique names with their respective face counts.
- `/api/person-faces?name=<name>`: Returns matched/outlier faces for a person (outliers defined as similarity < 0.85).
- `/api/face-matches?id=<face_id>`: Evaluates face similarity and returns the top 5 closest matched people.
- `/api/face-matches-unmatched?id=<face_id>`: Returns other unmatched faces with cosine similarity $\ge 0.8$ for bulk profile creation.
- `/api/unmatched-faces/people`: Returns unique names of people who have associated unmatched faces.
- `/api/unmatched-faces/person-matches?id=<face_id>`: Evaluates face matches against people names.
- `/api/browse-folder`: Invokes native folder dialog and returns selected path.

### `POST` Endpoints
- `/api/face/match`: Expects JSON body `{"face_id": int, "person_name": string}`.
- `/api/face/unmatch`: Expects JSON body `{"face_id": int}`.
- `/api/faces/match-bulk`: Expects JSON body `{"face_ids": list, "person_name": string}`. Matches face IDs in bulk. Implements duplicate-tagging protection.
- `/api/faces/unmatch-bulk`: Expects JSON body `{"face_ids": list}`. Unmatches face IDs in bulk.
- `/api/person/rename`: Expects JSON body `{"old_name": string, "new_name": string}`. Renames a person in the database and updates photo tags.
- `/api/faces/recluster`: Expects JSON body `{}`. Runs face clustering algorithm dynamically.
- `/api/photo/unmatch-all`: Expects JSON body `{"photo_path": string}`.
- `/api/photo/automatch`: Expects JSON body `{"photo_path": string}`. For each unmatched face in the photo, finds the closest resolved face in the DB. If similarity > 0.8, assigns the name and appends it to the photo's `people` array.
- `/api/folder/automatch`: Expects JSON body `{"folder_path": string}`. Automatches unmatched faces across all photos in the folder recursively.
- `/api/photo/rotate`: Expects JSON body `{"photo_path": string, "direction": string}`. Rotates a photo's visual thumbnail or raw representation on disk.
- `/api/photo/open-explorer`: Expects JSON body `{"photo_path": string}`. Opens the photo's directory in Windows File Explorer and selects it.
- `/api/photo/save-metadata`: Saves caption, people, and tags metadata directly to the image file via ExifTool and syncs the DB.
- `/api/photos/bulk-tags`: Adds or removes tags in bulk across a selection of photo paths.
- `/api/folder/time-shift`: Shifts timestamps recursively by camera model.
- `/api/folder/rename-photos`: Expects JSON body `{"photo_paths": list, "grouping": string}`. Sequentially renames selected photos based on the custom pattern and grouping template.

## Database Schema (SQLite)

### Table: `photos`
- `path` (TEXT PRIMARY KEY)
- `mtime` (REAL)
- `size` (INTEGER)
- `tags` (TEXT - JSON Array)
- `people` (TEXT - JSON Array)
- `captions` (TEXT - JSON Array)
- `raw_metadata` (TEXT - JSON)
- `embedding` (BLOB - 1024 floats)

### Table: `faces`
- `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `photo_path` (TEXT, FOREIGN KEY)
- `box` (TEXT - JSON coordinates)
- `embedding` (BLOB - 512 floats)
- `name` (TEXT)
- `crop_image` (BLOB - JPEG thumbnail cache)
- `prob` (REAL - MTCNN detection confidence score)

---
[◀ Back to README](README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)

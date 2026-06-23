# TagTuner UI Specification

This document records the design, specifications, prerequisites, and instructions for the TagTuner User Interface and its matching mechanics.

## Design and Visual Aesthetics
- **Core Principle**: Dark mode, premium styling with Outfit (headings) and Plus Jakarta Sans (body) typography.
- **Glassmorphism**: Backdrop blur headers (`backdrop-filter: blur(12px)`) with subtle gradient borders.
- **Layout**: Two-pane split view. Left sidebar lists photos with unmatched faces. Right details panel shows the main image, interactive face cards, and photo metadata tags.
- **Interactive States**: 
  - Face cards transition smoothly when selected (expanding to vertical layout with custom options).
  - Validation styling displays inline errors for unauthorized names.

## Features and Mechanics

### 1. Interactive Face Tuning
- Clicking on a face card in the "Detected Faces" grid selects it and expands it to show the editing panel.
- **Deselection/Cancel**: Clicking "Cancel" or selecting another face card deselects the current face and hides the editing panel.
- **Suggestions (Top 5 matches)**: Dynamically fetches and displays the top 5 names of people whose faces are most similar to the selected face embedding (calculated via cosine similarity/dot-product of 512-dimensional embeddings).
- **Match Selection**:
  - Input field with standard HTML5 autocomplete linked to a global `<datalist>` of all known people.
  - Strictly prevents creation of brand-new people from this view (validates against the known database list).
  - Appends the name to the photo's `people` array in the database upon matching.
- **Unmatching**:
  - Displays an "Unmatch Face" button for resolved faces.
  - Clears the name from the face record (`SET name = NULL`).
  - Removes the person from the photo's `people` array if no other face in the same photo is matched to them.

### 2. Photo List Sorting and Grouping
- **Sorting**: Photos in the left sidebar list are sorted by their modification time (`mtime`) descending (newest first).
- **Grouping**: Photos are grouped under folder header elements representing their physical parent directory path.
- **Folder Sort**: Folders are sorted by the latest `mtime` of the photos contained within them.
- **Manual Refresh**: The left sidebar list is not updated automatically upon match/unmatch operations to prevent layout shifting during rapid tuning. Users can manually refresh the list using the "Refresh List" button located at the top of the sidebar.

## Backend APIs

### `GET` Endpoints
- `/api/photos?mode=unmatched`: Returns JSON array of photo records with unmatched face counts, file metadata, and folder paths. Joined with the `photos` table to retrieve `mtime` for sorting.
- `/api/photo-details?path=<photo_path>`: Returns metadata details (path, filename, caption, people, tags, faces list).
- `/api/photo-file?path=<photo_path>`: Serves the original image file.
- `/api/face-crop?id=<face_id>`: Dynamically crops the face from the original photo and returns it as a JPEG.
- `/api/people`: Returns a sorted list of all unique people names in the database for autocomplete.
- `/api/face-matches?id=<face_id>`: Evaluates face similarity and returns the top 5 closest matched people.

### `POST` Endpoints
- `/api/face/match`: Expects JSON body `{"face_id": int, "person_name": string}`.
- `/api/face/unmatch`: Expects JSON body `{"face_id": int}`.
- `/api/faces/recluster`: Triggers the DBSCAN face clustering and identity resolution offline algorithm. Automatically syncs newly resolved face names back to their photos' `people` tag arrays.
- `/api/photo/unmatch-all`: Expects JSON body `{"photo_path": string}`. Clears names for all faces in the photo and removes matched names from the photo's `people` array.
- `/api/photo/automatch`: Expects JSON body `{"photo_path": string}`. For each unmatched face in the photo, finds the closest resolved face in the DB. If similarity > 0.8, assigns the name and appends it to the photo's `people` array.

## Database Schema (SQLite)

### Table: `photos`
- `path` (TEXT PRIMARY KEY)
- `mtime` (REAL)
- `size` (INTEGER)
- `tags` (TEXT - JSON Array)
- `people` (TEXT - JSON Array)
- `captions` (TEXT - JSON Array)
- `embedding` (BLOB)

### Table: `faces`
- `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `photo_path` (TEXT, FOREIGN KEY)
- `box` (TEXT - JSON coordinates)
- `embedding` (BLOB - 512 floats)
- `name` (TEXT)

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

### 0. Managing the Index

TagTuner can bring folders into the current database and take them out again, using the
**Add folders** and **Remove folder** controls in the header. This follows the division of
labour between the two interfaces: TagTuner is where identities are established and
confidence is raised, so it needs to be able to pull in new material directly rather than
requiring the folder to be added in TagPup first.

**Add folders** opens a picker rather than the native folder dialog. That dialog returns a
single path and cannot multi-select, so adding a season of shoots meant opening it once per
folder — and, while a second folder was refused outright, waiting beside the machine for
each to finish before the next could be started. The picker browses to a parent, lists its
subfolders with a recursive image count and how many of those images this database already
holds, and queues every ticked folder in one request. Folders already fully indexed are
listed unticked and can be hidden. A folder that holds images directly is offered as a row
of its own ("this folder itself"), which is also what a leaf folder with no subfolders
shows.

Queued folders are indexed **one at a time** — that constraint has not changed, because
indexing is GPU-bound — but the queue is drained without further asking. The header shows
what is running and what is waiting behind it, and **Cancel queue** forgets everything that
has not started. A folder that fails is reported and the rest of the queue carries on: one
unreadable folder should not cost the other nine. **Add folders** stays enabled while
indexing, because folders now queue rather than collide.

Indexing runs in the background with a progress bar, and **does not re-cluster**: that
would re-derive every name in the database, discarding corrections made here. Run
Recluster deliberately when it is wanted.

Removing a folder deletes index rows only — the photo files are untouched — but the face
rows go with the photos, so any assigned names and exclusions on them are discarded. The
confirmation says so, and the result reports how many of each were lost.

### 0.1 Tune Targets

The **Tune target** selector chooses what is being worked on. The three targets are
deliberately disjoint:

| Target | Sidebar lists | Tabs | Answers |
| :--- | :--- | :--- | :--- |
| **Folder Matches** | Photos containing at least one unmatched face | — | *Who is in this picture?* |
| **Identify Faces** | Names with unmatched candidates, plus `Unknown Faces` and `Ungrouped` | **Likely** / **Possible** | *Who are these nameless faces?* |
| **Review People** | Named people, by face count | **Confirmed** / **Needs Review** | *Are this person's faces correct?* |

The tab pairs are distinct on purpose. In **Review People** they split faces that already
carry a name by how well each matches that person's visual centroid, so *Needs Review*
means "named, but probably wrong" (similarity < 0.85). In **Identify Faces** they split
faces that carry no name at all by how confident the suggestion is. Earlier versions
labelled these *Matches/Outliers* and *High/Lower Confidence* and additionally offered an
`Unmatched` pseudo-person inside Review People, which dropped a flat, unordered list of
every nameless face into a mode meant for auditing named people. That entry has been
removed; nameless faces are reached through **Identify Faces**, which groups them.

### 0.2 Identify Faces: what the panel shows

**Every candidate the server returns is reachable.** Candidates are split across three
tabs: **Likely** (similarity >= 0.9), **Possible** (0.8 to 0.9) and **Ungrouped**
(everything else, which is what DBSCAN could not group -- returned deliberately with
`cluster_id: -1` and `similarity: 0.0`, because a face that forms no cluster is still a
face somebody may recognise). Earlier versions bucketed only the first two and let the
rest fall off the end of the chain, so a name whose candidates all failed to cluster --
the common case for a face seen once or twice -- showed a count in the sidebar and an
empty panel.

The **Ungrouped** tab appears only when it has something in it, and the panel opens on
whichever tab has faces rather than always on the most confident one: opening on an
empty tab is indistinguishable from the person having no candidates at all.

**Ignore Cluster** sits beside **Assign Cluster** and is the decision it is the
counterpart to -- this group is somebody, or this group is nobody we will ever name. It
excludes every face in the group in one action, states how many faces and photos that
covers, and makes clear that nothing is deleted: the faces move to the **Excluded**
bucket and can be restored. Reaching the same outcome by ticking each face meant thirty
clicks to say one thing, so it did not get said.

**Face Crop Details** shows both the crop being matched and the photo it came from, with
the crop's bounding box drawn over that photo. The two answer different questions: who is
this, and is the box even on a face. The crop's pixel size is shown, and flagged when its
shorter side is under 40px, because a suggestion made from a crop that small deserves
more doubt. The box overlay carries a large spread shadow to dim everything outside it,
so it is positioned directly when the preview image is already cached rather than only
from its load event -- otherwise the whole preview sat dimmed behind a zero-sized box.

### 0.3 Why a face appears where it does

The queue is **keyword-driven**. A face is offered under a name when its photo's
keywords mention that name and no face in that photo is linked to it yet. Two
consequences are worth stating, because both have been reported as bugs and neither
is one:

- A photo naming two people who both still lack a face offers **both** its faces under
  **both** names. The tool cannot know which is which -- that is the question being
  asked. Assigning one removes it from the other's list. Candidates now carry the other
  names their photo is missing, and the group header says "also names Miko Zellweg".
- **Unknown Faces** holds unnamed faces whose photo leaves no name unaccounted for --
  either it names nobody, or every name it carries already has a face. In practice most
  of these are photos with no people keywords at all: of 6,393 such faces in the
  kr-track library, 5,464 are in photos naming nobody.

That second case is why a plainly recognisable person can sit in Unknown Faces: the
keyword mechanism has no name to file them under, however obvious they are. So each
cluster is additionally compared against the **faces already named**.

- At **0.85 and above** the pill reads "Looks like Emory Kade (93%)".
- Between **0.70 and 0.85** it reads "Possibly Emory Kade (72%)", styled down and
  saying to check the faces first. The floor sits at 0.70 rather than higher because
  a weak guess is still a shortlist of one, and confirming or rejecting it costs a
  glance -- which beats reading a nameless grid.

The similarity is always shown, so the judgement stays with the person. The pill's
label fills the name box without committing; its **Assign** button commits the whole
cluster immediately, with no confirmation, because a prompt on every cluster is the
friction the button exists to remove. What makes that fair is the undo offered
straight afterwards.

**Clusters are ordered by confidence**, then by size. Sorting by size alone put the
biggest puzzles at the top and scattered the easy wins, so the page opened on the
hardest thing on it. Within a confidence band size still decides, because a bigger
cluster is more work resolved by the same click.

**Ignoring a cluster** asks once, through a real dialog rather than `confirm()` --
a native one cannot carry the "don't ask again" checkbox that somebody clicking
through hundreds of clusters needs. The preference is remembered across sessions.
With the question off it acts immediately, and either way the result is offered back:
an assignment is undone by unmatching, an exclusion by restoring.

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
- **Matched Photos Toggle**: A toggle checkbox in `folder-match` mode is intended to control whether photos with zero unmatched faces are displayed. **Known limitation:** the toggle is currently inert. It re-requests `/api/photos` with a `show_matched` parameter, but the endpoint ignores that parameter and its query excludes fully-matched photos unconditionally, so they are never returned to the client.

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
- `/api/databases`: Returns `{"databases": list, "selected": string}` — the selectable database names (without the `.db` suffix) and the current default from `config.ini`.
- `/api/photos?mode=<mode>`: Returns JSON array of photo records with unmatched face counts, file metadata, and folder paths. Only photos having at least one unmatched face are returned (`HAVING unmatched > 0`). The UI also appends `show_matched`, but the server does not currently read it — see the known limitation under *Matched Photos Toggle*.
- `/api/photo-details?path=<photo_path>`: Returns metadata details (path, filename, caption, people, tags, faces list with `max_similarity` scores).
- `/api/photo-file?path=<photo_path>`: Serves the original image file (supports dynamic resizing via `size=<int>` parameter).
- `/api/face-crop?id=<face_id>`: Dynamically crops the face from the original photo and returns it as a JPEG (caches the JPEG crop binary in the database).
- `/api/people`: Returns a sorted list of all unique people names in the database.
- `/api/people-with-counts`: Returns unique names with their respective face counts.
- `/api/person-faces?name=<name>`: Returns matched/outlier faces for a person (outliers defined as similarity < 0.85).
- `/api/face-matches?id=<face_id>`: Evaluates face similarity and returns the top 5 closest matched people.
- `/api/face-matches-unmatched?id=<face_id>`: Returns other unmatched faces with cosine similarity $\ge 0.8$ for bulk profile creation.
- `/api/unmatched-faces/people`: Returns the **Identify Faces** queue as `[{"name", "count"}]` — each name that has two or more unmatched candidates library-wide, plus two catch-all buckets: `Unknown Faces` (unmatched faces whose photo names nobody new) first, and `Ungrouped` last (faces whose every unmatched tag has only a single candidate, so no group can form). Counts are photo counts. The result is cached per database against a fingerprint of the `faces` table and recomputed when a face is added or named.
- `/api/unmatched-faces/person-matches?name=<person_name>`: Returns the unmatched faces that are candidates for the given name, as `{"faces", "total_count", "unclustered_total", "unclustered_shown", "has_more"}`. Candidates are clustered with DBSCAN and ordered by similarity to their cluster centroid. Faces DBSCAN treats as noise are still returned, reported with `cluster_id: -1` and ranked last, capped at 500 per request so a person with tens of thousands of unclustered candidates does not lock up the browser. Accepts the two bucket names `Unknown Faces` and `Ungrouped` in place of a person. Cached like the queue above.
- `/api/folder/index-active`: Returns `{"busy": bool, "remaining": int, "active": [{"folder", "name", "percent", "message"}], "queued": [{"folder", "name"}]}` for whatever is indexing right now. `index-status` can only answer about a folder the caller already knows about, so a freshly loaded page cannot use it to discover a job started before the page existed — it would show an idle, enabled **Add folder** button over a busy server. The page asks this on load and restores the progress bar and the disabled controls from the answer.
- `/api/folder/subfolders?path=<folder_path>`: Returns `{"parent", "own_images", "has_subfolders", "folders": [{"path", "name", "images", "has_subfolders", "indexed"}]}` — the immediate subfolders of a folder, each with a recursive count of indexable images and how many of them this database already holds. Indexing already recurses, so pointing it at a parent would work, but as one opaque job with a single progress bar for the lot; listing the children lets each be queued on its own, which is what makes progress legible and lets one folder be left out rather than sinking the run.
- `/api/folder/index-status?path=<folder_path>`: Returns `{"status", "percent", "message"}` for the background folder indexer. Status values are `queued`, `running`, `completed` or `failed`; a folder never indexed in this session reports `completed`.
- `/api/faces/excluded`: Returns `{"faces": list, "total_count": int}` for every face marked as not-a-person, each carrying its `reason`, so exclusions can be reviewed and undone.
- `/api/browse-folder`: Invokes native folder dialog and returns selected path.

### `POST` Endpoints
- `/api/databases/select`: Expects JSON body `{"db_name": string}`. Persists the chosen database as `default_db` in `config.ini`.
- `/api/databases/create`: Expects JSON body `{"db_name": string}`. Creates a new empty database file, seeded with the default taxonomy categories.
- `/api/photo/delete`: Expects JSON body `{"path": string}`. Sends the photo to the Windows Recycle Bin and removes it from the index.
- `/api/face/match`: Expects JSON body `{"face_id": int, "person_name": string}`.
- `/api/face/unmatch`: Expects JSON body `{"face_id": int}`.
- `/api/faces/match-bulk`: Expects JSON body `{"face_ids": list, "person_name": string}`. Matches face IDs in bulk. Implements duplicate-tagging protection.
- `/api/faces/unmatch-bulk`: Expects JSON body `{"face_ids": list}`. Unmatches face IDs in bulk.
- `/api/folder/index-start`: Expects JSON body `{"folder_paths": [string], "cluster": bool (optional, default false)}`, or `{"folder_path": string}` for a single folder. Folders are **queued**, not run together: indexing is GPU-bound, so two at once do not go twice as fast so much as make each other crawl. A single worker drains the queue in order. Returns `{"queued": [], "already_queued": [], "invalid": [], "pending": int}`, so a folder that does not exist, or is already running or queued, is reported rather than silently dropped; the request is refused with `400` only when no requested path is a folder at all. A folder that fails is recorded against itself and the rest of the queue still runs. Clustering is opt-in because it re-derives every name in the database rather than only the folder being added.
- `/api/folder/index-cancel`: Expects JSON body `{"all": true}` or `{"folder_paths": [string]}`. Drops folders that have **not started yet** and returns `{"cancelled": [], "pending": int}`. The folder currently being indexed is deliberately left alone: it owns a subprocess partway through writing rows, and killing that is a different and riskier operation than forgetting something that has not begun.
- `/api/folder/remove`: Expects JSON body `{"folder_path": string}`. Removes every indexed photo under that folder, and their faces, from the current database. Returns `{"photos_removed", "faces_removed", "manual_lost", "excluded_lost"}` — the last two report how much curated face work the removal discarded, since those rows go with the photos. **The photo files themselves are never deleted.**
- `/api/faces/exclude`: Expects JSON body `{"face_ids": list, "reason": string (optional)}` (a single `face_id` is also accepted). Marks faces as not-a-person. Any name they carried is cleared, and the person is dropped from the photo when no other face of theirs remains in it. Excluded faces take no part in clustering, match suggestions, or the Identify Faces queue.
- `/api/faces/restore`: Expects JSON body `{"face_ids": list}`. Reverses an exclusion, returning the faces unnamed and unclaimed so they can be identified again.
- `/api/person/rename`: Expects JSON body `{"old_name": string, "new_name": string}`. Renames a person in the database and updates photo tags.
- `/api/faces/recluster`: Expects JSON body `{}`. Runs face clustering algorithm dynamically.
- `/api/photo/unmatch-all`: Expects JSON body `{"photo_path": string}`.
- `/api/photo/automatch`: Expects JSON body `{"photo_path": string}`. For each unmatched face in the photo, finds the closest resolved face in the DB. If similarity > 0.8, assigns the name and appends it to the photo's `people` array.
- `/api/folder/automatch`: Expects JSON body `{"folder_path": string}`. Automatches unmatched faces across all photos in the folder recursively.
- `/api/photo/rotate`: Expects JSON body `{"path": string, "direction": string}`. Rotates the photo 90 degrees on disk. `direction` must be `"left"` or `"right"`.
- `/api/photo/open-explorer`: Expects JSON body `{"path": string}`. Opens the photo's directory in Windows File Explorer and selects it.
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

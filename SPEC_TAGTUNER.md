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
would re-derive every name in the database, discarding corrections made here. Cluster
deliberately when it is wanted: the runner's **Run Identity Resolution Clustering**, or
`tagpup_cli.py cluster-faces`.

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

- The candidate list for one person can be very large. Somebody named in eight photos,
  two of them crowd shots, offers every unnamed face in all eight: 105 candidates, of
  which perhaps three are them. That is the honest consequence of keyword-driven
  matching, not a fault -- but it is only workable if the list is **ordered**. Where the
  person already has faces named, every candidate carries `person_similarity`, the
  resemblance to those faces, and the list is sorted by it; the **Likely** (>= 0.75) and
  **Possible** (0.60 to 0.75) tabs use it too. Each face shows the number. Candidates are
  ranked, never filtered: two can both genuinely be this person in different photos --
  measured here at 0.826 and 0.822 -- so a cutoff would throw a real match away. Where
  the person has no named faces yet, there is nothing to rank against and the bands fall
  back to cluster similarity.
- A photo naming two people who both still lack a face offers **both** its faces under
  **both** names.The tool cannot know which is which -- that is the question being
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

**Every face carries its own suggestion, and the bucket is ordered by it.** A cluster
cannot hold a suggestion on behalf of faces that resemble nothing, but each of those
faces can hold its own -- and Unknown Faces is full of exactly that. Each card shows
the name and score where one clears 0.70, clicking the badge selects the face and fills
the name box (assigning still takes **Assign Selected**), and faces are ordered by their
best match to anyone already named -- *including* the ones below the floor, whose score
the server reports even while withholding the name. Ordering only the named ones left
470 of 500 in arrival order, which is no order at all.

Suggestions are scored against the **best single named face**, which is exactly what the
diagnostics panel under a selected face reports. They were briefly scored against the
average of a person's faces instead, which put two different numbers for one comparison
on one screen -- and averaging is the more cautious of the two, dragging down when
somebody's named faces vary in light and angle, as they always do at a meet. That
caution was costing real matches.

The selection controls -- **Select All**, **Assign Selected**, **Exclude** -- are shown for
every view including Unknown Faces. They were once hidden there, on the assumption that
each cluster's own **Assign Cluster** button was enough; once the Unclustered section
correctly lost its cluster-wide buttons, that left the bucket with no way to act on
anything. The count line also says when the list is a window rather than a total
("first 500 of 800, more appear as you clear these"), because a cap presented as a total
makes the remainder look lost.

The **third tab is named for what it holds**,which depends on whether there is
anybody to rank against. Where the person has reference faces it is **Unlikely** --
candidates in photos naming them that do not resemble the faces already named as them
(under 0.60). Most of a crowd photo lands there, and that is the point: it is what is
left once the likely ones are lifted out. Where the person has no named faces it is
**Unclustered** -- the faces grouping could not place. One tab, two meanings, so it
carries two names rather than one misleading one.

The **Unclustered** section is not a cluster:it holds the faces grouping could not
place, which resemble each other no more than they resemble anything. It is therefore
denied every cluster-wide action -- no **Assign Cluster**, no **Ignore Cluster**, and no
cluster-level name suggestion, since one member's guess says nothing about the rest. It
briefly had all three, which offered "Possibly Mira Wexford -- Assign 105" over 105
unrelated faces in one click. The faces stay listed and selectable, because per-face
assignment is the reason to show them at all. The tab is called **Unclustered** rather
than Ungrouped, because the sidebar's **Ungrouped** bucket means something else entirely.

**Clusters are ordered by confidence**, then by size.Sorting by size alone put the
biggest puzzles at the top and scattered the easy wins, so the page opened on the
hardest thing on it. Within a confidence band size still decides, because a bigger
cluster is more work resolved by the same click.

**Ignoring a cluster** asks once, through a real dialog rather than `confirm()` --
a native one cannot carry the "don't ask again" checkbox that somebody clicking
through hundreds of clusters needs. The preference is remembered across sessions.
With the question off it acts immediately, and either way the result is offered back:
an assignment is undone by unmatching, an exclusion by restoring.

### 0.4 Ordering the people list

The sidebar orders people by how many photos are waiting, which puts the most work
first. **Name (A-Z)** is offered alongside, because finding one person among twenty-two
ordered by count means reading all of them, and you usually already know the name. The
comparison ignores case, so a lower-case name does not sort after every capitalised one.
The choice is remembered across sessions.

The buckets -- `Unknown Faces`, `Ungrouped`, `Excluded` -- stay pinned to the top in
either order. They are not people, and sorting them into the alphabet would bury them
between two names.

### 0.4.1 Counting, ranking and assigning in Identify Faces

**The sidebar counts faces.** A face is the unit of work here: one photo of a start line
holds thirty, and clearing it is thirty decisions. It previously used three units in one
list — photos for people and Ungrouped, faces for Excluded, photos for Unknown Faces —
so "710 photos" sat beside "4,739 faces" in the panel. The photo count is on hover.

**The strongest candidates are shown, not the first ones found.** Unclustered faces are
capped at 500 because a person in many group photos can have tens of thousands and a
card for each locks up the browser. The cap used to be applied *before* ranking, so with
4,739 candidates the 500 on screen were an arbitrary sample that happened to be sorted,
and the best matches in the other 4,239 were unreachable — clearing the queue sliced the
same way next time. Every candidate is now scored, then the top 500 are built.

**Selection behaves like a file list.** A plain click selects only the face it lands
on, and clicking the only selected face clears it. `Ctrl` (or `Cmd`) adds and removes
one at a time. `Shift` takes everything between that face and the last one clicked on
its own, measured in render order; a second `Shift`-click re-measures from the same
anchor rather than growing, so overshooting a range costs one click to fix.
`Ctrl`+`Shift` adds a range to what is already chosen. The grid disables text selection,
because `Shift`-clicking it is a selection gesture and highlighted labels between two
cards look like a broken page.

Every click used to toggle. That is fine for two faces and unusable for two hundred:
picking a run meant two hundred clicks, and one stray click in the middle silently took
a face back out of a selection about to be assigned.

**A mixed selection is assigned by its badges.** Selecting several faces that each
resemble a different person left one box asking for one name. Where the selected faces
disagree and nothing is typed, the button reads *Assign N to their matches* and sends
each face to the person its own badge names, after stating the split. Typing a name
still overrides them, because sometimes the machine is wrong about all of them.

### 0.4.2 Excluding a face

Excluding keeps the row and the crop but takes the face out of identity work entirely,
and is reversible from the **Excluded** bucket.

**The reason is picked, not typed.** Four buttons — *not a person*, *stranger*, *bad
crop*, *duplicate* — cover every exclusion in this library. It was a free-text prompt,
which produced "fuzzy" and "wrong person" beside "bad crop" (three ways of recording two
things) and cost a typed answer on the fastest action in the app. Cancelling excludes
nothing.

**The reason is shown on the face** in the Excluded bucket. It had been recorded since
exclusions existed and never displayed, so the one place it could be useful — reviewing
what you ruled out and why — did not have it. It takes no part in matching; only the
`excluded` flag does.

### 0.5 Review Tags

A third mode beside Folder Matches and Identify Faces, for the word tags face curation
could not reach. A tagged photo is what the suggester learns the next photo from, so a
misspelling spreads exactly as a wrong name does — and until this view there was nowhere
to ask *where is this tag, and what does it touch*.

The sidebar lists every tag with its photo count, ordered and searched by the same
controls the people list uses. Four buckets sit pinned above the alphabet, because a
flat list of 881 tags hides the handful worth looking at: **No hierarchy** (a tag with
no path), **Used once** (where typos hide, never confirmed by a second photo), **On no
photo** (in the vocabulary, describing nothing, still offerable), and **People missing a
path** (a person written as a bare leaf, which the keyword convention forbids). An empty
bucket is not shown. A tag carries a short note where something is wrong with it.

Opening a tag shows its photos and says what it is — how many carry it, whether it has a
hierarchy, whether the suggester has it cached. **Rename**, **Merge into…** and
**Retire** change it everywhere it lives: the photo files, `photos.tags`,
`tag_taxonomy` and `tag_embeddings`. Each asks the server for a plan first and states
what it would touch before writing; a tag can sit on hundreds of photo files and the
plan is the last point at which that costs nothing. Dropping the cached embedding is not
bookkeeping — a tag cleaned out of every file goes on being suggested while zero-shot
matching can still read its name.

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
- `/api/people`: Returns a sorted list of all unique people names in the database, leaving out people hidden from autocomplete. `include_hidden=1` includes them: the page asks that way to check whether a name already exists.
- `/api/people-with-counts`: Returns unique names with their respective face counts.
- `/api/person-faces?name=<name>`: Returns matched/outlier faces for a person (outliers defined as similarity < 0.85).
- `/api/face-matches?id=<face_id>`: Evaluates face similarity and returns the top 5 closest matched people.
- `/api/face-matches-unmatched?id=<face_id>`: Returns other unmatched faces with cosine similarity $\ge 0.8$ for bulk profile creation.
- `/api/unmatched-faces/people`: Returns the **Identify Faces** queue as `[{"name", "count"}]` — each name that has two or more unmatched candidates library-wide, plus two catch-all buckets: `Unknown Faces` (unmatched faces whose photo names nobody new) first, and `Ungrouped` last (faces whose every unmatched tag has only a single candidate, so no group can form). Counts are photo counts. The result is cached per database against a fingerprint of the `faces` table and recomputed when a face is added or named.
- `/api/unmatched-faces/person-matches?name=<person_name>`: Returns the unmatched faces that are candidates for the given name, as `{"faces", "total_count", "unclustered_total", "unclustered_shown", "has_more"}`. Candidates are clustered with DBSCAN and ordered by similarity to their cluster centroid. Faces DBSCAN treats as noise are still returned, reported with `cluster_id: -1` and ranked last, capped at 500 per request so a person with tens of thousands of unclustered candidates does not lock up the browser. Accepts the two bucket names `Unknown Faces` and `Ungrouped` in place of a person. Cached like the queue above.
- `/api/unmatched-faces/build-status?name=<person_name>`: Returns `{"name", "active", "percent", "stage", "message"}` for a grid that is being built right now, or `{"active": false}` when none is. Building a person's grid on a large library is the better part of a minute, nearly all of it grouping the candidates, so the request that does the work publishes how far it has got and the page polls this alongside its own in-flight `person-matches` request. Touches no database; the threaded server answers it while the slow request is still running. Stages are `reading`, `grouping`, `suggesting`, `ranking` and `building`, weighted so `percent` only moves forward. A cached grid never reports progress, because it never builds one.
- `/api/folder/index-active`: Returns `{"busy": bool, "remaining": int, "active": [{"folder", "name", "percent", "message"}], "queued": [{"folder", "name"}]}` for whatever is indexing right now. `index-status` can only answer about a folder the caller already knows about, so a freshly loaded page cannot use it to discover a job started before the page existed — it would show an idle, enabled **Add folder** button over a busy server. The page asks this on load and restores the progress bar and the disabled controls from the answer.
- `/api/folder/subfolders?path=<folder_path>`: Returns `{"parent", "own_images", "has_subfolders", "folders": [{"path", "name", "images", "has_subfolders", "indexed"}]}` — the immediate subfolders of a folder, each with a recursive count of indexable images and how many of them this database already holds. Indexing already recurses, so pointing it at a parent would work, but as one opaque job with a single progress bar for the lot; listing the children lets each be queued on its own, which is what makes progress legible and lets one folder be left out rather than sinking the run.
- `/api/folder/index-status?path=<folder_path>`: Returns `{"status", "percent", "message"}` for the background folder indexer. Status values are `queued`, `running`, `completed` or `failed`; a folder never indexed in this session reports `completed`.
- `/api/faces/excluded`: Returns `{"faces": list, "total_count": int}` for every face marked as not-a-person, each carrying its `reason`, so exclusions can be reviewed and undone.
- `/api/browse-folder`: Invokes native folder dialog and returns selected path.

- `/api/tags/list`: Every word tag the library knows, with `count` (photos carrying it), `flat`, `in_taxonomy`, `has_embedding` and `is_person`. People are excluded unless `?people=1`, since the point of this view is the tags face curation could not reach. Also returns `buckets`: `flat`, `used_once` (where typos hide), `unused` (in the vocabulary, on no photo, yet still feeding zero-shot matching) and `people_without_a_path` (a person written as a bare leaf, which the keyword convention forbids).
- `/api/tags/photos?tag=`: The photos carrying one tag, newest first. Matches the whole tag, never a prefix.

### `POST` Endpoints

**What may be set.** A tag or a person's name being set is refused with `400` and a message saying why if it holds `|` or `\` (which other programs read as a break between levels), a control character such as a tab or a line break, or nothing at all. A tag also may not have an empty level (`A//B`, `A/`); a person's name is one level, so it may not hold `/`. The rules are `problem_with_tag` and `problem_with_name` in `tagpup/core/vocabulary.py`, the same as TagPup's, and the page asks them before sending.

- `/api/databases/select`: Expects JSON body `{"db_name": string}`. Persists the chosen database as `default_db` in `config.ini`.
- `/api/databases/create`: Expects JSON body `{"db_name": string}`. Creates a new empty database file, seeded with the default taxonomy categories.
- `/api/face/match`: Expects JSON body `{"face_id": int, "person_name": string}`. An excluded face is refused with `409`: it must be restored before it can be named. `person_name` must be a name that may be set (above).
- `/api/face/unmatch`: Expects JSON body `{"face_id": int}`.
- `/api/faces/match-bulk`: Expects JSON body `{"face_ids": list, "person_name": string}`. Matches face IDs in bulk. Implements duplicate-tagging protection. Excluded faces are skipped, never named. Returns `{"success", "matched", "matched_ids", "skipped_excluded"}` — `matched` is the rows actually changed, and the page offers Undo for `matched_ids` only. `person_name` must be a name that may be set (above).
- `/api/faces/unmatch-bulk`: Expects JSON body `{"face_ids": list, "undo": bool}`. Unmatches face IDs in bulk, marking each as a person's decision that it is nobody (`name_source = 'manual'`). With `"undo": true` -- the page's Undo after an assignment -- they go back to unreviewed (`name_source` NULL) instead.
- `/api/folder/index-start`: Expects JSON body `{"folder_paths": [string], "cluster": bool (optional, default false)}`, or `{"folder_path": string}` for a single folder. Folders are **queued**, not run together: indexing is GPU-bound, so two at once do not go twice as fast so much as make each other crawl. A single worker drains the queue in order. Returns `{"queued": [], "already_queued": [], "invalid": [], "pending": int}`, so a folder that does not exist, or is already running or queued, is reported rather than silently dropped; the request is refused with `400` only when no requested path is a folder at all. A folder that fails is recorded against itself and the rest of the queue still runs. Clustering is opt-in because it re-derives every name in the database rather than only the folder being added.
- `/api/folder/index-cancel`: Expects JSON body `{"all": true}` or `{"folder_paths": [string]}`. Drops folders that have **not started yet** and returns `{"cancelled": [], "pending": int}`. The folder currently being indexed is deliberately left alone: it owns a subprocess partway through writing rows, and killing that is a different and riskier operation than forgetting something that has not begun.
- `/api/folder/remove`: Expects JSON body `{"folder_path": string}`. Removes every indexed photo under that folder, and their faces, from the current database. Returns `{"photos_removed", "faces_removed", "manual_lost", "excluded_lost"}` — the last two report how much curated face work the removal discarded, since those rows go with the photos. **The photo files themselves are never deleted.**
- `/api/faces/exclude`: Expects JSON body `{"face_ids": list, "reason": string (optional)}` (a single `face_id` is also accepted). Marks faces as not-a-person. Any name they carried is cleared, and the person is dropped from the photo when no other face of theirs remains in it. Excluded faces take no part in clustering, match suggestions, automatch, or the Identify Faces queue. Returns `excluded` as the rows actually changed.
- `/api/faces/restore`: Expects JSON body `{"face_ids": list}`. Reverses an exclusion, returning the faces unnamed and unclaimed so they can be identified again. Only faces that are excluded are touched; returns `restored` as the rows actually changed.
- `/api/tags/merge`: Expects JSON body `{"from": string, "into": string, "apply": bool, "retire": bool}`. Renames a tag or merges it into another across the photo files, `photos.tags`, `tag_taxonomy` and `tag_embeddings`. **Defaults to a dry run** — without `apply` nothing is written and the plan comes back, reporting how many photos are affected, how many already carry the target, and how many embedding and taxonomy rows would go. `retire` drops the tag without a target. Dropping the cached embedding is not optional bookkeeping: a tag cleaned out of every file still gets suggested while its embedding survives. TagPup's in-memory `suggest_status` lives in another process and is not refreshed by this. `into` must be a tag that may be set (above); `from` is not checked, so a bad tag can always be merged away.
- `/api/person/rename`: Expects JSON body `{"old_name": string, "new_name": string}`. Renames a person in the database and updates photo tags. `new_name` must be a name that may be set (above).
- `/api/photo/unmatch-all`: Expects JSON body `{"photo_path": string}`.
- `/api/photo/automatch`: Expects JSON body `{"photo_path": string}`. For each unmatched face in the photo, finds the closest resolved face in the DB. If similarity > 0.8, assigns the name and appends it to the photo's `people` array.
- `/api/folder/automatch`: Expects JSON body `{"folder_path": string}`. Automatches unmatched faces across all photos in the folder recursively.

Photo actions -- rotate, delete, open in Explorer, save metadata, bulk tags, time shift and
Smart Rename -- are TagPup's (`SPEC_TAGPUP_GUI.md`). TagTuner kept copies of their routes
that its page never called; they were removed in phase 2.

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

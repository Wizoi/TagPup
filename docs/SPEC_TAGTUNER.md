# TagTuner UI Specification

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
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

### The gear

A gear at the right of the header holds what is not tuning: **Tag editor** opens the tag
tree's editor -- the same module TagPup's gear opens (`web/common/tag-editor.js`), over the
same routes, which both apps serve; its tags are in alphabetical order, and a rename,
new tag, flag or delete changes the tree in place, without rebuilding it
(docs/SPEC_TAGPUP_GUI.md, "Tag Taxonomy Tree Manager"). **Library settings** opens the library's settings
(below). **Open in TagPup**
opens TagPup on the same library in a new tab, its address from `/api/apps`. **Activity...**
opens the Activity page in a new tab (below). The menu
opens on click, Enter or Space; the arrow keys move through it, Escape closes it and puts
the focus back on the gear, and a click anywhere else closes it. Below its items it says
which version of TagPup answers (`/api/server`). While the always-on process moves onto
a new version, a request turned away (503, `X-TagPup-Updating`) is sent again until the
new version answers, for up to two minutes (`web/common/api.js`). An edit made in the
editor refreshes the names offered while typing, and one that rewrote photos refreshes
the list.

### History

**History...** in the gear lists the library's last changes and offers **Undo** beside
each that can be undone, rehearsed first and confirmed (`web/common/history-dialog.js`,
over `/api/history`; the same dialog as TagPup's). A photo file changed since is
refused, named by photo id, never overwritten. After an undo that changed something the
list beside the photo is refreshed.

### Folders to review

Sync walks the library's root folders (Library settings, below) and never indexes a
folder holding no indexed photo on its own: it lists it to review. **Folders to review...**
in the gear, and a notice in the header saying how many there are (as the library's last
sync counted them, `/api/sync`), open a dialog (`web/tuner/review.js`, over
`/api/sync/review`) listing each -- the topmost such folder below a root, with its photo
count -- with **Include**, which queues it for indexing with its subfolders (the indexing
bar follows it), and **Ignore**, which adds it to the library's ignored folders (a
journaled change of its settings, which History can undo). Either takes it off the list.
The page opened with `?review=1` opens the dialog as it starts: the Activity page's link.

### Activity

**Damaged photos** are said, not left as gaps (findings #407). The header carries a badge while the library has any -- "⚠ 13 unreadable photos, 1 possibly incomplete copy" (`/api/damaged-photos`) -- linking to the Activity page's Needs attention; nothing when it has none.

**Activity...** in the gear opens the Activity page in a new tab, at `/activity/` -- one page for the background work of every library in the data folder, served by both apps alike, and to this PC only (`web/activity/`, over `/api/activity/`). Its sections:

- **Needs attention**, first: each library's photos found damaged -- a picture that does not decode (cut short, zero bytes), never indexed or written to, and one that decodes but ends in zero bytes, possibly an incomplete copy -- with its path, why, when it was first found, its size and modified time, a link opening its folder in TagPup, and **Check again** (for each, and **Check all again** for a library) reading them again now whatever their modified time says; **Nothing needs attention.** when there is none, and the section's link in the page's menu counts them. A photo replaced by a good copy leaves the list by itself.
- **Now**, asked again every three seconds while the tab is in view and not while it is hidden: each library's indexing (the folders being indexed and how far, those waiting), its Suggest runs, a recurring job running, the folder watcher's sync, and the always-on process (an update waiting, the server draining, an update refused as not newer).
- **Scheduled jobs**: each job with why it is scheduled (`safety`, `retention`, `catch-up`) and, for each library, its last run -- when, how long, how it ended, what it changed as counts, why it failed -- and when it is due; **Run now**, asked first; its last runs, unfolded; a failed last run flagged until a later one is done. **Logs for this run** shows the lines the run logged.
- **Sync & watcher**: each library's root folders (on disk or missing, watched), when a change was last noticed, its last sync of one folder and of the whole library ("last in step"), and the folders to review, opening TagTuner's dialog.
- **Snapshots**: each library's, with kind, age and size, and the disk they take.
- **Always on**: the version answering, running since, crashes, the last update, an update refused.
- **Recent activity**: one timeline of what was done -- changes of the journal, job runs, syncs, runs of the indexer, updates -- newest first, with their counts; **More** reads further back.
- **Logs**: a tab for each log in `data/logs`, newest lines first, each as time, level, logger and message, filtered by level (warnings and worse unless asked) and by text; **Follow** asks for new lines every three seconds; **Raw** shows the end of the file as written, **Download** saves it.

### Library settings

**Library settings** in the gear opens the settings of the library the page is on
(`web/common/settings-dialog.js`, over `/api/settings`): the CLIP model its vectors are
made with, face detection, the ExifTool program, and the library's folders -- its root
folders, which sync walks, and the folders it ignores, one full path a line, none a whole
drive and none holding `"`, `<`, `>`, `|`, `?` or `*` (the validator's `folders`); not locked. A library has no roots until the owner
sets them, here or with the CLI's `settings set library.roots`; until then nothing is
offered for review and the review notice shows nothing. When a library's roots are set, every folder under a new root that holds photos and no indexed photo at that moment is added to the ignored folders in the same journaled change (`settings.excluded_under`; owner, 2026-09-26: "any folders not added assume excluded"): only a folder that appears later is offered for review. Suggest's candidate words and the
rename format are TagPup's features, and are in TagPup's gear. They are the library's own, kept in it (`tagpup.services.settings`), not the
machine's. The dialog is made from each setting's one declaration
(`tagpup.core.validation.SETTINGS`): its label, its type (a checkbox for true or false,
a box of text otherwise), its value, and an info button (i) that shows what it changes
and when. Each value is checked as it is typed against the rules `/api/rules` publishes,
the message shown under it and Save disabled while one is refused; the server checks it
again before it writes.

The CLIP model (model, weights, keep the full frame, widest aspect ratio, image size),
face detection (smallest face, confidence, stage thresholds) and the ExifTool program are
**locked**: shown, not editable. Each group opens only through its **Change...**, which
lists what changing it does, each with a box to tick -- every photo's vector was made
with the old model, so Suggest finds nothing until every folder is indexed again; face
detection applies only to photos indexed afterwards; every read and write of a photo file
goes through ExifTool. Save is enabled only when something changed, every value may be
set, and every box of every group opened is ticked. After a locked setting is saved the
page reloads. Candidate words and the rename format are edited directly, and apply from
the next Suggest or rename.

Save sends only what changed, as one change of the library's journal: it is in the
library's history and can be undone (`tagpup_cli.py history`, `undo`). A library opened
for the first time since its settings moved into it is stamped once, from the home's
`config.ini` if it has one, else with the defaults; a new library is made with the
defaults. Escape, Cancel or the close button close it without saving.

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

**Remove folder** lists the folders the library holds photos in (`/api/folder/indexed`),
and the folders above them, not the folders on disk, with a filter box. Each shows every
photo under it -- what removing it takes -- a folder above says its photos are "in folders
under it", and one gone from disk is marked "not on disk": that is the usual
reason to remove a folder, and the native dialog, which lists the disk, could not pick it
(findings.md, #47). Choosing one and confirming removes its rows; the files are never
touched.

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
means "named, but probably wrong": less like the person's closest face than a name would
be offered at (`tagpup.core.clustering.looks_wrong`, below 0.70). In **Identify Faces** they split
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

- In the **likely** band (`tagpup.core.clustering.band`: alike enough to be named unasked,
  0.80 and above) the pill reads "Looks like Emory Kade (93%)".
- In the **possible** band (alike enough to be offered, 0.70 to 0.80) it reads
  "Possibly Emory Kade (72%)", styled down and
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
comparison is the shared one for tags and people -- alphabetical, case and accents ignored, numbers in order ("Trip 3" before "Trip 10"), a path one level at a time (`compareTagNames` / `sortedTags`, `web/common/vocabulary.js`; the server's `tag_sort_key`, `tagpup.core.vocabulary`, is the same order, and `tests/fixtures/tag_order.json` holds the two to one table) -- so a lower-case name does not sort after every capitalised one. By count, people with the same count are in that order too.
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
controls the people list uses (by name in the shared alphabetical order, by count with the alphabet breaking a tie). Four buckets sit pinned above the alphabet, because a
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
  - Input field with standard HTML5 autocomplete linked to a global `<datalist>` of all known people, alphabetical.
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
- **Matched Photos Toggle**: A toggle checkbox in `folder-match` mode controls whether photos whose faces are all named are listed. Off (the default), only photos with an unnamed face are listed, and a photo leaves the list when its last face is named. On, `/api/photos?show_matched=true` lists every photo with a face; a finished photo stays listed, dimmed (`all-matched`), so the folder can be stepped through in order.

### 3. Detected Faces Grid Sorting
- The photo's tag and people pills in the details panel are alphabetical, in the shared order.
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

**Who can reach it.** The server listens on this PC only: 127.0.0.1 and ::1, one socket each, so `localhost` answers at once whichever address the browser tries first (`tagpup.web.app.bind`) *(owner, 2026-09-26)*. `tagpup_web.py --listen lan` binds every interface instead; it is for phase 10, when the apps have logins, and off by default. Every request must also name this machine in its Host header and, when it has one, its Origin (`tagpup.web.security`), else 403.

### `GET` Endpoints
- `/api/databases`: Returns `{"databases": list}` — the selectable database names (without the `.db` suffix). Which one was opened last is the browser's to remember.
- `/api/photos?mode=<mode>`: Returns JSON array of photo records with unmatched face counts, file metadata, and folder paths. Only photos having at least one unmatched face are returned, unless `show_matched=true`, which returns every photo with a face (see *Matched Photos Toggle*).
- `/api/photo-details?path=<photo_path>`: Returns metadata details (path, filename, caption, people, tags, faces list with `max_similarity` scores).
- `/api/photo-file?path=<photo_path>&size=<int>&upright=1`: Serves the original image file, or with `size` a JPEG copy no larger than that on a side. The copy is as stored -- the face views draw boxes over it in the stored pixels' coordinates -- unless `upright=1`, which turns it by its Orientation as a person sees it; the Tags view's cards, which draw no boxes, ask that way.
- `/api/face-crop?id=<face_id>`: Dynamically crops the face from the original photo and returns it as a JPEG (caches the JPEG crop binary in the database).
- `/api/people`: Returns an alphabetical list (`tag_sort_key`) of all unique people names in the database, leaving out people hidden from autocomplete. `include_hidden=1` includes them: the page asks that way to check whether a name already exists.
- `/api/people-with-counts`: Returns unique names with their respective face counts.
- `/api/person-faces?name=<name>`: Returns matched/outlier faces for a person (an outlier is a face `possibly_wrong`:
  `tagpup.core.clustering.looks_wrong` against the person's closest face in the years around the photo).
- `/api/face-matches?id=<face_id>`: Evaluates face similarity and returns the top 5 closest matched people.
- `/api/face-matches-unmatched?id=<face_id>`: Returns other unmatched faces with cosine similarity $\ge 0.8$ for bulk profile creation.
- `/api/unmatched-faces/people`: Returns the **Identify Faces** queue as `[{"name", "count"}]` — each name that has two or more unmatched candidates library-wide, plus two catch-all buckets: `Unknown Faces` (unmatched faces whose photo names nobody new) first, and `Ungrouped` last (faces whose every unmatched tag has only a single candidate, so no group can form). Counts are face counts (`"unit": "face"`), with the photo count beside each. The result is cached per database against a fingerprint of the `faces` table and recomputed when a face is added or named.
- `/api/unmatched-faces/person-matches?name=<person_name>`: Returns the unmatched faces that are candidates for the given name, as `{"faces", "total_count", "unclustered_total", "unclustered_shown", "has_more"}`. Candidates are clustered with DBSCAN and ordered by similarity to their cluster centroid. Faces DBSCAN treats as noise are still returned, reported with `cluster_id: -1` and ranked last, capped at 500 per request so a person with tens of thousands of unclustered candidates does not lock up the browser. Accepts the two bucket names `Unknown Faces` and `Ungrouped` in place of a person. Cached like the queue above.
- `/api/unmatched-faces/build-status?name=<person_name>`: Returns `{"name", "active", "percent", "stage", "message"}` for a grid that is being built right now, or `{"active": false}` when none is. Building a person's grid on a large library is the better part of a minute, nearly all of it grouping the candidates, so the request that does the work publishes how far it has got and the page polls this alongside its own in-flight `person-matches` request. Touches no database; the threaded server answers it while the slow request is still running. Stages are `reading`, `grouping`, `suggesting`, `ranking` and `building`, weighted so `percent` only moves forward. A cached grid never reports progress, because it never builds one.
- `/api/folder/index-active`: Returns `{"busy": bool, "remaining": int, "active": [{"folder", "name", "percent", "message"}], "queued": [{"folder", "name"}]}` for whatever is indexing right now. `index-status` can only answer about a folder the caller already knows about, so a freshly loaded page cannot use it to discover a job started before the page existed — it would show an idle, enabled **Add folder** button over a busy server. The page asks this on load and restores the progress bar and the disabled controls from the answer.
- `/api/folder/subfolders?path=<folder_path>`: Returns `{"parent", "own_images", "has_subfolders", "folders": [{"path", "name", "images", "has_subfolders", "indexed"}]}` — the immediate subfolders of a folder, each with a recursive count of indexable images and how many of them this database already holds. Indexing already recurses, so pointing it at a parent would work, but as one opaque job with a single progress bar for the lot; listing the children lets each be queued on its own, which is what makes progress legible and lets one folder be left out rather than sinking the run.
- `/api/folder/indexed`: Returns `{"folders": [{"path", "photos", "own_photos", "on_disk"}]}` — each folder the library holds photos directly in, and each folder above those up to (not including) its drive's root, by path, spelled as the library stores it (a folder above, as the first folder under it spells it). `photos` counts every photo under it, which is exactly what removing it takes (removal takes the folders under it too); `own_photos` counts those directly in it, 0 for a folder above; `on_disk` says whether the folder is still there. Remove Folder offers these, not the disk's folders: the usual reason to remove a folder is that it is gone from disk, and a dialog of the disk's folders cannot pick one that is (#47).
- `/api/folder/index-status?path=<folder_path>`: Returns `{"status", "percent", "message", "folder"}` for the background folder indexer (`tagpup.jobs.indexing`, one queue per library). Status values are `queued`, `running`, `completed`, `failed` or `cancelled`; a folder never indexed in this session reports `completed`, with the message `Ready`.
- `/api/faces/excluded`: Returns `{"faces": list, "total_count": int}` for every face marked as not-a-person, each carrying its `reason`, so exclusions can be reviewed and undone.
- `/api/browse-folder`: Invokes native folder dialog and returns selected path.

- `/api/tags/list`: Every word tag the library knows, with `count` (photos carrying it), `flat`, `in_taxonomy`, `has_embedding` and `is_person`. People are excluded unless `?people=1`, since the point of this view is the tags face curation could not reach. Also returns `buckets`: `flat`, `used_once` (where typos hide), `unused` (in the vocabulary, on no photo, yet still feeding zero-shot matching) and `people_without_a_path` (a person written as a bare leaf, which the keyword convention forbids).
- `/api/tags/photos?tag=`: The photos carrying one tag, newest first. Matches the whole tag, never a prefix.
- `/api/taxonomy/tree`: The tag tree's nodes, with each node's photo count and its `has_face` and `hidden_from_autocomplete`. The tree's six routes are TagPup's too, the same routes on both apps (`tagpup.web.taxonomy_routes`); the tag editor, which both pages open, is what calls them. docs/SPEC_TAGPUP_GUI.md says what each does.
- `/api/apps`: Returns `{"this": "tagpup" | "tuner", "apps": {"tagpup": url, "tuner": url}}` -- each app's page for the library the request names, on the host the page was reached by and the port the process serves that app on. The gear's link to the other app is read from it, so a page never spells a port. Both apps serve it alike.
- `/api/history?limit=<n>`: Returns the last `n` (default 20, at most 100) changes of the library the URL names, newest first, as the gear's History dialog lists them (`web/common/history-dialog.js`): `{"library": name, "retention_days": int, "changes": [{"id", "operation", "status", "created", "applied", "undone", "summary", "rows": {table: {action: count}}, "files": {state: count}, "undoable": bool, "why_not": text or null}]}`. Counts only, never a change's values, which can name people (`tagpup_cli.py history --reveal` shows them). `why_not` is why the undo would be refused out of hand -- a stamp of the library's first settings, not applied, pruned, made at another schema version, or a newer change not undone of the same rows or photo files -- the account the rehearsal refuses by (`tagpup.services.journal.refusals`, over `tagpup.store.journal.refusal`); the dialog shows it instead of Undo. A change of files is still offered when its files changed since; its rehearsal reads them. Both apps serve it alike (`tagpup.web.history_routes`).
- `/api/sync`: Returns when the library the URL names was last in step with its folders, and its last applied sync (`tagpup.services.sync.last`, over `sync_runs`): `{"library": name, "last_in_step": time or null, "last_run": null or {"id", "started", "finished", "whole", "in_step", "found": {what: count}, "changed": {"rows", "from_files", "relinked", "queued_folders"}, "change_id"}}`. `last_in_step` is when the newest sync of the whole library that left it in step finished. `syncing` (phase 9c) is whether this process's folder watcher is syncing the library at this moment (false when it runs no watcher); TagPup's library view says so beside "last in step". Counts and times only, never a path. Both apps serve it alike (`tagpup.web.sync_routes`).
- `/api/damaged-photos`: How many of the library's photos the indexer found damaged and not replaced since (`tagpup.services.damaged_photos.counts`): `{"library": name, "unreadable": int, "incomplete": int}` -- pictures that do not decode, which are not indexed and not written to, and possibly incomplete copies (they decode, and the file ends in 64 KiB or more of zero bytes), indexed and not written to. Counts only, never a path; the header's badge. Both apps serve it alike (`tagpup.web.sync_routes`).
- `/api/damaged-photos/check` (POST): Expects JSON body `{"paths": [path]}` (optional). Check again: reads the library's photos recorded damaged -- those of `paths`, or every one -- again now, decoding each whole picture whatever its stamp says (a good copy laid over a damaged file can keep its modified time and size), `tagpup.runtime.check_damaged`: one that reads whole is forgotten and indexed again for real -- its vectors taken away, and its faces unless one carries a decision -- its folder on this process's index queue; one still damaged stays listed. The folder watcher does the same for a photo it is told was written. Returns `{"success", "library", "checked", "whole", "still", "unreachable", "queued", "kept_faces"}`, counts only. Both apps serve it alike (`tagpup.web.sync_routes`).
- `/api/sync/review`: Returns the folders to review in the library the URL names (`tagpup.runtime.review`, over `tagpup.services.sync.review`): each folder under the library's root folders that holds photos and no indexed photo at any depth, and is not under an ignored folder, as the topmost such folder below a root, with how many photos it holds: `{"library": name, "count": int, "photos": int, "folders": [{"path", "photos"}]}`. Paths, for the page that asks, as Remove Folder's list gives them. One walk of the roots and no file read. Both apps serve it alike (`tagpup.web.sync_routes`).
- `/api/jobs`: Returns `{"library": name, "jobs": [{"name", "period", "reason", "per_library", "about", "last": {"started", "finished", "outcome", "changed": {count: int}} or null, "running": bool, "next_due": time}]}` -- each recurring job (`tagpup.jobs.recurring`: `snapshots`, daily, for `safety`; `prune-journal`, weekly, for `retention`; `sync`, daily, a `catch-up`; a job is scheduled only for what no event announces, and says which: `safety`, `retention` or `catch-up`), when it last ran for the library the URL names and how it ended (`running`, `done`, `failed`, `abandoned`), what it changed as counts, and when it is due next (local time, `YYYY-MM-DD HH:MM:SS`; now for a job never run). Counts only, never the note a failed run left. The runs are the library's own (`job_runs`), recorded by whichever TagPup process ran them. Both apps serve it alike (`tagpup.web.jobs_routes`).
- `/api/server`: Returns `{"version": name or null, "supervised": bool, "taking_work": bool, "requests": int, "busy": [what]}` -- which version of TagPup answers (the installed version's name, `YYYYMMDD-HHMMSS-<commit>`, or null for one run from its code folder), whether the always-on process started this server, whether it takes new work (false while it drains for an update), how many requests are in flight, and what runs beside them (Suggest runs, indexing, the background tasks). The gear shows the version below its items. Answered while the server drains, with a library in the URL or none. Both apps serve it alike (`tagpup.web.lifecycle`).
- `/api/server/drain`: Expects JSON body `{"seconds": number, "quiet": number}`. The always-on process's alone: the `X-TagPup-Supervisor` header must carry the token it gave the server, else 403 -- always 403 on a server it did not start. Refused at once, `{"success": true, "drained": false, "waiting_for": [what]}`, while a Suggest run, an index or a recurring job is under way, or a request came in the last `quiet` seconds (default 0; the supervisor asks for 120 until an update has waited an hour), nothing turned away. Otherwise the server takes no new work -- every other request is answered 503 with `Retry-After` and `X-TagPup-Updating: 1`, having done nothing, which the pages wait out and send again (`web/common/api.js`) -- stops its background tasks, and waits up to `seconds` (default 120, more than 0 and at most 1800; else 400) for the requests in flight to finish, a write among them: `{"success": true, "drained": true}`, after which the supervisor stops it; or, the time run out, it takes work again and answers `{"success": true, "drained": false, "waiting_for": [what]}`. A drained server not stopped within three minutes takes work again.
- `/api/server/resume`: The always-on process's alone, as the drain: take work again after a drain. Returns `{"success": true, "resumed": bool}` -- whether it had stopped taking work.
- `/api/settings`: Returns the settings of the library the URL names, as the settings dialog is made from them: `{"library": name, "stamped": bool, "exiftool_found": path, "groups": [{"name", "title", "locked", "consequences": [text], "settings": [{"key", "kind", "label", "type", "default", "info", "locked", "consequences", "value"}]}]}`. Each setting's declaration is `tagpup.core.validation.SETTINGS`'; `kind` is what `/api/rules` checks its value as; `exiftool_found` is the ExifTool the machine has, which an empty `paths.exiftool` runs. A library holding no settings is stamped first (from the home's config.ini if it has one, else with the defaults). Both apps serve it alike (`tagpup.web.settings_routes`).
- `/api/rules`: Returns `{"version": string, "kinds": {kind: {"rules": list}}}` -- what may be set, as data: every kind of input (`tag`, `name`, `caption`, `library name`, `grouping`, `folder`, `time shift`, `exclusion reason`, `rotate direction`, and `setting <section>.<key>` for each setting), each with its rules in order (patterns, forbidden text, lengths, ranges, choices) and the message each gives. The rules are `tagpup.core.validation`'s, which every service checks before it writes; `web/common/validate.js` fetches them once and the page checks against them before sending (docs/SPEC_TAGPUP_GUI.md). `version` changes whenever a rule does. The same for every library, so both apps serve it alike, and a page open on no library may ask it.

- `/api/activity/now`: The Activity page's "Now", which it asks every three seconds while it is in view: `{"at": time, "libraries": [{"name", "indexing": {"running": null or {"run", "started", "folders", "name", "percent", "message"}, "queued": [{"name", "folders"}]}, "suggesting": [{"library", "folder", "status", "completed", "total"}], "jobs": [{"job", "run_id", "started", "run"}]}], "syncing": null or {"library", "folder", "started"}, "watching": bool, "server": {"version", "taking_work", "requests", "busy"}, "supervisor": null or {...}}` -- for every library in the data folder, its index queue and Suggest runs in this process, and the recurring jobs a process is running for it now (their `running` rows in `job_runs`, whichever process runs them); the folder watcher's sync under way; whether the server takes work (false while it drains for an update); and what the always-on process says in `data/supervisor.json` (its state -- running, restarting, waiting for the ports, gave up -- why, since when, the version it runs and the one before, an update refused as not newer, crashes in its window, server starts, `alive`), less the token it gave its server. A folder is named by its last part. The Activity routes answer this PC only: a request from any other address is refused `403` (`tagpup.web.activity_routes`). Neither this nor the page's other reads count as somebody using the app: an update's quiet moment is not held back by an open Activity page. Both apps serve the Activity routes alike, under no library's address.
- `/api/activity/jobs?runs=<n>`: Each recurring job for each library: `{"runs_jobs": bool, "check_every": seconds, "running_here": [{"job", "library", "run_id", "started", "forced", "run"}], "libraries": [{"name", "jobs": [{"name", "period", "reason", "per_library", "about", "last", "running", "next_due", "failing": bool, "runs": [{"id", "started", "finished", "seconds", "outcome", "changed": {count: int}, "error": text or null, "run": tag}]}]}]}` -- as `/api/jobs` says each, with its last `n` runs (default 10, at most 50), newest first, and `failing` while its newest run that ended did not end `done`. `error` is the note a failed or abandoned run left, which can name a folder. `run` is the run's tag (`job:<library>:<id>`), which the lines it logged carry. `runs_jobs` says whether this server runs the recurring jobs (the always-on process does; one a test started or with `TAGPUP_NO_JOBS` does not).
- `/api/activity/sync`: Each library's syncs and watched folders: `{"watching": bool, "libraries": [{"name", "last_in_step", "last_whole", "last_folder", "review_folders", "review_url", "roots": [{"path", "there", "watched"}], "watches": [path], "watcher": null or {"last_event", "pending_folders", "whole_pending", "last_sync", "not_watched"}}]}` -- when it was last in step, its newest sync of the whole library and of one folder (`{"id", "started", "finished", "seconds", "whole", "in_step", "found", "changed", "change", "run"}`, counts), how many folders under its roots wait to be reviewed (as its newest whole sync counted them) with TagTuner's page on the library opening Folders to review (`?review=1`), its root folders -- each on disk or not, watched or not -- the folders the watcher watches for it, when a change in them was last noticed, and the watcher's last sync of it.
- `/api/activity/snapshots`: Each library's snapshots, read-only: `{"libraries": [{"name", "snapshots": [{"name", "kind", "taken", "age_seconds", "bytes"}], "bytes"}], "bytes"}` -- kind (daily, weekly, monthly, before-restore), when taken, age and size, and the disk they take, each library's and all together. Restoring one is the CLI's (`snapshots restore`).
- `/api/file-access/check?library=<name>&refresh=1`: Which other programs can interfere with TagPup's files, read-only, for the Activity page's File access section; this PC only (403 from any other address), under no library. Returns `{"checked_at": time, "cached": bool, "findings": [{"id", "level": "ok" | "info" | "warn", "title", "why", "what_to_do", "commands": [text], "places": [folder]}], "facts": {...}}` -- Microsoft Defender's real-time scanning and whether its exclusions cover the data folder and the roots' places (unreadable without administrator: then a warning with the commands for the owner to run), Defender scanning of network files, other registered antivirus products, Windows Search's scope and the not-indexed attribute, cloud-synced folders, and the scanning, sync and backup programs running. `library` limits the places to that library's roots (404 for one that is not there; every library's when none is named); `refresh=1` reads again instead of the answer remembered for ten minutes. Changes nothing: the commands are for the owner to run (`tagpup.services.file_access`).
- `/api/activity/server`: The always-on process: `{"version", "supervised", "taking_work", "busy", "running_since", "pid", "background": [task], "supervisor": null or {...}}` -- the version answering, since when this server runs, the background tasks it runs, and what the supervisor says (as `/api/activity/now`).
- `/api/activity/attention`: Needs attention: every library's photos found damaged and not replaced since (`tagpup.services.damaged_photos`): `{"unreadable": int, "incomplete": int, "libraries": [{"name", "faces_to_detect": int, "photos": [{"path", "name", "folder", "folder_url", "kind", "reason", "detail", "zero_tail", "indexed", "size", "mtime", "found", "seen", "run"}], "error"}]}` -- `kind` how it is damaged (`truncated`, `zero-filled`, `all zeros`, `empty`, `not an image`, `damaged`, or `incomplete`: it decodes, and ends in 64 KiB or more of zero bytes), `reason` what the owner is told, `detail` what the decoder said, `indexed` true for a possibly incomplete copy (indexed, and left as it is), `found` when the indexer first found it with this stamp, `run` the run of the indexer that did, and `folder_url` TagPup's page on its folder; `faces_to_detect`, how many of the library's photos, indexed from a damaged copy and whole now, still wait for their faces to be detected (`faces_pending`). Paths, to this PC only, as every Activity route.
- `/api/activity/attention/check` (POST): Expects JSON body `{"library": name, "path": path}` (each optional). Check again from Needs attention: as `/api/damaged-photos/check`, for `library` (every library without), only `path` when given: `{"success", "libraries": [{"name", "checked", "whole", "still", "unreachable", "queued", "kept_faces"}]}`. `404` for a library there is none of. To this PC only.
- `/api/activity/timeline?limit=<n>`: One timeline of what was done, newest first, at most `n` entries (default 50, at most 500): `{"limit", "more": bool, "entries": [{"kind": "change" | "job" | "sync" | "found" | "index" | "update", "library", "time", "finished", "seconds", "what", "outcome", "counts": {what: int}, "id", "run", "error"}]}` -- each library's journal changes (a sync's own change is its sync's entry), recurring job runs and syncs, the damaged photos its indexer found ("found 2 unreadable photos", one entry for each run that found some, with `run`, as they are recorded now), this process's runs of the indexer, and the always-on process's moves onto a new version (from the end of its log). Every run the Activity routes list -- here, in Now, a job's runs, a library's last syncs -- carries `logs`: the logs its lines are in, its own first (a run of the indexer's own file, then the server's; a sync or a job run, the server's, then the own file of each run of the indexer it queued, whose lines carry its tag too), named by `tagpup.logs` alone; the page reads a run's lines from the names it is sent and never spells one.
- `/api/activity/logs`: The logs in `data/logs`: `{"folder", "server": the web server's own log, "levels": [level], "logs": [{"name", "program", "source", "bytes", "modified", "rotated": [{"name", "bytes"}]}]}`, the most recently written first: the web server's (`tagpup_web.log`), its console output (`tagpup_web.console.log`), the always-on process's (`supervisor.log`), each run of an indexer (`indexer-<library>-<run>.log`, one file a run, the oldest beyond 30 removed as a run starts), the MCP server's, and any other TagPup wrote. Each rotates at 5 MB, keeping five (`<name>.1` ... `.5`); the console output is rotated when the server starts.
- `/api/activity/logs/<name>?limit=<n>&level=<level>&text=<text>&run=<tag>&before=<offset>&after=<offset>`: The newest `n` records (default 200, at most 1000) of the log `name` (a file in `data/logs`; `404` for any other name), newest first, each `{"time", "ms", "level", "thread", "logger", "runs": [tag], "message", "offset"}` -- a line and the lines under it (a traceback) are one record; a line the log's format did not write (the console's) is a record of its own, with `level` null -- at `level` or above (`DEBUG` ... `CRITICAL`; a line without a level always is), holding `text` in any case, and carrying the run's tag `run`. Read from the end, never more than 2 MB of it: `before`, the `start` a read answered, reads the records before it; `after`, the `end` a read answered, only those written since -- a file smaller than that has rotated, and is read from its end again (`rotated`). Returns `{"name", "bytes", "records", "start", "end", "looked_at", "complete", "rotated"}`; `400` for an unknown level or a `run` that is no run's tag.
- `/api/activity/logs/<name>/raw`: The end of the log `name` as written, at most 1 MB, from a line's start, as plain text.
- `/api/activity/logs/<name>/download`: The whole log `name` as a file to save, streamed from the disk.

### `POST` Endpoints

**What may be set.** A tag or a person's name being set is refused with `400` and a message saying why if it holds `|` or `\` (which other programs read as a break between levels), a control character such as a tab or a line break, or nothing at all. A tag also may not have an empty level (`A//B`, `A/`); a person's name is one level, so it may not hold `/`. The rules are the `tag` and `name` kinds of `tagpup/core/validation.py`, the same as TagPup's, which `/api/rules` publishes and the page asks before sending. A name is not one of TagTuner's lists (`Unknown Faces`, `Ungrouped`, `Excluded`, `Unmatched`), whatever the case.

- `/api/databases/create`: Expects JSON body `{"db_name": string}`. Creates a new empty database file, seeded with the default taxonomy categories.
- `/api/history/<int:change_id>/undo`: Expects JSON body `{"apply": bool}`. Without `apply`, rehearses undoing the change (`tagpup.services.journal.undo`): nothing is written, and the answer says what would be put back and what refused. With `apply`, undoes it: a change of rows only where the rehearsal restores every row exactly, a change of photo files file by file, each file no longer holding what the change left refused and named by photo id. Returns `{"success", "dry_run", "change", "attempted", "changed", "refused", "errors": [{"what", "why"}], "differences": [text], "would_put_back": int}` (`would_put_back`, of a rehearsal: the rows or files the undo would put back; `differences`: what it would not), with `error` when it is not a success; a refusal, with nothing written, is `400`. After an undo that changed something, TagPup's cached folder scans are let go. Both apps serve it alike (`tagpup.web.history_routes`).
- `/api/sync` (POST): Expects JSON body `{"apply": bool, "folder": path}`. Syncs the library the URL names with its folders -- its root folders (`library.roots`) and every folder it holds photos in -- or with `folder` (`tagpup.runtime.sync`, over `tagpup.services.sync`): each file compared with its row by path, size and modified time. Without `apply`, a dry run: what it found, and a rehearsal of the rows it would write; nothing is written, neither a row nor a file. A library behind this version's migrations is not migrated by a dry run: `behind` counts the migrations it lacks, and the rehearsal waits until an app has opened it. With `apply`, rows of files changed outside are read again from the files and rows of files that moved follow them (by name, size and modified time where that match is one-to-one, else by DocumentID; a match that is not one-to-one is counted as ambiguous, and the folders of its files are not queued), as one change of the journal (`sync`), undoable; the folders the library holds that hold new files go on this process's index queue, each without its subfolders (TagTuner's indexing panel shows them); a folder under a root holding no indexed photo is counted to review (`/api/sync/review`), never indexed on its own, and one under an ignored folder is passed over; a new file the indexer found does not decode, unchanged since, is passed over and counted (`unreadable_files`; `tagpup.services.damaged_photos`), so a folder holding nothing else new starts no indexer, and it does not keep the library out of step; missing files are counted, never removed; and the run is recorded (`GET /api/sync`) -- unless it was refused or its write failed, when nothing is queued or recorded; a record that could not be written is a `warnings` entry, not a failure of the write. Returns `{"success", "dry_run", "refused", "attempted", "changed", "changed_by_kind": {"from_files", "relinked"}, "queued", "in_step", "change", "counts": {"rows", "files", "folders_walked", "new", "new_folders", "review_folders", "review_photos", "ignored_files", "outside_roots_files", "changed", "never_stamped", "to_write", "fields", "unreadable", "moved", "moved_faces", "moved_named", "moved_changed", "occupied", "ambiguous_rows", "ambiguous_files", "held_back_folders", "unreadable_files", "missing", "missing_folders", "folders_gone", "roots_gone"}, "behind": int, "skipped": int, "warnings": [text], "errors": int}`, with `error` when it is not a success; counts only, never a path. A refusal, with nothing written, is `400`: for a `folder` that is not a full path (`tagpup.core.validation`'s `folder`), and for one the library holds no photo under that is under none of its root folders. After an apply that changed rows, TagPup's cached folder scans are let go. Both apps serve it alike (`tagpup.web.sync_routes`).
- `/api/sync/review/include` (POST): Expects JSON body `{"folder": path}`. Include: queues the folder, with its subfolders, on this process's index queue (`tagpup.runtime.include`); sync keeps it in step from then on. Returns `{"success": true, "queued": int}`; `400` for a folder that is not a full path, not on disk, or under none of the library's root folders. Both apps serve it alike.
- `/api/sync/review/ignore` (POST): Expects JSON body `{"folder": path}`. Ignore: adds the folder to the library's ignored folders (`library.ignored`, `tagpup.services.settings.ignore_folder`), a journaled change of its settings that History can undo; sync never offers it, or a folder under it, again. Returns `{"success": true, "changed": 0 or 1, "change": id or null}` (`changed` 0 when it was ignored already); `400` for a folder that is not a full path. Both apps serve it alike.
- `/api/settings` (POST): Expects JSON body `{"values": {key: value}, "acknowledged": [group]}`. Changes those settings as one journaled change (`change settings`), so it is in the library's history and can be undone (`tagpup_cli.py undo`). Refused with `400` and the validator's message, and nothing written, for a key that is no setting or a value that may not be set -- and for a change to a locked setting whose group (`clip`, `faces`, `exiftool`) `acknowledged` does not name, the message listing each such group and what changing it means (the lock is `tagpup.services.settings.change`'s, so it holds for every caller; the dialog names each group whose consequences were ticked). After a change the process lets go of the models and photo index the library no longer uses (`tagpup.runtime.Runtime.settings_changed`). Returns `{"success": true, "changed": int, "settings": [key], "locked": bool, "change": id}`: `changed` counts the settings whose value changed; `locked` says one of them was a locked one (the CLIP model, face detection, the ExifTool program), after which the page reloads.
- `/api/taxonomy/create`: Expects JSON body `{"name": string, "parent_id": int, "has_face": int}`. Adds a tag to the tree (docs/SPEC_TAGPUP_GUI.md).
- `/api/taxonomy/update`: Expects JSON body `{"id": int, "has_face": int, "hidden_from_autocomplete": int}`. Sets a node's flags, and its descendants'.
- `/api/taxonomy/delete-check`: Expects JSON body `{"tag_id": int}`. How many photos carry the tag or one under it.
- `/api/taxonomy/delete-confirm`: Expects JSON body `{"tag_id": int, "action": string, "target_tag": string}`. Takes the tag off its photos, or moves them to `target_tag`, then out of the tree. Clears TagPup's cached scans of the library whichever app served it.
- `/api/taxonomy/rename`: Expects JSON body `{"tag_id": int, "new_name": string}`. Renames a node, its descendants and the photos carrying them. Clears TagPup's cached scans of the library whichever app served it.
- `/api/face/match`: Expects JSON body `{"face_id": int, "person_name": string}`. An excluded face is refused with `409`: it must be restored before it can be named. `person_name` must be a name that may be set (above).
- `/api/face/unmatch`: Expects JSON body `{"face_id": int}`.
- `/api/faces/match-bulk`: Expects JSON body `{"face_ids": list, "person_name": string}`. Matches face IDs in bulk. Implements duplicate-tagging protection. Excluded faces are skipped, never named. Returns `{"success", "matched", "matched_ids", "skipped_excluded"}` — `matched` is the rows actually changed, and the page offers Undo for `matched_ids` only. `person_name` must be a name that may be set (above).
- `/api/faces/unmatch-bulk`: Expects JSON body `{"face_ids": list, "undo": bool}`. Unmatches face IDs in bulk, marking each as a person's decision that it is nobody (`name_source = 'manual'`). With `"undo": true` -- the page's Undo after an assignment -- they go back to unreviewed (`name_source` NULL) instead.
- `/api/roots`: TagTuner's Roots (`tagpup.web.tuner_routes`, over `tagpup.services.roots_location`; docs/ARCHITECTURE.md, "Roots and machines"). Returns each root of the library the URL names and where this machine keeps it: `{"library", "map": path of machine_roots.json, "problem": null or a sentence (the map cannot be read), "adopt_hint": the CLI command that adopts a root, "busy": [what runs or is queued for the library], "poll_ms": how often the page asks while a full Verify runs, "roots": [{"name", "address", "added", "places": [native folder], "active": the first place or null, "previous": the second or null, "writes_to": "Tags and renames are written to files at <active>." or null, "mapped": bool, "rows": photos under it or null, "shared_with": [names of the other libraries of the data folder that hold a root of this name: the machine's map is one for them all], "last_verify": null or {"when", "mode": "sample" | "all", "location", "outcome", "checked", "matches", "differs", "missing", "unreadable", "rows", "not_in_library", "partial"}, "verifying": null or the full Verify's progress {"root", "location", "state": "running" | "done" | "cancelled" | "failed", "checked", "rows", "folders", "cancelling", "started", "finished", "result": the Verify answer below or null, "error"}}]}`. The first place is where files are read and written; the others are where the root was, kept so every path is still recognised. A library with no roots answers `"roots": []`. Answers this PC only (`403` from any other address); never writes.
- `/api/roots/verify` (POST): Expects JSON body `{"root": name, "location": folder, "all": bool}`. Verify: how well `location` (default: the root's first place) holds the files the root's rows describe (`tagpup.services.roots_verify`), read-only -- no row, no file and not the map. Without `all`, a sample of 2,000 rows chosen to cover every folder, answered in the request: `{"success": true, "started": false, "verify": {"root", "location", "mode", "reachable", "state": "ok" | "away" | "no_drive" | "no_folder" | "unreadable", "message", "rows", "checked", "matches", "differs", "missing", "unreadable", "folders", "not_in_library": null (a sample does not walk the place), "other_rows", "outside": [{"group", "count"}] (the rows under no root, by folder), "outside_rows", "native_inside" (rows kept by this machine's own spelling where the place would be this root's), "not_converting", "partial", "stopped": null | "cancelled" | "unreachable" | "time", "poor", "poor_why": [sentence], "summary": sentence}}`: `matches` has the size and modified time the row recorded, `differs` is there and changed since (or a copy with other times; sync settles it, it is never `missing`), `missing` is not there. A place that cannot be reached -- a drive not connected, a share away, a folder not there -- is `reachable: false` with `message` ("This location cannot be reached: ..."), and nothing is counted as missing; each look at the disk waits 15 seconds at most, and a share that did not answer is taken as away for 30. With `all`, every row, and the photos at the place that no row has, as a job on this process (`tagpup.jobs.verifying`): `{"success": true, "started": true, "status": {...}}`, its progress in `GET /api/roots` (`verifying`) and its result kept in the library's `job_runs`, which the Activity page lists. `409` when a verify of the root is under way (here or in another process), `404` for a root the library does not have, `400` for a location that is not an absolute folder here.
- `/api/roots/verify-cancel` (POST): Expects JSON body `{"root": name}`. Stops the full Verify of the root between folders; what it counted is kept, as partial. Returns `{"success": true, "cancelled": bool}`.
- `/api/roots/change-location` (POST): Expects JSON body `{"root": name, "location": folder, "dry_run": bool, "override": bool, "from": folder}`. Moves the root to `location` on this machine (`tagpup.services.roots_location.change_location`, `tagpup.config.set_location`): the map's edit alone, never a row -- the new place first, the old kept after it, written whole and renamed over under the map's edit lock. A dry run unless `dry_run` is false: the same answer with `verify` (a sample of the new place, as above) and nothing written. Refused, nothing written, `400`, with `error` a sentence: a location that is not an absolute folder here, whose drive or folder is not there, over or inside another root of the library, or a poor result (more than 5% of the rows looked at missing, or the place cannot be reached) unless `override` is true; `409` while anything of the library is running or queued (an index run, Suggest, a sync, a verify, named in `error`; `busy` lists them), while another change of the root is under way, and when the map has changed since the page looked (`from` is the place it saw: another tab, a hand edit). Returns `{"success", "dry_run", "changed": 1 when the map was written and 0 when the root was at that place already (`unchanged`), "error", "root", "current", "places", "location", "writes_to", "verify", "message"}`. Each change is a run in `job_runs` ("moved root pictures to ... (was ...)"), which the Activity page lists. A running server finds the new map by itself (a connection within a second) and rebuilds the caches of this machine's paths.
- `/api/roots/change-back` (POST): Expects JSON body `{"root": name, "dry_run": bool, "override": bool, "from": folder}`. The reverse of the last change: the first two places swap. As `change-location`, to the root's previous place; refused, saying so, when the root has no previous place or that place is no longer there.
- `/api/folder/index-start`: Expects JSON body `{"folder_paths": [string], "cluster": bool (optional, default false)}`, or `{"folder_path": string}` for a single folder. Folders are **queued**, not run together: indexing is GPU-bound, so two at once do not go twice as fast so much as make each other crawl. A single worker drains the queue in order. Returns `{"queued": [], "already_queued": [], "invalid": [], "pending": int}`, so a folder that does not exist, or is already running or queued, is reported rather than silently dropped; the request is refused with `400` only when no requested path is a folder at all. A folder that fails is recorded against itself and the rest of the queue still runs. Clustering is opt-in because it re-derives every name in the database rather than only the folder being added. Adding goes through `tagpup.services.libraries.add`, as TagPup's Add does: each folder on disk is recorded as added (`added_folders`), so it is the library's at once; the response also carries `added`, the folders not added before.
- `/api/folder/index-cancel`: Expects JSON body `{"all": true}` or `{"folder_paths": [string]}`. Drops folders that have **not started yet** and returns `{"cancelled": [], "pending": int}`. The folder currently being indexed is deliberately left alone: it owns a subprocess partway through writing rows, and killing that is a different and riskier operation than forgetting something that has not begun.
- `/api/folder/remove`: Expects JSON body `{"folder_path": string}` (the page sends one `/api/folder/indexed` listed). Removes every indexed photo under that folder, and their faces, from the current database. Returns `{"photos_removed", "faces_removed", "manual_lost", "excluded_lost", "ignored"}` — `manual_lost` and `excluded_lost` report how much curated face work the removal discarded, since those rows go with the photos. What was added at or under the folder is forgotten; a folder a parent's add still covers (added with its subfolders, `tagpup.store.folders`) is put in the library's ignored folders (`library.ignored`, a journaled change of the settings, which History can undo), `ignored` true, and the page says so: otherwise it would stay the library's, and the next Suggest there would make it so again. **The photo files themselves are never deleted.**
- `/api/faces/exclude`: Expects JSON body `{"face_ids": list, "reason": string (optional)}` (a single `face_id` is also accepted). The reason is one of `not a person` (the default), `stranger`, `bad crop`, `duplicate` or `ignored cluster`, in any case; any other is refused with 400 and nothing is excluded. Marks faces as not-a-person. Any name they carried is cleared, and the person is dropped from the photo when no other face of theirs remains in it. Excluded faces take no part in clustering, match suggestions, automatch, or the Identify Faces queue. Returns `excluded` as the rows actually changed.
- `/api/faces/restore`: Expects JSON body `{"face_ids": list}`. Reverses an exclusion, returning the faces unnamed and unclaimed so they can be identified again. Only faces that are excluded are touched; returns `restored` as the rows actually changed.
- `/api/tags/merge`: Expects JSON body `{"from": string, "into": string, "apply": bool, "retire": bool}`. Renames a tag, and every tag under it, or merges it into another, across the photo files, `photos.tags`, `tag_taxonomy` and `tag_embeddings` (`tagpup.services.tags.merge`). In the tree, the tag's branch moves under the target and joins any nodes already there. `retire` drops the tag without a target and takes its branch out. **Defaults to a dry run** — without `apply` nothing is written and the plan comes back, reporting how many photos carry the tag or one under it, how many already carry the target, and how many embedding and taxonomy rows would go (the taxonomy count is the tag's branch). Taking the tag out of the tree is what stops it being suggested, because zero-shot candidates are the configured words and the tree's leaves. Its cached CLIP embedding is dropped too. A photo that could not be rewritten still carries the tag, so the tree keeps it and the reply carries `error`. TagPup's in-memory `suggest_status` lives in another process and is not refreshed by this. `into` must be a tag that may be set (above), may not be `from` or under it, and is written in its one spelling, each level trimmed; `from` is not checked, so a bad tag can always be merged away.
- `/api/person/rename`: Expects JSON body `{"old_name": string, "new_name": string}`. Renames a person everywhere (`tagpup.services.tags.rename_person`). That covers the faces named for them and each photo's list of people, where a name matches whatever its case. It covers every node holding faces filed under the name, and the photos carrying those tags. Where the new name already has a node, the two become one: the photos are rewritten first, and the old node goes once none carries it. `new_name` must be a name that may be set (above).
- `/api/photo/unmatch-all`: Expects JSON body `{"photo_path": string}`.
- `/api/photo/automatch`: Expects JSON body `{"photo_path": string}`. For each unmatched face in the photo, finds the closest resolved face in the DB. If similarity is 0.80 or more -- the value for naming a face with no one looking (`tagpup.core.clustering.NAME_WITHOUT_ASKING`) -- assigns the name and appends it to the photo's `people` array.
- `/api/folder/automatch`: Expects JSON body `{"folder_path": string}`. Automatches unmatched faces across all photos in the folder recursively.

Photo actions -- rotate, delete, open in Explorer, save metadata, bulk tags, time shift and
Smart Rename -- are TagPup's (`SPEC_TAGPUP_GUI.md`). TagTuner kept copies of their routes
that its page never called; they were removed in phase 2.

- `/api/activity/jobs/run`: Expects JSON body `{"job": string, "library": string}`. Runs the recurring job now for the library (`library` is ignored for a job not run per library), on this server's runner, on a thread of its own, forced whether or not it is due. Answered once the run is claimed or refused: `{"success": true, "started": true, "run_id", "run"}`; `{"success": false, "started": false, "why", "error"}` when a run of it is under way already, or the library is behind this version's migrations; `{"success": true, "started": null, "why"}` when it has not started within ten seconds. `404` for a job or library there is not, `409` from a server that runs no recurring jobs. The page asks first.

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
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)

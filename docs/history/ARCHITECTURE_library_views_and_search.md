# TagPup architecture, history: Phase 9: the derived tables

Moved verbatim from `docs/ARCHITECTURE.md` on 2026-10-09 (trunk 17afc1b), when that file became a map.
Nothing in the sections below was edited, so a number, a date or a branch name in them is as it was on
the day it was written: read it as a log, not as the current design (the map, [../ARCHITECTURE.md](../ARCHITECTURE.md),
and [../INVARIANTS.md](../INVARIANTS.md) are current). Index: [README.md](README.md).

Contents: Phase 9: the derived tables, the thumbnail cache, the windowed grid, the navigator, bulk edits by id, the owner's reviews, search, and the camera and lens words.

Sections in this file:

- Phase 9: Library views (planned for October 2026)
- Phase 9a-1: the derived tables the views stand on *(built 2026-10-02; migration 19; branch `arch/phase-9a-derived`)*
- Phase 9a-2: the thumbnail cache, the library views' query service and their routes *(built 2026-10-02; migration 20; branch `arch/phase-9a2-view`)*
- Phase 9b-1: the windowed grid, and the folder view on it *(built 2026-10-02; branch `arch/phase-9b1-grid`)*
- Phase 9b-2: a library source for the grid *(built 2026-10-03; branch `arch/phase-9b2-library`)*
- Phase 9c: the navigator, the move between a folder and its view, stale marks, the grid's keys *(built 2026-10-03; branch `arch/phase-9c-navigator`)*
- Phase 9d-1: bulk edits by photo id, as a job; a selection's tally *(built 2026-10-03; branch `arch/phase-9d1-bulk-jobs`)*
- Phase 9d-2: editing across folders -- the selection by id, bulk edits through the job, the strip, the tally *(built 2026-10-03; branch `arch/phase-9d2-bulk-page`)*
- Phase 9, the owner's review: several rows at once, People by branch, one sort *(built 2026-10-04; branch `arch/library-review-2`; findings #671-#673)*
- The owner's first review of the library views *(2026-10-04; #668-#670, #674, #675 on `arch/library-review-1`; #671-#673 beside it)*
- The owner's second review of the library views *(2026-10-04; #712-#714 on `arch/library-review-3`)*
- Phase 9e-1: search on the server *(built 2026-10-04; migration 24; branch `arch/phase-9e1-search`)*
- Phase 9e-2: the search page *(built 2026-10-04; branch `arch/phase-9e2-search-page`)*
- The library view's selection details navigate, and a photo has a way back *(owner, 2026-10-04; built in wave 3A, #780, #781)*
- Camera and lens words *(built 2026-10-09; migration 27)*
- Backlog: which photo fields are searchable *(owner, 2026-10-04; after phase 9)*

---

### Phase 9: Library views (planned for October 2026)
The owner's idea *(2026-09-25)*: TagPup shows the whole library, not only the folder it
has open -- by folder, by keyword, by person, by date -- as Windows Live Photo Gallery
did, from the database, with the editing TagPup gives a folder today. It follows phase 8:
a view of the rows is only as good as the rows are current.

The design, to be settled before it starts:
- **One grid, two kinds of source.** A source says which photos are shown: a folder on
  disk (today's view: it walks the folder and reads what is new or changed), or a
  library query -- a folder and its subfolders, a keyword and everything under it, a
  person, a year or month (`photos.taken`). The grid, the details panel, the selection
  and the bulk edits are the same components on photo ids; an edit does the same
  whichever source showed the photo. Two grids drifting apart is the failure to avoid.
- **The transition.** A navigator beside the grid: Folders (the library's tree, counts,
  each marked on disk or gone), Keywords (the tag tree with counts), People, Dates. A
  library folder opens its library view; where the disk holds files the library does
  not, a banner says so and offers to index them. "Show in library" goes from a disk
  view to the same folder's library view. The header always says which source is shown,
  and the URL names it, so Back and a bookmark work.
- **Staleness is shown, not hidden.** A library view checks the thumbnails on screen
  cheaply (size and modified time, no ExifTool) and marks a photo changed on disk or
  missing; a missing photo is shown and not editable.
- **Edits and sync.** Every edit writes the file and records its new size and modified
  time in the transaction that marks the file written (phase 7.5), so sync (phase 8)
  never takes our own edit for an outside one. A file changed outside while an edit is
  planned fails the edit's precondition: a conflict, reported for sync to settle per
  file, never overwritten. The views show sync's state ("last in step: ...").
- **What it needs underneath.** Keywords are JSON in `photos.tags`, so "everything under
  Trips/" reads every row: a derived `photo_tags(photo_id, tag_id)` table, by the tree node's id,
  indexed and kept by the writes as `photo_people` is. Folder counts scan the same way
  (findings #168): derived `folders` and `photo_folder` tables and an index-friendly path range,
  both built (see "Phase 9a-1" below). Browsing
  thousands of photos needs cached thumbnails (derived, keyed by photo and modified
  time) and a grid that renders only what is on screen -- the Identify Faces work showed
  what rebuilding tens of thousands of cards costs.

The owner's answers *(2026-09-26)*:
- **The tag pane is there already**: the folder view's details panel and bulk tags are
  what Photo Gallery's tag pane was used for. A library view gets the same panel.
- **What is missing is the left navigation over the whole library**: by date (year, then
  month) and by tag (the tag tree), as Photo Gallery's navigation pane did.
- **Search, as Photo Gallery's**, with results shown as a folder view is. Photo Gallery
  searched file name, tags, caption, author and camera for the words typed, within what
  the navigation pane had selected; tags picked in the pane with Ctrl were OR (any of
  them), the words in the search box were AND (all of them); it had no way to leave a
  tag out ([Find your photos 2](https://ludwigkeck.wordpress.com/2009/09/21/find-your-photos-2-%E2%80%93-windows-live-photo-gallery/)).
  2011's Find tab filtered by people, descriptive tags, date, place, folder, rating or
  flag, in any combination. The owner wants more than that: three family members and
  not a fourth -- *all of*, *any of* and *none of*, over tags and people, with text.
- **Albums are folders**, here and in phase 10: no album entity.
- **The thumbnail cache is not bounded.**
- **Libraries are always distinct**: a view shows one library.

**Where the code stands** *(2026-09-26)*: the grid (`web/tagpup/grid.js`, `folder.js`)
builds a card for every photo of the folder it has open, keyed by path; nothing renders
only what is on screen. A thumbnail is made on every request (`files.images.smaller_copy`,
Pillow, `size=300`), never kept. Keywords are JSON in `photos.tags`; there is no
`photo_tags` table. Folders under a folder are an index range now (#168); Remove Folder
already lists the library's folders with counts (#47). `photos.taken` and `photos.year`
exist. The journal, History and sync (phase 8) are what a library view's edits and
staleness stand on.

**Stages**, each reviewed and merged on its own; the performance ones measured as the
click in a real browser on a sandbox copy (CLAUDE.md, "Performance work"):
- **9a. What views stand on (server only).** In two. **9a-1, built** *(2026-10-02, migration 19; see
  "Phase 9a-1" below)*: the derived tables `photo_tags(photo_id, tag_id)` *(owner, 2026-10-02: identity by id,
  not by name; see "Identity by id" below)*, `folders` and `photo_folder`, and `photo_meta`, kept by the writes
  that keep `photo_people` and rebuilt by the migration and the doctor; "a keyword and everything under it" is
  the node's descendants, a range on `tag_taxonomy.tag` (unique, indexed) that gives ids, joined to
  `photo_tags`. **9a-2, built** *(2026-10-02, migration 20; see "Phase 9a-2" below)*: A
  thumbnail cache on disk: derived, keyed by photo id and the file's size and modified
  time, under `data/cache/<library>/thumbs`, made when a photo is indexed or its file
  changes (queued, phase 8's events) and on first ask, dropped when its stamp changes or it
  leaves the library -- never by a schedule; not bounded
  *(owner)*: about 20-40 KB a 300 px thumbnail, 1.5-3 GB for photo_index, measured. One query
  service, `tagpup.services.library_view`: a source (folder and subfolders, keyword and
  everything under it, person, year or month) to an ordered page of photo ids and the
  total; and the navigator's counts (folder tree, tag tree, people, years and months),
  each one query. Routes and specs; EXPLAIN QUERY PLAN on photo_index for each.
- **9b. One grid on photo ids.** *(9b-1, built 2026-10-02, not merged: the windowed grid and the folder view on it, see
  "Phase 9b-1" below; 9b-2, a library source fed by the order of a view and the cards near the window, built 2026-10-03, see
  "Phase 9b-2" below.)* The grid, the details panel, the selection and the bulk
  edits take a source and work on photo ids; today's folder view becomes the "folder on
  disk" source through the same components. Only the cards on screen (and a screen
  either side) exist in the DOM. Exit: a 20,000-photo keyword scrolls without a stall,
  measured; the folder view's behaviour and tests unchanged.
- **9c. The navigator and the move between views.** *(Built 2026-10-03; see "Phase 9c" below.)* Folders, Keywords, People and Dates
  beside the grid, with counts; the header and the URL name the source, so Back and a
  bookmark work; "Show in library" from a disk folder; a banner where the disk holds
  files the library does not (offering to index them, through sync); staleness marks on
  the cards on screen (size and modified time, no ExifTool) and a missing photo shown
  but not editable; "last in step" from sync.
- **9d. Editing from a library view.** *(9d-1, the server's half -- bulk edits by photo id as a job, a selection's tally -- built 2026-10-03, see "Phase 9d-1" below; 9d-2, the page: the selection by id, the edits through the job, the strip and the tally, built 2026-10-03, see "Phase 9d-2" below.)* Bulk edits on a selection that spans folders,
  through the same journaled writes (History lists and undoes them); a file changed
  outside while an edit is planned is a conflict for sync to settle, never overwritten.
- **9e. Search.** *(9e-1, the server's half -- a search as a source, migration 24's word index -- built 2026-10-04, see "Phase
  9e-1" below; 9e-2, the page -- the Library pane's search box, the picker, a search as a view -- built 2026-10-04, see "Phase
  9e-2" below.)* A search is a source: *all of* these tags or people, *any of* those,
  *none of* these, and words matched against file name, tags, captions and people (as
  Photo Gallery's search box did), optionally within the folder, tag or date the
  navigator has selected. Built on `photo_tags` and `photo_people` as set operations in
  one query; its results open in the same grid, panel and bulk edits as a folder, and
  the URL holds the search so it can be bookmarked or opened again. A picker that
  completes tag and person names (the tag editor's vocabulary) builds the three lists.

Exit: the owner can open the whole library by folder, keyword, person or date, move
between a disk folder and its library view without losing place, and edit from either,
with every edit undoable and nothing overwritten that changed outside.

### Phase 9a-1: the derived tables the views stand on *(built 2026-10-02; migration 19; branch `arch/phase-9a-derived`)*
Server and store only: no page, no route, nothing the owner sees until 9a-2 reads them. One additive
migration makes four tables from the photos' own rows, and `tagpup.store.derived` owns them (docs/DATABASE.md,
tables 21 to 24):
- **`photo_tags(photo_id, tag_id)`**, `tag_id` the `tag_taxonomy` node's id *(owner: identity by id)*, indexed on
  (`tag_id`, `photo_id`). A keyword is matched to the node whose tag it is, else the node it is without case, with
  its segments trimmed and `|` and `\` read as `/` (`vocabulary.normalize`), the lowest id when two nodes differ only
  in case (none do on photo_index). **A keyword with no node gets no row and no node is made**: the owner's tree
  does not change by indexing a photo. On photo_index that is 4 distinct keywords on 32 uses (counted 2026-10-02:
  151,425 uses on 876 keywords, 872 of them a node's exact tag, none differing only in case or spacing); the doctor
  lists them by count and, with `--show`, by keyword (`photo tags with no tree node`), reported and not broken. A bare
  leaf is such a keyword: it is the people rule's to resolve, not the tree's. "A keyword and everything under it" is
  two seeks of the tree's unique index (`tag = ?` and `tag >= 'tag/' AND tag < 'tag0'`, never LIKE) giving ids, joined
  to `photo_tags` by `tag_id` (`derived.under`, `photos_under_tag`, `count_under_tag`). A node renamed or moved keeps its
  id, so its rows are unchanged and its range follows its new path -- the photos' keyword text is rewritten in the files
  by TagTuner, one transaction after the tree's, and until each is, a photo's old text names no node and is reported; a
  node deleted takes its rows by a trigger and the keyword is reported, not made again.
- **`folders(id, parent_id, path, name)` and `photo_folder(photo_id, folder_id)`**, derived from `photos.path`: the
  folder tree and the folder each photo is directly in. There is **no `folder_id` on `photos`**: it would have been a
  second migration of every photo row beside the roots one, and a join through `photo_folder` costs one seek. `path`
  is a path column like the others: the folder in ROW form (`@pictures/2024/Coast` in a library that holds a root,
  native in one that holds none), unique, compared without case where the filesystem is, `from_row`ed on the way out
  (`derived.folder_tree`). A folder has a row for every ancestor of a photo's folder up to the top of its spelling --
  the root of a rooted path, the drive or share of a native one (photo_index would have 2,746 folders: its 2,674 and
  72 above them, in 3 trees: `@pictures` and two native tops) -- so the navigator can draw the tree. **A folder
  emptied goes**: its row, and the ancestors only it held, are pruned in the transaction that emptied it
  (`derived.prune`, called by `move_rows_in`, `forget_photo`, `remove`, `remove_under`); a delete that did not come
  through the store leaves them, which the doctor reports and does not count as broken (`folders holding no photo`).
  **The roots' adoption and its undo rebuild the folders** in their own transaction (`derived.rebuild_folders`), verified
  before it commits, with a step (`folders rebuilt`) the tests stop the process at; an adopted library's folders are those
  of an unconverted twin, by `from_row`, except that the root is a top (`tests/test_derived_follow_writes.py`). A folder's
  id is kept while the folder is (AUTOINCREMENT, never reused) and **not kept across an adoption or its undo**, which
  change every path's spelling: a page names a folder by its path. Per-folder counts: the photos directly in a folder are
  one GROUP BY of `photo_folder`, those in it and below that rolled up the tree in Python (`derived.folder_tree`, 7 ms for
  2,700 folders), or one range of `photos.path` (`derived.recursive_count`, which agrees with the roll-up).
- **`photo_meta(photo_id, rating, make, model, width, height, latitude, longitude)`**, one row for every photo, from
  `photos.raw_metadata` by one pure function (`tagpup.core.photo_meta`) written after counting the spellings on photo_index:
  `XMP:Rating` over `EXIF:Rating` (never disagree), an integer from -1 (rejected) to 5; `EXIF:Make`/`Model` over `XMP:`;
  the signed `Composite:GPSLatitude`/`Longitude` (the EXIF pair has no sign), both or neither, in range, and not the pair
  0, 0 that a camera without a fix writes (83 latitudes and 107 longitudes on photo_index are exactly 0). **`width` and
  `height` are empty in every row: no row of photo_index holds `ImageWidth`, `ImageHeight`, `ExifImageWidth` or
  `Orientation`**, since the indexer does not ask ExifTool for them (`fields.METADATA_FIELDS`). The extraction reads them
  where a row has them (the picture's own size, then the one EXIF declares, swapped for Orientation 5 to 8 so they are as
  shown). Asking for them changes what every read records, makes every row of the library differ from a fresh read, and is
  **the owner's decision, not made here**: until then a size filter has nothing to filter. photo_index would fill 58,551
  ratings (most 0), 65,011 makes, 63,020 models and 1,485 places.

**Kept by the writes, in their transactions** (the lesson of the bulk tag writes that once did not tell the index): the
index's `record_indexed` (the batch shares one `derived.Batch`, the tree read inside its transaction, so a record costs 0.103 s
for 500 photos where it cost 0.080 s), `record_tags`, `record_saved`, `follow_fields` (so a change of files and its undo),
`move_rows_in` (one refresh for all the photos it moves) and `ensure_row`; the tree's edits (`people.tree_edit` reads every photo's keywords once, as
`people.follow_tree` does, to find those a changed node names: 0.31 s on photo_index warm, measured read-only, under the
write lock of an edit that adds, moves or takes away a node, and nothing for a flag); the journal's `_derive` after an apply, an undo or a settle,
and its rehearsal, which compares the derived rows too. A photo deleted, or a node, takes its rows by trigger on any
connection. They are `journal.DERIVED` and `schema.UNWATCHED`: never journaled, rebuilt, and a migration that makes or
fills one no longer blocks the undo of an older change (`journal.schema_gap_blocker` asks only of `journal.KEYS`).
`tests/test_derived_writers.py` fails the build on a function of `tagpup/` that writes a photo's tags, path or
raw_metadata, inserts or deletes a photo, or writes the tag tree outside `people.tree_edit`, without going through
`derived`.

**Checks and repair.** `photo_tags_out_of_date`, `photo_folders_out_of_date`, `folders_out_of_date` (the parent chain complete,
every folder a photo needs there) and `photo_meta_out_of_date` are in `checks.RULES`, so the doctor and the MCP pick them up;
`tools/doctor.py --rebuild-derived` is a dry run and `--apply` one write that makes the four tables what the photos say and
verifies it, touching no photo, tag or file (so no backup). Migration 19 itself checks, before it commits, that what it made
is what a read of the photos finds (`derived tables agree with the photos`). A version of the app from before 19 still writing
photos to a library at 19 leaves the rows stale; the doctor says so and the repair is the one above.

**Measured** on a synthetic library of photo_index's scale (68,466 photos, 157,000 keyword uses, 2,674 folders, 889 nodes; rows
as the indexer stores them): migration 19 takes 3.6 s under the write lock, of which 1.6 s is its own check; the plans, with
no SCAN of the photos' or the tree's tables (checked on photo_index, read-only: a folder's range `SEARCH photos USING COVERING
INDEX idx_photos_path_nocase`, photos by id `SEARCH ... INTEGER PRIMARY KEY`; the one-per-run reads of every photo, a tree edit's
and the doctor's, are a `SCAN photos` by design): a tag's ids `SEARCH tag_taxonomy USING COVERING INDEX ... (tag=?)` and
`(tag>? AND tag<?)`; its photos `SEARCH photo_tags USING COVERING INDEX idx_photo_tags_tag (tag_id=?)` and a temp b-tree for the
order; the direct counts `SCAN photo_folder USING COVERING INDEX idx_photo_folder_folder` (a scan of the index, 4 ms); a folder
by path `SEARCH folders USING COVERING INDEX ... (path=?)`. A filter on rating or camera scans `photo_meta` (2.5 ms); an index is
9e's if a measured click needs it.

**What 9a-2 uses.** `derived.photos_under_tag(conn, tag, limit, offset)` and `count_under_tag` for a keyword source;
`derived.folder_tree(conn)` for the navigator's Folders (native paths, counts direct and recursive, one query) and
`photo_folder` (`folder_id` -> photo ids) or the `photos.path` range for a folder's photos; `photo_people` for a person; `photos.taken`
and `photos.year` for dates; `photo_meta` for the rest. The navigator's Keywords counts are a roll-up over `photo_tags` (151,393
rows on photo_index) by the tree's parents, one query and a pass in Python, as the folders' are. The thumbnail cache is keyed
by the photo's id and stamp, as before. `folders.path` is row form: ask for a folder by path through `store.roots.sql_equals`-style
conversion, never by an id kept in a page.

### Phase 9a-2: the thumbnail cache, the library views' query service and their routes *(built 2026-10-02; migration 20; branch `arch/phase-9a2-view`)*
Server only: no page, nothing the owner sees until 9b. Three things, each in its layer.

- **The thumbnail cache** (`tagpup.files.thumbs`, the files; `tagpup.services.thumbnails`, what an entry is and when it
  goes). DERIVED and not bounded *(owner)*: `<library folder>/cache/<library>/thumbs` (`Library.thumbs`; a test home holds its
  own), a 300 px JPEG for each photo, `<id // 1000>/<id>_<path hash>_<mtime ms>_<size>.jpg`. The key is **the photo's id, eight
  hex digits of the path its ROW holds, and the stamp of the file it was made from**, the stamp read from the file at each
  ask: a file changed is a new entry and the old is deleted as the new is made; a photo renamed keeps its id and gets a new
  hash, made once; an id handed out again after a restore never serves another photo's picture (its path hash differs);
  **a library moved to another root place keeps its entries**, since the rows' paths and ids are unchanged (tested through
  the roots' map). Two libraries on one machine have two folders, so the same ids never meet. **Made on first ask** (`serve`)
  and ahead of time by `tagpup_cli.py thumbs warm [--folder F] [--limit N] [--apply]` (a dry run: counts, the bytes the missing
  ones would take from the average entry kept, writes not even the folder; `--apply` makes them four at a time, can be stopped
  and run again, brings a library behind up to date first and deletes entries of photos the library no longer holds). **Not
  made when a photo is indexed or changed** -- a queue for that was in the first design and is the owner's to ask for: every
  entry is made by the first ask or the warm command. **Atomic**: a temporary file in the shard, `os.replace`d over the name
  (a rename that meets a reader on Windows leaves the entry that is there); two requests for one photo make it once (a lock
  for each of 64 stripes, the entry looked for again inside it); another process making it too -- `warm` beside the
  server -- writes the same whole file. A crash leaves a `.tmp-` file, taken when it is an hour old (`sweep`, `warm --apply`).
  **Taken away by the code that takes the photo away, not by a schedule**: `photos.delete` (its id read before the row goes),
  `faces.remove_folder` (the ids under the folder, likewise) and the indexer's `PhotoIndex.remove_paths` (`sweep`: every entry of
  a photo the library does not hold, or holds at another path); `index --reset` clears the library's whole cache. A restore from a
  snapshot, a journal undo of inserted photos (an undone index run or sync), or a delete outside the services leaves entries
  nothing will serve, which `warm --apply` sweeps.
  **A damaged photo** -- a `damaged_files` record whose stamp still describes the file, and not a possibly incomplete copy,
  which decodes -- is answered a placeholder (`X-TagPup-Thumb: damaged`) **without its file being decoded**; one that does not
  decode though nothing says so is answered the same and remembered in the process while its stamp holds (a damaged file is
  not decoded for each of the thousand cards that ask), and **nothing is written to the library for it**: recording it is the
  indexer's finding, and a false one refuses writes to a good photo. Nothing is cached for either. **A cache that cannot be
  written** (the folder missing and not makeable, read-only, full) costs only the cache: the thumbnail is made and answered
  with no ETag, and the first time is logged. **A share that is away** shares the folder listings' memory (`tagpup.files.shares`): the first look waits one second, the share is then taken as away for 30 s and answered at once,
  with no second thread while one hangs, so 24 cards on it cost one wait (a 503 with `Retry-After` and "the network share is away"); `thumbs warm` asks a share once and leaves its photos, and says so.
  **A file that is there and cannot be read** (permissions, a lock) is a different answer from one that is gone (`damaged_photos.stamp_of`: None, `CANNOT_READ`, `UNANSWERED`): a 503 "cannot be read", never "not there". The older `_stamp` keeps
  its None for both, so for the other callers nothing changed: `prune` and `forget_to_reindex` treat it as gone, `check_again` counts it unreachable, and `for_write` does not find an unreadable file damaged -- the write meets it itself.
  Either is `Unavailable` and nothing is decided about the photo. **A cache that cannot be written ends `warm --apply` at the first one** (exit 1) before more is decoded.
  `page_copy` and `/api/photo-file?size=` are unchanged: the folder view still makes its thumbnails per request until 9b.
- **`tagpup.services.library_view`** over **`tagpup.store.library_view`** (the SQL; `tagpup.services` holds none). A source is
  `all`, `folder` (by its PATH, never an id; `recursive` takes its subfolders), `keyword` (the tag as the tree spells it, else the node it is without case -- an exact match wins, none is empty --,
  read as a keyword is: `|` and `\` as `/`, trimmed; and everything under it), `person` (the leaf, compared without case),
  `year` (`photos.year`) or `month` (`YYYY-MM`, a range of `photos.taken`). `view` returns `{source, total, ids, next, limit,
  cards}`: **ordered by Date Taken then id, photos with no date after them by id, by a KEYSET** -- the token is the (phase,
  taken, id) of the last photo, opaque, checked when read (too long, not base64, not the shape, a part out of range: `400`),
  never an offset, so a photo added, taken away or re-dated between two pages neither repeats nor skips another beyond what
  that change itself explains (each is a test). `limit` is 200, at most 500, `400` below 1. A **card** is `{id, name, path
  (native), taken, damaged, damage, thumb}`, one read in batches of 500 of the columns it needs -- no `raw_metadata`, no
  BLOB, no query for each photo, no look at the disk (a test counts the statements for 3 and for 62 photos: equal) -- and
  `thumb` is `/api/photo-thumb?id=<id>&v=<the row's mtime>`. `damaged` is the record describing the row's stamp. A keyword's
  photos are `p.id IN (photo_tags of the nodes under it)`, so a photo holding two tags under the keyword is one row (the
  `EXISTS` form scans the date index and is 2.5 times slower; a `DISTINCT` join 46% slower). A source with nothing is an empty page,
  not an error. The navigator, `navigator(library, section)`: `folders` (`derived.folder_tree`: direct and recursive counts,
  by native path with `parent` a path), `keywords` (every node with the photos at or under it, each once per node: one pass of
  `photo_tags` by photo, `group_concat`ed and rolled up the parent ids in Python, **equal node for node to `count_under_tag`**,
  a test), `people` (`photo_people` by name, names differing only in case one entry, a photo counted once), `dates`
  (`{years: [{year, count, months: [{month, count}], other}], undated}`: by `photos.year` and, within it, `taken`; `other` the
  photos of the year whose `taken` is no month of it -- photo_index has 5 written with dashes, and 1,180 with no date, whose
  years come from their names). Every function reads through a read-only connection and migrates nothing.
- **Routes** (`tagpup.web.tagpup_routes`, loopback only -- `403` from any other address -- through the Roots gate and ingress,
  specified in SPEC_TAGPUP_GUI): `GET /api/library/navigator?section=`, `GET /api/library/view?kind=&value=&folder=&recursive=&after=&limit=`
  (a folder is named by `folder`, which the ingress resolves through the first place of its root, as any path of a request; the
  other kinds by `value`) and `GET /api/photo-thumb?id=&v=`. The thumbnail's URL carries the stamp: when `v` is the file's modified
  time the answer is `private, max-age=31536000, immutable`, with an `ETag` (the entry's name); otherwise `no-cache`, revalidated by
  that ETag (`If-None-Match` is a `304` with no body); a placeholder or a last-known picture is `no-cache` with no ETag. An id with no
  photo is `404` with a sentence; an id SQLite cannot hold is the same (it was a 500 until a test found it). A library not at
  migrations 19 and 20 -- the app brings a library up to date as it opens it, so only one that could not be -- is `409` and a
  sentence naming it and what to do; an unplaced root is the gate's `409` with `X-TagPup-Roots-Problem` before a route runs, and
  `paths.RootsError` from a service called any other way.
- **Migration 20** (additive, indexes only, touches no table, blocks no undo of an older change): `idx_photos_taken (taken, id)` and
  `idx_photos_year (year, taken, id)`. Without them "all photos" is `SCAN p` and a sort of 68,466 rows (checked read-only on
  photo_index's plan), and a month and a year are as bad. It is written to a library when the app opens it next, as every migration
  is -- **photo_index is not migrated by this branch**; `tests/test_taken_indexes.py` holds that nothing but the two indexes changes.

**Measured** on a SYNTHETIC library of photo_index's scale, made by the production writers (`record_indexed` with a shared
`derived.Batch`, `taxonomy.add_path`; 68,466 photos in 2,699 folders -- one of 20,000 --, 143,851 `photo_tags` rows on 700 nodes, 95,721
`photo_people` rows for 400 people, 810 undated; photo_index itself has 151,425 keyword uses on 876 keywords, 895 nodes, 80,053 people
rows for 413 names, 1,180 undated, counted 2026-10-02; the synthetic one is a little lighter). Best of several, warm, a library opened
for each call (about 12 ms of each is opening the library and SQLite reading its schema):

| A page of 500 with its cards | first page | page 100 of 137 / the last |
|---|---|---|
| all | 12 ms | 14 ms |
| a folder of 20,000, alone / with subfolders | 23 / 26 ms | 24 / 29 ms |
| a folder holding no photo | 14 ms | |
| keyword, 3,970 photos / 54,634 photos | 26 / 125 ms | 29 / 100 ms |
| a person (232) / a year (2,196) / a month | 19 / 9 / 6 ms | |

The statements alone are 0.2 to 9 ms except the 54,634-photo keyword (73 ms: the sort of its ids). The navigator: folders 19 ms (2,699),
keywords 138 to 179 ms (700 nodes, 68,466 photos: the pass in Python is most of it), people 12 ms, dates 33 ms. Plans (the exact statements,
`tests/test_library_view_plans.py` asserts them for each source on a library made the same way): all, a year, a month `SEARCH ... COVERING INDEX
idx_photos_taken` / `idx_photos_year` with no sort; a folder with subfolders `SEARCH p USING INDEX idx_photos_path_nocase (path>? AND path<?)`
and a temp b-tree for the order (checked on photo_index itself, read-only: the same lines); a folder alone `SEARCH folders ... (path=?)`, `SEARCH pf USING
COVERING INDEX idx_photo_folder_folder (folder_id=?)`, `SEARCH p USING INTEGER PRIMARY KEY`; a keyword `SEARCH tag_taxonomy` twice (`tag=?`, `tag>? AND tag<?`),
`SEARCH photo_tags USING COVERING INDEX idx_photo_tags_tag (tag_id=?)`, `SEARCH p USING INTEGER PRIMARY KEY`; a person `SEARCH photo_people USING INDEX
idx_photo_people_name (name=?)`; the cards `SEARCH photos USING INTEGER PRIMARY KEY`. The only scans are of a covering index: the totals of all
(`SCAN photos USING COVERING INDEX`, 2.4 ms), the person's spellings (400 names), and the navigator's passes. **Thumbnails**, 500 photos of 3000 x 2000 and 2.9 MB
(files on a local disk): first ask 47 ms each (23.7 s for 500; the per-request path the folder view still uses is 36 ms each), a cached ask 4.4 to
5.1 ms each (2.5 s for 500, nearly all of it SQLite opening a connection to find the row and the path), a revalidation `304` 5.2 ms, `warm --apply`
four at a time 14 ms each (7.1 s), a dry run when everything is cached 0.09 ms a photo. My test pictures are noise and made 6.3 KB entries; the owner's
estimate is 20 to 40 KB.

**How it fails**, each a test: interrupted part-way (a write that fails after its temporary file leaves no entry and no temp, an old temp is swept);
two at once (two requests make one entry; six writers leave one whole file); a read that fails (a file unreachable is a 503, a damaged one a
placeholder, a cache that cannot be written still answers, a library behind or an unplaced root is a sentence); a library moved to another root place; two
libraries holding one folder or the same ids; keyset pages while photos are added, deleted, re-dated, and while another thread writes; dates in the
future, before 1970, with a zone, with no day; ties; `%`, `_` and a quote in a folder, a keyword and a name; a forged, huge or corrupt token; a limit of
0, below 0, not a number, or huge.

**What 9b must know.** A card's `thumb` is for `api.image()`, which puts the library in front. `next` is the only way on (opaque; a `null` is the end) and
`total` comes with every page; ask 200 or fewer for a scrolling grid -- a page of 500 cards is about 150 KB of JSON. A photo is named by `id` in
the view and by `path` (native) in every write route today; the card carries both; identity by id for the writes is not built. A rotate changes the file's
stamp: refetch the card (its `thumb` has a new `v`) or the picture is the browser's old one for a year. A folder is named in the navigator by its `path`,
which is what `view` takes, never an id; a keyword by its `tag`, a person by `name`, a year by `year` and a month by `month` ("2024-06"), as `dates` gives
them. A month and a year do not always sum: `other` says by how many. `damaged` marks a photo to show as such; its thumbnail is the placeholder and says so
(`X-TagPup-Thumb`). A photo whose file is gone is answered the last picture kept or a `404` sentence: show it, not editable (9c). Counts are read at each
call, so the navigator is asked again after an edit. **Not built**: thumbnails made when a photo is indexed or changes, a bound on the cache, a
person by id.

### Phase 9b-1: the windowed grid, and the folder view on it *(built 2026-10-02; branch `arch/phase-9b1-grid`)*
The page only: no route, no migration. A folder opens as it did; what changes is that only the cards on screen exist.
`web/tagpup/vgrid.js` owns the windowing; `grid.js` is what a card is and what selecting does; `selected.js` owns the selection.

- **The grid** (`createVGrid`). A *source* -- `count()`, `recordAt(i)`, and optionally `indexOfKey(key)` (else a scan) --
  shown by `buildCard(record, i)` and `cardKey(record)` in the stylesheet's own CSS grid: the rows in view and two either
  side are in the DOM, and the rows before and after are the grid's padding (`--vgrid-before` / `--vgrid-after`,
  `style.css`), so the scroll bar is right for N cards. The columns are *read* from the browser (`gridTemplateColumns`
  of the grid, so a size class, a dragged sidebar and browser zoom stay the stylesheet's), and the height of a row from a
  rendered card (`getBoundingClientRect`, not `offsetHeight`, since a zoom makes it fractional), measured again when the
  grid's width or a size class changes, never assumed. Rows are then fixed (`grid-auto-rows`) at the measured height, so
  the arithmetic is exact. The scroller is `#folder-view-main` (the grid sits below the folder's header in it);
  `overflow-anchor: none` there, since the padding changes under the browser's feet. **Nodes are reused, not pooled**: a
  card whose key stays in the window is the same element, one that left is dropped; a pool would have needed a `bind()` for
  every card builder and measured nothing worth it (35 cards, 350 nodes drawn at most 500 while scrolling the whole
  folder, a frame costing a row's cards). `refresh()` after a data change draws the window again, keeping the scroll
  position; `reset()` goes to the top; `relayout()` after a size change keeps the row at the top of the view at the top
  (the same photos stay near the top of the view); `scrollToIndex(i, 'nearest' | 'start')`, `indexOfKey(key)`, `eachCard(fn)`.
  Recomputed on `scroll` (once a frame), on `ResizeObserver` (the grid's width: read the columns again; the scroller's
  height: redraw) and when asked. `destroy()` takes the listeners off.
- **Measured through one function**, `measure({grid, scroller, card})` (`measureDom` by default; `view.setMeasure(fn)`):
  jsdom has no layout, so a test gives numbers (`viewport`, `scrollTop`, `gridTop`, `columns`, `cardHeight`, `rowGap`,
  `cardWidth`, `columnGap`). **With no layout** (jsdom, a hidden grid: `viewport` or `columns` 0) the first 120 records
  are drawn whole and every picture at once, which is what the older page tests see. The page tests reach the grid through
  `pageExports(window, 'web/tagpup/state.js').state.grid` (`tests/frontend/harness.mjs`, tests only).
- **Pictures.** A card's `<img>` carries `data-src`; the grid sets `src` for a card that has stayed in the view's rows
  for 120 ms (one timer; when it fires it looks at where the view is *now*, since a slow frame is not a pause), and for the
  row either side only once the view has stood still that long. A card that leaves the window while its picture is
  loading has `src` taken off (the request is cancelled). A card drawn again by `refresh()` shows the picture it had at
  once. A picture that failed to load is not asked for again by that card. A damaged photo has no `<img>`; `markCard`
  (`damaged.js`) works on the cards there are, and a card built later is marked as it is built.
- **A title being typed** (`isBusy`): the card is kept as it is by `refresh()`, and when it scrolls out of the window it
  is not removed (that moves focus away, and a blur is a save) but laid, absolutely, where it would have been, at the
  grid's edge, where it cannot be seen and takes no room; it is the same element when it returns. Once the save is under
  way (`data-saving`) it is an ordinary card again and the redraw after the save shows the new title.
- **Selection by photo, not by card** (`selected.js`). `state.selectedThumbnails` is unchanged for every other module (an
  array of paths in pick order, copied with `slice()` by the bulk edits); `state.selectedKeys` is the same photos by
  `pathKey`, a Set, for O(1) membership. `selected.js` is the one writer of both (`setSelection`, `addToSelection`,
  `removeFromSelection`, `clearSelection`, `renameInSelection`; a test fails any other module that assigns, pushes or
  splices). A Shift-click range is computed over the source's order (`state.shownPhotos`, `state.shownIndex`: key to
  index), so it spans cards that are not in the DOM; Select all / Invert / Select none are one pass over the folder's photos
  and then `eachCard` marks the cards that exist (no card is built again). A bulk write receives `selectedThumbnails`,
  built once. The selection panel's tally no longer searches the array once per photo.
- **A photo renamed by saving its title** keeps its selection under the new path (it kept the old one), and its card is
  built again under the new key. "Extend selection to here" with nothing selected no longer throws.
- **The place in the list.** `renderThumbnails()` is `refresh()` for the same folder and filter, `reset()` for another folder
  or another filter text (`state.shownSource`). A photo open and then closed (the grid is hidden, which loses its scroll
  offset in the browser) comes back at the same place: a hidden grid keeps the window it had, builds no card and asks for no
  picture however often the photo view saves, and remembers where the view was and puts it back when it has a layout again
  (also when it was shown again before any render saw it hidden). The selection belongs to its folder: another folder starts
  with none, the same folder scanned again keeps what is still in it; Select all and Invert take what the filter shows. A source that shrinks while scrolled past its end shows its end.
- **What 9b-2 must know.** A library source implements `count()` / `recordAt(i)` / `indexOfKey(key)` over the pages it has
  fetched; `recordAt(i)` for a record not fetched yet must return a placeholder record the card builder can draw (its key
  stable, e.g. `'#' + i`) and ask for the page; when the page arrives call `refresh()`. The folder source builds
  `state.shownIndex` once per `renderThumbnails()` (O(N) with `pathKey`; for 20,000 it is the cost to watch); a library
  source should not do that per refresh. `cardKey` is the photo's id there, not its path, or a rename rebuilds the card.
  Selection is by path today (`selected.js`): 9d's selection that spans folders is by id, and `selected.js` is where that
  changes. The sidebar list (`renderFileList`) still builds a row for every photo; it is not a grid and 9c's navigator
  replaces it, but at 20,000 photos it is the next cost. Tab visits only the cards that exist; both are known limits,
  left to 9c.
- **Measured** with `scripts/measure_grid.py --run` (`--code-root <a `git archive` of the commit before>` for the baseline;
  without `--run` it prints what it would do). Its sandbox is `scripts/sandbox.py`'s, which `measure_identify_faces.py` and
  `measure_suggest_folder.py` share: a copy of photo_index (2.6 GB) in the temp directory under its own `TAGPUP_HOME`, its
  root placed at an empty sandbox folder, a free port, 759 synthetic JPEGs generated there (no real photo is read), headless
  Chromium 1600x1000, deleted afterwards; a fresh browser for each open; the trunk's code is the baseline, run the same
  way, two runs; an empty page's frame is 16.7 ms here). The click is the
  person's: open the folder, scroll it, select. Run to run the numbers move by up to 40%; the direction did not.

  | the click (759-photo folder) | trunk | this branch |
  |---|---|---|
  | folder opens: scan reply to cards painted, main thread idle | 128-168 ms | 55-57 ms |
  | longest main-thread task of the open | 80-106 ms | none over 50 ms |
  | cards / DOM nodes after opening | 759 / 7,590 | 35 / 350 |
  | scroll top to bottom in 3 s: DOM nodes at the peak | 7,590 | 500 |
  | the same: longest task, frames over 100 ms | none, 0-1 | none, 0-1 |
  | the same: frame interval, 95th percentile | 50-83 ms | 50 ms |
  | the same: pictures requested | 669 on the first sweep (native lazy loading), then none (kept in place) | 79-224, of which 25-119 from the network (the committed script, last) |
  | Select all / Invert / Select none: in the handler | 40-62 / 60-101 / 28-48 ms | 0.9-1.1 / 3.1-3.3 / 0.2 ms |
  | the same, until painted and the main thread runs | 84-197 ms | 60-78 ms |

  | 20,000 synthetic records through the folder view | trunk | this branch |
  |---|---|---|
  | render: in the handler / until painted and idle / longest task | 281-616 ms / 2.0-4.3 s / 1.2-2.6 s | 9 ms / 57-59 ms / none |
  | cards / DOM nodes | 20,000 / 200,000 | 35 / 350 |
  | scroll top to bottom in 10 s: peak nodes, longest task, frames over 100 ms | 200,000, 249-279 ms, 38-48 | 500, none, 0 |
  | the same: pictures requested | 4,132-5,523 | 0 |
  | Select all / Invert / Select none: until painted and idle | 6.0-7.8 / 4.8-5.6 / 3.3-4.0 s | 68-80 / 67-78 / 62-67 ms |

  What the numbers do **not** show: on the 759-photo folder, scrolling is the same 50 ms a frame before and after
  (headless Chromium, software compositing; an empty page runs at 16.7 ms), so a person scrolling a folder of that size
  will not feel a difference there, and the folder opens in about half the time (a tenth of a second or less either way).
  The gain is the bound: opening, selecting and scrolling cost the window, not the folder, and 20,000 photos are possible.
- **Tests.** `tests/frontend/vgrid.test.mjs` (the grid alone: the window, padding arithmetic, reuse, order, an editor kept,
  pictures asked for and cancelled, a size change, a width change, hidden and shown, the source interface),
  `grid-window.test.mjs` (the page on a layout it is given: the window, a Shift-range across unrendered cards, Select all
  of 400 and the bulk write that follows, a card's menu after it was recycled, a title being typed while scrolling away,
  a rename, the filter, a photo opened and closed, two folders, a size change, a damaged photo scrolled to, one owner of the
  selection), `selection-follows-the-photo.test.mjs` (the two fixes, which failed on the trunk).

### Phase 9b-2: a library source for the grid *(built 2026-10-03; branch `arch/phase-9b2-library`)*
The page, and three routes under it. The grid of 9b-1 shows a view of the library as it shows a folder: the whole library, a year,
a month, a keyword and everything under it, a person, or a folder and its subfolders, from the database, in the same grid, details
panel and selection. Nothing is migrated and nothing of a folder's machinery runs while one is open.

- **Three routes** (specified in SPEC_TAGPUP_GUI; loopback only, through the Roots gate and ingress, a library not at migrations 19
  and 20 answered the same sentence as the others). `GET /api/library/ids?kind=&value=|folder=&recursive=` answers `{source, total,
  ids, complete}`: the source's whole id list in `view`'s order (Date Taken, then id, the undated after by id), **one read of ids
  alone**, one statement for the dated photos and one for the undated, each an index-ordered read (`tagpup.store.library_view.all_ids`;
  the plans are `view`'s without the keyset, `tests/test_library_view_plans.py`), cut at 200,000 with the real `total` and `complete:
  false`. `GET /api/library/cards?ids=1,2,3` answers `{cards}` for at most 200 ids, the cards 9a-2 builds, in the order asked, each
  once, an id the library has no photo of simply absent. `GET /api/library/photo?id=` answers `{photo}`, the record a folder's scan
  gives a photo (`services.photos.page_record`: tags, people, title, mtime, size, year, taken, raw metadata) read from the library's
  row, no file touched, and `id`: **this third route was not in the brief**; the details panel and every edit work on such a record by
  path, a card (id, name, path, taken, damaged, thumb) is not one, and the other way to a photo's tags was `/api/folder/scan` of its
  whole folder, which walks the disk. `/api/library/view` is unchanged and unused by the page: the keyset cannot jump to the middle of
  68,000 photos.
- **The address is the view**: `?view=<all|folder|keyword|person|year|month>&value=<v>[&recursive=1]` (a folder by its path, a
  keyword by its tag, a person by name, a year `2024`, a month `2024-06`; `value` absent for `all`). `library-source.js`
  (`viewSpecFromSearch`, `viewSearch`) reads and writes it: an unknown kind, a missing value, a year or month that is not one, a value
  over 1,000 characters is an empty view with a sentence and no request. Opening a view **pushes** an entry (with the scroll offset of
  the one left saved in its history state), so Back and Forward move between views and between a view and the folder, and a bookmark
  opens one; a view opened from Back or Forward writes nothing. A `?view` at start wins over a `?path` (kept as the folder Back returns
  to). Choosing another dog park goes to that library without the `view`, `value` and `recursive` of the address
  (`common/library.js`, `goToLibrary`): ids belong to a library.
- **The source** (`web/tagpup/library-source.js`; every bit of its state is `state.library`, state.js). It implements the grid's three
  questions: `count()` is the ids; `recordAt(i)` is the card if it is held, else **a placeholder** of the same key (`#<id>`), and asks
  for the cards around the window; `indexOfKey` is the position of an id. Cards are **keyed by the photo's id**, so a rename redraws
  nothing. The request for cards is made **once the window has stood still for 100 ms** (the first window of a view at once), at most
  every 100 ms x 5 under a scroll that never stops (so about two a second), for the window and half a window either side, in batches
  of 200, **two at a time**; a batch for a part of the view the window has left is **cancelled** (the request is aborted, nothing is
  painted from it), and the cards that arrive are drawn by `vgrid.patch(keys)` (new: the cards of these keys are drawn again from the
  source, the rest left as they are). **The cards held are the 2,000 last used**; the far ones are let go. A batch that fails is tried
  once more -- after 2 s or at the next scroll, whichever is first -- and its placeholders then say "Could not load" and are not
  asked for again until Refresh view: no loop. A photo whose card the library does not return (deleted since the order was read) is
  dropped from the order and the total, quietly. The order is **one array** (`lib.ids`, the response's own, 68,000 numbers); `indexOf`
  finds a position on a click, not per frame.
- **The view** (`web/tagpup/library-view.js`): opening and closing, the strip above the grid (the source, its total, "Opening...",
  "Refreshing...", "Selecting N of M...", what went wrong; **Refresh view** and **Back to folder view**), the address, and the gear's two
  items. **Browse the whole library** opens `all`; **Library view of this folder** (greyed with no folder open) opens the open folder
  and its subfolders. **Refresh view** (and the sidebar's Refresh) asks for the order again, forgets the cards, and scrolls back to the
  photo that was first in the view; an order that cannot be read leaves the view as it was and says so. The order is **not recomputed
  by an edit**: a photo edited so that it would sit elsewhere, or no longer be in a keyword view, keeps its place until the view is
  refreshed, and its card is updated in place (`applyEditedRecords`: path, name, title, date from the record the details panel holds).
- **The folder's machinery is idle while a view is open** (`quietTheFolder`): the scan under way is aborted, `scannedFolder` is null,
  the suggestion and index pollers stop, nothing asks `/api/folder/membership` or `/api/folder/damaged` (every photo of a view is held;
  a card says whether its photo is damaged), the add-folder question cannot open, Suggest, Auto-apply, Smart Rename and Camera Time
  Shift are off (the last two work on a folder; across folders they arrive with 9d, and the brief's "keep" would have sent the server
  a folder it does not have), the filter is off with a tooltip, **no list is built** (`renderFileList` returns at once: "68,472 photos
  in this view" and "N of M" for the open photo), nothing is kept in this browser's folder cache, and a folder opened from the box or
  by Back closes the view and reads the folder as ever.
- **A card is its photo's cache thumbnail** (`/api/photo-thumb` by id with its `v`, through `api.image`), name, date written whole
  (a view spans years), a damaged or incomplete mark from the card's own flag. The card has no title: **the 9a-2 card read has no
  `captions` (a test holds it), so a library card shows the file name**, which Smart Rename makes of the caption; a title edited in
  the details panel shows on its card at once. Its title cannot be edited on the card (the card has no tags to send with it).
- **Selecting stays by path** (`selected.js`, unchanged) *(superseded in a library view by 9d-2: selection by photo id, see "Phase 9d-2")*. A click is the card's path; a Shift-range or Select all over cards not held
  **fetches those cards for their paths** (3 requests of 200 at a time, "Selecting N of M..." in the strip, replaced by a newer
  selection, abandoned when the view closes, all or nothing), and **Select all / Invert of more than 5,000 photos asks "Select all
  N photos?" first**. The selection panel does not tally tags and people of a selection it cannot count ("Not tallied for a library
  view"); the bulk editor writes by path through the existing routes, a body of 68,000 paths for a Select all.
- **The details panel** opens a card by id: the record is read (`/api/library/photo`) and is the page's only full record, in
  `state.folderPhotos` as a folder's photos are (so title, tags, people, date, rotate, delete work as ever, by path). Previous and
  Next (arrow keys, swipe) and "N of M" follow the view's order by `ids`; two quick presses go two photos on. A photo the library no
  longer has is dropped from the view. A rotate asks for its card again (its thumbnail's `v` changed); a delete takes its card away,
  drops the total and opens the next photo. Same-as-previous (Ctrl+D) is off: the photo before is not in the page.
- **The record is as fresh as a folder scan's, and a write names the file it was built from** *(review of 9b-2, findings #533)*. The panel
  sends a photo's whole tag list on a tag add, a Date Taken edit and carry forward, and the server writes it as given; a record built from
  the library's row (a view) or from this browser's half-hour cache of a scan (a folder) says less than the file when another program
  added a keyword after the index read it, or when the row is Suggest's (a path and no stamp): the next save removed what the file held.
  So: (1) `/api/library/photo` takes the file's stamp and sets it against the row's (`store.photos.describes`: size equal, time within
  0.1 s); where they agree the row is the record, where they do not -- or the row has no stamp -- the file is read with ExifTool as the
  folder scan reads it (`services.photos.read_file`); the record's `mtime` and `size` are the file's; a file that is gone is the row with
  `missing`. (2) Every metadata write the panel makes -- a save of tags, title or people, the Date Taken edit, carry forward, Apply a
  suggestion, the card's title in a folder -- sends `stamp: {mtime, size}` of its record, and `tagging.save_photo` (under the lock of
  changes to files, before it reads) refuses with `409`, `changed_on_disk` and "This photo changed on disk since you opened it: reload it
  first." when the file's stamp differs or the file is gone, writing nothing; held and file-only (Just look) photos alike, and a call
  with no stamp (the CLI, the MCP) as ever. The page (`edits.js photoChangedOnDisk`, `photo.js reloadChangedPhoto`) reads the photo again
  -- a view's by id, a folder's by scanning the folder (`scanFolder(true, {keepTyped})`, which asks nothing about what was typed: it stays
  in its fields) -- and says so in the status line and the alert; nothing is merged and nothing overwritten. A successful write's reply
  carries the new `mtime` and `size` (a rename by the caption: of the file under its new name) and the record takes them, so the next save
  passes; bulk tags reports each photo's (`stamps`; it adds and removes against the file's own tags, so it needs no precondition),
  rotate reports `size` with `mtime`. Auto-apply and Smart Rename read the folder again, so their records are fresh.
- **The stamp is the cheap first check; the base is the real one** *(review of the fix, findings #549 to #552)*. A stamp can be kept: a
  copy keeps its times, and a same-length rename of a tag with the time put back changes the keywords and nothing the stamp sees. So
  the page also keeps, with each record, what it read of the two things a save overwrites -- `base: {tags, title}` (`edits.js baseOf`,
  taken from the record the first time it is needed) -- and sends it with every write that carries a whole list or caption: a save of
  tags, title or people, the Date Taken edit, carry forward, applying a suggestion, a folder card's title. `tagging.save_photo`, under
  the file-changes lock and after it has read the file's current state, compares the file's keywords (as a SET of paths, `_tags_held`)
  and caption (the first, trimmed) with `base`; any difference is `409` `changed_on_disk` with the same sentence and nothing written;
  a field the save does not touch (a rating) is not compared, so another program's rating is no refusal. The page takes the next
  `base` from each reply (`base`: the tags and caption as written), and from the replies of bulk tags (`written`) and of undo, which
  also give the stamp (`edits.js takeWritten`: undo had left the record with the old one, so the next save was refused and the old
  stamp survived in the folder cache); the server's cached scan takes the file's stamp from a bulk write too (`_records_written`).
  A call with neither `stamp` nor `base` (the CLI, the MCP) is no check, as it was. **A photo ExifTool could not read when it was opened**
  (`read_error` of the reader, an empty file for one: `page_record` keeps it as `unreadable: true`) is not saved from: the page says
  "This photo could not be read just now: reopen it." and sends nothing, and the server refuses a page's save (one with a stamp) whose
  `base` is null with the same sentence, so a record that shows no tags because the read failed never replaces the keywords of a file
  that reads now. The cost is about a hundred bytes a save (`base` of a photo with three tags and a caption).
- **A bulk write is asked about and capped** *(findings #535)*. A selection over 200 photos asks "Add Trips/Coast to 3,412 photos?" (the
  write named, `selection.js confirmBulkWrite`) before any bulk tag, person or removal; the server refuses a request of more than 5,000
  photos with `400` "Narrow the selection: bulk edits over 5000 photos arrive with the editing stage" -- bulk tags and Smart Rename; the
  page says the same without sending, **before** a typed name is resolved (resolving may make a tree node: a refused or cancelled bulk
  makes none, findings #554). A job with progress, cancel and undo of its own is 9d's. Accepted and left: time-shift and auto-apply take a
  folder and have no cap, and the undo of an auto-apply over 5,000 photos is refused by the cap.
- **The tag tree's counts are one pass of `photo_tags`** *(findings #534)*. `/api/taxonomy/tree` parsed every photo's tags JSON for
  `usage_count` (`store.photos.tag_usage`): 350 ms on a synthetic library of photo_index's scale (68,466 photos, 159,651 `photo_tags`
  rows, 931 nodes), which held the Python process at page start so that `/api/library/ids` and `/api/tags` waited behind it. It now
  reads `photo_tags` (`library_view.keyword_counts`, whose per-photo grouping SQLite does: Python sees the distinct tag sets, not 68,000
  photos): **68 ms**, the same counts node for node (`tests/test_tag_usage_from_photo_tags.py`); a library without the derived tables
  is counted from the JSON as before. **What a count means** *(findings #553)*: it follows the views' derivation, so a keyword with no
  node counts toward nothing (the old lineage count credited the levels above it: 4 such keywords on 32 uses in photo_index), and a
  keyword that differs from a node only in case or spacing counts toward that node (`tests/test_tag_usage_from_photo_tags.py` pins both). With the page's first three requests in flight together: tree 785 -> 122 ms, ids 830 -> 170 ms,
  tags 920 -> 260 ms (`/api/tags` is itself 260 ms there: not looked at). So the "85-330 ms in the page" of the ids request was mostly
  the tree route holding the process, not the route (30 ms alone). Re-measured on the sandbox copy with `measure_library_view.py` on a
  machine eight times slower than the earlier runs (copying the library took 219 s, not 15), so its absolute numbers are not comparable;
  navigation to the ids reply of `?view=all` was 385 ms (304-529) against 458 ms (448-591) before.
- **A reply about the folder after a view opened shows nothing** *(findings #536)*: an index-status or suggest-status reply that lands
  once a view is open returns at once (it unhid the progress containers and set `folderSuggestions` over the view). A photo opened from a
  view carries what the library records of its damage (`damaged`, `damage` in the record), so the panel shows the note and asks for no
  picture of a file recorded unreadable; `/api/library/photo` reads `id` as digits only.
- **Measured** with `scripts/measure_library_view.py --run` (plan without `--run`; `--code-root <a git archive of the trunk>` for the
  baseline, which has no library view, so only the folder view is measured). The sandbox is `scripts/sandbox.py`'s: a copy of
  photo_index (68,472 photos, 2.6 GB; the server migrated it to 20 as it opened it), a free port, headless Chromium 1600 x 1000, a fresh
  browser for each open, the thumbnail requests answered by the browser's own routing with a 1-pixel picture (the sandbox has no
  photos; the requests are counted, not decoded), deleted afterwards. Three rounds, medians; this machine was not quiet (the trunk's
  759-photo folder opens in 112 ms here, 55 in 9b-1's table). There is **no library view on the trunk to baseline against**, so the
  library numbers are read against the folder view's of 9b-1 and of the trunk in the same session (last two rows).

  | the click (68,472 photos) | this branch (median of 3; range) |
  |---|---|
  | (a) open `?view=all`: the ids reply to the first window of cards painted, main thread idle | 41 ms (40-55) |
  | the same: thumbnails first asked for (the 120 ms rule) / navigation to painted / navigation to the ids reply | +152 ms / 504 ms (498-631) / 458 ms (448-591) |
  | the ids request in the page (390 KB, 68,472 ids): the route in-process, 30 ms (23 SQLite, 4 JSON) | 85-330 ms before the tree fix (it waited behind `/api/taxonomy/tree`); a request of one card is 5-6 ms |
  | (b) open the keyword node of 41,448 photos: the ids reply to painted, idle (ids 236 KB) | 39 ms (38-45); navigation to painted 438 ms; thumbnails asked +151 ms |
  | (c) scroll `all` top to bottom in 10 s (2.87 million px): frames / longest / over 33 ms / over 100 ms | 601 at 16.8 ms at most / no long task / 0 / 0 |
  | the same: DOM nodes at the peak / JS heap at the peak (4.4 MB after opening) | 500 / 4.8 MB |
  | the same: requests made: ids / cards / thumbnails | 0 / 15 (1.5 a second) / 27 |
  | (d) the scroll bar dragged to the middle, a third, two thirds: until the cards in view are real and painted | 208 ms (48-273; the 48 is a place already drawn); 1 card request; no long task |
  | (e) the library view of the largest library folder (759): the ids reply to painted, idle | 56 ms |
  | (e) the 759-photo folder view: scan reply to painted, idle / longest task / scroll 3 s p95 frame, frames over 100 ms | 93 ms / 50 ms / 50.1 ms, 0 (the trunk: 112 ms / 60 ms / 50.1 ms, 0) |

  What the numbers do **not** show: the photos are not in the sandbox (the thumbnail requests were answered by the browser with a
  1-pixel picture, so the server's 5 ms a cached thumbnail and 47 ms a first one, 9a-2, are not in them), a person's browser is not
  headless, and the ids request was 3 to 10 times its handler on this machine: that was the tag tree's route holding the process at page
  start (see the tree's counts above), not the route. `EXPLAIN QUERY PLAN` of the id lists on the copy: `all`, a year and a month
  `SEARCH ... COVERING INDEX idx_photos_taken` / `idx_photos_year` with no sort; a folder with subfolders `SEARCH p USING INDEX
  idx_photos_path_nocase (path>? AND path<?)` and a temp b-tree for the order; the keyword two seeks of the tree, `photo_tags` by
  `tag_id`, `p` by primary key and a temp b-tree (104-124 ms for 41,448 ids in SQLite alone, as 9a-2's page of it); no `SCAN` of photos
  (`tests/test_library_view_plans.py`).
- **How it fails**, each a test (`tests/frontend/library-view.test.mjs`, `tests/test_library_ids_routes.py`): a scroll through thirty
  windows asks for the one it stopped at; a batch for a window left is cancelled and not painted; the scroll bar dragged to the end
  (placeholders, then the last photos); cards bounded; a failed batch tried once more and then not at all; a partial answer dropped
  quietly; two quick changes of view (the first's order and cards never painted into the second); Back and Forward between a folder and
  a view and between two views (the offset restored); a bad address, a keyword with no node, a library behind (the sentence), an
  unplaced root (the banner); a source of 0 and of 1; the panel open while the view refreshes; a selection through a refresh; Select all of
  68,000 asking first and selecting every path once; right-click on a placeholder naming no photo; damaged and incomplete cards;
  thumbnails asked only after 120 ms in view; ids that change under the page (the total as it was until Refresh view); a scan still
  out when a view opens landing nowhere; another dog park dropping the view.
- **What 9c must know.** A view is opened by `openLibraryView({kind, value, recursive})` (`library-view.js`); the navigator's Folders,
  Keywords, People and Dates call it, and `/api/library/navigator` is asked again after an edit (a count is read at each call). The
  strip is where its "header says which source" lives; the sidebar is empty in a view (the navigator takes its place). A card carries
  `damaged` and `damage` only: 9c's staleness marks (size and modified time, "missing") are new card fields, and a card whose photo is
  gone shows a broken picture today (the thumbnail route answers a 404 sentence). `state.libraryReturn` is the folder Back returns to
  *(gone with #670, with the strip's Back to folder view)*.
  The folder cache and the folder's scroll are not restored by Back (a folder is read again from the cache of the scan, at the top).
- **What 9d builds on.** `selected.js` is by path because every write route is; a card has its `id`, and `selectInLibrary` is the one
  place that turns a range of ids into paths -- with id-based writes it disappears and Select all of 68,000 is instant. The selection
  panel's tallies need a server answer for a selection by ids. Smart Rename and Camera Time Shift need a form across folders.
- **Known limits.** The cards of a view show file names, not captions. The order is not live. A body of 68,000 paths is a long request.
  The first ask of a keyword view of 41,000 photos sorts its ids (100 ms in SQLite); `all`, a year and a month read an index in order.
  The ids of a source over 200,000 photos are cut there. Tab visited only the cards that existed (as in 9b-1): 9c made the grid one tab stop.

### Phase 9c: the navigator, the move between a folder and its view, stale marks, the grid's keys *(built 2026-10-03; branch `arch/phase-9c-navigator`)*
The page, and two small routes and a card field under it. Nothing is migrated. The owner sees: a switch at the top of the sidebar, **Library** and
**Folder**; behind Library, four tabs of what the whole library holds; a click opens the view; and a grid that can be walked with the keyboard.

- **The sidebar is two panes** (`index.html`, `navigator.js`): **Folder** is what it was (the folder box, Suggest, the open folder's file list), **Library** is
  the navigator. A library view shows the Library pane and a folder view the Folder pane; a person who chooses the other keeps it for that kind of view until
  the page is left (`state.nav.choice`, never written anywhere). The switch is an ARIA tab list (arrow keys), as the navigator's four tabs are.
- **The navigator** is three modules. `navigator-model.js` is data only: the route's lists made into the rows a tree shows (`indexFolders`, `indexKeywords`,
  `indexPeople`, `indexDates`, `sectionRows`, `locate`, `sectionOf`); `navigator-tree.js` draws rows as the ARIA tree (or listbox) pattern asks and owns the
  keys' arithmetic (`paintRows`, `treeKeyAction`, `tabKeyTarget`, all pure but the draw); `navigator.js` asks, listens and keeps the state (`state.nav`).
  *Folders* and *Keywords* are lazy trees from the route's flat list by parent (a branch is drawn only when open; folders in the route's order, keywords
  alphabetical by `compareTagNames`), *People* an alphabetical list, *Dates* the years newest first opening to January..December (and an "Other" row for the
  photos of the year whose date names no month). **At most 1,500 rows of a tree's open branches (5,000 of a flat list: the people, the years and months, what a filter finds) are drawn**, and a line says how many were left out, counted over the whole tree (a parent with 1,000 children shows all of them; all 413 of photo_index's people are on the page; findings #567); a filter box
  (120 ms after the last key) lists what matches wherever it is filed, with its place. **Years before 1900 or after next year** (the route marks them `implausible`; the server owns the rule, the page shows what it is told, #510) are one collapsed
  **Other years (N)** entry at the end, still reachable. A folder row shows the photos with its subfolders (the direct count is in its tooltip and its
  label); a click on a row opens the view of it **and** opens the branch; a click on the arrow only opens the branch. A keyword no tree node holds cannot be
  navigated and is not listed: the route does not say how many there are, so nothing says so under the tree (the doctor lists them).
- **Reading.** A section is read from `/api/library/navigator?section=` when its tab is first shown. Each ask has a number, and an answer that is not the
  newest of its section is dropped; a section is drawn into its own panel only, and only while it is on screen (else when its tab is shown). It says
  "Loading the folders...", "The library holds no photos in any folder yet." and the server's own sentence for a library behind or a root this computer
  does not place (a 409; logged as a warning, not an error), or "Could not read the people (Failed to fetch)." and is read again when its tab is opened. A
  section already read stays on screen when a re-read fails. **Counts are read again** when a write finishes (`write-queue.js markEntry` -> `upper.navigatorCountsChanged`:
  every write of this page goes through the one queue) after 1.2 s, once for a run of writes; and at once on Refresh view. The re-read is quiet: the rows are
  the same elements, what is open stays open, the focus stays; only the numbers change. A section not on screen is marked out of date and read when its tab is shown.
- **The view is followed** (`library-view.js` calls `upper.navigatorFollows` as a view opens or closes): the source is highlighted in its tab (`aria-selected`),
  the tab is selected and the path to the row opened -- also for a view opened from the address, by Back and Forward -- by `locate` over the parents the route
  gives (a keyword by its tag, else without case; a person without case; a folder by `pathKey`; a month opens its year; a year among the odd ones opens the group).
  A source with no row (a keyword with no node, a folder the library holds nothing in) is an empty view with its sentence and no highlight. Two libraries are two
  pages: nothing of one's navigator can reach the other's.
- **The move between disk and library** (`library-moves.js`): **Show in library** (beside Select All, with a folder open on disk) opens the folder's library
  view with its subfolders; **Show on disk** (in the strip of a folder's view) scans the folder FIRST and closes the view only when the scan has answered, through the history entry Back to folder view uses -- a folder that is not on disk any more (400), or cannot be read, keeps the view and says so in its strip, so the page is never left with neither a view nor a folder (#569);
  **This folder only / With subfolders** is another view of the same folder *(Show on disk and This folder only went with #670; the scan-first move is
  `openInOrganize`, a folder of the selection's Folders to Organize)*. The selection is cleared by every one (a view opening or closing clears it, 9b-1). The
  photo at the top of the grid is the one to land on: a folder view finds it by `pathKey` in `shownIndex`, a library view asks the library which id a path is
  (`GET /api/library/find?path=` -> `{id}`, `null` for a photo it does not hold: one seek of `idx_photos_path_nocase`, `COVERING INDEX (path=?)` on photo_index) and
  `scrollToIndex(..., 'start')`s there, below the sticky strip (`topInset`). Best effort: a photo in neither leaves the grid at the top.
- **The banner** (`library-banner.js`): a library view of a *folder* asks, once it has painted (never before), what the disk holds of it
  (`/api/folder/membership`, the call a folder opened makes) and, if `photos_not_held > 0`, says "N photos in this folder are not in <library>." with **Add them** and
  **Dismiss**. Add them opens the add-folder question of a folder opened -- the library's name, what it holds and lacks, the warnings -- with **Not now** in place of Just
  look; nothing is indexed until **Add to <library>** is pressed (`/api/folder/add` for the banner's folder, `state.moves.addFor`; a folder view's own Add still adds the
  open folder), and then the banner goes and the strip says it is being indexed and to Refresh view when it has finished (the folder's index pollers are idle in a view). The
  question has a deadline (15 s, then the request is aborted; the server gives up at 12) and **any** failure -- a share away, a folder gone from the disk (400), a library that cannot say, the
  view left meanwhile -- shows nothing and logs nothing: it is an offer. Dismiss lasts until the page is left, per folder.
  **A walk is a folder's worth of disk, so it is not made on every view change** *(findings #568)*: (a) the answer is kept per folder for the page's life (`state.moves.cache`) and forgotten by
  Refresh view, an Add, and a change in `last_in_step` or `syncing` (sync-state.js: the moments the disk and the library may have moved apart); a "could not check" is never kept; (b) it is
  asked by itself only for a view of **this folder only** and for one with subfolders of **under 1,000 photos** (the view's total); a larger one shows a quiet link, "Check this folder on disk for
  new photos", that asks when clicked and says "Every photo on disk in this folder is in the library." or "Could not check this folder on disk just now (why)."; (c) the server
  (`libraries.membership_checked`) first asks a folder on a network share (a UNC path or a mapped drive: `shares.on_a_network_drive`) to say it is there within a second on a thread that is
  waited for -- else the answer is `{could_not_check: true, why}` and no thread is left walking it -- then walks on a thread of its own and waits 12 s (`it took too long`); a request for a
  folder under walk is **answered by that walk**, never a second one; the route no longer calls `isdir` on a share itself. On the sandbox copy with 68,000 empty photo files under the
  root place, a click on the library's largest folder (68,324 photos) made one membership request taking 2.9 s (2.7-3.2); it makes none now and shows the link.
- **When the library was last in step** (`sync-state.js`): the strip says "Library last in step with its folders: 5 min ago" (or "never"; ". A sync is running now." while one is) from
  `GET /api/sync` -- the answer the Activity page's "Last in step" is made of, plus `syncing` (new: whether this process's folder watcher is syncing the library now, `activity_routes.is_syncing`;
  false when it runs none). Asked once as a view opens and again with Refresh view; a request that fails says "Could not read when the library was last in step with its
  folders." and nothing else changes; an older answer arriving late is dropped.
- **Stale marks** (`library_view.cards(check_disk=True)`, what `GET /api/library/cards` asks; `view` and the CLI do not): one `os.stat` for each card through
  `damaged_photos.stamp_of` (the bounded stat a page's request already uses: a share is asked within a second, one found away is answered at once for 30 s, so a page of
  cards on it waits once), at most 200 a request. A card gains `stale`: `"changed"` when the file's size and time no longer describe the row (`store.photos.describes`, the
  folder scan's own test), `"missing"` when the file is gone, and **no key** otherwise -- for a file that cannot be read, a share that did not answer, and a row nobody
  stamped (Suggest's row for a photo never read: "changed" would claim a difference nothing knows of; photo_index has none, counted 2026-10-03: 0 of 68,472 rows lack a size or time).
  The card carries a small badge ("changed on disk" at its bottom right, red "missing"), the picture that cannot be had is a grey card, and a screen reader hears it. A **missing**
  photo is shown (the library still holds its row) and **not editable**: opening it (`photo.missing` from `/api/library/photo`) shows a notice, hides the picture, and every
  element marked `data-writes` in the page -- rotation and delete, the date, title, people and keywords, the suggestions -- is `inert` and `aria-disabled` (`stale.js`; the server
  already refused a write to a file that is not there). A **changed** photo, opened, is read from its file (9b-2's rule), and its badge clears on the card; the library's row is
  sync's to bring up to date, so a view refreshed before it has says "changed" again, which is true. Of 400 rows of photo_index sampled evenly (read-only; counts), 400 were as their
  row said: the marks are rare on this library, and a person will mostly meet them after editing in another program.
- **The keyboard** (`grid-keys.js`, `vgrid.js`): the grid is a listbox and **one tab stop** (roving tabindex: the card the keys are on has `tabindex=0`, every other card, its
  checkbox and its detail button `-1`, a placeholder none). Arrows: one card, one row (the last short row's last card below a column with none); Home / End: the first / last photo
  of the view; PageUp / PageDown: the rows in view (`vgrid.geometry()`); Enter: opens the photo (a placeholder's too, by its id); Space: selects or deselects it (Shift+Space: from the last
  one picked, as Shift-click); Shift with an arrow: every photo from where the run began to the new one is **added** (stepping back does not take away). Keys move by index in the
  view's order, so they cross the window's edge and 20,000 photos: the grid scrolls to the index (`scrollToIndex`, below the sticky strip) and the focus goes to its card, or --
  a place is not focusable until its card arrives -- waits on the grid itself and goes to the card when it is drawn (`afterDraw`). The shortcuts of the photo panel and Ctrl+D/Ctrl+Z are
  unchanged and everyone's; the arrow keys stay the photo's when the focus is not in something marked `data-own-keys` (the grid, the navigator's trees and tabs, the sidebar's switch),
  and never fire from a filter box or a dialog. Cards are options of the listbox, named "file name, date, selected / not selected, damaged, changed on disk / file missing"; the
  navigator's tabs and trees follow the ARIA tabs and tree patterns (arrow keys, Home/End, Right/Left to open and close, Enter, `aria-expanded`, `aria-selected`, `aria-level`).
  **vgrid.js** learned three things for this: `afterDraw` (after every draw), `topInset` (a card scrolled to goes below what is sticky over the scroller), `geometry()`; and
  **the focused card is never taken from under the focus**: where a draw would release it (it left the window) it is kept, laid out of sight as a card being typed in is, and
  one rebuilt for changed data (a refresh, an edit) gives the focus to the card built in its place.
- **Measured** with `scripts/measure_navigator.py --run` (plan without `--run`; `--code-root <a git archive of the trunk>` runs only (f) and (g) on the trunk): a sandbox copy of
  photo_index (68,472 photos, 2,746 folders, 895 keyword nodes, 413 people, 61 years), headless Chromium 1600 x 1000, a fresh browser for each, 3 rounds, thumbnail requests answered by the
  browser. **There is no navigator on the trunk, so (a) to (e) have no baseline**: they are read against 9b-2's budgets (a window of cards painted in 40 to 60 ms, no task over 50 ms
  while scrolling); (f) and (g) are on both. The machine was not quiet; run to run these move by up to 40%.

  | the action | this branch (median; range) |
  |---|---|
  | (a) click Library: Folders rows painted, main thread idle -- cold (first after the server started) | 115 ms (the request 55 ms; the reply 792 KB, the 2,746 folders), before gzip; 245-430 ms on later, busier runs |
  | (a) the same, warm | 151 ms (109-162; the request 57-122) before gzip; 196 ms (138-461) after, the machine busier: the request is the server's 30-50 ms either way, and the reply is now 58 KB (792 KB gzipped, below) |
  | (b) the folder tree opened to depth 3: each of three clicks until painted and idle | 47 / 46 / 47 ms (45-48), 74 rows after, no task over 50 ms |
  | (c) a click on the largest keyword node (41,448 photos): click to the first window of cards painted and idle | 480 ms (6 rounds: 413-597); the same view by its address: navigation to painted and idle 500 ms (440-1,119) |
  | (d) five letters typed in the People filter, 60 ms between keys | the list redrawn 17 ms after the filter's own 120 ms wait (15-21); no task over 50 ms; 400 rows to 2-118 |
  | (e) ArrowDown held through 2,000 photos (5 columns, 400 keys at 30 a second, 17.6 s) | 1,056 frames, p95 16.8 ms, longest 16.8 ms, none over 100 ms, no long task, peak 550 grid nodes, the focus in the grid at every frame and on a card at the end |
  | (f) `GET /api/library/cards` for 200 ids, the sandbox holds no photo file so all 200 are `missing`: branch / trunk | 19.8 ms (18.8-21.0) / 8.8 ms (8.3-9.1): the 200 stats and the 200 looks at the drive's kind cost 11 ms (6 ms before the mapped-drive check; asked of Windows per card it cost 0.6 ms each, 130 ms a batch, until the answer was kept per drive letter for 30 s); 200 stats of files that are there, on a local disk, 5.2-6.6 ms (26-33 us each), of files that are gone 3.0-3.9 ms |
  | (h) a click on the largest folder row (68,324 photos) with 68,000 files on disk under it: membership requests, click to painted and idle | before (`866e279`): 1 request, 2.9 s (2.7-3.2), 597 ms (514-781); now: 0 requests and the quiet link, 480 ms (447-583) |
  | the navigator's folders reply and a view's order, through the real Waitress server with `Accept-Encoding: gzip` | 811,009 -> 59,588 bytes (13.6 times) and 399,821 -> 159,182; the browser fetch decodes it unseen (58.2 KB in the page's resource timing); server time 30-50 ms either way |
  | (g) open the 759-photo folder view, scan reply to painted and idle: branch / trunk | 93 (90-95) / 94 (52-103) ms |
  | (g) the same, scrolled top to bottom in 3 s: p95 frame, frames over 100 ms, peak nodes | 50.0 ms, 0, 500 on both |

  What the numbers do **not** show: the click on a 41,000-photo keyword is the server's ids request (190-440 ms in the page, the same 9b-2 measured, 100 ms of it a sort in
  SQLite), and in many of its rounds the page acted on the reply 85-266 ms after Chromium's own `responseEnd` for it. **That gap is not the page's work** *(CDP trace, findings #572)*: in
  eight traced click rounds the renderer's main thread ran nothing over 2 ms between `responseEnd` and the page having the response (no task, no GC -- the scavenges all came before it -- no
  layout; the navigator's painting happens at the click, before the request is even made), and in the four rounds whose network events were listed the renderer's `ResourceReceiveResponse`, `ResourceReceivedData` and `ResourceFinish` for the ids request
  all came at one instant (three of them 127-203 ms after `responseEnd`, one with no gap), after which the page asked for its cards 7 ms later. So it is the delivery of the response from the browser process to the renderer.
  It also happens when the view is opened by a bare `popstate` (no click, the navigator closed) and with the `/api/sync` request faked away, with and without the thumbnail route, with and
  without a rAF loop; it does not happen to a bare `fetch` of the same URL on an idle page (22 of 22 rounds: 1 ms) with or without an AbortController, nor when the view is opened by its
  address. The navigator's work is therefore not deferred: there is none in the window. The cause below the page is not known. Headless Chromium at 16.7 ms a frame has no compositor to fall behind; the photos are not in the sandbox (the stat is of a file that is gone, the
  thumbnail a 1-pixel picture); and nothing is measured on a network share, where each stat of a share that answers is a thread (`shares.bounded`) and one that does not costs the one
  second, once.
- **How it fails**, each a test (`tests/frontend/navigator.test.mjs`, `library-moves.test.mjs`, `stale-cards.test.mjs`, `grid-keys.test.mjs`, `vgrid.test.mjs`;
  `tests/test_library_cards_stale.py`, `test_library_find.py`): the four tabs at photo_index's size (rows bounded, all 413 people on the page, an expanded tree's count left out right: 1,549 rows is 49 left out; a parent with 1,000 children whole); a tab
  opened while another loads, an older answer for the same section dropped, a click while a view is loading; Back and Forward restoring the view, the highlight and the tab; the address
  opened cold to a folder, a keyword, a person, a month and a year among the odd ones; a folder or keyword that is gone; counts after a write (quiet, once for five writes, the section off
  screen read when shown); a library with nothing, one at schema 18, an unplaced root, a network failure; two libraries; the banner after the paint, for a folder's view only, with a
  share away, a folder gone, a library that cannot say, a question that never answers (the deadline), a view left meanwhile, Add them (nothing indexed until answered), Dismiss, Refresh
  view; "last in step" never / running / failing / an older answer late; Show in library and on disk (selection cleared, landing on the photo, the photo not held, a folder not on disk);
  stale badges, a missing photo inert, a changed one cleared, 200 in a batch, an away share (no mark); the keys at 20,000 photos with the DOM bounded, Enter and Space on a place, a
  Shift range across unloaded cards, the focus across a refresh and a wheel scroll, Ctrl+Z with the focus on a card. The server's: a file as the row says, edited, deleted, touched, a
  row nobody stamped, a share away or a file unreadable (no mark), `view` that looks at no disk, one stat each; a batch whose stats answer in 0.6 s each ends within its 1.5 s budget with the rest
  unmarked; a mapped drive is a share for the bounded stat and its type is asked of Windows once; a bulk tag write with a missing photo in the middle writes the photos after it and lists it
  (`tests/test_bulk_tags_missing.py`); a membership walk is shared by two askers, abandoned at its deadline, refused for a share away (`tests/test_membership_is_bounded.py`); a long JSON reply is gzipped
  (`tests/test_gzip_json.py`); and the page's: a bulk write leaves out missing photos and says how many (`bulk-missing.test.mjs`), the banner's cache and its link (`banner-cost.test.mjs`), Show on disk of
  a folder that is gone keeps the view, the keys of a card's magnifier and checkbox move the card.
- **What 9d builds on.** A write of a selection that spans folders is by path today and `selectInLibrary` is the one place that turns ids into paths (Shift+arrows and Select all use
  it): with id-based writes it goes and a range is instant. A card says `stale`: a bulk edit must skip a **missing** photo and say how many (the server refuses it), and take the new
  stamp a write returns into the card (`applyEditedRecords`). Both halves exist for bulk tags *(findings #566)*: the page leaves a photo whose card said `missing` out of the paths it sends
  (`state.library.missing`, by `pathKey`, kept as cards arrive; the confirmation names the count actually written; the selection panel says how many are left out; a selection of only missing
  photos sends nothing), and `tagging.change_tags` skips a photo whose file is gone and writes the rest (`details["skipped_missing"]`, the reply's `skipped_missing` and `skipped`), so a stale card
  never stops a batch; 9d's other bulk writes should take the same two steps. Every write of the page goes through `queuePhotoWrite`, which is what re-reads the navigator's counts: a new write path that
  does not will leave the counts as they were until Refresh view. The grid's keys call `handleCardSelectionClick`, `selectInLibrary` and `openLibraryPhoto` and know nothing else of a photo.
  `state.nav` is the place the search (9e) reads "within what the navigator has selected": `state.nav.followed`.
- **Known limits.** Shift+arrows only add *(in a library view 9d-2 lets a step back take away)*. After Enter the photo panel hides the grid and the focus is on nothing; Tab starts from the top of the page (the card the keys were on is still the
  grid's tab stop). Adding from the banner shows no
  progress (the folder's pollers are idle in a view): Refresh view when it has finished. A membership walk abandoned at its deadline goes on to its end (the next asker is answered by it);
  a view with subfolders of 1,000 photos or more asks for the disk only when told to, so it can be a day behind the disk. The card stat budget is 1.5 s a batch: past it the cards are
  unmarked, quietly. A card is an `option` of the grid's listbox, though in a folder view it can hold an editable title input: a `gridcell` would need a row structure and counts a windowed
  grid cannot give, so the option stays and the inner controls are `aria-hidden` with `tabindex=-1`; their own keys are left to them (a title being typed keeps all its keys, Space and Enter stay
  the checkbox's and the magnifier's) and every other key of an inner control acts on its card *(findings #571)*. Counts shown are `toLocaleString()`, so a thousands separator is the browser's.
  A card's title is still the file name. A library view of a folder with a very long path names it on several lines in its header. The navigator cannot show a folder "gone from disk" (the route does not say).

### Phase 9d-1: bulk edits by photo id, as a job; a selection's tally *(built 2026-10-03; branch `arch/phase-9d1-bulk-jobs`)*
Server only: no page, nothing the owner sees until 9d-2. Nothing is migrated. Today a bulk edit from a library view fetches every
selected photo's path (`selectInLibrary`: about 340 requests for 68,000) and posts them to the synchronous `/api/photos/bulk-tags`,
which holds the lock of changes of photo files for the whole request, is capped at 5,000 and has no progress or cancel. Three
pieces replace that for a library view; the folder view's routes (`bulk-tags`, `time-shift`, `rename-photos`, their 5,000 cap) are
untouched.

- **A SELECTION** (`tagpup.services.selection`) is `{"ids": [...]}` (at most 20,000; duplicates are one photo, an id with no photo
  is not in it) or `{"source": {"kind", "value", "recursive"}, "excluded": [...]}` (every photo of a library-view source but at
  most 20,000 excluded ones). A page that has Select all of a keyword and then deselects 300 photos sends the source and 300 ids,
  not 68,000. **One resolver** turns either into the ids that exist, in order, each once: an ids selection in the order named; a
  source in exactly `/api/library/ids`'s order (it calls the same `store.all_ids`, a test holds the two equal for every kind of
  source), read in **one read transaction** (`store.source_ids`: the dated photos and the undated are two statements, and a photo
  dated between them -- another bulk time shift, a sync -- would be listed twice or not at all by two snapshots; a test re-dates a
  photo between the statements and fails without the transaction). A selection of more than 200,000 photos is refused, naming how
  many it was, and the cap counts the source, not what is left after the exclusions.
- **THE TALLY** (`POST /api/library/selection/tally`) replaces the panel's "Not tallied for a library view": `{"total", "tags":
  [{"tag","count"}], "more_tags", "people": [{"name","count"}], "more_people"}`, alphabetical by `tag_sort_key`, at most 500 each (the
  most used kept, the rest counted). From `photo_tags` (by tree node: a tag no node holds is not counted, as the navigator's counts
  leave it out) and `photo_people` (names that differ only in case are one entry, each photo once), one grouped read over the
  selection. The ids go into a TEMP table of the read-only connection (a read-only connection may make one) and the photos are
  looked up by key from it (`sel CROSS JOIN photos p ON p.id = sel.id`: left to itself the planner scanned the whole library's index for
  one photo, 20 ms for one id, 1 ms now); a source is joined in SQL and its excluded ids taken out there. **Measured on photo_index
  itself, read-only** (`scripts/measure_bulk_jobs.py --scale`, 68,472 photos, 151,414 `photo_tags`, 80,060 `photo_people`; medians of
  3): resolve the whole library 23 ms; tally the whole library as a source 160 ms (872 tags, 413 people), minus 20,000 excluded 139 ms;
  20,000 ids 88 ms, 68,000 ids 304 ms, one id 1 ms. The tally is not capped at a job's 200,000.
- **THE JOB** (`tagpup.jobs.bulk_edits`; the work of a chunk is `tagpup.services.bulk_edit`). `POST /api/library/bulk/start` with
  `{"op": "tags"|"people"|"time_shift", "selection", "params"}` reads and checks the edit (`bulk_edit.prepare`: a tag the rules
  refuse, a person the tree files in two places, a shift of 0, nothing to do, a tag both added and taken off -- each a `400` with a
  sentence and nothing begun), resolves the selection on the server and returns `{"job", "total", "requested", "missing",
  "excluded"}` while a thread of the server does the work; `GET .../status?job=` is an in-memory read (no query of the library:
  a test fails if the status reads the library's run or the job's record), `POST .../cancel`, `POST .../resume`.
  - **Chunks of 25, the lock per chunk.** Each chunk resolves its ids to their rows' paths **at that moment** (a photo renamed
    meanwhile is found by id; one deleted is `skipped_missing`), takes `file_changes.exclusively()` for the chunk only and writes it
    by the machinery a single save and Add to all selected use: the photos' fields read, planned and committed in the journal, each
    file **read again before its write** (a file another program changed meanwhile is a conflict, reported and never overwritten), written,
    its row and the derived tables told in the same transaction that marks it done, the files read back. A job **adds and removes
    against each file's own tags** (`change_tags`), never a list; a person goes through `vocabulary.person_tag` (the one filing
    rule) and the leaf rule (`persons`: a file naming the person already is not written), no node of the tree is made; a time shift
    moves each date field the photo holds (`photos.date_shift_plan`, the folder shift's own plan), a photo with no Date Taken is
    `unchanged`. A held and a not-held photo would be written as ever (`libraries.split`), but a photo named by id has a row, which makes
    its folder the library's (`store.folders.holds`), so a bulk edit by id is always journaled; a row deleted between the chunk's
    lookup and its write leaves a file-only write, a race of milliseconds on a photo being deleted.
  - **Between chunks the job lets a waiting write in.** The lock is not fair: the thread that has let go of it can take it again
    before a waiter has woken. `file_changes.waiting()` counts the threads blocked for it and the job waits (at most 5 s) while it is
    above zero. A test makes a single-photo save wait in the middle of chunk 2 and shows it complete before the job's third chunk
    writes. **Honest limit of that test:** without the wait the save is not starved on this machine either (the job's own reads and
    state writes between chunks give the waiter time to wake), so the test fails the old design of one hold of the lock for the whole
    job, not the unfair re-acquire; the wait is the guarantee, not an observed need.
  - **What is counted.** Every photo of a chunk is exactly one of `changed` (a file written and read back), `unchanged` (holds what it
    was to hold, or no Date Taken), `skipped_missing` (no row, or its file is gone), `skipped_damaged`, or an **error entry** (the
    first 50 `{id, name, why}` are kept, every one counted in `error_count`). A photo that cannot be read, an unwritable file, a
    conflict: an error, and the job goes on. A photo on a **network share that does not answer** is an error too and is never sent to
    ExifTool (`bulk_edit._reachable`: a bounded stat; asked of ExifTool it would cost its five-minute deadline and then one more for each
    photo of the batch it is retried singly for). **Five chunks in a row in which nothing could be done** stop the job as `failed`, with
    the last sentence; **an exception that is not one photo's** (ExifTool that cannot start, the library gone) stops it as `failed`
    with the message. A chunk **refused as a whole** (a share holding a recorded-damaged photo did not answer; the roots changed) is
    that chunk's photos as errors.
  - **One at a time per library.** A second start is `409` with a sentence naming the one running (its kind and how far it is); the
    claim is in the library's `job_runs` as Verify's is (`bulk edit`), so another process is refused too (a test of two starts at once
    gives one `200` and one `409`). A server drain waits for a running job (`lifecycle.long_work`: "1 bulk edit(s)"). The job holds its
    Library, not the page's: the page may close or switch library and the job goes on, its status reachable.
  - **The Activity page** lists the run (`job_runs`, `outcome` running/done/failed/abandoned) with `what` -- "bulk tags: add 1 and
    remove 1 tag(s), 1,200 of 5,000 photos [done]", no tag or person named -- and the counts, rewritten at most every 2 s while it
    runs and at the end, never per photo. A cancelled job is `done` there (the table has no cancelled) and says so in `what` and
    `state`.
  - **The journal.** Each chunk is one journaled change of its held photos, named after the job (`bulk tags (job 12)`, `bulk people
    (job 12)`, `bulk time shift (job 12)`): **a job of 68,000 photos is about 2,700 entries in History**, each undoable by the existing
    mechanism and none by a job-level undo. Decided, not gold-plated (the owner said not to build undo): the lock is held per chunk, so
    a chunk is the unit that is planned, committed and can be settled after a crash; a change for the whole job would hold a plan of
    68,000 files open for hours. Collapsing a job's changes into one History line is a page-or-History task (9d-2 or later).
  - **Interrupted part-way.** The thread dies with its process and leaves a `running` row; the status says `abandoned` (the row's
    owner is not alive) with how far it got ("TagPup was closed before this finished: 1,200 of 5,000 photos were done."), and the
    next claim marks the row so. The chunk in flight when it stopped is settled by the journal's existing settle (planned files are
    written forward at the next write or start). **Tags and people are idempotent** (adding a tag a photo holds, taking one off that
    it lacks, changes nothing): start them again; a resume of one is `400`. **A time shift is not**, so its resolved list (`<job>.ids`)
    and its record (`<job>.state.json`: counts, the first 50 errors, `done` -- the photos settled -- and `inflight` -- the end of the
    chunk about to be written, kept BEFORE it is written) are kept in `cache/<library>/bulk/` (`Library.bulk_jobs`,
    `tagpup.files.job_files`: atomic replace, fsync, a damaged file reads as none; nothing the rows or the files
    depend on). **They are kept only while a resume is possible** *(review, #580)*: the state file can hold an edit's tags and people and the
    names of files that failed, so a tags or people job removes both files when it ends (done, cancelled, failed), and a time shift when it
    ends done; a time shift that is cancelled or failed keeps them, and a job's files are swept together by its last activity (30 days when a resume is possible, 7 otherwise; see below) when the next job starts.
    A finished job's status then comes from the library's record of the run (`job_runs`: `state`, `op`, the counts; the first 50 errors are
    lost with the file). Nothing a person or an exception wrote is kept in `job_runs.note` or the state: a failed job's message is a fixed
    sentence and the exception's class name ("It could not go on (FileNotFoundError); the server's log says why."), the detail is logged;
    each error's `name` and `why` are cut at 200 characters.
    **Resume** (`POST /api/library/bulk/resume`, a time shift that is `abandoned`, `cancelled` or `failed`) *(rewritten, #577, #578; the journal rule below, #595-#634)*: the state names the journal change of the chunk in flight (`journal_chunk`, cleared before each chunk, set once the chunk is planned and before its first file is written); a resume refuses (`400`) when the journal no longer holds it (or holds another operation's under that id, or pruned it), refuses (`409`, could not settle) while any file of the chunk is still planned, writing, or in a change being undone, and takes over its own job's changes whoever owns them but never an undo. A chunk the owner undid is shifted again (said in the resumed job's `message`); an undo begun in another process between the resume's reads is not seen. A power loss between volumes is accepted. The changes of a bulk time shift that has a record to resume from (its list of photos and its state, kept 30 days from its last activity, and while it runs) are never pruned, whatever the age: they are the only account of which photos of its last chunk were shifted, and a resume without it could shift them twice. Once the record goes their changes are pruned like any other. 
    the record is the state file plus the journal, **never this process's memory of the job** (two processes share a library: the installed
    app and the repo-run app). The state file carries a **sequence number** (`seq`, one more on every write). A resume takes the claim
    first (outside the module lock: it may ask the system who is alive, which takes seconds, and status and cancel never wait for it), reads
    the state under it, settles the journal and asks it -- the one record of what was written -- which photos of the in-flight chunk a
    change **named after the job** left `done` (those are not shifted again and are counted `changed`; `inflight` is carried into the resumed
    job, and the count credited from the journal is not written into the state until the chunk is finished, so a second resume cannot count
    them twice), checks that the file's `seq` is still the one it read (else `409` "Another TagPup process moved this job on: reload and look
    at it again.", nothing begun), and ONLY THEN registers, writes and starts the job. If the settle (or anything before the start) fails --
    the library busy under another process's long write -- the claim is given back, the state file and the job are exactly as they were, and
    the route answers `409` "Could not settle the last chunk that was being written: try again in a moment." (never `200`); the next Resume
    starts from the intact record. `status` of a job that is over in this process but has moved on in the file is the file's. A test crashes the process at the 30th write, between a file's write and its row, and before a chunk planned anything, and
    after each resume every photo's file was written exactly once. `GET status?job=&ids=1` adds `shifted_ids`, from the same journal:
    exactly which photos a job shifted. **Deviation from the brief:** it said refuse a resume unless abandoned/cancelled; a `failed`
    shift (ExifTool gone mid-job) is resumable too, since otherwise its first half could never be completed without shifting it again.
  - **A cancel** stops after the chunk being written (a test: 50 of 60 done, the 3rd chunk never starts) and the report says how many were done; a cancel after
    the end is a `200` with `cancelling: false`.
- **Measured** (`scripts/measure_bulk_jobs.py --run --photos 500`: 500 small real JPEGs, the real ExifTool, rows as the indexer records
  them, the app called in-process through Flask's test client in a temporary TAGPUP_HOME, deleted afterwards; the machine was not
  quiet):

  | the action | result |
  |---|---|
  | (a) a tags job over 500 photos (add one, take one off) | 26.9 s: **18.6 photos a second**, a chunk of 25 is 1.34 s; 500 changed, 0 errors |
  | (b) a time-shift job over the same | 21.7 s: **23.0 photos a second**, a chunk 1.09 s |
  | (c) a single-photo save made during (a), 18 of them at 300 ms intervals | median 1,262 ms, longest 1,580 ms: it waits for the chunk being written and does its own work (a chunk is 1,343 ms) |
  | (d) the status request, polled every 20 ms while the job ran | median 2.8 ms, p95 17 ms (the process is busy writing), longest 49 ms; no query |
  | (e) a second start while one runs; cancel to stopped | `409`; 599 ms (the rest of the chunk) |

  So **68,000 photos is about an hour** at this machine's pace, in chunks of a second and a third each. What a photo costs is the
  per-file precondition the brief asked to keep (each file read again before its write) plus ExifTool's write and the read back; a
  longer chunk would raise throughput a little and the wait of a single save with it. Not tuned.
- **How it fails**, each a test (`tests/test_selection.py`, `test_selection_tally.py`, `test_bulk_edits.py`, `test_bulk_edits_real_exiftool.py`
  -- the last three with the real ExifTool): a restart mid-chunk, between a file's write and its row, before the chunk planned anything
  (resume writes every file once); a cancelled and a failed shift resumed; resume refused for a job that is done, running, a tags job, or
  whose list is lost; two starts at once; a photo deleted, renamed or replaced while the job runs; a file edited by another program
  between the plan and the write; a damaged photo; an unreadable file; a write that fails; 55 errors (50 named, 55 counted); ExifTool that
  cannot start; five failing chunks; a chunk refused as a whole; a share away (some photos; all of them); a library at schema 18; the roots
  gate; this PC only; a library switched meanwhile; the status of a job the process did not run; a person filed in two places, named by
  a path, already named by their leaf, taken off every way the tree files them; a blank name; a tag the rules refuse (before anything
  starts); a time shift of a photo with no Date Taken and of one holding both date fields; selection of 1, of ids nobody has, of
  duplicates, above the cap, of a source minus more than it holds.
- **A share that stops answering in the middle of an ExifTool command** *(was "could not be made safe"; #581)*. The session's deadline is 300 s
  and `field_values.read` retried a timed-out batch a photo at a time, each with its own 300 s: 2 h 10 min for one chunk of 25, with the lock
  held. Now `field_values.read` treats an `ExifToolTimeout` (or a dead process) as the batch's answer -- every photo `Unreadable("ExifTool
  did not answer in N s")`, nothing retried singly (a batch refused by one bad path is still retried singly) -- and a bulk chunk opens its
  OWN `ExifToolSession(timeout=60)` (`bulk_edit.CHUNK_TIMEOUT`), started before the lock is asked for, closed at the chunk's end, wrapped so
  that **once a command has timed out every later command of the chunk answers the same at once** (pyexiftool would start its process again
  and wait a minute more for the next file). A chunk's worst case is about a minute, counted as errors, nothing written to a file whose
  read timed out, the lock released; five such chunks in a row stop the job. The session is not reused across chunks (starting one is
  small against a chunk of a second or more); the held and single-photo paths are unchanged. `settle` inside a chunk still uses the
  default session, which matters only when a crash left changes to finish.
  Also in this pass: a shift that moves a date out of range (before the year 1, after 9999) is that photo's **error** ("the shifted date
  would be before the year 1") and not `unchanged`, only for the job (`dates.shifted_strictly`; Camera Time Shift still leaves such a
  photo alone), and `validation` refuses a shift of more than 100 years (52,560,000 minutes) up front; the journal's question
  (`file_journal.photo_ids_done`) drives from `changes` and finds each change's files by `idx_change_files_change` (the plan was a scan
  of every `change_files` row; no migration was needed; 67,500 rows in 2,700 changes asked in well under 0.5 s, a plan test holds it).
- **The durable-record principle** *(follow-up review, #583-#589)*. A time shift is the one operation that cannot be done twice, and every
  way it could be done twice was a way its progress record was missing, stale or false. The rule now: **nothing is written to a photo file
  until the record that lets a resume know about it is durably written, and a record is never older than the files.**
  - **The record before the chunk is mandatory** (#583). `inflight` (the end of the chunk about to be written) is written before the chunk's
    first file, tried 5 times with a doubling wait (0.1 s) -- and `job_files` itself replaces the file with 5 tries of its own for the
    Windows error of replacing a file another handle holds open (the other TagPup process's status poll, Defender, the search indexer);
    if it still cannot be written the job stops as `failed`, resumable, with "It stopped before writing the next chunk because it could not
    record where it had got to; nothing of that chunk was written", before the chunk's first file. The write AFTER a chunk is progress and
    best-effort: if it fails, the older record still covers the chunk (the journal says what it wrote) and the next before-chunk write is
    the mandatory one. Readers (`read_state`) open the file only for the read and ask again a few times when it is being replaced; a
    running job's status is memory and reads no file. A write that failed does not use up the sequence number (#589).
  - **A command that stalls leaves the chunk's files UNKNOWN until they are read** (#584). After a timeout in a time shift's chunk the
    chunk's session stays dead; the job then opens a fresh session (`CHUNK_TIMEOUT`, 60 s), under the same lock, and reads every planned
    file that is not recorded done (`file_changes.reconcile`): one holding the shift is DONE (its conflict row recorded done, counted
    changed -- a conflict for a file that holds the target is not a conflict), one holding what it held is NOT WRITTEN (its row taken out
    of the change; the error says "read afterwards, the file was not shifted"), one holding neither is a conflict ("could not confirm
    whether it was shifted"), and one that cannot be read is UNKNOWN: the job stops as `failed` with the chunk still IN FLIGHT in its
    record and counts nothing of it. A resume reads the journal's conflict rows of the job for the chunk in flight the same way (and
    refuses, changing nothing, if it cannot), so it never plans again from a file that may be shifted. Tags are idempotent and are not
    reconciled.
  - **All or nothing around the thread** (#585). `start` and `resume` register the job, write its files and start its thread inside one
    block: if anything raises before the thread runs, the job is unregistered (the job it replaced is put back), the claim's row is
    deleted, the files a start made are removed, and the caller gets a `409` "The bulk edit could not be set up (OSError): nothing was
    changed. Try again in a moment." -- never a registered job with no thread, which would refuse every start, ignore a cancel and hold
    an update's drain.
  - **One identity across runs** (#586). The job id is its first run's id; every later run of it carries `job` in its counts, and the
    status resolves the id to the LATEST run of the chain (`_chain`), so a resumed job that finished is `done` and not resumable after a
    restart, from the other process, or once it has left the 20 kept in memory. `resumable` is true only where a resume would work:
    the list of photos AND the state both exist (`has_record`); a job whose record is gone is never promised a Resume. A process dying
    between removing the record and ending the run is reported as `abandoned`, not resumable, with "Its record is gone, so it cannot be
    resumed (History lists what was shifted)."
  - **The sweep goes by the job's last activity, and a job's files go together** (#587). A job's `.ids` and `.state.json` are one unit,
    aged by the newest of them (the state file is rewritten whenever the job runs or is resumed), kept **30 days** when both exist (a
    time shift that can be resumed) and **7 days** otherwise; an expired resumable job's last run in `job_runs` is amended `state:
    "expired"` and the status says "Too old to resume: its record was removed after 30 days." (`state` `expired`, `resumable` false,
    Resume `400`).
  - **The library keeps a resumable job's first run** (#588): `job_runs` keeps 50 runs per job, but never deletes a run whose id is a
    job a resume can still carry on (`job_runs.finish(keep=...)`); a resume that is refused deletes the row its claim made instead of
    ending it as a failed run, so refusals do not use the rows up (sixty refused resumes leave the job resumable).
  - **Limits stated** (#589). A command of a bulk chunk has **60 seconds** (`CHUNK_TIMEOUT`): one very large file on a very slow share can
    never be written by a job -- it fails with "ExifTool did not answer in 60 s", a clear error, not a hang. The claim's refusal says
    "in another TagPup process" only when the claim is another process's.
- **What could not be made safe.** (1) (see above: made safe.) (2) A tag no tree node holds is not in the tally. (3) A
  time shift of a photo whose row vanishes between the chunk's lookup and its write is written to its file without a record; a resume
  cannot tell it (milliseconds, on a photo being deleted).
- **What 9d-2 must know.** (a) The page sends a SELECTION (ids, or the view's source and the ids it excluded) and never paths; `selectInLibrary`
  goes, a Select all of 68,000 is instant. (b) `start` -> `{job}`; poll `status` once a second (cheap; do not poll a `done`/`cancelled`/`failed`/`abandoned`
  job); show `done` of `total`, `changed`, the counts left out (`skipped_missing`, `skipped_damaged`) and `error_count` with the first 50
  `errors` (`id`, `name`, `why`), `eta_seconds`, and the `message` (a sentence for cancelled/failed/abandoned). `409` on start names the job
  running: the page should offer to show it. `resumable` says when a Resume button applies (a time shift that stopped); a tags/people job that was
  abandoned or cancelled is offered "Start again". (c) The server clears the folder view's cached scans as the job writes
  (`forget_scans`), but the page must refresh a library view's navigator counts (`navigatorCountsChanged`) and the cards it holds when
  the job ends, and re-read an open photo (a record read before the job names the old stamp: the next save is refused as changed on disk,
  which is right). The job is not in `queuePhotoWrite`: single saves interleave between its chunks, so the page need not wait for it, but it
  should not offer a second bulk edit. (d) The confirmation ("Add X to N photos?") can use the tally's `total` or the start's `total`
  (the photos that exist now, which can be fewer than the page thought: `missing`). (e) The tally route is what fills the panel for a
  selection; it is not capped at 200,000 and its lists carry `more_*`. (f) A person's name is sent bare (`params.add: ["Rowan Thackeray"]`
  for op `people`, or a full path, `People/Friends/Rowan Thackeray`, for a person filed twice); a refused name comes back as a `400` sentence.
  (g) History will show a job as one change per 25 photos.

### Phase 9d-2: editing across folders -- the selection by id, bulk edits through the job, the strip, the tally *(built 2026-10-03; branch `arch/phase-9d2-bulk-page`)*
The page for 9d-1's server. Nothing is migrated. The one server addition is `GET /api/library/bulk/current` (below). The owner sees: **Select all** of
the whole library selects at once and asks nothing; a Shift-click across 20,000 photos the grid never held selects them at once; the selection panel lists
the tags and people of the selection with their counts; **Add tag**, **Add person**, the **x** of a tag or person in that panel and **Shift Date Taken** each ask
once by name and count and then run as a job that has a strip under the header -- a bar, "done of total", what was changed, left as it was, missing, damaged
and refused, the time left, **Cancel** -- which is still there after the view changes or the page is reloaded.

- **A library view's selection is photo ids** (`selected.js`; `state.library.sel`, so it is the view's and goes with it). Two shapes, one at a time:
  `{mode: 'ids', ids: Set}` -- the photos picked -- and `{mode: 'source', excluded: Set}` -- every photo of the view's source but those. The count is `ids.size`, or the
  view's total less `excluded.size`. **Select all** assigns `{source, excluded: {}}`: no request, no id, 68,472 photos in 57 ms to the count shown and every card painted
  selected (it was 5.9 s). A click toggles an id (in `source` mode: membership of `excluded`). A **Shift range** (Shift-click, Shift+Space, Shift+arrows) is a loop over
  `lib.ids` between two indexes -- the card need not be there, nothing is fetched -- and **stepping a Shift+arrow back toward where the run began now takes
  the photos off** (the 9c limit is lifted for a library view; a folder view's keys are as they were). **Invert** swaps the shapes (picked ids become the excluded ones and
  back) while the list swapped is at most 20,000; past that it says "Too many to invert: use Select all and deselect." and changes nothing. `state.selectedThumbnails` and
  `selectedKeys` stay empty in a view (every reader audited: the folder's panel, Smart Rename and Suggest work on a folder and read nothing else); cards show
  `isIdSelected(photo.id)`. The selection is cleared by every view change (9b-1's rule), kept by Refresh view (ids the refreshed view no longer holds are let go:
  `reconcileIdSelection`) and by edits; a photo found deleted when its card is asked for leaves the view and the selection (`dropPhotos`).
  `selectInLibrary`, `pathsOfIds`, `idsBetween`, `cancelLibrarySelection`, "Selecting N of M..." and "Select all N photos?" are gone; so are `leaveOutMissing` and the page's own
  "N photos are missing" count: the server skips and counts a missing photo (9d-1) and the strip's summary says how many.
- **What a request can carry** (`selected.js selectionRequest`, decided from the counts, nothing built until it is sent): `{ids}` for at most 20,000 picked, in the view's
  order; `{source, excluded}` for at most 20,000 excluded; and -- **added here, not in the brief** -- the same selection said the other way round when only that way fits: 50,000
  picked of 68,000 is sent as the source and the 18,000 left out, a Select all less 50,000 as the 18,000 ids that remain (only when the page holds the whole order). What fits neither
  (30,000 picked of 68,000: not 30,000 ids, not all but 38,000) is **said in a sentence in the panel as soon as it is so** ("30,000 photos are selected and 38,000 are not: a bulk
  edit can name at most 20,000 photos one by one, or everything in the view but 20,000. Select fewer, or Select all and deselect.") and again, as an alert, if an edit is tried;
  more than 200,000 photos selected is refused the same way. Nothing is asked of the server for any of these. **Honest limit:** that last case is a real hole between the two
  shapes the server takes -- a selection in the middle of a large view, between 20,000 and all-but-20,000, cannot be sent. The way round is Select all and deselect, or the
  navigator to a smaller view.
- **The edits** (`bulk-edit.js`; the words are `bulk-words.js`, plain functions). Each is asked by name and count, always in a view even for one photo -- "Add Trips/Lighthouse
  to 3,412 photos? This changes the photo files and takes about 4 minutes. It cannot be undone as one step; remove the tag to reverse it." (the time at an assumed 15 photos a
  second until the job reports its own; 9d-1 measured 18 to 23) -- and over 5,000 a second time, "This is 67,997 photos. Continue?". **The selection is read before the question
  and that is what is sent**: a click made while a placement dialog is open changes nothing that was asked; a second click on Add while the first one's question or dialog is open
  asks nothing (`state.bulk.asking`: four rapid clicks and Enter are one question and one request). A name is resolved (`resolveTagOrPerson`, which may make a node of the tag tree
  and may ask where a new person goes) only **after** the question, as in a folder, and a person is sent as the path it resolved, op `people`. The pills of the tally: **x** removes the
  tag or person from the whole selection (op `tags` or `people`, `params.remove`), the arrow adds it to every photo (shown only when the count is below the total). **Shift Date Taken**
  in a view is the minutes and a direction (Later or Earlier; the camera filter is not offered), checked beside the field: 0, negative ("Enter the minutes as a positive number and
  choose Earlier or Later."), a fraction, empty, and more than ten years of minutes (5,256,000; **a limit of the page's own, not a rule of the server's**: a mistyped zero otherwise
  asks ExifTool to move a date out of the file's range) are each a sentence and nothing is asked. A note on the panel says both Date Taken fields of a photo move and one with none is
  left as it is. Smart Rename stays off in a view (a name is per folder) and so does Ctrl+D.
- **The job's client** (`bulk-job.js`; state is `state.bulk`). `startBulk` POSTs `{op, selection, params}`; **one bulk job at a time**: while one runs (or one is being started) the view's
  Add buttons, Apply Time Shift and the tally's pills are disabled with the tooltip "A bulk edit is running (see the strip at the top)..." (`lockBulkControls`; a folder's own bulk
  writes are another route and are never disabled), and a `409` from the server shows its sentence in the strip with **Show it**, which asks which job runs and picks it up. A start whose
  answer is lost (the network dropped after the server took it) says so and asks what runs, once, two seconds later. **The status is polled about once a second** (`POLL_MS`;
  every 5 s while the tab is hidden, and at once when it is brought back); a request unanswered in 15 s is a miss and is asked again; after 3 misses in a row the strip says "TagPup
  is not answering (...). The edit goes on if TagPup is running; still trying."; the pause between tries grows to six times as long; **after 40 in a row it stops** ("has not answered for a
  long time") and offers **Ask again** -- never for ever; the first answer clears the message. A `404` for the job stops the asking at once. The status of a finished job is never polled. **Cancel**
  is one request however often it is pressed ("Cancelling..."), and the job stops after the chunk it is writing; pressed as the job ends, the server's `200` carries the end and the strip shows
  that. **Resume** (a time shift that was cancelled, failed or abandoned: `resumable`) calls `/api/library/bulk/resume` for the same job; **Start again** (a tags or people job that
  was cancelled, failed or abandoned) sends the same selection and edit it started with, kept in memory -- so after a reload, when the page no longer knows the edit, the strip says what
  was done and "Select the photos and make the edit again to finish it: adding or removing what a photo holds already changes nothing", with no button to a guess.
- **Finding it again** (`GET /api/library/bulk/current`, new: `tagpup.jobs.bulk_edits.current`). The job is the library's: the strip is under the header, not in the view or the folder,
  and it survives opening another view or going back to the folder. The page asks as it starts and as a view opens (one question when they come together, none within five seconds of
  the last); it answers `{job: status}` for the job running (here or in another process), else the latest that stopped part-way -- cancelled, failed, abandoned by a restart -- if
  it began within 30 days (the cache folder sweeps its list of photos then), else `null`; `done` and `expired` are never offered. A stopped job that was **Dismiss**ed is not offered again to
  this browser (`localStorage`, per library). A different library's page asks under its own name: it cannot show this one's strip. A read that fails shows nothing and logs a warning
  (the strip is an offer; a second start is refused by the server anyway).
- **When a job ends** (`bulk-job.js jobEnded`, once for each end): the status line says the summary -- "Added Trips/Lighthouse to 3,380 photos; 32 missing on disk were skipped; 0
  errors." (a time shift: "Shifted Date Taken 90 minutes later in 4 photos; 1 with no Date Taken was left as it was; 0 errors.") -- in the error colour if the job did not finish
  or had errors; the navigator's counts are read again at once; **every folder scan this browser kept is forgotten** (`forgetFolderCaches`: they name the old stamps and tags); the
  cards in the window are asked for again (`refreshHeldCards`: their `thumb` carries the file's new stamp, and `stale` is what the file is now; the others are let go and asked for when
  scrolled to); the photo in the details panel is read again unless it has edits of its own (`reloadChangedPhoto`; else the next save is refused as changed on disk, which is right); and the
  selection is counted again. The strip keeps the sentence, **the first 50 errors** (`<details>`: file name, why; the rest "N more, not listed"; long names wrap) and the counts until
  **Dismiss**. A job is not in `queuePhotoWrite`: single saves interleave between its chunks (9d-1). "Shown on the Activity page too" links to it.
- **Reading it** (a11y). The strip is a labelled region with a native `<progress>`; the buttons are buttons; it never takes the focus when it appears (Dismiss moves the focus to the grid, not
  to the top of the page); a separate polite live region is told at the start, at each tenth, and at the end -- 25 polls with the same answer say nothing.
- **The tally** (`tally.js`; `POST /api/library/selection/tally` from 9d-1). 250 ms after the selection stops changing the selection is counted on the server; the two lists say
  "counting..." meanwhile (never the last selection's chips); a request the selection has left behind is aborted and an answer that is not the newest, or is for a view that has closed,
  is dropped; none selected asks nothing; alphabetical by `compareTagNames`; at most 500 each with "N more, not listed"; names are text, never markup. A selection that cannot be sent
  says "Not counted: see the note above." (the whole library may be counted: a tally is not limited to what a job takes). The folder view's panel is its own and is untouched.
- **Measured** with `scripts/measure_bulk_page.py --run` (plan without `--run`; `--code-root <git archive of the trunk> --only abc` runs only (a) to (c) on the trunk, the baseline): a
  sandbox copy of photo_index (68,472 photos) and a small library of 500 JPEGs of its own, headless Chromium 1600 x 1000, a fresh browser for each, 3 rounds, thumbnails answered by the browser's
  routing, dialogs accepted. **The trunk has no selection by id**: its Select all, Shift range and Invert fetch the card of every photo for its path (`selectInLibrary`), so (a) to (c) are the
  same clicks on both. The machine was not quiet; run to run these move by up to 40%.

  | the action | trunk (`selectInLibrary`; median; range) | this branch |
  |---|---|---|
  | (a) Select all of 68,472: click to the count shown and every card painted selected, main thread idle | 5,858 ms (5,601-7,501); JS heap 61 MB | 57 ms (46-69); heap 6.9 MB |
  | (b) Shift-click from the first photo to the 19,948th (the grid scrolled there; the cards between are not held) | 1,588 ms (1,463-3,492); heap 47 MB | 49 ms (41-59); heap 10 MB |
  | (c) Invert of that (68,472 less 19,948 = 48,524) | 6,316 ms (5,433-6,972) | 63 ms (51-458) |
  | (d) the tally panel for all 68,472 selected: click on Select all to the tags and people painted | no tally | 731 ms (664-768): 250 ms is the wait, the request 224 ms (211-320; a 34 KB reply, 500 tags and 413 people), and about 250 ms after the reply is not attributed (913 chips, 2,779 nodes; no task over 50 ms) |
  | (e) a tags job over 500 small JPEGs, the real ExifTool: click on Add to the strip shown / to the first progress shown | no job | 66 ms / 2,136 ms (the first poll at 1 s, and the first chunk of 25 takes 1.3 s) |
  | (e) the page while it polled: 28 status requests in 28.7 s (17.4 photos a second) | | longest task 0 (none over 50 ms), the main thread busy 1.2% of the time, 12.7 ms of it per poll (everything the page did) |
  | (f) the strip when the server reports 3,000 errors (50 named): the answer to the 51 rows painted | | 1.9 ms; 120 nodes in the strip; the longest task of the page's life 57 ms (its start, not the strip) |

  The sandbox was deleted afterwards. What the numbers do **not** show: a browser with a GPU and an extension or two; a library on a network share (the job's cost is the server's: 9d-1); and
  the (d) cost after the reply, which is unattributed.
- **How it fails**, each a test (`tests/frontend/library-selection.test.mjs`, `bulk-edit.test.mjs`, `bulk-job.test.mjs`, `library-tally.test.mjs`; `tests/test_bulk_edits.py` for `current`):
  Select all of 68,000, 3 excluded, Add tag (the request is the source and the 3 ids, never ids or paths, and not the folder's route); a range across 20,000 unloaded cards (no card request); invert
  at exactly 20,000 and at 20,001; 30,000 picked, more than 20,000 excluded, 60,000 of 68,000, a Select all less 50,000 and more than 200,000 (sentence or the other way round, no request); the
  question refused, the second question refused, the selection edited while the question was open, a tag the rules refuse, nothing selected, four rapid clicks and Enter; time shift 0, -5, 1.5, empty,
  more than ten years, exactly ten, earlier, the both-fields note, a folder's panel unchanged; the pills (alphabetical, tags with markup in them as text, a person, the arrow only below the total); a
  start while one runs (controls disabled with a tooltip, the 409 sentence, Show it, a folder's controls not disabled); the strip's bar and counts, the ETA at 15 photos a second and then the job's own,
  once a second and every 5 s hidden, no announcement for 25 identical polls, the focus left where it was; the end (summary, polling stops, navigator counts, cards, folder scans, tally), every photo
  missing, a time shift's summary, 3,000 errors with long names in 51 rows; Cancel twice, after the end, and unsent; three misses, an answer that never comes, giving up and Ask again; a restart
  (abandoned, Resume, Start again); a page reloaded mid-job, another library's page, a dismissed job, a start whose answer was lost; the tally for 1, 68,000 and 0 selected, three quick clicks (one
  request), a stale answer dropped, counting..., over 500, a failure, a view left meanwhile. The server's: `current` with no job, a running one, an abandoned one after a restart, a resumed one,
  a cancelled one and one older than 30 days.
- **Decisions made, not copied.** (1) The selection lives on the view (`state.library.sel`), not in `state`. (2) The 20,000 limits are enforced when a request is made, not when a selection is
  made (a Shift range of 30,000 is allowed to exist; Invert refuses past 20,000 as decided), and the other way round is used when only it fits. (3) A question is asked even for one photo. (4) The
  ten-year limit of a shift. (5) 15 photos a second. (6) The poll's 15 s, the 40 misses and the six-fold pause. (7) `current` offers a stopped job for 30 days and a Dismiss is remembered per
  library in the browser. (8) Every folder scan in this browser's storage is forgotten when a job ends. (9) The panel's "missing" note became the limit note (`selection-note`). (10) The page
  sends a person as the path `resolveTagOrPerson` found, not the bare name, so the placement dialog a folder shows is shown here too.
- **What could not be made safe.** (1) The hole between the two shapes (above). (2) A job started from a selection read just before another program changed the library: the server resolves the
  source when it starts, so "3,412 photos" in the question can be 3,400 when it runs; the strip says what was done. (3) Nothing on the page can undo a job as one step (the question says so); each 25
  photos is a change in History (9d-1). (4) A page reloaded after a tags job was cancelled cannot offer Start again (it does not know the edit). (5) The 9d-1 limit on a share that stops answering
  mid-command (a chunk can stall much longer than a chunk should; Cancel waits for it) is the server's.
- **What 9e builds on.** A search is a source: `selection.js`'s two shapes already name "the view's source but these" and "these", and the `source` object of `selectionRequest` is the one place that says it
  (`{kind, value, recursive}` of `/api/library/ids`); a search view needs a new `kind` in `library-source.js` and the same in `tagpup.services.library_view.source_of`, and Select all, ranges, the tally, every
  bulk edit and the strip work on it unchanged. `state.nav.followed` is still where "within what the navigator has selected" is read. `lockBulkControls` and `bulkBusy` are the one place that says an
  edit may not start. *(The navigator's union, `any_of`, is the shape a search's `any_of` is: see "Phase 9, the owner's review" below.)*

### Phase 9, the owner's review: several rows at once, People by branch, one sort *(built 2026-10-04; branch `arch/library-review-2`; findings #671-#673)*
The owner installed phase 9 and asked for three things of the navigator (#671-#673; #668-#670, #674 and #675 are another branch's). Server, page
and one index-only migration (22).

- **A source may be a union** (`any_of`, #672): `{"kind": "any_of", "value": [source, ...], "recursive": false}`, each a source of
  the existing kinds -- none a union, at least one, at most 1,000 (`library_view.MAX_MEMBERS`; Refused with a sentence past it: the
  address of more would outgrow what the server reads) -- the same source twice is one, a union of one is that source. Two kinds
  are new because a navigator row holds them alone: `keyword_only` (a keyword's node without the nodes under it) and `year_other`
  (the photos of a year whose date names no month of it: the year less its twelve month ranges, so a year is exactly its months and
  this). **One statement** over `photos p` (`store._union_scope`): the members gathered by kind -- the folders' ids (a folder with its
  subfolders is its own id and every folder under it, by `folders.parent_id`, read once), the tag tree's node ids (`derived.under` or the
  node alone), the people's spellings (one pass of the name index for the whole union, not one a person), the years -- each one
  `IN (...)`, the months and the years' "Other" OR'd; the clauses are OR'd as a **balanced tree**, since SQLite refuses an expression
  nested 1,000 deep and a chain of ORs is nested as long as it is (a test holds 1,000 months). A photo in two members is one row and
  counted once; a member that names nothing (a keyword deleted since the address was written) adds nothing. The walk under a
  folder with its subfolders keeps its own set of folders walked, so a child folder named alone before its recursive parent does
  not stop the walk at it (#695: every order of the members gives the same photos, a test). It is a source like
  any other: `source_of` reads it (the JSON text of a query's `value`, or a list in a body), so `/api/library/ids`, `/view`, a
  selection by source (`selection.read`), the tally and every bulk edit take it unchanged; the #607 widening notice compares the
  job's total with the page's count as for any source.
- **A union travels in a body** *(review, #696)*. The 1,000-source count bounds the work, not the length: 1,000 folders of
  photo_index are a 145,613-character address, and a share's longer paths pass the 262,144 bytes Waitress reads of a request's
  first line and headers. So `/api/library/ids` and `/view` also take a POST of the same words as JSON (`_source_asked`), and the
  page asks every union so (a single source stays a GET of its address's words). The page's own address still holds the union, for
  Back, Forward and a bookmark, and that address is read by the same server: the page refuses, with a sentence and no request, a
  selection whose address would pass 100,000 characters (`MAX_ADDRESS`, `addressTooLong`), the limit that bites first for long
  paths (a test: 600 share folders refused, 300 accepted and posted). An address made longer by hand is refused by Waitress before
  the page loads.
- **How 9e extends it.** A search is `{"kind": "search", "value": {"any_of": [...], "all_of": [...], "none_of": [...], "words": "..."}}`:
  `any_of` is exactly this list and compiles to this clause (`_union_scope`); `all_of` is the members' clauses joined by AND
  (each member's own `p.id IN (...)`, not gathered, since AND of a gathered IN is "any"); `none_of` is `AND NOT (<its any_of
  clause>)`; `words` the FTS match. The navigator's selection is then the `any_of` of a search (or its scope: "within what the
  navigator has selected" is `state.nav.followed`, which is this union), and nothing in the page's selection, tally or bulk
  edits changes: they carry `{kind, value, recursive}` whatever `value` holds.
- **Orders** (#671): `order=taken | taken-desc | name | name-desc` on `/api/library/ids` and `/view` (`library_view.order_of`; another is
  `400`). By Date Taken the photos with none come **after the dated ones in both directions**, by id in the order's direction (newest
  first: the undated from the highest id down). By file name: what follows the last separator of the row's path, **without case**
  (`NOCASE`, ASCII), ties by id in the order's direction; there is no undated phase. The keyset token of an order other than the
  default carries the order and is refused in another (`400`); the default's token is the 3-part one it always was. The page reads
  the order once with the ids, so a sort is a new view: Back returns to the order before.
- **Migration 22** (additive, index only, touches no table, blocks no undo): `idx_photos_name` on `(<file name> COLLATE NOCASE, id)`,
  the file name an expression of built-in deterministic functions (`library_view.name_sql`: `substr(path, length(rtrim(path,
  replace(replace(path, '/', ''), char(92), ''))) + 1)`), so SQLite keeps it and no writer of a path has to know of it -- chosen over a
  column in `photo_meta`, which every writer of a path, the doctor's checks and the migration's backfill would have had to keep.
  Checked by hand against #464 (nothing checks a migration's declared `touches`): a test holds that 22 adds exactly one index on
  `photos` and no column or trigger, that every table's rows are unchanged, and that a change journaled at 21 is still undoable. An
  order by name reads `photos p INDEXED BY idx_photos_name`, walking the index in order and testing each entry for the source (5 to
  25 ms on photo_index for the sources then measured -- *not* for a small range of another index, which walked the whole library
  for each page until #722: see the owner's second review; sorting 15,000 photos by a name computed for each was 130 ms, the whole library
  630 ms); without the index (a library not yet migrated) it is the sort, the same order. On the sandbox copy migration 22 took 0.7 to 1.1 s.
- **The navigator** (`navigator.js`, `navigator-model.js`, `navigator-tree.js`): every tab is a multi-selectable tree
  (`aria-multiselectable`). A click selects the row **and every row under it** (shown selected) and opens a closed branch; Ctrl-click
  adds or takes away a row and the rows under it; Shift-click the rows drawn from the last row picked (Ctrl+Shift adds them); Ctrl+A
  every row drawn; the keys Space/Enter, Ctrl+Space, Shift+Space. **A row's own photos stay when a row under it is taken off**
  (decided): a row holds photos of its own -- a folder's own, a keyword's node alone, a person, a month, a year's "Other" -- and, with
  the rows under it, its whole; `compress` turns the rows selected into the fewest sources (a row with every row below selected is its
  whole: Select all of the folders is the one top folder with its subfolders), so the year 2021 with March taken off is eleven months
  and `year_other`, and Trips with Trips/Coast taken off is `keyword_only` Trips and the other branches. A branch of people or the junk
  years hold nothing of their own: they are the rows under them, and are shown selected when all of those are. **The selection is the
  view's source**, held in the address (`?view=any_of&value=<JSON>&order=`), so Back, Forward and a bookmark restore it, and each tab
  draws its rows of it (`selectedRows`). Decided: **switching tabs keeps the selection** (and the grid); **Ctrl-click in another tab
  adds its rows to the same union** (a month and a person), a plain click or Shift-click anywhere starts again; **a row the filter hides
  stays selected** and the note says how many are hidden; a click that would leave **nothing** selected leaves the selection as it is and
  says so; more than 1,000 sources is a sentence and no request. The "Other" row under a year now opens the year's undated-by-month
  photos alone (`year_other`), not the whole year. The filter box was already there on every tab (9c): typing "July" in Dates lists
  each year's July, and Shift-clicking the first and the last shows every July.
- **People by branch** (#673): `/api/library/navigator?section=people` adds to each person `group`, the tag of the branch above their
  node -- by `person_ids`' one rule (a leaf `has_face` node, not a root, the only one called that name; a branch is never a person) --
  and `groups` (`tag`, `name`, `parent`, `count`: every branch above a person, the photos naming anyone under it, each photo once,
  one pass of `photo_people` grouped by photo in SQLite and rolled up the tree as the keywords' counts are) and `unfiled` (photos naming
  someone with no such node). The tab is a tree: branches nested as the tree nests them, alphabetically, branches before people,
  open at first; those not filed under "Not filed in the tag tree" at the end; a library that files no one is the flat list it was.
  A branch's row selects its people (a union of them). Counted read-only on photo_index: 413 people, 404 filed under 12 branches (two
  deep at most), 9 not filed (117 photos); kr-track: 75 people, 62 filed under 1 branch. The read is 46 ms in-process where the flat list
  was 5 ms.
- **The sort** is one control above the tabs (`#nav-sort`, `data-own-keys`): Date taken oldest or newest first, Name A to Z or Z to A.
  *(Replaced by the header's Sort by, #714: the owner's second review, below.)*
  Choosing one reads the view open again in it (a new history entry) and the views opened after it are read in it until another
  is chosen or an address names one (`state.nav.order`).
- **Measured** with `scripts/measure_library_review.py --run` on a sandbox copy of photo_index (68,324 photos; the copy migrated
  to 22 by the served code before the server started), headless Chromium 1600 x 1000, a fresh browser each, three rounds, medians,
  thumbnails answered a 1-pixel picture by the browser; the trunk measured the same way with `--code-root` (a `git archive` of
  `6714b92`), which has no union and no sort:

  | the action | this branch | trunk |
  |---|---|---|
  | (a) twelve Julys (filter "July", click the first, Shift-click the twelfth): the Shift-click to the first window painted and idle | 109 ms (73-141, five rounds); the ids request 44 ms (31-64), 4,003 photos, 23 KB; no long task | -- (one row at a time) |
  | (b) the People tab: click to its rows painted and idle | 165 ms (145-199); the request 115 ms (97-152), 23 KB; 426 rows, 13 branch headers | 65 ms (64-66); the request 19 ms, 13 KB; 413 rows |
  | (c) the whole library: navigation to painted and idle, by Date Taken | 389 ms (332-677); ids 158 ms | 373 ms (361-646); ids 175 ms |
  | (c) the whole library, the sort changed to newest first / name A-Z / name Z-A | 295 / 294 / 380 ms; ids 132 / 207 / 194 ms | -- |
  | (c) the largest keyword node (41,434 photos): navigation to painted and idle | 541 ms (509-643); ids 359 ms | 522 ms (500-557); ids 306 ms |
  | (c) the same, the sort changed to newest first / name A-Z / name Z-A | 411 / 414 / 529 ms; ids 142 / 83 / 186 ms | -- |

  An earlier run of the branch on a busier machine (the copy took 86 s, not 12) gave (b) 324 ms and (c) 815 / 679 ms for the
  unchanged date order: these numbers move by a factor of two run to run. **The People tab is slower** -- about 100 ms in the page,
  45 ms of it the branches' photo counts in-process (one pass of `photo_people` grouped by photo: 4,179 distinct sets of names), the
  rest a reply twice as long; a header counting its people instead of its photos would cost nothing, and is the owner's call.
  In-process on photo_index read-only (no index of names there yet, so the name orders are the fallback): the id list of 12 Julys
  17 ms either way by date; 400 people 181 ms (44,431 photos, the sort of them); a mixed union (a keyword, a person, a year, a
  month, a year's Other) 72 ms; all 22 ms by date either way, 490-516 ms by name without the index; the 14,838-photo keyword
  34 ms by date, 117 ms by name without the index. With the index (a copy of photos made in a scratch file): all 25 ms, that
  keyword 15 ms, a person 7 ms, either way.
- **Plans** (checked on photo_index read-only; `tests/test_library_union_and_order.py` asserts them on a library made the same way):
  twelve Julys `SEARCH p USING COVERING INDEX idx_photos_taken (taken>?)` with no sort, either direction (the index read from the
  first July, each row tested); a mixed union `MULTI-INDEX OR` of `idx_photos_taken (taken>? AND taken<?)`, `idx_photos_year (year=?)`,
  `photo_tags` by `idx_photo_tags_tag (tag_id=?)` and `photo_people` by `idx_photo_people_name (name=?)` each then `p` by primary key,
  and a temp b-tree for the order; its undated read `SEARCH p USING INDEX idx_photos_taken (taken=?)`; a union of people the name
  index for each name and a temp b-tree; newest first the same indexes read backwards with no sort; by name `SCAN p USING INDEX
  idx_photos_name` (the index walked, nothing sorted) with the source's own seeks as list subqueries, and a keyset page `SEARCH p USING
  INDEX idx_photos_name (<expr>>?)`. The people's branches: `SCAN photo_people USING INDEX sqlite_autoindex_photo_people_1` (80,000
  small rows, not covering: the name is read) and a temp b-tree for the grouping.
- **How it fails**, each a test (`tests/test_library_union_and_order.py`, `test_name_index.py`, `test_navigator_people_groups.py`,
  `tests/frontend/navigator-multi.test.mjs`): a union of overlapping keywords (one photo, counted once); a deleted keyword or person
  in it (nothing from it, the rest shows; all of it deleted is an empty view); a union holding `all`; 1,000 months in one statement;
  1,001 sources, a nested union, JSON that is not a list, a month that is not one (a sentence each); a selection by a union source,
  resolved and tallied with an id excluded; every order's keyset pages equal to its id list for all, a folder, a keyword and a union;
  a token read in another order; the order by name without the index; the migration adding one index and nothing else, an undo
  across it. On the page: twelve Julys by the filter and a Shift-click, Back and Forward to and from it; Ctrl-click on, off, and the
  last row; four Ctrl-clicks with no answer awaited between them; a parent with one child taken off (its own photos stay) and put
  back (the whole again); Ctrl+A of the whole tree (one source) and of 2,705 filtered rows (a sentence, no request); a Ctrl-click in
  another tab and the tab switch; a selected row hidden by the filter; a bookmark of a union with a deleted keyword and an order, and
  four bad ones (no request); Select all of the grid on a union (the union is the bulk edit's source); the People tree, a branch's
  row, a person opened by the address under their branch; the sort changed, kept for the next row, restored by Back; a bad order in
  the address; the sort's arrow keys not stepping the open photo.
- **Known limits.** The order is read once with the ids, as before: an edit that moves a photo in the order shows at Refresh view.
  `NOCASE` folds ASCII only, so "Émile" sorts after "Zed"; a number in a name sorts as text ("IMG_10" before "IMG_9"). A union's
  folders are read through `photo_folder` (the derived table) where a single folder with its subfolders is a range of `photos.path`:
  the two agree while the derived tables are in step (the doctor checks them). A folder spelled by a previous place of its root
  inside a union is canonicalised by `source_of`; going to another library takes the view's parameters away, the order with them,
  by the one list `common/library.js VIEW_PARAMS` (#697),, but the response does not carry the ingress's moved-root header. The People
  tab is about 100 ms slower to open than the flat list was (see Measured).

### The owner's first review of the library views *(2026-10-04; #668-#670, #674, #675 on `arch/library-review-1`; #671-#673 beside it)*
The owner installed phase 9 and reviewed the TagPup page. A library view is for seeing the library at a high level; the work on one
folder -- Smart Rename, Camera Time Shift, the folder's own mechanics -- is **Organize**, the pane that was called Folder.

- **#668, Organize.** The switch beside Library says **Organize** (it said Folder, which was taken for the navigator's Folders tab). Only
  the words changed: the code still calls the pane `folder` (`sidebar-tab-folder`, `state.nav.shown === 'folder'`).
- **#669, no Smart Rename and no time shift in a view.** `showChrome` (library-view.js) hides both buttons in every kind of view, and
  `hideChrome` shows them again for a folder; 9d-2's Shift Date Taken of a view's selection (`shiftSelectionInView`, the direction field,
  the note) is gone from the page. **The server's bulk time shift is kept** (`op: time_shift`, resume and all, 9d-1): nothing on the
  library page starts one now, and a job of it found running or stopped part-way is still shown in the strip and resumed by it.
- **#670, the strip.** One small line: the view, its count, the status sentence when there is one, and **Refresh view**. When the library
  was last in step is said only when something is wrong (`sync-state.js syncSentence`): the read failed; a sync is running; never in step;
  the newest sync (`last_run.in_step`, now read) left it out of step; last in step more than 48 hours ago (`QUIET_FOR_HOURS`: the daily
  catch-up has not left it in step); or the view's folder holds photos on disk the library does not (the banner's offer: the strip then
  says when it was last in step). *Decided, not copied:* a card marked changed on disk does not make the strip speak -- the card says so,
  and the strip would have to follow every batch of cards (library-source.js has no hook for it). On the live libraries every one's newest
  run was in step and the last whole sync this morning (counted read-only, 2026-10-04): the line is empty. **Back to folder view** is gone.
  Why it did not work: it returned to `state.libraryReturn`, set only when a view opened while a folder was open in Organize (or from a
  `?path` in the address); a view opened from the navigator, a bookmark or the gear with no folder open had nothing to return to, and
  the link closed the view onto an empty page with "No folder was open". `libraryReturn` went with it; Back (the browser's) returns to
  the folder a view was opened from, as it always did. **This folder only** and **Show on disk** went from the strip; `showOnDisk`
  became `openInOrganize(folder)` -- scan first, close the view only when the scan answered (#569), a folder gone or a share away keeps
  the view and says so in the strip -- used by #675's Folders to Organize. Show in File Explorer (the grid's context menu) stays.
- **#675, Folders to Organize.** The tally's answer gains `folders: {count, listed}` (`tagpup.services.selection._folders` over
  `tagpup.store.selection_folders`, a module of its own beside `store.library_view`, which another branch has this week): one
  `SELECT folder_id, COUNT(*) FROM photo_folder WHERE photo_id IN (<the tally's selection>) GROUP BY folder_id`, in the tally's own read
  transaction and over its own `sel` table (`library_view._selected`, private there and used, not copied: the counts and the folders are
  of one set of photos), and the `folders` rows by key only when 10 or fewer (`MAX_FOLDERS_LISTED`). The page never holds the paths of
  the selected photos. Plans on a sandbox copy of photo_index: a list of ids seeks `photo_folder` by key for each (`SEARCH photo_folder
  USING INTEGER PRIMARY KEY`, the ids from `sel`); a Select all scans the covering path index of `photos`, as the tally's own reads do.
  **Measured** (sandbox copy of photo_index, 68,324 photos, its own TAGPUP_HOME, deleted afterwards; the route through Flask's client, 7
  warm rounds): Select all's tally 179 ms median without the folders (172-218), 218 ms with them (209-244); the folder read alone 33 ms
  for 2,672 folders; the reply 34.8 KB. `tally.js` draws them: a button a folder, its name as text (two of one name show the folder
  above), "(N photos)"; a click is `openInOrganize` through `upper` (rapid clicks: one scan, `state.moves.leaving`). More than 10 is a
  sentence with the count. The Date Taken group is hidden in a view's panel (it showed "--": a view's selection holds no records) and
  shown in Organize's, which keeps its range; *decided*, as the finding names the library page's panel.
- **#674, Delete of a view's selection** -- a **bulk job op** (`op: delete` of 9d-1's `tagpup.jobs.bulk_edits`), not batches of the
  one-photo route: it is how a selection across folders and of thousands is named (by id, the source less the excluded), and the job
  gives progress, Cancel, one bulk edit at a time in a library and in another process (`job_runs`), the strip, and the Activity
  page's record, for nothing new. Not resumable: its list of photos is fixed when it starts, and Start again of a cancelled one
  asks the question again (#691, below). The chunk
  (`bulk_edit._delete`) deletes each photo through **the one owner of a delete**, `tagpup.services.photos.delete` (the file to the
  Recycle Bin or, where its place has none, through this PC's, #694; then `forget_photo` -- row, faces -- and the thumbnail), under the lock of changes
  of photo files for the chunk, so no single save of one of them interleaves. **History/journal: as the folder view's delete, none**
  -- a delete of a held photo has never been a journal change, so History neither lists nor undoes it; the Activity page lists the
  job with its counts. **The question is asked of the server first**: `POST /api/library/selection/delete-check`
  (`selection.where_deleted`: the folders of the selection, `recycle_bin.no_bin_reason` once a folder -- 2,672 folders of
  photo_index in 2.1 s on this machine, counted read-only, every one with a Bin), and the question says how many go through this
  PC, how much they take and where they restore to (#694, below). *(It said, until #692/#694, "params.permanent ... a photo found
  with no Bin that the question did not name is left": not so -- the flag was one bool for the whole job, so a photo found binless
  in a folder the question had not named WAS deleted for good. Since #694 nothing is deleted for good, and the flag is gone.)* A file already gone is `skipped_missing` and keeps its row (sync reports it). A share that does not answer is an
  error a photo (`_reachable`, as the other ops). The cap is the other bulk edits' (200,000, refused before any request). When the
  job ends the page reads the view's order again (`library-view.js photosDeleted`, through `upper`), which drops the deleted photos
  from the view, its total and the selection, and closes the open photo onto the grid if it was deleted; the navigator's counts and
  the folder scans are let go as for any job. **What is accepted:** each photo of the job is the one-photo delete's cost --
  `libraries.split`, `refuse_writes`, `forget_photo` with `derived.prune` and the thumbnail, each its own read or write of the
  library -- rather than a per-chunk version of them, so as not to make a second owner of a delete; not measured on the live
  library (it would delete). Deleting the open photo while a job runs is not stopped: its save fails as its file is gone.
- **#691, a delete deletes exactly what its question named.** delete-check resolves the selection (`selection.resolve`) and answers
  its `total` and a `token`, the SHA-256 of the sorted ids (`selection.token_of`; 68,000 ids are about 20 ms). The page's question
  names that total, and the start carries the token; the route resolves the selection again and `bulk_edit.refuse_if_changed`
  refuses, `409`, nothing begun, when the token differs ("The selection changed since you were asked ...: nothing was deleted. Ask
  again"). The job's own list of ids is the one resolved at that start, so nothing that comes into the source afterwards is in it. A
  delete is not resumable (`bulk_edits.resumable` is the time shift's alone); the strip's Start again of a delete goes through
  `deleteSelection` (delete-check, the question, a new token) through `upper`, never `startBulk(request)`. Reproduced on the old
  code by the reviewer (question "3 photos", one photo indexed meanwhile, 4 deleted); `test_bulk_delete` holds it.
- **#693, how long, and what waits.** Measured on a sandbox copy of photo_index (its own TAGPUP_HOME, roots placed at sandbox
  folders, throwaway JPEGs made at 200 and 400 of its rows' places, the real Recycle Bin, the job run through Flask's client; the
  sandbox deleted afterwards): 200 photos in 12.8 s (63.8 ms a photo, 15.7 a second), 400 in 25.0 s (62.6 ms, 16.0 a second).
  The question says the time at 15 a second (`DELETE_PER_SECOND`; 68,000 photos: about 1 hour 16 minutes) and that saving a
  photo elsewhere waits for the photos being deleted at that moment (each chunk of 25 holds the lock of changes of photo files,
  about 1.6 s). A photo copied from a share costs its copy too; that was not measured (no share here).
- **#694, nothing is deleted for good** *(the owner's decision, 2026-10-04)*. A photo in a place with no Recycle Bin (a network share,
  a removable drive, a SUBST drive) goes through this PC: `tagpup.files.recycle_bin.delete_file` -- the one way every delete of a
  photo takes, Organize's (`photos.delete`, `file_only.delete`) and the bulk Delete's -- copies it to
  <Downloads>\TagPup deleted from shares\<server>\<share>\<path> (`mirror_of`; the Downloads known folder by `SHGetKnownFolderPath(FOLDERID_Downloads)`,
  wherever the owner moved it, the profile's Downloads if Windows cannot say, and a test home's own through `TAGPUP_DOWNLOADS`,
  which `tests/own_home.py` sets: no test writes the owner's Downloads), checks the copy (size, then SHA-256 of each), sends the
  COPY to this PC's Recycle Bin (`send_to_recycle_bin`, the existing owner), and only then deletes the original. **Restored, a copy
  goes to that folder under Downloads, not to the share**: the question, the reply and the SPEC say so. Each step that fails
  leaves the original and its row, the photo an error: a copy that fails or differs is taken away; this PC's Bin refusing the copy,
  the copy taken away; a crash or a refusal of the original's delete after the copy is in the Bin leaves the original AND a copy
  in the Bin -- the photo is there twice, and the error says so. A name already in the mirror folder is kept, the copy takes
  `name (2).jpg`. **Free space**: the Downloads folder's drive must have room for the copy and 1 GB spare (`room_for`, before each
  copy); delete-check answers `copy_bytes` (the index's sizes of the photos in places with no Bin) and `no_room`, and the page then
  says the sentence and asks nothing. Nothing is deleted for good any more: only if this PC's own Recycle Bin refuses is the photo
  an error, its original kept. `/api/folder/membership`'s `permanent_delete` keeps its name and now means "this place has no
  Recycle Bin: a delete goes through this PC".
- **#703-#706, a copy is made only where it is kept** (`recycle_bin.can_copy_here`, before every copy and, for the bulk Delete, before
  each chunk's copies, `bulk_edit._cannot_keep`; delete-check asks it with the Bin's size fresh). **#703:** Windows makes room in a
  full Recycle Bin by deleting its OLDEST items for good, so "nothing for good" holds only while the Bin of the Downloads volume can
  keep what it holds plus the copies: its capacity is the volume's `HKCU\...\Explorer\BitBucket\Volume\{guid}` MaxCapacity (no
  key: 5 % of the volume, a guess on the safe side), `NukeOnDelete=1` refuses everything (nothing would be kept), a photo larger
  than the capacity is refused, and `used + copies > capacity * 0.95` refuses with the sizes. `SHQueryRecycleBinW` is slow -- **11.8 s**
  for this PC's Bin (3,353 items), read once 2026-10-04 -- so its answer is kept per volume and counted on with what this process
  sends (`note_binned`), and asked again after 10 minutes (`BIN_FRESH`); a copy sent by another program meanwhile is not seen until
  then (accepted: the 5 % margin). **This PC's Bin was full when read** (49,707 of 49,710 MB): every delete from a share will be
  refused, with the sentence, until the owner empties it. Out of scope, said: the same capacity check for a photo deleted from a LOCAL
  folder with its own Bin -- that is Windows' ordinary behaviour for any file the owner deletes, and the Bin is not TagPup's copy; it
  could share `room_for_copies` if wanted. **#704:** a copy's path (or its `.partial`) of 260 characters or more is refused up front,
  the sentence naming the length and the folder (`too_long`); delete-check counts them and the question says they are left. **#705:**
  the question's time for photos through this PC adds the copies at **200 MB a second** (`COPY_BYTES_PER_SECOND`: copy, two hashes,
  rename, measured on this PC's disk, 100 throwaway files of 3.4 MB: 204 and 210 MB a second) and says "at least", since a share
  reads slower and was not measurable here. **#706:** `.partial` copies a crash left for the same name are taken away on the way in
  (they no longer push the name to "(2)"); a Downloads folder under a OneDrive folder (`OneDrive`, `OneDriveConsumer`,
  `OneDriveCommercial`) is refused. Tests set `TAGPUP_RECYCLE_BIN` (own_home: a large empty Bin) so none reads the owner's.

### The owner's second review of the library views *(2026-10-04; #712-#714 on `arch/library-review-3`)*
- **#712, Folders to Organize switches the sidebar.** Opening a folder of the selection details' Folders to Organize closed the view onto
  it and showed the pane last chosen for a folder -- Library, when the owner had picked it before any view was open. `closeViewOntoFolder`
  now ends with `choosePane('folder')`, the switch's own click handler (navigator.js, through `upper`): the switch, its pane and the
  remembered choice are what a click on Organize leaves. A folder gone or a share away keeps the view and the switch as they were.
- **#713, one floating header.** The strip and the header card sit in one wrapper, `#folder-view-top`; in a view (`.in-library-view`,
  set by `showChrome`) it is the one sticky header: the view's name and total (one line, a long name cut short with the whole in its
  tooltip), Refresh view as an icon button (`aria-label`, tooltip), Sort by, and the card's actions -- Select All, Select None, Delete,
  the count, the thumbnails' size -- the card's own title and count hidden (they said the same and scrolled away). It sticks flush with
  the scroller's top (`top: -20px`, the scroller's padding), so no card shows above it; `grid.js topInset` is its height and the 16 px
  gutter, so a card the keys walk to is below it. Organize is not touched: the wrapper is a plain block there and the card is as it was
  (Organize had no duplicate header). *Measured* in Chromium on a sandbox copy of photo_index (`measure_library_review.py --only d`):
  at a 1,600 px window the header is 65 px tall; at 1,000 px the grid's column is 296 px (the sidebar and Selection Details keep their
  360 and 340) and the header 163 px; at 720 px the column is 40 px and the page scrolls sideways -- it did on the trunk too, the grid's
  cards are 150 px at the least; the header's controls stay inside it at 1,600 and 1,000. A card walked to by the arrow keys (30 rows
  down, 12 up) landed 16 px below the header at every width (on the trunk, at 720, under the strip).
- **#714, Sort by.** A button in the header opens a menu of two sections, as Windows Live Photo Gallery's: the field (Date Taken,
  Caption, File name) and the direction (Ascending, Descending), each a group of `menuitemradio` showing the current choice
  (`web/tagpup/sort-menu.js`; its state `state.sortMenu`). ArrowDown on the button opens it on the field chosen, ArrowUp on the
  direction; ArrowUp/Down, Home and End move through the five items, Enter and Space choose, Escape closes it onto the button, Tab, a
  click or the focus elsewhere close it, and so does the view changing under it (`showSortOrder`, called from `libraryChanged` and
  `hideChrome`). A field keeps the direction and a direction keeps the field; what is chosen already asks for nothing; the order opens as
  a new view (Back returns), and `state.nav.order` follows only when the view has opened, so Cancel on unsaved edits leaves the next
  view's order alone. The sidebar's select is gone. **The address** keeps `order`, its grammar extended: `caption`, `caption-desc` beside
  the first review's four, which read as they did. **The server**: `order=caption | caption-desc` (`store.library_view`): by the
  photo's caption -- the first of `photos.captions`, the one the page shows -- its first 200 characters (`CAPTION_KEY`, which bounds
  what a page token holds), without case, ties by id; the photos with none after the captioned ones in both directions, by id in the
  order's direction, as the undated are by Date Taken. Name and caption are one machinery (`_KEYED`: the index walked in order, each
  entry tested for the source; the keyset page a seek of it). **Migration 23** (additive, index only, touches no table, blocks no undo):
  `idx_photos_caption` on `(<caption> COLLATE NOCASE, id)`, the caption an expression of built-in functions over `captions`
  (`library_view.caption_sql`: `NULLIF(substr(CASE WHEN json_valid(captions) THEN json_extract(captions, '$[0]') END, 1, 200), '')`),
  so SQLite keeps it and no writer of a caption has to know of it, as migration 22's on the path -- *decided over a column* (the owner's
  rule of a column over `json_extract` is about reading JSON on every read; this is read once, by the index): a column would have had
  every writer of captions (the indexer, a save, the bulk edits, an undo) keep it. Text that is not JSON is NULL, never an error that would
  refuse the row's write. Checked by hand against #464: a test holds that 23 adds one index on `photos` and nothing else, every table's
  rows unchanged, and a change journaled at 22 still undoable. The captioned photos are asked as `<caption> COLLATE NOCASE >= ''` -- a
  range of the index -- not `IS NOT NULL`, which read each photo's row to compute the caption again; after a cursor only the cursor's
  bound is given, or SQLite took the first of two lower bounds and walked each page from the start. **Measured** on a sandbox copy of
  photo_index (68,324 photos; 57,775 with a caption, 10,549 without, 8,665 with more than one; the longest 616 characters -- counted
  read-only): migration 23's index 0.18 s, the whole `ensure` 0.3 to 0.7 s; the whole library's id list by caption 24 to 31 ms either
  way (by Date Taken 23, by name 25; with `IS NOT NULL` it was 218), the keyword node with the most photos of its own (14,838) 20 to
  32 ms (its uncaptioned photos a seek of each id and a sort of those ids); 50 keyset pages of 200 in caption order 42 ms for the whole
  library, 153 ms for that keyword (741 and 1,608 with two bounds). photo_index itself, read-only and not yet at 23: `SCAN p` and a
  `TEMP B-TREE`, 394 to 1,255 ms -- what a library opened without its migration (a look) does, the same order. In Chromium (three fresh browsers each, from the click on Sort by to
  the first window painted and idle): the whole library by Caption 332 to 717 ms (median 716; the ids request 172 to 305), by Caption
  descending 215 to 230 ms; the largest keyword by Caption 544 to 595 ms, descending 229 to 253; the other orders 235 to 1,122 ms on
  the same runs (a noisy machine: Date Taken's own first open was 404 to 2,330). Bulk edits, the tally and the selection resolve the
  view's source in Date Taken order whatever the view's: the order is not part of a selection.
- **#722, which sources walk the index.** Name and caption order forced `INDEXED BY` for every source, so a month, a year's Other
  or a folder with its subfolders walked all 68,324 index entries for each page. `_walks` (store.library_view) now walks the index for
  the whole library and for a source given by a list of ids (a keyword, a person, a folder alone, a union holding one of those), and
  leaves a range of another index (`Scope.ranged`: a month, a year, a year's Other, a folder with its subfolders, a union of months and
  years) to SQLite -- the range read and sorted -- unless it holds more than 1/16 of the library (`RANGE_SHARE`; counted at each read,
  one covering count): the top folder of the Folders tab is the whole library through the path range, 114 to 949 ms a page sorted,
  0.2 to 1.8 walked. **Measured** on a sandbox copy of photo_index, caption and name, either way, the first page of 200 and the
  eleventh, then the id list, the walk forced everywhere (before) against the rule: a month (1,201) 1-117 / 44-156 ms against 2-11 /
  7-9; a year's Other (50) 45-149 against 4-10 every time; a folder with subfolders of 3,573, 2-34 / 47-157 against 8-40 / 16-22; the
  top folder 0.2-1.8 / 69-193 against 7-9 / 78-184 (the count, about 7 ms, paid on each read); a year of 5,579 (walked either way)
  0.2-33 / 47-153; a keyword (14,838) 2-4 / 14-24, a person (14,840) 8-16 / 19-29, a folder alone (759) 0.3-7 / 6-7 and unions
  (3,251 to 17,463) 3-29 / 27-175, unchanged. A test asserts the plan for each kind: the range's own index and a sort for a small
  range, the name or caption index and no sort for the library, a keyword, a person, a year holding most of it and the top folder.
- **#723.** A forged caption- or name-order token whose key held a lone surrogate passed `decode` and reached SQLite, which cannot
  bind it: a 500. `decode` refuses a key that does not encode as UTF-8 (400).
- **Found on the way.** The page token was refused over 400 characters, and an order by name's token holds the file name in JSON, six
  characters for each one that is not ASCII: the page after a photo named with some 50 Cyrillic or Greek letters could not be asked for.
  `MAX_TOKEN` is 5,000. No name on the live libraries came near 400 (counted).

### Phase 9e-1: search on the server *(built 2026-10-04; migration 24; branch `arch/phase-9e1-search`)*
Server and store only; the page's search box and picker are 9e-2's, and this is the contract they use.

- **A search is a source** (`library_view.source_of`, kind `search`): `{"kind": "search", "value": {"all_of": [source], "any_of":
  [source], "none_of": [source], "words": "text"}}`, every part optional, another part refused (a misspelt `none_of` would
  otherwise be a search for more). The lists hold sources of the kinds a union holds (`all`, `folder`, `keyword`, `keyword_only`,
  `person`, `year`, `month`, `year_other`), at most 1,000 each. **One statement** over `photos p` (`store._search_scope`), the AND
  -- a balanced tree, as the union's OR is -- of: the `any_of` union's clause (`_union_scope`, unchanged); each `all_of` member's
  own clause (its Scope's where; a folder alone by `photo_folder`); `NOT IFNULL((<none_of union>), 0)`; and the words. So Select
  all, ranges, the tally, every bulk edit and the strip take it unchanged, and Delete's token (#691) refuses a search whose photos
  changed between the question and the start (a test). Keyset paging in every order is the source's: `Scope.ranged` is set, so in
  an order by name or caption the name or caption index is walked when the search holds more than 1/16 of the library (counted,
  #722) and left to SQLite to read and sort when it holds less.
- **Decided: "within" is an `all_of` member.** The navigator's selection is a union; a union in `all_of` is ONE member (its photos
  ANDed with the rest), while a union in `any_of` or `none_of` is its sources (an OR in an OR). "Three family members and not a
  fourth within July of these years" is `all_of: [{any_of: [twelve Julys]}, A, B, C], none_of: [D]`.
- **Decided: a person is by name**, as the person source, the navigator's People and the tally read one in identity stage 1 --
  `photo_people.name` compared without case. By `tag_id` a name no person node is called, one on a branch (#660) or one two nodes
  share has NULL and would be found by nothing (photo_index's `photo_people`: 4, 4 and 1 such names); stage 2 moves every read to
  the id at once. A person on a branch tag is found as the navigator's row of that name finds them (a test). A keyword member is by
  the tree's node, `keyword` with everything under it and `keyword_only` the node alone, as the sources are.
- **Decided: a search that says no more than a source is that source**, as a union of one is: no part is `all`, `any_of` alone its
  union, one `all_of` member alone that member; the reply's `source` says which. `none_of` keeps a photo with no date (NOT of a
  month's comparison is NULL, which would have dropped it: the IFNULL; a test). A member naming nothing (a keyword with no node, a
  person nobody is) makes `all_of` or `any_of` hold nothing and takes nothing from `none_of`; `none_of: [all]` holds nothing.
- **Words: two contentless FTS5 tables, migration 24** (`tagpup.store.search_index`; docs/DATABASE.md 25 and 26):
  `search_words(tags, captions, people)`, `unicode61 remove_diacritics 2`, prefixes 2 and 3 -- each keyword as the row holds it
  (path and leaf: "People/Élodie Marchetti" gives people, elodie, marchetti; a keyword with no node is found by its words), the
  captions and titles, and the names `photo_people` lists (faces too, by the leaf rule) --; and `search_names(name, folders)`,
  `trigram remove_diacritics 1`, the file name and its folders: **every folder below the root of a root-relative row, the root's
  name never a word; of a native row only its own folder** (*decided*: above it are the machine's layout -- drive, "Users", the
  profile, "Pictures" -- whose words every photo would match: renton_parkrun's 1,150 rows are all native and five folders
  deep, counted; photo_index's and kr-track's are all root-relative). `content=''`,
  `contentless_delete=1`, rowid the photo's id: a row is replaced by INSERT OR REPLACE and taken by its id; a photo deleted on any
  connection takes its rows by the trigger `search_goes_with_its_photo`. **Each typed term** (split at blanks, at most 20, at most
  500 characters in all) is quoted, its quotes doubled -- AND, OR, NOT, NEAR, `*`, `^`, `"`, `:`, `(`, `)`, `-` are text -- and is
  found when the words table matches it as a prefix phrase (`"term"*`: every term a prefix, decided, so "beach" finds "beaches"
  and a word being typed finds as it is typed) or, from three characters, the names table holds it (`"term"`: "0412" inside
  "20190412_1430.jpg"); the terms are ANDed with each other and with the sources, a set, never ranked. A term of no letter or digit
  and under three characters says nothing and is dropped. A library below migration 24 answers a search of words `503` with
  `Retry-After: 5` and "The word index is being made now; try again in a few seconds." (`WordIndexComing`, #753): the server
  brings every library it serves up to date as it starts and as a request first names it, so such a library's migration is under
  way (the startup thread, about 4 s on photo_index; another program holding it) or due. One at 24 without the tables (dropped by
  hand) is `400` (`NoWordIndex`).
- **Kept by the store's writes, in their transactions**: `derived._put` refreshes the word rows of every photo it is asked about
  (the index's record, the bulk tag writes, sync, moves and renames, `ensure_row`, `follow_nodes`, the journal's `_derive`);
  `people.rebuild` of the photos whose people it wrote (a face named or unnamed, a tree edit that changes who is a person, a rename
  of a person); `derived.rebuild_all` and the adoption's `rebuild_folders` rebuild them whole. Captions joined the columns the
  derived data comes from: `tests/test_derived_writers.py` now fails a writer of `photos.captions` that does not go through
  `derived`, and the journal's undo of a caption refreshes the photo
  (`_touched`). A tree rename changes no word: the words are the keyword as the file holds it, which TagTuner rewrites one
  transaction after the tree's -- until then the old words find the photo and the new do not (a test), as its keyword rows name no
  node meanwhile.
- **The doctor**: `search_index_out_of_date` (in `checks.RULES`): every photo with no row, every row of no photo, and of 500
  photos spread over the ids those whose texts, each column matched as one phrase, are not found in their row (a contentless table
  cannot be read back, so a word left behind is not seen by the sample). The report says so beside the rules, with the remedy
  (`search_index.LIMITS`, #752): an older checkout writing a library at 24 never refreshes the index, and a word it leaves behind
  is found only by `--rebuild-derived --apply`. `tools/doctor.py --rebuild-derived --apply` rebuilds it
  with the derived tables. Migration 24 runs the same check before it commits. On real rows the phrase check needed one fix found
  by running it: 255 of photo_index's photos have captions with no letter or digit (a lone dash), which no phrase matches; with
  it, every one of its 68,324 photos passes (counted on an in-memory copy of its rows).
- **#464, checked by hand.** Migration 24 declares `touches` = the two virtual tables; FTS5 also makes eight shadow tables
  (`_data`, `_idx`, `_docsize`, `_config` of each), and the migration adds the trigger on `photos` (as migration 19's
  `derived_go_with_their_photo`). A test holds that 24 adds exactly those ten tables and that trigger -- no column, no index --
  that every row of every table that was there is unchanged, and that a change journaled at 23 is still undoable after it and is
  undone. All ten are in `schema.UNWATCHED` (a later migration's watch cannot make a trigger on a virtual table) and the two
  virtual ones in `journal.DERIVED`. Interrupted, the migration leaves the library at 23 with none of them, and runs again on the
  next open (a test).
- **#751: a search's members are read once.** `all_of` resolved each person through a pass of `photo_people`'s names and each
  keyword not spelled as a node through a read of the tree; one `_Reads` per statement now serves the `any_of` union, every
  `all_of` member and `none_of` (a test counts the reads). photo_index, read-only, the id list: all_of 100 people 310 -> 75 ms,
  3 people 56 -> 55 ms.
- **Measured** on a sandbox copy of photo_index (68,324 photos, its own TAGPUP_HOME, roots placed in the sandbox, deleted
  afterwards), in-process through `tagpup.services.library_view` (the route's work less Flask and JSON), medians of five:
  **migration 24 through `schema.ensure` 4.3 s** (a copy just written, cold), of which the backfill is 2.0 s warm and its check
  (counts and 500 phrases) 1.1 s; it runs in the server's startup thread, as every migration since #661 does. The file grows
  36.6 MB (the words' index 8.5 MB, the trigrams' 21.4 MB). The writes, with and without the word index (rolled back): a bulk
  chunk of 25 refreshed 2 ms either way; 2,000 photos refreshed (a folder renamed) 54 ms without, 75 ms with; `people.rebuild` of
  5,000 that changes nothing 89 against 100 ms; the 2,000-photo follow of `test_derived_batches` holds the lock 0.70 s without and
  0.89 to 0.92 s with (its limit is 2.5 s). Searches, ids / first page of 200 with its count, ms, the six orders' range:
  all_of 3 tags (6 photos) 12-20 / 16-27; any_of 2 people less a tag (22,373) 81-164 / 91-155; none_of 1 tag (65,099) 57-91 /
  29-79; a common caption word alone (10,433) 49-74 / 52-64; a rarer word (396) 11-21 / 14-28; two words (9,120) 59-107 /
  77-138; a common word within a year (57) 16-43 / 24-49. Select all of the 65,099-photo search: resolved (a bulk edit's list)
  105 ms, tallied 416 ms; of the 10,433-photo word, resolved 50 ms. The structured searches' plans on photo_index itself
  (read-only): a person's photos `SEARCH p USING INTEGER PRIMARY KEY` from the name index and a temp b-tree for the order; all_of
  a tag and a year `SEARCH p USING COVERING INDEX idx_photos_year (year=?)`; none_of a tag the date index walked
  (`idx_photos_taken (taken>?)`), by name `SCAN p USING INDEX idx_photos_name`, nothing sorted; words `SCAN search_words VIRTUAL
  TABLE INDEX 0:M3` (FTS5's own index) as a list, then the photos by key, as `MULTI-INDEX OR` of the two tables for each term.
- **The contract for 9e-2.** The request is the source above: a `GET /api/library/ids?kind=search&value=<the value as JSON
  text>&order=` or, as the page sends every union, a `POST` of `{"kind": "search", "value": {...}, "order"}` (and the same for
  `/view` with `after` and `limit`); a selection's source is `{"kind": "search", "value": {...}}` (an object or its JSON text), the
  tally, the bulk start and delete-check take it unchanged. **The page's address** for a search is
  `?view=search&value=<encodeURIComponent(JSON of the value)>&order=<order>`, as a union's is `?view=any_of&value=...`; the value's
  parts in the order `all_of`, `any_of`, `none_of`, `words`, empty parts left out, so one search has one address (the page's own
  `sameSource` compares them); within what the navigator has selected is `all_of: [state.nav.followed]` (a union, or the one source
  it is). `MAX_ADDRESS` (100,000 characters) refuses a longer one with a sentence, as for a union. The picker sends names and tags
  as the navigator does: a person `{"kind": "person", "value": <name>}`, a tag `{"kind": "keyword", "value": <tag path>}` (with
  everything under it; `keyword_only` for the node alone). The reply's `source` may be another kind (a search that says no more
  than a source); the page keeps the address it asked by. `400` sentences: a part not known, a list over 1,000, more than 20 terms
  or 500 characters of words, a search in a search. **`503` with `Retry-After`** (#753) while the word index is being made: the
  page shows the sentence and asks again after `Retry-After` seconds, a few times (a migration that fails keeps answering it;
  after about a minute the page leaves the sentence and stops asking).
- **Known limits.** A term is matched within one column of one table: "rowan coast" is two terms, each found anywhere, but a
  quoted phrase typed as one term ("rowan_coast") must be in one column. The camera and the lens
  are words since migration 27 (`search_gear`; below); the author is not (the owner's call). The words of a photo's people are the names as `photo_people`
  holds them, so a rename shows in a search as it shows in the navigator. CLIP (semantic) search is not 9e.

### Phase 9e-2: the search page *(built 2026-10-04; branch `arch/phase-9e2-search-page`)*
The page's half of 9e, over 9e-1's contract; no server change. `web/tagpup/search.js` (the box, the picker, the chips),
`search-model.js` (what the picker offers and a chip says, as data), and a new kind of view in `library-source.js`.

- **Where the box is** *(decided)*: at the top of the **Library pane**, above the navigator's tabs -- not in the floating header
  (#713). A search is a way to open a view, so it is there with no view open; it sits beside the navigator whose selection it
  can look within; and the header stays one line at 1,600 px (it is 163 px at 1,000 already). Organize's "Filter files" box is
  the folder's list filter and stays Organize's (in a view its tooltip points at the Library pane's box). As WLPG's: words in
  one box and the filters beside it -- a **Filters** toggle opens All of, Any of and None of, each a row of chips and a picker.
  **Within the sidebar's selection** is a checkbox under the box, shown only while the navigator's rows are the view open,
  naming them (#713's header is untouched).
- **A search is a view of kind `search`** (`LIBRARY_KINDS`): `searchValue` keeps its value in the contract's one order (`all_of`,
  `any_of`, `none_of`, `words`, empty parts left out, a source named twice in a list once, words' blanks made single), so
  `sameView` compares one text and the same search asked twice asks once. It is always POSTed (`idsRequest`), like a union. The
  address is `?view=search&value=<JSON>&order=`; `viewSpecFromSearch` refuses with a sentence, no request, a part it does not know,
  a search in a search, a list over 1,000, words over 500, JSON that is not an object, and a search that asks for nothing. The
  header names it ("Search: “beach”; all of Trips/Coast and Rowan Thackeray; none of Places/Home", three of a list and "N more"); an
  empty one says "Nothing in the library matches this search." The selection, the tally, every bulk edit and Delete carry
  `{kind: 'search', value, recursive}` unchanged (a test: Select all's tally names the search). The navigator selects no row of a
  search (`membersOf` is `[]`), so a Ctrl-click in a search starts a selection again rather than make a union holding a search,
  which the server refuses.
- **Words are searched on Enter** (or the Search button), *decided over a pause*: each search is a new view -- the grid rebuilt,
  the selection cleared, the address changed -- and every term is matched as a prefix, so a half-typed word finds a broad set
  (a common word: 16,203 photos, 94 KB of ids); searching on a pause would clear a selection and redraw the grid while the
  person is still typing. The picker completes as it is typed: it asks nothing per key (photo_index's 895 keyword nodes and
  413 people, counted read-only, are filtered in the page).
- **One source of names** *(decided)*: the picker offers what the navigator's rows are -- `readSectionIndex('keywords')` and
  `('people')` (navigator.js; a read already under way is waited for, not repeated) -- because a member is exactly a row's
  source. People by name, by the store's one rule (`person_ids`); every node of the tag tree as its path, but a filed person's
  own node (`<branch>/<name>`), who is offered by name. **A name that is a branch is never a person** (#660): the people answer
  lists "Family" when a photo is tagged People/Family, unfiled; a person not filed whose name is a branch of the answer's
  `groups` is not offered as a person, the branch is offered as the tag (photo_index: 4 such names, each a group of the answer;
  kr-track and renton_parkrun: none -- counted read-only 2026-10-04). Not `/api/tags` and `/api/people`: those are the edit
  fields' lists (tags typed into photos, names on faces). Best first: a name that starts with what is typed, then a word of it,
  then one that holds it; people before tags; then alphabetically; 30 shown, "N more: keep typing".
- **Keys**: the pickers are ARIA comboboxes (`aria-activedescendant`); ArrowDown and ArrowUp move, Enter adds (the first name if
  none is chosen; a name of no tag or person is a sentence, nothing added), Escape closes the list and a second empties the box,
  Backspace on an empty box takes the last chip off, Tab closes; Enter in an empty picker searches. A chip's x is a button named
  "Take Trips/Coast off All of"; the focus goes to the next chip or the box. A mousedown on a name keeps the focus in the box.
- **When it runs, and the history** *(decided)*: a chip added or taken off searches at once, as a navigator click does; ticking
  Within does when there is something to search. A search started from another view is a new place; a change while a search is
  open **replaces** it, so Back from any search returns to the view before it. That place's history state holds `searchBack`
  (1, or one more for each search after it in a new place -- a sort; 0 when not known: a search opened by its address), and
  **Clear** (the x, or a search emptied of its words and its last chip) is `history.go(-searchBack)`, or, for a
  bookmarked search, closes the view as a new place (`closeViewAsNewPlace`). The box mirrors the view (`searchFollows`, called from
  `libraryChanged`, `closeLibraryView` and the stay of "Save changes?" through `upper`): a search opened -- by the box, a bookmark,
  Back, Forward -- puts its words and chips in the box; leaving a search empties them; a move between two views that are not
  searches leaves what is typed alone (type, then click a year and tick Within). Within becomes the first member of All of and
  is shown from then on as a chip "Within July 2021" (a keyword or person selection is its own chip: the same member).
- **How it fails**, each a test (`tests/frontend/search-page.test.mjs`; 23 of its 24 fail on the trunk, the 24th guards
  `VIEW_PARAMS`): rapid Enter (the same search twice asks once; a second search while the first is out wins, the first answer
  dropped by the view's identity); a search with no result; words of only punctuation (a sentence, nothing sent; beside a chip
  they are left out and said so; "..." is three characters and sent, as the server looks for it in file names); the vocabulary
  changing while the picker is in use -- the tag editor renames Trips/Coast, the picker offers Trips/Shore when the focus comes
  back to it and the chip already added is marked (dashed, struck through, its tooltip "the library has no such tag now: this
  finds nothing"); the tag editor's edits now mark the navigator's counts out of date (`gear.js`), which they did not; a bookmark
  naming a tag that is gone (the chip so marked, the search sent, the empty view says why); an address over `MAX_ADDRESS` (a
  union of 375 share folders within, a 400-letter word: the sentence by the box, nothing sent, the view as it was); a bookmark of
  1,000 people in Any of (one request, 50 chips and "and 950 more"; a 1,001st refused with a sentence); five addresses that are no
  search; markup in a tag (text, no element); the server's **400** (its sentence by the box and in the strip); **503** with
  Retry-After (#753: the sentence in the strip, asked again after each Retry-After, at most 30 s each, until a minute of them has
  passed -- twelve asks after the first at 5 s -- then the view fails with the sentence; a view replaced meanwhile asks nothing
  more, its timer cleared by `destroyLibrary`); going to another library drops the search with the view's parameters. Found by
  the tests on the way: the picker took a section read under way for fresh (`loadSection` marks it not stale as it starts) and
  painted the old names.
- **Measured** with `scripts/measure_search.py --run` on a sandbox copy of photo_index (68,324 photos; migration 24 by the served
  code 5.9 s), headless Chromium 1,600 x 1,000, a fresh browser each, three rounds, medians (range), thumbnails a 1-pixel picture;
  from the key or click to the first window painted and the main thread idle; no long task in any:

  | the action | painted and idle | the ids request | photos |
  |---|---|---|---|
  | (a) a common caption word (9,531 captions hold it), Enter | 336 ms (327-517) | 226 ms, 94 KB | 16,203 |
  | (b) All of three tags, the third Enter | 136 ms (136-139) | 29 ms | 5 |
  | (c) Any of two people, None of the largest tag, the last Enter | 253 ms (221-272) | 165 ms, 52 KB | 22,867 |
  | (d) the word within the year with the most photos (5,579), Enter | 136 ms (120-173) | 26 ms | 62 |
  | (e) Select all of None of the largest tag, to the tally's tags and people painted | 879 ms (878-937) | the tally 349 ms, 33 KB | 65,949 |

  (e) is the tally's 250 ms pause, its request and the panel; the search adds nothing to it. There is no trunk to compare: the
  page had no search. An earlier run on the same copy gave (a) 512 ms (ids 115 ms): these move by a factor of two run to run.
- **The review (#768-#772)**, each a test that failed on the code before it:
  - #768: the picker reads its names again on the navigator's own pause after writes (`NAV_COUNTS_MS`, inside its timer, after
    the tab's own read whose answer it shares), once for a run of saves, not once a save.
  - #769: Within and the box's words change only when a search view has opened (`searchFollows`): Cancel on "Save changes?"
    after Enter keeps Within ticked and the words; after Clear keeps the search and its words.
  - #770: every place the page makes in the history carries this load's id and its position (`state.entries`; positions are
    consecutive, as a new place drops those after it); Back or Forward cancelled for unsaved edits goes back to the place the page
    shows (`history.go`), whose own state (`searchBack`) is then right, and that return asks nothing (the address names what is
    shown). When a position is not known (a place another load made, or one a folder's address replaced with `{}`), the place
    moved to takes the view's address and its own state less `searchBack`: Clear then closes the view rather than go to a wrong place.
  - #771: Enter while the picker's names are being read waits for them ("Reading the library's tags and people..."), then adds
    the first offered.
  - #772: a section the library could not answer is asked again only when the picker's box is focused again, not on each key;
    when one of the two fails the list says so ("Could not read the library's tags, so only people are offered: ...").
- **Known limits.** A list shows 50 chips; the rest of a long bookmarked list are counted and can be taken off only by Clear. A
  chip's mark (a tag or person gone) is as the navigator last read the section: a rename made in another tab shows when the
  picker or the navigator reads again. Back steps over the changes made to an open search (decided above).

### The library view's selection details navigate, and a photo has a way back *(owner, 2026-10-04; built in wave 3A, #780, #781)*
The right pane of a library view is two halves (`web/tagpup/selection-panel.js`). **Navigation**, above: Folders to Organize (as
before), **People jump** and **Keyword jump** -- each person, and each keyword, the selected photos carry (the server's tally, so the
whole selection and not the cards the page holds) a link that opens the People view of that person or the Keywords view of that tag
(the tag and everything under it), as a click on the navigator's row does: the address names the view, the navigator shows it
selected, Back returns, and the selection is left behind as it is whenever a view opens. Keyword jump leaves out the tags that are
the people already listed and a folder offered to Organize; a list shows 12 and "and N more". A link has an address (`viewSearch`), so
a Ctrl-click opens a new tab. **Tagging**, below, is a collapsible section that starts closed -- the choice is kept in this
browser, `tagpup.selectionTagging` -- holding the people and tags with their arrows and x, and the bulk add of 9d-2, as they
were; Suggest's auto-apply is not in a library view. Organize's panel is not split. *Decision:* the person's link is the tally's
`people` (the server's own list: a person is a leaf name, a branch is never one); a tag is a person's tag when its leaf is a listed
person's name; no query was added and no route changed.

A photo opened over a view (the magnifying glass, Enter, the arrow keys with no photo open) is shown in the details panel, over a grid
the browser hides and so forgets the scroll of. `web/tagpup/view-left.js` is the one owner of **the view as left**: when the photo is
shown it keeps the grid's place (`state.viewLeft`: the scroll offset and the photo at the top of the view, which holds the place if
the order changes) and makes the photo **one place in the history** (a state with `photo`, however many photos are stepped through;
`history-entries.js` is the owner of the places the page makes, moved out of `library-view.js`), so the page's **Back** button,
**Escape** and the browser's **Back** all return to the grid as it was left and Forward opens the photo again. The selection (ids, or a
whole source less the ones left out), the order, the address, the navigator's open rows and the search box are not copied: they are
the page's and are not touched while a photo is open. The grid comes back through `openFolderView`, which asks `restoreViewAsLeft`,
whichever way the photo was closed (a delete of the open photo, a bulk delete that took it). Another view opened over the photo
forgets the place (it was the first view's); a search's `searchBack` goes with the photo's place, one place further
(`searchPlacesBack`). After a reload the view comes from its address and a Back finds the place the view's own history entry kept,
best-effort; the selection is not kept.

The grid keeps *a place*, not an offset (`vgrid.js`: `placeOfView`, `showPlace`; `relayout` reads the row at the top from the cards where
they are). A real browser showed why: a card's height is measured when the grid is laid out and can differ by a pixel or two when it is
shown again (the width moved), and an offset 1,669 rows down is then 3,300 pixels -- 16 rows -- from where it was; the row was read from
the offset and the stride of a layout that was no longer the rows' own. Measured in headless Chromium on a copy of photo_index (a search
of 20,995 photos, Select all less one, scrolled to row 1,677; `scripts/measure_photo_back.py`): before, no Back button, Escape left the
photo open and the browser's Back left the page; after, the button 43 ms, Escape 31 ms, the browser's Back 28 ms (medians of 3) to the
painted grid, the same row at the top, the same selection and address in 3 of 3. A People jump, click to the People view painted: 130 to
171 ms.

### Camera and lens words *(built 2026-10-09; migration 27)*
A term typed in the search is also looked for as a prefix in the photo's camera and lens (`search_index`'s third table,
`search_gear(camera, lens)`; docs/DATABASE.md): "canon", "r6", "eos r6", "pixel" and "24-70", "ef24", "f/2.8" (a letter-digit
run is also indexed split, so a lens spelled `EF24-70mm` is found by `24-70`). **Search only**: the views' navigator has no
place for a camera or a lens (Folders, dates, Keywords, People), so there is no facet and `photo_meta` gains no column.
The words are made from the photo's raw metadata by the one extractor (`photo_meta.gear`) in the transaction of every write of
it (`derived._put`, the index's record, bulk tag writes, sync, the journal's undo) and by `derived.rebuild_all`, the
doctor's `--rebuild-derived`. **Migration 27 reads no photo's JSON**: it makes the camera from `photo_meta`'s make and model
(photo_index copy: 1.0 s with its checks; the fill 0.5 s), and the lens comes with the rebuild (whole rebuild of a photo_index
copy: 21 s, one transaction, nothing changed if interrupted). **The lens fields are asked for now** (2026-10-09, owner's
decision): `fields.METADATA_FIELDS` holds `LensModel`, `LensMake` and `LensID` (and `EXIF:`/`XMP:`/`Composite:` forms), so every
read records them -- 0 of 68,324 rows of photo_index held one before. **A row says which read it comes from**:
`TagPup:ReadGeneration` in its `raw_metadata` (`fields.READ_GENERATION`, 2 = the lens fields are read; findings #1019), written
by every full read (the indexer's, sync's, refresh_rows', a save's read back) and absent from a row read before: a photo with no
lens has no lens key either way, and only this tells "read, holds none" from "read before". No migration, no stamp on existing
rows. **The re-read** of the rows read before is `tagpup_cli.py reread-fields` (`tagpup.services.reread_fields`, on the
refresh's own reading and edits): metadata only (ExifTool; no picture decoded, no model, no graphics card, no file written;
faces, names, embeddings untouched), a dry run by default that counts and times a 100-file sample, `--apply` writing 2,000
photos to a change of the journal (so it is resumable and undoable chunk by chunk; the derived tables, `photo_meta` and the
camera and lens words, are rebuilt for exactly those photos by the journal's own write, so no doctor run is needed),
`--folder` for one folder first. **It writes a row's `raw_metadata` only** (and a `document_id` where the row has none): never
tags, captions, people, modified time or size, and prints per chunk how many rows differ from their file in each field. A row
whose tags, captions or people differ from the file's is "disagrees" and left for sync or refresh_rows (findings #1024). Left
alone and counted: rows never read, rows found damaged before (`ExifTool:Error` in their raw_metadata: not tried on every run),
files missing or unreadable, files changed since indexed (sync reads those with the same fields and sees the size change
`library.reread_resized_pictures` is about; this never writes a new stamp over it), photos on a network share nobody named
(`--folder` or `--shares` names it; every look at a share is bounded, and after the first that does not answer in 10 s the rest
of its rows are left), a row the app saved meanwhile (read by the next run). **Stops** over the whole run, not per chunk: at a
chunk whose every file is gone (a drive unplugged, said once, not 34 times), and when a run that has read none of 10 or more
files finds ExifTool cannot be started; merely unreadable files are counted and the run ends as it began (findings #1023). Two
runs at once, or one beside the app, are held apart by the journal's expected values: the second to write finds the row changed
and skips it. The camera's placeholder lens `24.0-70.0 mm` (167 of 1,499 kr-track photos) is kept as the lens and found by
`24-70` (the words also hold a spelling without a pointless `.0`; findings #1027). Raise `READ_GENERATION` when a field is added that rows
already read should gain, and the same command reads them.
**One name for a camera** (`photo_meta.camera_name`; findings #1003): `fields.camera_of`, by which Shift Date Taken's list
and the photos a shift takes are chosen, is the same rule, and the page takes the name from the record's `camera` and keeps no
copy of it. Image Details shows `camera · lens` on one line (`record.camera`, `record.lens` from `photos.page_record`; hidden for a photo
that names neither). A search of a term costs one more FTS5 probe: "canon" (48,824 photos) 67 ms, "r6" 17 ms on a copy.

### Backlog: which photo fields are searchable *(owner, 2026-10-04; after phase 9)*
A later review of which metadata fields search offers, as members (all of / any of / none of) and as words. Named
so far: **camera make and model** (already columns of `photo_meta`, phase 9a-1), so a time shift can be aimed at
one camera's photos (a camera whose clock was wrong); **lens** (not in `photo_meta` yet: a column made from the
file's metadata as the others are, read from `raw_metadata` by the derived-table rebuild, no file re-read if the
indexer already keeps the lens tag -- check first). Author is not used and stays out. Not part of 9e. *Camera make and model, and the lens, are searchable words since
2026-10-09 (above); a members filter (all of / any of / none of) by camera is not built, and a time shift aimed at one camera
still takes its cameras from the folder's own photos, by the same name.*

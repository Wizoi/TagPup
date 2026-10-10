# TagPup architecture, history: Roots and machines

Moved verbatim from `docs/ARCHITECTURE.md` on 2026-10-09 (trunk 17afc1b), when that file became a map.
Nothing in the sections below was edited, so a number, a date or a branch name in them is as it was on
the day it was written: read it as a log, not as the current design (the map, [../ARCHITECTURE.md](../ARCHITECTURE.md),
and [../INVARIANTS.md](../INVARIANTS.md) are current). Index: [README.md](README.md).

Contents: Roots and machines (the root-relative stored path, adoption, verify, relocate) and folder ids (the .tagpup marker, following a renamed folder).

Sections in this file:

- Folder ids *(owner, 2026-10-08; built, branch `worktree-agent-a83e22241d527a5ec`)*
- Roots and machines (design *(2026-10-02)*; root-relative stored path approved by the owner, 2026-10-02; stages 1 to 4 built)

---

### Folder ids *(owner, 2026-10-08; built, branch `worktree-agent-a83e22241d527a5ec`)*
Following a renamed folder by what its photos hold (phase 8, "A folder renamed outside the apps") is evidence and a filter, and it fails where the evidence is thin: a folder of photos with no DocumentID and no Date Taken, a rename that changes the date at the start of the name, a folder moved to another parent. A folder that carries its own id follows any rename exactly. It is opt-in, and it is the key folder tags come to hang on later.
- **The marker** *(owner, 2026-10-08)*. A file named `.tagpup` in each **leaf** folder -- a folder that holds photos directly; a folder holding only subfolders gets none -- hidden on Windows (the hidden attribute set as it is written). A hidden file in each leaf folder is acceptable on the server master as well as on a working copy. It holds a list of entries, a line each: a library's identifier and that library's id for the folder (a random UUID), and nothing else -- no name, path, date or anything that names a person. No other TagPup file is called that (searched 2026-10-08). A file of that name that does not parse as that list is not a marker, is never rewritten, and is reported. It is written only by an explicit command, never by indexing, sync or the watcher.
- **The folder id is per library.** A folder in several libraries holds one entry for each, and an entry is added when the folder is added to another library (the same explicit command, run for that library: idempotent, a folder already carrying the library's entry is adopted and skipped). The file is rewritten whole to a temporary name and renamed into place, every other line kept byte for byte, so an interrupted run leaves the old file or the new. **A library never rewrites an entry another library wrote**, nor one that another location of the same root records.
- **The library identifier.** There is none today (searched 2026-10-08): a library is known by its file name and path (`tagpup.core.library.Library`), the settings are a validated registry of the owner's settings and not a place for an id, `schema_version` records migrations, and the only unique ids in the code are DocumentIDs, which belong to photos. A file name will not do: it changes when a library is copied, renamed or restored. **Needed, and proposed, not built**: a migration (additive, creating an empty one-row table `library_identity`: `id`, a random UUID, and `stamped`) which opening a library never fills; the id is stamped by the explicit command that first marks a folder for the library (`folder-ids mark --apply`), in the same transaction, and by nothing else. Stamping writes the owner's library and cannot be taken back (an id handed out in markers is in files), so it is the owner's to approve before it is built. A snapshot restored keeps its id -- it is that library -- while a library file copied for a trial carries the same one: a second library file in the data folder with an id already seen is refused by `folder-ids` and named by `tools/doctor.py`, since two libraries answering to one entry would follow each other's folders.
- **In the library.** A table `folder_ids` (a migration, additive, empty): `id` (this library's id for the folder, the key), `path` (the folder as last seen, in the stored form, `@name/rel` under a root), `marked` (when). A primary table, journaled, not derived: the derived `folders` table (9a-1) is rebuilt from the photos' rows and its integer ids change with them, which is why a folder tag cannot hang on it. Folder tags, when they come, reference `folder_ids.id`.
- **`folder-ids mark`** (a dry run unless `--apply`): for the leaf folders the library holds, it counts those it would mark, those already carrying its entry, those it cannot write (never written: a read-only share, a read-only attribute, a refused write is counted and left, never made writable), and those whose marker does not parse; `--apply` writes the entries and records the ids in one journaled change. A library that never runs it never has a marker written for it, and `relink-folders` is what it was.
- **Relinking by the marker is exact and automatic** *(owner, 2026-10-08)*, per library. A sync of any scope (the watcher's, which syncs the folder a folder was renamed in, included) that finds a `folder_ids` row whose recorded path is gone, and a marker carrying that id under this library's identifier in a folder where it found files new or moved to, or beside the folder that is gone, points the rows under the old path at the new folder (ids, faces and names kept; the destination checked first, as relink does), the `folder_ids.path`, and the roots and ignored folders under the old path, in one journaled change, with no evidence needed.
- **A copy** carries the marker, so every entry in it, and two folders then hold one id of a library. Per library: the folder whose recorded path is gone takes the link (it was moved or renamed); if the recorded path is there and another folder carries the entry, that one is a copy, nothing follows it, and the library may give the copy a fresh id of its own -- only its own line, and only when no other location of the same root records the folder (a mirror of the share holds the same marker at the same place under the root, and is the same folder, not a copy). If the copy cannot be written, it keeps the shared id and is reported; a shared id never moves a row. Three folders with one id: the recorded path keeps it, the others are copies.
- **Decided** *(owner, 2026-10-08)*: `relink-folders` does **not** mark a folder it has followed; the sync report only prints a hint that `folder-ids mark` exists (when folders are wholly gone and the library has marked none).
- **Built**, in the layers' places: `tagpup/files/folder_marker.py` (the file: parse, `with_entry`, stage/publish/discard, the hidden attribute, `find`), `tagpup/store/folder_ids.py` (`library_identity`, `folder_ids`, twins), `tagpup/services/folder_ids.py` (`mark`, `follow`), migration 26 (additive, both tables empty; it lists only `library_identity` among its `touches`, so the schema-gap rule that decides whether an older change may still be undone is unchanged: no change of the journal can have recorded a row of a table that did not exist), `folder_ids` in the journal's `KEYS` and `NAMED`, its `path` converted by the roots machinery (`store.roots.row_value`) and by `roots adopt` / its undo (`store.adoption.TABLES`). `tools/doctor.py` names a second library file carrying the identifier.
- **The order of a mark** (crash-safe at every point): (1) every file is **staged** under a temporary name beside its marker (`.tagpup.<random>.tmp`, hidden), which finds the places that refuse a write *before* anything is recorded; (2) the ids of the staged files, and the identifier with the first of them (`library_identity`, stamped by `journal.apply(..., also=)` in the same transaction as the change's rows), are recorded as ONE journaled change, `mark_folders`; (3) each staged file is renamed into place after checking the marker is still as it was read, and read back. A crash after (1) leaves a stale temporary file (swept after an hour); after (2) an id whose file the next run writes again (*restore*, the recorded id, never a second); a marker holding this library's line and no row (an undone change, a table restored) is *adopted*, not given another id. A snapshot restored from before the first mark brings back a library with no identifier while the folders still carry the old one: `folder-ids mark` then says some folders hold "a line of an identifier that is not this library's -- another library's, or this one's from before a snapshot restore", keeps every such line, and adds this library's new line beside it (the old line follows nothing; to have the old identifier again, restore a snapshot taken after the first mark; nothing else puts it back) (#970). Two runs at once: the second's rows break the unique path and the whole change is refused before it writes; its staged files are removed. A file replaced meanwhile by another program (or another library marking the same folder in the same instant) is left as that program wrote it and counted; the next run adds the line. The one gap a rename cannot close -- two libraries replacing one file in the same instant -- is read back, counted and healed by the next run (#969).
- **Following**: the one place that decides "this new folder is a lost marked folder" is `folder_ids.follow`, a step at the start of the tail of **every** sync, whatever its scope -- the watcher syncs the folder a folder was renamed in, not the library, and a step only in the whole sync let it queue the renamed folder as new, indexing fresh rows beside the old ones (reviewed 00c5d16, #974). It runs after the sync's own change and before any folder is queued, when the sync found a file missing or moved and the library has marked folders, and looks only where the sync already looked: the marked folders asked about are those of the missing or moved rows (one stat each), the markers are read in the folders where it found files new or moved to and under the folders it would only review, and, for an id still not found, beside the folder that is gone (one listing of its parent, the same path under each sibling that no other marked folder is recorded at); never a walk of a root (measured, #975). A folder none of whose rows can go -- every file already has a row of its own -- is **left and reported**, its id not moved: moving it would say "followed" and leave the old rows and their named faces missing for ever (#974). Before a folder is queued as new its marker is read (`folder_ids.lost_among`: one read per new folder, no ExifTool, no stat of a recorded folder): a marker carrying this library's entry for an id recorded for another folder makes that recorded folder one to follow whatever this sync's own missing and moved counts are -- a marked folder moved into a folder the library holds shows the sync of its new parent nothing missing or moved, and its source's sync cannot find it (#980); asked only of a library that has marked folders, 7 ms for 300 new folders without a marker, about 3.5 ms for each that has one. `relink-folders` (no sync) looks beside the folder only; a folder moved to another parent is found by the sync that finds its files there. **Undone is done again**: `undo` of a `follow_folder_markers` puts the rows and the id back at the old folder, but the marker in the new folder still carries the id, so the next sync that finds the old folder gone follows it again (#981). To stop it, take the line out of the folder's `.tagpup` (or delete the file; there is no command that removes a marker yet) and then `undo`; `folder-ids mark` writes the recorded id again into a folder whose marker is missing. What a sync could not follow is not silent: the counts `left` (its files already have rows of their own) and `not_found` are printed by `sync` and logged by the watcher when not zero (never names) (#982). A root kept in two places on the machine holds one folder, not a copy (places are compared as the library holds them). Photos pair by the file of the same name that has no row, else by DocumentID or size and Date Taken (`folder_moves.pair_by_evidence`); a file with a row is no destination. The change `follow_folder_markers` is undone by History like `relink_folders`, the folders added following back. Not done: following by marker a folder tag (folder tags do not exist yet).

### Roots and machines (design *(2026-10-02)*; root-relative stored path approved by the owner, 2026-10-02; stages 1 to 4 built)
The owner's model *(2026-10-02)*: the library is rooted at the share `\\idziserver\Pictures`
(`D:\ServerFolders\Pictures` on the server). Everything under it is the family's catalog and
part of the backups; what TagPup **indexes** is a subset of folders under it (the library's
roots, less its ignored folders). Today photo_index indexes a desktop copy of the share's
`Pictures\` subfolder (`D:\Training\Pictures`), kept there so the vectors run on the desktop's
graphics card. The master is the share, not the copy. Later the indexing may move to the server
(not now): a server component and a client-only TagPup. The server has no GPU and 8 GB today;
the design assumes a GPU and more memory later and does not wait for them.
- **A folder outside every root** can be opened and tagged (the disk-folder source) and uses the
  database, but cannot be added to the index; adding says it is outside the roots and where to
  move it. The owner tags, verifies, then copies the folder under a root by hand; sync lists it
  for Include / Ignore.
- **The problem.** Rows hold absolute paths in this machine's spelling: `photos.path`,
  `change_files.path` and `new_path`, `added_folders.path`, `damaged_files.path`, and the
  settings `library.roots` and `library.ignored`. Moving the library, or running it from the
  server, re-spells every one at once; re-pointing rows once made 233 duplicate faces.
- **Proposed: a stored path is a root's name and a path under it**, so the library says
  "`pictures`, `Pictures\2024\...`" and not where the machine keeps it.
  - The library holds its roots (name and the share's own address). Each machine holds where it
    keeps each root, in `TAGPUP_HOME` beside the libraries (`tagpup.config`'s concern), not in
    the library, which moves between machines. Longest match wins, so the desktop maps
    `\\idziserver\Pictures\Pictures` to `D:\Training\Pictures` and the server maps
    `\\idziserver\Pictures` to `D:\ServerFolders\Pictures`.
  - **Native in memory, root-relative in the database** *(refined 2026-10-02, after the audit of
    about 150 places that open, walk, rename or delete a file by a stored path)*. Everything above
    the store -- the walks, ExifTool, the watcher, sync, renames, the browser, the lock files --
    keeps handling this machine's native path, as it does today, and does not change. Only the
    boundary with the database converts: `tagpup.core.paths` gains `to_row()` (native to a root's
    name and the path under it) and `from_row()` (back), `sql_equals` / `sql_under` / `sql_in`
    convert their argument and still answer a range on the same indexed column, and the store's
    reads and writes of a path column call the two. A photo under no root (a tag-only folder, which
    can have rows) keeps its native path, with no mark (decided 2026-10-02): a native absolute path
    starts with a drive letter, a separator or `/`, never `@`, so the two forms cannot be confused.
  - **The row form, as built in stage 1** (`tagpup.core.paths`, tested by `tests/test_paths_roots.py`):
    `@name` is the root itself, `@name/a/b.jpg` a path under it, with `/` on every machine and the
    case the file has; a root's name is `[a-z0-9_-]`, 1 to 32 characters, lower case. The name ends
    at the first `/`, so `@photos/` is never a prefix of `@photos2/`, and `sql_under` is still one
    range on the NOCASE index (the prefix, and the prefix with its `/` raised by one); a folder
    above a root's location adds the root's own range to an OR. `from_row` refuses a part that is
    empty, `.`, `..`, holds a colon or a backslash, or an empty part after `@name/`, and matches the
    root's name in any case.
  - **The machine's map** is `TAGPUP_HOME/machine_roots.json`, owned by `tagpup.config`:
    `{"version": 1, "roots": {"pictures": ["D:\\Training\\Pictures"]}}`, UTF-8, the first location
    being where a path is put and all being recognised. Absent means nothing is mapped; malformed
    is an error naming the file, never an identity. Refused at load: a location that is not
    absolute or starts `\\?\` or `\\.\` (the long-path and device prefixes), one place listed twice or under two roots, a root's own places
    (locations and share address) nested in one another, a share address equal to another root's
    location. A UNC share root with or without its trailing separator is one place. Nesting across
    different roots is allowed and the deeper wins. The map is re-read only when its (mtime, size)
    changes; an operation builds one `Roots` and passes it down.
  - **A root with no location on this machine is refused both ways** (`UnmappedRoot`): `from_row`
    of its rows, and `to_row` and every `sql_*` of a path under its share address, so a row is never
    written that cannot be read. So is a path under no root when such a root exists, since it might
    be its. The message names `machine_roots.json`, its path, the root and the line to add. A
    library with roots opened on a machine with no map therefore refuses; it does not behave as
    if nothing were rooted.
  - What the audit adds to the change: the paths inside JSON (`photos.raw_metadata`'s `SourceFile`,
    `suggestions.raw`), the journal (`change_files`, `change_rows` values of `photos.path` and the
    roots settings, which an undo replays), the six raw-SQL comparisons that bypass `key()`
    (`store/photos.py` 163 and 771, `store/faces.py` 589, `store/inspection.py` 116,
    `store/file_journal.py` 341), and the two-copy files beside the library (the suggestions JSON).
    The journal holding root-relative paths makes an undo portable between machines.
  - One migration, with a dry run, a backup and a doctor check: each table's prefix is rewritten
    in one transaction, a row under no root is reported and not guessed at, and the journal keeps
    what it was. Moving a library afterwards is a change to the machine's map, not to rows.
  - Cheaper alternative, not preferred: keep absolute paths and add a journaled "relocate root"
    that rewrites a prefix (Lightroom's "update folder location"). It costs less now, but a
    server and a client cannot then share one library, and every move is another bulk rewrite.
- **Stage 2, built** *(2026-10-02: the library holds its roots, the store converts, an explicit command adopts)*:
  - **Nothing converts a library unasked** *(owner)*. Migration 18 (additive) makes the empty `roots` table and
    nothing else; a library with no roots has `core.machine.IDENTITY` for its Roots, which never asks the map, and
    every store function is then what it was, byte for byte. The explicit command is the one step that converts.
  - **`tagpup.store.roots`**: the library's roots (`listing`, `every`, `insert`, `delete`), and `roots_for(conn)`,
    the `paths.Roots` a connection converts its paths by, built from the table and the machine's map
    (`core.machine`, answered by `config.roots_of`). It is kept on the connection (`db.Connection.roots_state`;
    `db.connect` makes them), so an operation that holds a connection converts every path of it by one Roots and
    asks neither the table nor the map a row: re-read only when `PRAGMA data_version` says another connection
    changed the library (2.6 microseconds, outside a transaction; inside one, nothing), and the map looked at again once a second at most (a
    stat; the same Roots back when the file is as it was), so an idle connection finds an edited map. `pinned(db_path)` holds one Roots
    for every connection of a library in this process for a whole run -- a map edited meanwhile changes
    nothing until the run ends, and a change of the library's own roots by another process stops it
    (`RootsChanged`); the adoption, the journal and every `db.write_with_connection` hold one Roots for their whole
    length by holding one connection. A write prepared before another process adopted the library is refused
    at its commit and run again (`write_with_connection` retries it, `roots.unchanged`); a write on a connection the
    caller commits (the index's `record_indexed`, `remove`) begins its transaction first (`roots.begin_write`), so the
    roots are the library's at the moment the write lock is taken. The operations that pin -- an index run, a sync pass, a file
    change -- are pinned in `services` (stage 3, below); one that is not holds the Roots of its connection, which keeps
    a single operation consistent.
  - **The boundary.** Every read of a path column returns the native path and every write stores `to_row` (idempotent:
    a path already in row form is written as it is). `store.roots.sql_equals` / `sql_under` / `sql_in` convert their
    argument by the connection's Roots and answer the same ranges on the same NOCASE indexes (checked with
    `EXPLAIN QUERY PLAN` on a library of 68,466 photos: `SEARCH`, never `SCAN`, for a photo by its path, a folder, a
    folder's own photos, a photo's faces); `tests/test_roots_store.py` fails a store module that calls `paths.sql_*`
    without the connection's Roots. The six comparisons that bypassed it (`photos.row_as_recorded`,
    `faces.counts_on`, `inspection.ids_of_stored`, the lookups in `store/photos.py`, `faces_pending` and
    `file_journal`) convert their argument. Rows read in a loop are converted after they are read, never a function of
    the column in a WHERE. **Order**: an `ORDER BY path` over rooted rows sorts `@pictures/...` among native paths, so
    what the owner sees listed in path order (`damaged_files.every` / `under`, `inspection.ids_and_paths`,
    `whose_file_is_gone`) is sorted again after the paths are native, in the order the NOCASE index gave
    (`roots.ordered`); every other `ORDER BY` is by id or time.
  - **Caches of native paths.** The Identify Faces grids, the folders the watcher watches and the index hold
    paths of this machine, built while a generation of photos or faces stood; moving a root in the map moves no row,
    so `store.generations` adds a salt -- the library's roots and where this machine keeps them, 0 for a library with
    none -- to the generations of photos and faces, and every cache keyed by them is built again when the map moves.
  - **The paths inside JSON.** `photos.raw_metadata`'s `SourceFile` is ExifTool's spelling of the path (forward
    slashes) and is compared with a fresh read of the file (`refresh_rows.differences`), and after a rename it names
    the old file, so it is converted, not derived: `@pictures/2024/a.jpg` when the file is under a root and
    converting back gives exactly the string (any other spelling stays as it is, which loses nothing), read back
    native on every read of the column (`roots.raw_to_native`). `suggestions.raw`'s `path` and each
    `nearest_neighbors[].path` are converted the same way, only where the round trip is exact.
  - **The journal speaks one form** (`journal._canonical`): an edit's values are native, the library holds rows;
    both sides of every comparison are converted, what a change records is the row form, and a change recorded before
    the adoption (native) is converted as it is read, so undoing it writes the rooted form for a rooted photo, never
    a native row beside the rooted one. `history` shows what was recorded. The folder settings are shown native
    (`store.settings`), and their old and new values in the journal hold the row form.
  - **`roots repair-address`** (`tagpup.services.roots.repair_addresses`, `tagpup.store.adoption`; the CLI's `roots`
    group; no MCP tool): a share address starts with two backslashes, and Git Bash turns a leading pair in an argument
    into one, so `roots adopt` refuses an address that starts with a single separator (#914) and this command gives a
    root already stored so its two back. A dry run unless `--apply`; one journaled change (`roots repair-address:
    <names>`) that `undo` reverses, refused once the address is no longer the one the repair left (a hash of it is kept,
    never the address). It takes no backup (one text column, the journal is the way back) and, like any change of the
    library's roots, stops runs in other processes that hold them (`RootsChanged`): run it with TagPup and TagTuner stopped.
  - **`roots adopt --name pictures --address <share> --location <folder here>`** (`tagpup.services.roots`,
    `tagpup.store.adoption`, the CLI's `roots` group; no MCP tool: a one-way step on the owner's data is theirs, not
    a tool's): a dry run unless `--apply`. The dry run counts, for each table, the rows it would convert, the rows under
    no root (grouped by folder with `paths.outside_roots`; they keep their native path), rows that would not convert
    back, rows that would become one, the settings it rewrites, and says why it would be refused; it reads and
    writes nothing. `--apply`: the map first if it lacks the root (atomic, one editor at a time, only then), then in
    ONE transaction under the library's write lock: a new backup of the library (always a fresh one, under the
    lock, so it is the library as it stands; a full copy, so another process's write waits for it -- a note beside
    the library tells that write why if it gives up, `db.busy_note`, and the dry run and `--apply` say to run it with
    TagPup and TagTuner stopped and how long to expect, from the speed the last copy ran at, kept in the backups
    folder), every table converted, verified before it commits (row counts equal, every rooted row converts back, no
    row is held under one root though it lies under another's place, the map places the root where the rows were
    converted by), one journaled change, `roots adopt: pictures`, whose summary holds counts and never a name. Every
    place the map lists for the root is equivalent: a row under any of them converts, taking the first one's spelling
    (counted as respelled). **One root holds each folder**: nested roots are the model's (`core.paths` resolves
    them, the deeper winning) and are refused at adoption, in either direction -- a root whose place, or address, is
    under, over or the same as another root's of the library -- naming both, since moving the outer root's rows under
    the inner would be a conversion an undo could not reverse once a rename or an index had touched them. It refuses,
    writing nothing: a root of that name already, a nested root, a location that is not a folder here, a location no
    row lies under, a map that places the root elsewhere, a row that is not an absolute path here or does not convert
    back, rows spelled by the share's address (named as their own count and folder list: converting them would
    retarget them from the master, the share, to this machine's copy; fix their spelling first, or adopt a root whose
    location is the share), two rows that would become one when at least one of them converts (two rows of one file
    that both stay outside the root are only reported as duplicates), another process holding the write lock, an
    unfinished change of photo files. `undo` reverses it (`adoption.undo_in`: the same conversion back by the map,
    verified) and also converts back the paths that later changes recorded, in the same transaction, so those changes
    stay undoable in a library with no root: `change_files`, and in `change_rows` only the values that hold a path
    by their structure (`photos.path`, the two folder settings, the `SourceFile` of `raw_metadata`, the path fields of
    `suggestions.raw`) and only when the value is a row of the undone root; no text is searched, and a title or an
    address with an "@" in it is the same bytes afterwards. The dry run says plainly before `--apply` what it will do
    and which roots remain (the undo's rehearsal notes, which the CLI prints). It is refused only when this machine
    does not place the root, or a later change recorded a path of the root that cannot be converted back (named).
    Every step is `_reached`, and tests stop the process at each: the library is exactly as it was. A refused
    `--apply` still migrates the library to schema 18 first (additive and empty, *decided 2026-10-02*).
  - **Checks**: `tools/doctor.py` and the MCP's checks gain `rooted_rows_convert` (rooted rows that name a root the
    library does not have, that this machine does not place, that are not what `to_row` writes, or that are held
    under one root though they lie under another's place) and
    `native_rows_under_a_root` (a write made between another process's adoption and its own commit, the one race
    nothing can close), and the doctor lists the photos under no root, by folder. A library whose map does not place
    a root is told of at once (`services.roots.problem`, which the CLI prints when it opens the library) and its
    paths are refused with a message naming `machine_roots.json`, its path and the line to add: never an empty library.
  - **What the owner sees**: nothing, until they run it; then `roots` lists each root with where this machine keeps it,
    and History lists `roots adopt: pictures` with its counts. On photo_index's shape (68,466 photos, 225,000 faces,
    made here) the dry run takes 1.5 s and `--apply` 5.9 s with the backup, an undo 6.6 s.
  - **Stages 3 and 4, built** *(2026-10-02: TagTuner's Roots, the server's answer for an unplaced root, pinned runs, the sandbox's own map)*:
    - **Verify** (`tagpup.services.roots_verify`, read-only: no row, no file, never the map). For a root and a CANDIDATE
      location -- a hypothetical map, `paths.Roots` built from the library's roots with the candidate in the root's
      place -- it reads the root's rows as the library holds them (`store.root_rows.stamps`: path, mtime, size; never a
      BLOB; 0.08 s on photo_index's 68,466 rows) and looks at the disk: per row **matches** (size and modified time as
      recorded, `store.photos.describes`), **differs** (there, and changed since indexed, or a copy with other times:
      sync's to settle, never "missing"), **missing**, **unreadable**; and, in a full run, the photos at the place no
      row has, the rows under no root grouped by folder (`paths.outside_roots`), and the rows kept native where the
      place would be the root's (they would be missed by every lookup and indexed again). **A place that cannot be
      reached is not "all missing"**: a drive not connected, a share away, a folder not there, a share that stops
      answering half-way are each said (`state`: `no_drive`, `away`, `no_folder`, `unreadable`) and the rows not looked
      at are not counted. Every look at the disk is a thread waited for 15 s at most; a share that did not answer is
      "away" for 30 s by its drive or server and share, and no second thread is started for it (`damaged_photos` has
      its own away-cache, which cannot say missing from away, so Verify keeps one). A **sample** (2,000 rows, at least one
      from every folder -- 2,674 on photo_index, so about 2,700 -- the same rows for the same library, visited in a
      spread order so one cut short has seen folders from everywhere) lists each folder once, answered in the request
      within 25 s; **all** walks the place, listing each folder once, and is a job (`tagpup.jobs.verifying`): its
      progress and cancel (between folders) are this process's, its claim and its result are the library's `job_runs`
      (`verify root <name>`), which the Activity page lists, so a second Verify of the root -- another tab, another
      process -- is told one is under way. Measured on a synthetic library of 68,466 rows in 2,674 folders (real small
      files, a warm local disk): the sample 1.0 s, all 1.0 s; a share's cost is the 2,674 listings, which the
      deadlines bound.
    - **Moving a root** (`tagpup.config.set_location` / `change_back`, `tagpup.services.roots_location`): the map's edit
      alone, never a row. `set_location` puts the new place first and keeps the old after it (so Change back is the
      reverse: the first two swap); **only the current and the previous place are kept**: a move drops anything older
      (a place the map still lists is a place a stale spelling is recognised by), and the dialog says so, written whole to a temp file and `os.replace`d under the one `_edit_lock`; it
      refuses a place that is not absolute, has the long-path or device prefix, does not exist, is another root's, or
      is nested with the root's own other places, and, given `expected` (the place the page saw), a map that moved
      since -- another tab, a hand edit. The service asks first: a **dry run** shows Verify's sample of the new place;
      a **poor result** (more than 5% of the rows looked at missing, an unreachable place, rows kept native where it
      would be) is refused unless the request says `override` -- but a drive or folder that is not there is refused
      whatever is said, and so is a place that would put the root inside or over another root of the library (the same
      photos reachable under two roots). It is refused **while TagTuner's own runs or a Roots verify are running or queued** -- an index
      run or Suggest in this process, its watcher's sync, a verify -- and, from the library's own `job_runs`
      (any process's, ignoring a run whose process has ended), a recurring job; and names which. **TagTuner cannot see
      TagPup's Suggest, a CLI index or a CLI sync** (no cross-process lock was built, *owner, 2026-10-02*): the refusal
      and the dialog tell the owner to stop those before moving a root. A run that is going keeps the map it started
      with (pinned) and so keeps writing to the old copy until it ends. The machine's map is one for
      every library of the home, so a root of one name in two libraries moves in both: the dialog says which others use
      it, and a run queued or running in any of them holds the move up too. Two requests at once:
      the claim of the change in `job_runs` lets one in and tells the other; a second Confirm, or a tab that came after
      and found the root already at that place, changes nothing and says so. Each change is a run in `job_runs`
      ("moved root pictures to X (was Y)", which the Activity page lists: a run may say what it did in `changed.what`).
      A running server needs no restart: a connection that sits idle finds the new map within a second
      (`store.roots.RECHECK_SECONDS`), the generations' salt rebuilds the caches of this machine's paths, and the web
      layer lets go of its folder scans (`tests/test_roots_routes.py`: the next `/api/photos` after a change names the
      new place, and no row changed -- a full dump compared).
    - **TagTuner's Roots** (the gear's "Roots...", `web/tuner/roots.js`; `GET /api/roots`, `POST /api/roots/verify`,
      `/verify-cancel`, `/change-location`, `/change-back` in `tagpup.web.tuner_routes`, loopback only, specified in
      SPEC_TAGTUNER): one small dialog -- a row per root with where it is kept, "Tags and renames are written to files at
      <place>", the earlier place (a separate copy: nothing written now goes there), the last check as a sentence,
      Verify, Verify all (progress, Cancel), Change location (a place typed or picked, Check this place, then Move it
      here -- once; a poor result shows why and needs "I mean it"), Change back. A library with no roots says "This
      library has not adopted a root yet; nothing to change here." and shows the CLI command. A root this machine does
      not place is listed so, and Change location is how it is placed.
    - **The server's answer for an unplaced root** (`tagpup.web.roots_gate`, the MCP's `find_library`): a request that
      needs a photo's path on a library whose root this machine does not place, or whose map cannot be read, is answered
      409 with the sentence that names `machine_roots.json` and the line to add (`services.roots.problem`, kept for a
      second per library) and `X-TagPup-Roots-Problem`; both pages show it as a banner (`web/common/roots-banner.js`,
      fed by `api.js`). The picker, rules, version, Activity, history and Roots are let through, so the page opens
      and the dialog can place the root. The MCP's tools say the same, except `history`, `undo`, `prune_journal`,
      `sync_state` and `query_plan`, which read no photo's path.
    - **Pinned runs** (`services.roots.pinned`): an index run (the CLI's `index`, in its own process, holding the map
      it started with), a sync pass (`services.sync.sync`) and a change of photo files (`file_changes.write_fields`,
      `rename`) convert every path by one Roots however the map is edited meanwhile, and stop with a sentence when
      another process changes the library's roots (`RootsChanged`: the indexer exits 75, which the server's queue turns
      into the sentence; a sync or a write answers a refused Result; a rename raises). A root this machine does not
      place refuses the run in words (`Unplaced`).
    - **The sandboxes** (stage 4): `services.roots.place_in_sandbox` -- called by `measure_identify_faces.py` and
      `measure_suggest_folder.py` after they copy a library -- writes the sandbox's OWN `machine_roots.json` (in its
      own `TAGPUP_HOME`) placing each root of the copy in an empty folder of the sandbox, and fails loudly for a root it
      did not place, a place outside the sandbox, or a map that is not the sandbox's: a converted copy would otherwise
      point, through the machine's map, at the real photos. `generate_screenshots.py` finds its photos from its own
      checkout.
    - **An old place's spelling** *(review of stage 3, #465)*: after a move the previous place is still recognised, so a page that
      still holds a path spelled by it, a bookmark, or a folder typed under it, names the same row. No request takes such a
      path as written: **one ingress** (`tagpup.web.roots_ingress`, a before_request after the gate) turns every
      path-bearing parameter of an `/api/` request -- `path`, `paths`, `folder`, `folder_path(s)`, `photo_path(s)`, in
      the query or the JSON body -- into the first place's spelling (`paths.canonical`: `from_row(to_row(path))` for a
      path under another listed place of the root, the path as given for any other) before any route, cache or
      pre-check reads it. TagPup's folder scans are therefore one entry for a folder however it is spelled, the photo
      file served and opened is the first place's, and a request holding old paths works once the old place is gone.
      A folder to open or add (`/api/folder/scan`, `membership`, `subfolders`, `add`, `index-start`) that exists only at
      the previous place is refused naming both places and the root. The ingress costs a request with no path
      parameter nothing (it returns before opening anything), and one with a path one look per library per second
      (about 9 us and 60 us per request measured; it opened a connection per request, 4.2 ms, before); `/api/roots`
      and the folder box's autocomplete (half-typed text) are left alone. **A page that sent an old place's
      spelling is told**: the response carries `X-TagPup-Roots-Moved: <root>`, and `web/common/api.js` shows the
      banner "The place of root X changed since this page loaded; reloading" and reloads once (not again within 30 s,
      kept in sessionStorage); the page's held paths are not translated. **Accepted**: a Suggest run keyed by a folder's
      old spelling does not survive a move (the run is lost, not wrong). The
      services keep `services.roots.canonical_args` as the second line (the CLI, the MCP tools, jobs): the writes
      (`tagging`, `photos` delete / rotate / Smart Rename / time shift, `file_changes.write_fields` and `rename`), the
      folder scans, Add, `sync` and the photo's details -- not `index_folder`, which runs on the queue's worker and
      is handed what Add and sync resolved; the CLI's `index` resolves its folders itself, through the roots it pins,
      refusing one that exists only at the previous place. An undo of a change recorded before the adoption writes the
      first place's file (`file_journal` reads a path through `paths.canonical`).
    - **Verify, as reviewed**: a result is also poor when more than half the rows checked differ (a stale copy: sync
      would re-read those rows from the files there, replacing tags newer in the rows than in those files, which the
      dry run says and counts); a row the index never read is counted apart ("never read"), not as "differs"; a library
      older than the schema says to open it with TagPup or the CLI once. A write stopped half-way by `RootsChanged`
      says how many files it wrote and the change that recorded them. "A share is away" has one owner,
      `tagpup.files.shares`, which Verify and the damaged photos' lists both use. An unplaced root is a check result
      (`roots_placed`, its sentence in `message`) in `inspect.all_checks` / `check` / `summary` and the MCP tools, and
      the Activity page's attention list carries the sentence.
    - **Verify reads the folder markers too** *(#984)*: for the root's folders that have a `folder_ids` row, the `.tagpup` at the
      CANDIDATE place (the row's `@name/rel` turned into a path by the same Roots built for the candidate; the machine's map is
      never touched, so a root kept in two places is read at the one asked about). Counted as `markers` in the answer:
      **match** (the marker holds this library's entry for the row's id), **differs** (this library's entry for ANOTHER id:
      likely a different folder at that path; only our entry is evidence, so another library's line, beside ours or alone,
      is never a difference),
      **unmarked** (no marker, or none with an entry of ours: the library predates marking, the folder is new, or `mark`
      recorded the id before a publish that was then skipped, as with two libraries over one folder -- sync reads it the same), **malformed** (a hand-edited file: counted,
      never a reason to refuse), **unreadable** and **not there**, with `line`, one sentence for the dialog ("N of M marked
      folders match; K differ; L not marked"; never a folder's name). A sample reads at most 300 folders, each
      once, starting no read after 10 s (the worst case is that plus one look, 15 s), and reads them BEFORE the rows, so a sample
      that runs out of its row budget still has its marker verdict; a full run all of them, with the job's progress and cancel. Each read is made on the bounded thread (a share that
      stops answering ends the run as unreachable, never as "all differ"). Any DIFFERING marker makes the result poor, so
      Change location refuses it, with the counts, unless the owner overrides, as it does for missing files. A library with
      no `folder_ids` row for the root (marked nothing; or behind migration 26, which has no such table: the read-only
      connection answers empty) has `markers: null` and nothing is said. The live libraries are marked
      (the owner marked all three on 2026-10-09; the `folder-ids mark` dry run counted photo_index 2,672 of 2,673 leaf
      folders, kr-track 20 of 20, renton_parkrun 8 of 8, 16 folders of photo_index and 16 of kr-track holding another
      library's line too), so Verify and Change location read markers for real: 300 in a sample, every marked folder in a
      full run. A COPY of a marked
      folder keeps its marker and so matches: a marker proves a folder is the same folder, not that it is the original.
    - **Not built**: `roots remove` (an undo is the way back); Verify from the CLI or the MCP; moving a root while a
      run is under way (refused, by design); a library behind more than one machine map (one map per `TAGPUP_HOME`).
- **Changing where a root lives, in TagTuner** *(owner, 2026-10-02; built, see "Stages 3 and 4")*: for now the libraries stay on
  `D:\Training`, and once the core features are in and trusted the same libraries are pointed at
  the official share, losing nothing. TagTuner's gear (the server-side component's page) shows
  each root with where this machine keeps it, and offers:
  - **Verify** a location, before and after a change, read-only: for the root's rows, how many
    files exist there, match the row's size and modified time, differ (changed since indexed;
    sync's to settle), are missing, and how many photos there are that no row has. It samples
    first and can run over every row.
  - **Change location**: a dry run first showing Verify's counts for the new location, then
    the change, which only edits the machine's map, never a row. The previous location is kept
    and **Change back** is one click. A location whose Verify is poor (many missing) is refused
    unless the owner overrides it. Every change is logged on the Activity page.
  - The two locations are two copies until the old one is retired; the page says which one
    writes go to, so a tag written to the share is not mistaken for one on the desktop copy.
- **Server and client.** The boundary is the HTTP API the pages already use, plus the CLI. The
  server component is the supervisor, the job runners, the models, the database and file
  access; the client is a page or the CLI. Inference is a job the runner starts, so where it
  runs (a worker on the desktop now, the server later) is the runner's business and not the
  callers'. Decided here: nothing but the abstraction is built before the move.
- **Decided** *(owner, 2026-10-02)*: the root-relative stored path, not the relocate alternative.
  Logins and a client on another machine are not part of this: the server keeps listening on this
  PC only until phase 10 (family access), *(owner, 2026-10-02)*. The design here serves the
  library views (phase 9) and a later move of the indexing, not remote users.
- **Before any of it is built:** an audit, read-only, of every place that touches the filesystem
  with a stored path, to size the change; the research on similar tools (`reports/`) read for how
  they relocate libraries.

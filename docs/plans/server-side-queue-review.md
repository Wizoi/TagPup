# Review: one path for single and bulk writes, and a server-side id-keyed action queue

Status: review for the owner's decision, 2026-11-19. Nothing is built. Not a plan yet (docs/plans/README.md); a plan follows the owner's pick.

## The short version

- **Decided so far (owner, 2026-11-19):** the restart rule (a clean stop resumes, an unclean stop undoes a non-atomic change and retries, atomic ones are checked and run again) and the wish that the client only sends and shows server state, keyed by id.
- **Open:** whether to build it, and how much of it.
- **Recommendation:** do **A first, and a narrow B second.** (A) Put every write of photo files behind one server entry point that always journals, so a single save, a bulk and a delete share the same rules. (B) Make the journal the durable queue: a request is committed as a `planned` change (plan up front) before it is answered, and start-up *resumes* it. Keep the new stored data to one small intent table (migration 40), and do NOT move the page's pending map (`pending.js`) to the server yet.
- **Why:** most of B's durability already exists. The journal's `planned` changes ARE a durable queue and `owner` is the claim (counted below: 20 callers of `write_fields`). What is missing is (1) a request that is accepted but not yet planned has nowhere durable to live (an intent is not a `planned` change: Data owner), (2) Delete and the folder time-shift are not under the same rules, (3) the page still holds three queue-like things in memory.
- **The 2026-11-19 hand-over hang is already fixed on the trunk** (lifecycle commits 7335c38f, f604f5a8; `launcher.IDLE_PATIENCE`; plan docs/plans/installer-hand-over-never-hangs.md). "An install need not wait for a drain" is therefore a smaller prize than the brief assumes: it is the cost of at most one 25-file chunk now, not a hang class.

## 1. Today's queues (counted this session by reading and grep, read-only)

| Queue | Where | What it carries | Durable? |
|---|---|---|---|
| The page's write queue | `web/tagpup/edits.js` `queuePhotoWrite` (one promise chain, `state.detailSaveInFlight`), `queueWriteOf` (adds `state.photoWrites[pathKey]` so leaving a photo waits only for its own writes); status UI in `web/tagpup/write-queue.js` (`queueEntry`, `markEntry`, `retryEntry`) | Saves (save-metadata), rotate/mirror, rename-one, Date Taken, clip convert, Apply All suggestions, face naming. 12 call sites in 8 files (grep of `queueWriteOf(`/`queuePhotoWrite(`, definitions excluded). | No: browser memory. A closed tab loses what is not sent. |
| Pending tag changes | `web/tagpup/pending.js`: `state.pending.files` keyed by `pathKey`, `pendChanges`, `takeChunk`, `sendChunk` (CHUNK 25, one group token per owner action), `pendingOf`, `shownTags`; drains through `queuePhotoWrite` | Tag add/remove only, per file, last op wins | No: memory only (the plan says so: `beforeunload` warns) |
| Library view bulk | `web/tagpup/bulk-edit.js` -> `bulk-run.js runBulk` -> `POST /api/library/bulk-edit` (`tagpup/web/tagpup_routes.py library_bulk_edit`, `tagpup/services/bulk_edit.py run/_write/_delete`) | Tags, people, Delete, by photo id and a selection token; ONE request held open until done (up to 5,000 photos) | Its writes are journaled (`write_fields`); the request is not. If the page stops waiting the server goes on to the end. |
| Folder time-shift | `web/tagpup/rename.js applyTimeShift` -> `/api/folder/time-shift` -> `photos.shift_date_taken` (uses `write_fields`) | A camera's photos in a folder, by path | Journaled on the server; the page's confirm text still says it "CANNOT be undone from here" |
| Delete | `photo.js` -> `/api/photo/delete`; selection Delete in `bulk-edit.js deleteSelection`; `services/photos.py delete` -> `files/recycle_bin.delete_file` then `photos.forget_photo` | The file to the Recycle Bin, then its rows | NOT journaled (no `changes`/`change_files` row, no undo) |
| The server's journal | `tagpup/store/file_journal.py` (`plan`, `writing`, `set_state`, `unfinished`, `claim`, `release`), driven by `tagpup/services/file_changes.py` `write_fields` (20 callers in `tagpup/`), `rename`, `settle`/`settle_once`/`_settle_in_turn`, `_settle_stopped`, `_held_or_conflict` | One change per request chunk: per file `planned -> writing -> done / conflict / undone`, before/after as JSON, `owner` = pid:start | **Yes**, in the library file |
| Server-side gating | `file_changes.exclusively`/`batch_exclusively`, `batches.claim`/`stop_all`, `_unsettled`, `_put_off`; `lifecycle.drain/_drain_until` | One writer of photo files at a time per process, chunk by chunk | Per process (memory); the journal is the only per-library part |

Net: **three browser-memory queues** (page chain, pending map, `photoWrites`), **one durable store** (the journal) that holds only what has been planned, and **two actions outside it** (Delete; the whole-request lifetime of a bulk).

### Seam evidence (all from docs/findings.md and docs/history/findings_closed_3001-3250.md)

- **#3208** and **#3210** (wrong, fixed): the drain's own ordering against single saves. Second fix for one symptom: the single place was `pending.js takeChunk`. Both are "two queues, two orderings" bugs.
- **#3202** (wrong, fixed) and its siblings #3209, #3227 (open, low): the page reads a record, a write lands, and the page redraws from the older record. Cause: the client decides "what this photo holds" from the record PLUS its own map of pending ops. A server that owns "what is pending and what the photo now holds" removes this class; a page that keeps the map keeps it.
- **#3203** and **#3211** (fixed): Delete is not a write of the queue; the panel's Delete and the selection's Delete answered "may I?" differently and Delete ran beside the chunk's write of the same file (it is not under `file_changes.exclusively`).
- **#3200** (open, low): a server-side seam: folder remove asks only the index queue, a whole sync in this process is not an overlap. Not a client queue, but it is the same "one list to ask" shape that a per-library action queue would own.
- The hang of 2026-11-19 (memory note installer-hand-over-stuck-drain; fixed on trunk): the old server's drain never returned. It shows what the drain waits for is *busy() + requests*, and that is the part B would shrink.

## 2. Option A: one path for single and bulk writes

**What it is.** Every write of photo files, single or many, enters ONE server entry point: `POST /api/photos/write` taking a list of per-photo operations (tags add/remove, fields, rotate, date, rename, delete) with a group token, which always becomes a journaled change (as `bulk-tags` + `write_fields` already is) and runs under `exclusively`/`batch_exclusively`. Delete joins the journal as an operation whose before-state is the Recycle Bin copy (or a file-only note when there is no Bin). The page has one client for it (a successor to `pending.js`'s drain), and `save-metadata`, `rotate`, `/api/photo/delete`, `bulk-edit`, `time-shift` become thin callers of the same service, then are deleted.

**What it fixes:** the seams above (#3203, #3208, #3210, #3211), because one ordering and one "is this photo busy" answer exist; Delete gets a crash record; the time-shift and library view bulk get the same Retry/Cancel/History language.

**What it costs and risks:**
- Large: 12 queue call sites plus 9 route calls from the pages (counted: grep of `api.json('/api/photo/...` and bulk routes in `web/`). Cut in merges of about 1,200 lines.
- A single save is *interactive*: the owner types and expects it fast. One path must keep a small request cheap (a chunk of one).
- Delete into the journal is a new meaning for an existing idea: History lists changes and Undo undoes them; Delete's before-state (the Bin copy) is not restorable by TagPup (the Bin restores, Windows does, and a share's delete goes through this PC's Downloads copy). Recommended: journal Delete for **crash record only** (like `INDEX_BAKE`: erased when finished, never undone, listed in `INTERNAL`) so it has a settle but no Undo button.
- Keeping `shownTags`/`pendingOf` on the page means the rule stays written twice (see B).

## 3. Option B: a server-side, durable, id-keyed action queue

**What it is.** The page posts an action and the server owns it from then on: queued, in flight, done, failed. The page shows the server's list. Work is keyed by photo id; the server drains one queue per library and resumes after a stop.

**What already exists (Integration and Data owners):** the journal is a durable queue for everything that has been *planned*: `file_changes.write_fields` plans every file (`change_files`: before, after, state) before it touches one; `batches.stop_all` (called under the lock in `lifecycle.drain`) asks each running batch to stop at its chunk, the change stays `planned`, and the next start's `settle_once` -> `_settle_on_a_thread` -> `_take` (dead owner via `file_journal.owner_alive`) finishes it in a cancellable batch (`_settle_in_turn`). That is already "clean stop = resume" for a started bulk. The Integration owner's gap: *a queued edit that has not started has no plan yet* (`write_fields` plans at the time it runs), so an accepted-but-unplanned action lives only in memory.

**What B adds, minimally:**
1. A request is accepted by committing an **intent** (photo id or path, ops, group token, order) before it is answered. The Data owner's answer: this is NOT a `planned` change. A `planned` change has already read each file and fixed `fields_before`/`fields_after` (`file_journal.plan`); one whose owner process is dead is crash debris that settle writes forward, and `_settle_stopped` withdraws its planned rows. An intent not yet read has no before/after and must survive restarts unexecuted, so it is a different concept: **a new table (migration 40; also `journal.KEYS/CASCADES/checks`, `tests/owners.py`) or a new `changes.status` value, which rebuilds `changes` (CHECK constraint; DECISIONS 2026-10-18) and is worse.** Recommended if B is chosen: a small `queued_actions` table, one row per action, deleted when its change is planned (the writer reads, plans and journals as today); the journal stays the record of what was written. The latest migration is 39 (`tagpup/store/schema.py`, counted: last `Migration(39, ...)`); the Data owner notes the three libraries were counted at 38 on 2026-11-09, so whether 39 has run on all three must be counted before 40 may start (one migration in flight). I did not open a library.
2. Start-up resume: `settle_once` also *starts* `queued` changes after finishing `planned` ones (the Integration owner's "settle_once extended").
3. The page sends once and then polls/reads one list (`GET /api/library/actions`), replacing `write-queue.js` state, `pending.js` map, `photoWrites`. This is the big part and the one the Views owner warns about (next section).
4. Id keying. See below.

### The id-keying problem (Views owner)

- The folder page's records carry **no id** for still photos (`services/photos.py page_record` has no `id`; `_rows_under` reads `photo_id` and drops it). Library views already have ids (`lib.ids`, `lib.sel`, `photo.id`).
- A photo has no id when: it is in a Just-look folder (no row by design: `services/file_only.py`; `libraries.split` decides held vs file-only per photo at write time), its row is stale (mtime/size mismatch, re-read from the file), or the share does not answer (`_rows_under` returns `{}`).
- So an id-only queue needs a **path arm for file-only photos**, or file-only writes remain path-keyed: one concept more, unless the server keys by (id or path) exactly as `libraries.split` already does. The journal's `change_files` already has both `photo_id` and `path`; settle goes by path and fingerprint (`follow_renamed`), because ids are re-minted by the indexer's identity mint and are not what a crashed file is found by.
- Where id keying genuinely helps: library view selections (already ids), renames (a path changes under a queued action; an id does not), and the two-apps case (TagTuner and TagPup see one id). It helps less where paths are what the OS needs (the file) and nothing is saved on the write itself.

### What the server's state must serve the page (Views owner)

`pendingOf` (read-only panel with the sentence), `shownTags` (record plus pending ops, in order), `deleteWaits`, `openPhotoWrite` (leaving a photo waits for its own writes) and `pageFollows` (change event per held record, including a view's `previous.record`) all read the page's map today. A server queue replaces them only if it serves per photo: the pending ops (and which were the panel's own), a settled/failed signal per group token, and a change feed. If the page keeps its map and the server also keeps a queue, **the rule is written twice** (health condition 2).

## 4. Pros and cons

### Option A

| Pros | Cons |
|---|---|
| Removes the seam class behind #3203/#3208/#3210/#3211 with no new stored data | Touches 12 queue sites and 9 route calls; 3-4 merges |
| No migration; fits the one-migration-in-flight rule | The page still holds its memory queues, so a closed tab still loses unsent work |
| Delete gets a crash record and a shared busy check with writes | Delete's "undo" stays "restore from the Bin": no new capability |
| Reuses `write_fields`, `settle`, History, Undo of a group | A single save must stay as fast as today (chunk of one) |
| Deletes four routes' worth of bespoke handling | Does not by itself change what an install waits for |

### Option B

| Pros | Cons |
|---|---|
| Resume after a clean stop: the install no longer waits on queued work; at most one chunk in flight | The drain already ends (hang fixed on trunk), so the gain over today is the page's own unsent work and queued-not-started work |
| The server knows what is queued and in flight; Retry/Cancel become ids the server owns | The client-side rules (`shownTags`, `pendingOf`, `pageFollows`) must move to a server feed or be written twice |
| #3202/#3227-class "redraw from the older record" disappears if the server gives the post-write state | A migration 40 (a new intent table), one in flight, to run on all three libraries; 39 must be confirmed run first |
| Multi-writer (a phone, a second tab) sees one list; the library-level order replaces per-process `_unsettled`/`_put_off` | Folder records have no id; file-only and share-away photos need a path arm |
| Server-side batching/coalescing of a tag change and a save of the same file in one write | A queue of accepted actions the owner did not intend to run after a crash is a new danger (an old Delete resumes after an hour); needs an age rule and a confirm |
| Fits a future phone/second client without the page's memory | Page-to-server polling or a stream is new (no live-update channel today) |

## 5. Restart and crash semantics

Per-file classification, from the Files owner's answers (code cited):

| Action | Atomic? | Why (code) | Unclean stop: what to do |
|---|---|---|---|
| ExifTool field write (photo) | **Atomic** per file | `field_values.write` writes `-o scratch` then one `os.replace` (`_landed`); never `-overwrite_original`. A kill leaves the old file or the new file plus at most a scratch beside it | Read the file: holds after -> done; holds before -> run again; neither -> conflict, reported. This is today's `_held_or_conflict`. Never undo-then-redo. The scratch is removed by `file_changes.removed_scratch` |
| In-place patch (clips: XMP in the boxes) | **Non-atomic** | `in_place._write` seeks/writes ranges; the record and the old bytes are saved first (`file_journal.save_patch`, `patch`/`patch_before`) | `file_changes._by_bytes`: NEW -> forward, OLD -> redo, TORN -> `put_back`, then redo, NEITHER -> conflict, never overwritten. **This is already the owner's "undo to before-state, then retry"**, for exactly the files that can tear |
| Rename / move | **Atomic** per file | per-file `os.replace`-class rename (`names.rename_all`), rows moved in the same transaction that marks `done` | `_settle_renames`: at target -> record; at origin -> redo; both names -> conflict (a copy). Never undo |
| Delete | Atomic per step, not as a whole | `recycle_bin.delete_file` then `photos.forget_photo` in a separate transaction; through-this-PC: copy -> Bin -> `os.remove(source)` | File in Bin and row there: row is a ghost (a whole sync's second walk removes it); file gone: run the row step again. After the Bin step and before `os.remove`: the file is twice, harmless, finish it. Never "undo then redo": the Bin copy is the before-state but TagPup cannot restore from it, Windows does |
| Rotate/Date/tag batch | per-file atomic (ExifTool) | same as field write | same |

Where today's start-up settle goes **forward by reading the file** and where "undo then retry" would be **wrong** (Files owner): (1) the file holds `after` written by **another program**: it is taken out (`held_out`, #1937), not redone; (2) a rename settled "both names"; (3) a patch whose bytes are NEITHER (another writer); (4) ExifTool reads values back differently from what it wrote (`"1.50"` -> `1.5`, `_record_held`), so a byte-undo would invent a conflict. The rule that survives: **the before-state is a fingerprint of what the change read, not a byte image, except for a patch.** So the owner's classification is already in the code, and "settle forward vs undo-then-redo" is mostly a question of naming: forward for atomic, restore-then-forward for torn.

**A file another program changed during a crash** (compared with both before and after, reported, not overwritten): this is `conflict` (`file_journal` states; `_held_or_conflict`, `_by_bytes`, `OutsideWrites`). It exists and is reported in the doctor's notes; B needs no new rule, only that a resumed queue item reports it in the list the page shows.

**Clean stop (update, hand-over, close).** Today: `lifecycle.drain` refuses while any `busy(but=BATCH)` (jobs, naming, index) is running, calls `batches.stop_all`, and waits for at most one chunk in flight (Integration owner: about one 25-file chunk, or one clip copy under `DRAIN_SECONDS` of 120 s). The change stays `planned`, owner = the old process, and the new process's settle takes it over by `owner_alive`. **So the journal half of "clean stop resumes" exists.** What B adds: the page's unsent work and queued-but-unplanned actions survive; what stays unchanged: the one chunk in flight, and non-batch jobs (index, Suggest, a root move) still refuse a drain and are not part of this queue.

**Unclean stop (crash, kill).** As the table: re-run atomic ones by reading the file; torn patches are put back then rewritten; a change owned by a dead process is taken over at the next start (`_take`). A CLI writer and the server share one journal through `owner`/`claim`/`owner_alive` (`tests/test_one_library_three_programs.py`, scenario I7); per-process state (`_put_off`, `_unsettled`, `batches._line`, `_settled`) is not per-library, so a durable queue must live **in the library file**, never a new in-process structure, or the CLI's `index`/`sync --apply` sees nothing of it.

**Two apps.** TagPup (8090) and TagTuner (8080) are one server process, so one `batches._line`; one queue per library is therefore the journal's queue, not a per-app one.

**An old queued action resuming.** A durable queue means a Delete queued, never run, and resumed after a crash an hour later. Recommended: **a queued Delete is never resumed after an unclean stop; only after a clean one**, and it says so. Tag and field changes may resume either way (safe to repeat: the planner re-reads and skips what the file already holds, as `write_fields` does).

## 6. Data owner's question: what is a "saved queue"

(The Data owner's answer, with my reading of the code.) The journal (`changes`, `change_rows`, `change_files`; migrations 11, 12, 37) holds per-file before/after, state, owner and a group token (`journal.GROUP`, in `changes.summary`: one History line and one Undo for a group, newest first). It cannot hold a queued intent, because a `planned` change has already read its files. So the queue's one new thing is the intent before it is planned: a new table (migration 40), not a new `changes.status` (that rebuilds `changes` for its CHECK constraint). The group token is exactly the id of an owner action and should be the intent's id; the undo group matters only when one action becomes several chunk-changes (it does, 25 files at a time). Photo ids: stable across TagPup's own rename, tag writes and re-index of the same file; across an external rename only if sync's `relink_photos.pair` re-points the row, otherwise the row is deleted and a new id minted. `follow_renamed` tracks `change_files` fingerprints, not ids. So id keying is sound, and a queue entry must refuse a vanished id; but the crash-safe key of a half-written file stays its path and fingerprint.

## 7. Recommended order of work (each cut leaves the trunk whole; counts to be fixed by the plan)

0. **Not part of this review:** finish the lows queue. The hand-over fix is already on trunk (no cut needed; owner to confirm it is installed before relying on it).
1. **Cut 1, Files + Integration, about 300 lines:** Delete is journaled as an INTERNAL crash record (like `INDEX_BAKE`), and runs under `file_changes.exclusively` so it cannot run beside a chunk of the same file (#3203). Settle resumes the row step. Test first: kill between the Bin and the row; kill after the Bin copy and before `os.remove`.
2. **Cut 2, Integration, about 400:** one entry point `POST /api/photos/write` with ops and a group token; `bulk-tags`, `save-metadata`, `time-shift` and `bulk-edit` call it, old bodies deleted (the plan's pattern for `changes`).
3. **Cut 3, Views, about 800:** the page's `queuePhotoWrite` and `pending.js` become one client of that entry point; **decision for the owner: does the server also serve the page's `shownTags`/`pendingOf` (B) or does the page keep its map (A)?**
4. **Cut 4, Data + Files, about 500:** `queued_actions` table, migration 40 (one migration in flight; only when the owner picks B), start-up resume of queued intents, age rule for Delete.
5. **Cut 5, Views, optional:** the page reads the server's list; deletes `write-queue.js` state, `photoWrites`, the pending map. The only cut that removes the #3202-class risk for good.

## 8. Owner decisions needed

1. **A, B, or A then a narrow B?** (Recommended: A first; B only after cut 3 shows the page cannot be thin without it.)
2. **Does the server own what a photo's tags will be while a change waits (`shownTags`)?** Yes -> cut 5 and the feed; no -> the page keeps its map and the rule is written in two places.
3. **Delete in the journal: crash record only (no Undo), or a full change with an Undo that points at the Bin?** (Recommended: crash record only.)
4. **A queued Delete after an unclean stop: never resume without a question?** (Recommended: yes, never auto-resumed.)
5. **File-only photos (Just-look folders, no row): path arm in the queue, or leave them path-keyed outside it?** (Recommended: path arm, key = id or path as `libraries.split` already does.)
6. **Migration 40 (a `queued_actions` table, additive) on three live libraries**: allowed once cuts 1-3 are installed and migration 39 is confirmed run on all three? (One in flight.)
7. **The time-shift's "cannot be undone from here" sentence**: it is journaled and undoable by History; say so, or keep it? (A one-line fix, independent of the rest.)
8. **Install timing**: the hand-over fix is on trunk; do you want to confirm it before this work starts, so cut 1 is installed without a hang?

## 9. How the numbers were counted

All by read-only grep/read this session on the trunk checkout: 12 queue call sites in 8 files (`queueWriteOf(`/`queuePhotoWrite(`, definitions excluded); 20 `write_fields(` callers in `tagpup/` (definition excluded); 9 page calls of the write routes (`/api/photo/...`, `/api/photos/bulk-tags`, `/api/library/bulk-edit`, `/api/folder/time-shift`); migration 39 as the latest (`tagpup/store/schema.py`); 31 findings open per `tools/add_findings.py --open` (including #3200, #3227). No library data was read, so no live row counts are quoted; the plan that follows must give the live counts of changes and unfinished `planned` rows (`tagpup history`/`sync_state`) before building.

Owner agents consulted: Files (atomicity, Delete, settle), Integration (clean stop, one queue per library, CLI), Views (id keys, what the page reads). Data (migration number, intent versus planned change, undo group, id stability).

9. **A typed tag's "file changed on disk" refusal (from #3220, owner 2026-11-19: built inside A, not before).** `save-metadata` carries a `base` and answers 409 when the file changed since the page read it; `bulk-tags` (the queue's route) sends no base, so moving a typed tag into the queue loses that refusal unless the one entry point carries a base per photo and refuses on mismatch. Keep the refusal for typed tags (and for which other operations), or accept applying to the file's current tags?

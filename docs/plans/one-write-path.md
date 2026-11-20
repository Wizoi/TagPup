# One write path: every write of a photo file, single or bulk, enters one server entry point that always journals

Status: DECIDED 2026-11-20 by the owner (answers below); the review that led here is
[server-side-queue-review.md](server-side-queue-review.md). Option A only: the durable intent table (B, migration 40) is NOT part of
this plan and is raised again only if cut 3 shows the page cannot be thin without it.
Live count (read-only, 2026-11-20, `change_files` by state): kr-track 4,696 done, 0 planned or writing; photo_index 107,398 done,
140 conflict, 0 planned or writing; renton_parkrun 4,319 done, 0 planned or writing. Delete leaves no `change_files` row today
(that is the gap); the one `delete%` change per library is not a photo Delete. So no unfinished change exists to resume, and the
140 photo_index conflicts are an existing, separate state this plan does not touch.
Domains: Files (Delete, atomicity), Integration (the route, lifecycle), Views (the page's client); Data only if B is ever built.
Integration reviews the seams.

| Mechanism | The ONE place it lives | Owner | New or existing |
| --- | --- | --- | --- |
| Journaled write of photo files (tags, fields, rotate, date, rename) | `tagpup/services/file_changes.py write_fields` / `rename` | Files | existing, reused |
| Delete's crash record (INTERNAL, like `INDEX_BAKE`), run under `file_changes.exclusively` | `tagpup/services/photos.py delete` + `file_journal` | Files | new record, existing delete |
| The entry point `POST /api/photos/write` (ops list, group token, id or path per photo) | `tagpup/web/` route over a new service function in `tagpup/services/` | Integration | new; `bulk-tags`, `save-metadata`, `time-shift`, `bulk-edit` become callers, old bodies deleted |
| A base per photo for a typed tag, refused with 409 when the file changed | the same entry point, reusing `save-metadata`'s check | Integration | existing check, moved |
| The page's one client | `web/tagpup/` successor to `pending.js`'s drain and `queuePhotoWrite` | Views | new; the page keeps its pending map (`shownTags`, `pendingOf`) |

## Owner's answers (2026-11-20)

1. Option A first; a narrow B only if needed later.
2. The page keeps its map (`shownTags`, `pendingOf`) for now; the rule stays written in two places until a later cut.
3. Delete is journaled as a crash record only: no History line, no Undo; restore stays the Recycle Bin.
4. A queued Delete is never resumed after an unclean stop; only a clean stop resumes it, and it says so.
5. A photo is addressed by id when it has a row and by path when it has none (Just-look folders), as `libraries.split` already decides.
6. No migration 40 in this plan.
7. The folder time-shift's confirm says it can be undone from History.
8. Settled: the hand-over fix is on the trunk and two installs went through without a hang.
9. A typed tag keeps the "file changed on disk" refusal through the entry point (a base per photo, 409 on mismatch); other operations apply to the file's current state.

Steady-state writer: a photo changed afterwards goes through the same entry point; no command is run. One-offs: none.

## Cuts (each merge leaves the trunk whole, each about 1,200 lines or fewer, one domain per worker)

1. **Files + Integration, about 300 lines:** Delete journaled as an INTERNAL crash record and run under `file_changes.exclusively`
   (#3203); settle finishes the row step; the time-shift sentence (answer 7). Tests first: killed between the Bin and the row;
   killed after the Bin copy and before `os.remove`; a queued Delete is not resumed after an unclean stop (answer 4).
   **Cut 1 is split (the first worker spent its whole budget reading; this is what it found, checked on the trunk 2026-11-20):**
   - **1a, Files:** panel Delete + settle. `file_journal.py`: `DELETE = "delete photo"`, `INTERNAL = (INDEX_BAKE, DELETE)` (erased by
     `finish`, out of History and `newer_overlapping`); `journal.UNDO_CHECKS[DELETE]` sentence "a Delete is not undone; restore the
     photo from the Recycle Bin" (`tests/test_every_change_kind_has_an_undo_check.py` needs it). `photos.forget_photo` takes an
     `also(conn)` so the row's `set_state(done)` commits in the same transaction that removes the row and faces. In
     `services/photos.py`: `plan_deletes`, `delete_planned` (`file_journal.writing`, `file_only.send`, then `_forget`: forget_photo,
     `thumbnails.forget`), `_by_look(redo)` using `shares.stamp_of` (UNANSWERED: stay `writing`, unsettled; gone: `_forget`; there:
     `redo` runs the Bin step then `_forget`, else withdraw), `end_deletes(library, change_id)`, and
     `settle_delete(library, change)` reached from `file_changes._finish` by a lazy import as convert's `settle_change` is. `delete()`
     runs under `file_changes.exclusively()` (the panel's Delete holds no lock today: #3203's last piece); a loose (Just-look) path goes
     to `file_only.delete` with no record; a held path is one planned row.
   - **Clean vs unclean (as built, after review #3256):** the `owner` column cannot tell them apart: `file_journal.release`
     sets it NULL after a clean stop, and ALSO after a settle that took a dead process's change was put aside (`_put_aside`,
     `_settle_taken`). So in 1a `settle_delete` withdraws EVERY `planned` Delete row, whatever its owner, with a warning naming
     the change and a count, no paths (answer 4); every `writing` row goes to `_by_look(redo=True)`, which sends it again only
     while it is the file whose stamp `file_journal.writing` recorded (#3257). The clean-stop resume of a bulk Delete is 1b's,
     marked by a marker of its own in the change (not the owner column). An update's `stop_all` is told from the owner's
     Cancel by a `for_an_update` flag on the batch (set by `stop_all`, cleared by `cancel`; Cancel wins in either order), not
     by `batches.closing()`.
   - **1b, Files:** `services/bulk_edit.py _delete` (already under `exclusively` around its loop, line ~279) plans all photos as one
     change, runs `delete_planned(fresh=False)`, on a stop with `batch.for_an_update` leaves the rest planned and released (the
     photo's error says it is deleted when TagPup starts again) with a marker of its own in the change that `settle_delete`
     reads to resume them (never `owner` NULL, #3256), otherwise withdraws them ("stopped as asked: not deleted");
     `end_deletes` in a `finally`; fix the docstring saying a delete is "not journaled". The time-shift sentence
     (`web/tagpup/rename.js applyTimeShift`): `shift_date_taken` journals a held folder (`UNDO_CHECKS["time shift"]` is None) but a
     Just-look folder goes through `file_only.write_fields` with no journal, so the text says "can be undone from History" only for
     a held folder; check first whether the sentence can show for a Just-look folder and test the text.
   - **Decided by the main session:** a released Delete resumed by this process's next write after a given-up drain
     (`batches.reopen`) is acceptable (it is a clean stop's resume); the resume of a bulk Delete after a clean stop says so in the
     log and in each photo's error text, no page sentence (the batches list hides INTERNAL changes by design).
   - **Tests (Files scenarios, `photo_rows.add_read`, a `_reached` hook in photos.py to stop between steps):** killed after the Bin
     step and before the row step; killed after the copy and before `os.remove`; planned, never begun, then an unclean stop
     (dead pid): withdrawn, nothing deleted; planned, then put aside by a stopped settle (owner NULL): withdrawn too (#3256; the
     clean-stop resume is 1b's); killed after the Bin step, another file put at the path: not sent (#3257); Delete while a chunk holds the lock:
     waits; a share that does not answer: row stays `writing`, released, settled once it answers; two Deletes of one photo at
     once; a Just-look path with no record; History lists nothing and Undo says the UNDO_CHECKS sentence.
2. **Integration, about 400:** `POST /api/photos/write`; `bulk-tags`, `save-metadata`, `time-shift`, `bulk-edit` call it, old
   bodies deleted; the base check for typed tags (answer 9); id-or-path addressing (answer 5).
   **Cut 2 is split (Integration owner's design view, 2026-11-20; the four routes sit over four services with four reply shapes the
   pages read, so the old routes stay as thin reshapers until cut 3 moves the page, then are deleted):**
   - **2a, Integration, ~700:** `tagpup/services/photo_writes.py` (a dispatcher only: op `tags` = per-photo add/remove with `group`
     -> `tagging.change_tags_of`; op `set` = ONE photo's title/tags/date with stamp+base -> `tagging.save_photo`; no multi-photo `set`,
     no user action makes it) + `POST /api/photos/write` + its guards (`name_faces_routes.GUARDED`, `roots_ingress.KEYS`,
     `security.THIS_PC_ONLY`, SPEC_TAGPUP_GUI, `tests/test_write_routes_are_classified.py`, the new module named Integration's in
     `docs/OWNERS.md` and the owner agent's scope in the same commit). `bulk-tags` and `save-metadata` become callers that reshape to
     today's replies. `records_written` / `_followed_rename` run once, in the entry point. The base check STAYS in
     `tagging.save_photo` (no ExifTool read in the route layer); the route only parses.
   - **Request:** `{"op", "group", "changes": {path: {"id": int|null, ...op fields}}, "params"}`; keyed by path, `id` inside when the
     row exists (answer 5), refused 409 if that id's path is no longer this path. **Reply, one envelope:** `success, changed,
     attempted, skipped_missing, skipped[{path,why}], error_count, errors[{path,id,why}], stopped, change, file_only, with_rows,
     written{path:{mtime,size,tags,base,new_path,taken,rename_failed}}, pictures_refused?, named/unnamed/renamed_faces?,
     changed_on_disk (409)`. The reshapers keep every field the pages read today (pending.js, photo.js, photo-write.js, edits.js,
     grid.js, mobile-edit.js, rename.js, bulk-run.js).
   - **2b, Integration, ~500:** op `shift` (paths + minutes; the camera filter stays in the time-shift caller until cut 3) and
     bulk-edit's `tags`/`people` by resolved ids. **Delete stays `bulk_edit._delete` / `photos.delete`** (no field write; its own token
     contract), not a caller of the entry point.
   - **#3263 is Files', not the route's:** `file_changes._write_fields` and `rename` already settle first; Delete's path gets
     `file_changes.settle` inside its `exclusively` block in `services/photos.py` (~10 lines + one test), beside 2a.
   - **2c, after cut 3:** delete the four old routes and reshapers; spec and page fixtures follow.
3. **Views, about 800:** `queuePhotoWrite` and `pending.js` become one client of the entry point; the page keeps its map.
   After it: re-read whether B is needed.

A worker brief for each names how it fails (killed part-way, two at once, a share that does not answer, rapid clicks, switching
library mid-folder, an update while working) and cites `Plan: docs/plans/one-write-path.md`.

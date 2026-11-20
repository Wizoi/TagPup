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
2. **Integration, about 400:** `POST /api/photos/write`; `bulk-tags`, `save-metadata`, `time-shift`, `bulk-edit` call it, old
   bodies deleted; the base check for typed tags (answer 9); id-or-path addressing (answer 5).
3. **Views, about 800:** `queuePhotoWrite` and `pending.js` become one client of the entry point; the page keeps its map.
   After it: re-read whether B is needed.

A worker brief for each names how it fails (killed part-way, two at once, a share that does not answer, rapid clicks, switching
library mid-folder, an update while working) and cites `Plan: docs/plans/one-write-path.md`.

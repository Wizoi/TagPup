Status: CLOSED 2026-11-23. Cuts 4 and 5 are NOT built (DECISIONS 2026-11-23: no live instance). Kept as the record of why.

# One write path for photo files, and a durable server queue: a save, a bulk and a delete follow one set of rules, and a restart loses no accepted work
Live count: counted 2026-11-23, read-only through the MCP tools: three libraries (kr-track 2,186 photos, photo_index
60,270, renton_parkrun 1,309), all at schema 39, so migration 39 has run on all three and migration 40 may be the one
in flight. photo_index's last 5 journal entries are all `applied` (no `planned` debris). Code counts from the review
(2026-11-19, to be re-counted by each cut's worker): 12 queue call sites in 8 files, 20 `write_fields` callers, 9 page
calls of the write routes.
Domains: Files (Delete, the write path), Integration (the entry point, settle at start), Views (the page client),
Data (cut 4 only); integration reviews the seams
Source: docs/plans/server-side-queue-review.md (the owner accepted every recommendation, 2026-11-23).

## What the owner decided (2026-11-23, all the review's recommendations)
1. A first, then a narrow B: one entry point that always journals; then a `queued_actions` table so an accepted action
   survives a restart. The page's pending map moves to the server only if cut 3 shows the page cannot be thin without it.
2. Cut 5 is chosen too (owner, 2026-11-23: "and optional as well"): the server owns what a photo's tags will be while
   a change waits, and serves it to the page. Until cut 5 lands the page keeps its map (`shownTags`, `pendingOf`), the
   rule is written in two places, and cut 3's brief names it as the debt cut 5 removes.
3. Delete is journaled as a crash record only (INTERNAL, like `INDEX_BAKE`): no Undo button.
4. A queued Delete is never auto-resumed after an unclean stop; after a clean stop it resumes.
5. File-only photos (no row) get a path arm: key = id or path, as `libraries.split` decides today.
6. Migration 40 (`queued_actions`, additive) may run once cuts 1-3 are installed. One migration in flight.
7. The time-shift confirm text stops saying it "cannot be undone from here" (History undoes it). One line, any cut.
8. The typed tag's "file changed on disk" refusal (#3220) is kept: the entry point carries a `base` per photo for
   typed tags and answers 409 on a mismatch. (Taken from the review's recommendation; the owner may change it.)

| Mechanism | The ONE place it lives | Its owner | New or existing |
|---|---|---|---|
| Write photo files (tags, fields, rotate, date, rename, delete), single or many | `tagpup/services/file_changes.py` `write_fields` and a new `tagpup/services/photo_writes.py` (the route's body) | Files | existing writer; new entry service |
| The entry point | `POST /api/photos/write` in `tagpup/web/tagpup_routes.py`; `bulk-tags`, `save-metadata`, `time-shift`, `bulk-edit`, `/api/photo/delete` become callers, then are deleted | Integration | new route, old bodies deleted |
| Delete's crash record and recovery | `file_changes` (plan, settle) with `files/recycle_bin.delete_file`; `INTERNAL` operation list | Files | existing journal, one new operation |
| One busy check for a photo | `file_changes.exclusively` / `batch_exclusively` (Delete joins it) | Files | existing, extended |
| The page's one client of the entry point | `web/tagpup/pending.js` + `edits.js queuePhotoWrite` merged into one drain; status UI stays `write-queue.js` | Views | existing, merged |
| An accepted action not yet planned | `queued_actions` table, `tagpup/store/schema.py` Migration(40), `journal.KEYS/CASCADES/checks`, `tests/owners.py` | Data | new (cut 4) |
| Start-up resume | `file_changes.settle_once` also starts queued actions after finishing `planned` changes | Integration | existing, extended |

Steady-state writer: a photo changed afterwards is written by `write_fields` through the one entry point; a stop with
work queued is resumed by `settle_once` at the next start (a clean stop) or settled by reading the file, with a torn
patch put back first (an unclean one), with no command run.
One-offs: none. No backfill: `queued_actions` starts empty, and no existing row changes meaning.

## Failure the briefs must name
- Interrupted part-way: Delete killed between the Bin copy and the row step, and between the copy and `os.remove` (a
  share's delete goes through this PC's Downloads copy). Atomic steps are checked and run again, never undone.
- Two at once: a save and a Delete of one file (#3203); two tabs; TagPup and TagTuner (one process, one `batches._line`);
  the CLI and the server (one journal, `owner`/`claim`/`owner_alive`).
- A read that fails: the share does not answer (`_rows_under` returns `{}`): the action stays queued, reported, not dropped.
- Real data shape: folder page records carry no id for still photos; a Just-look photo has no row; a stale row is re-read.
- What the owner sees: rapid clicks on one photo, switching library mid-folder, an update while working, a closed tab.
  Wrong words are a defect: History still lists the change, and Delete's row has no Undo.

## Cuts

Cuts 1-3 of the first draft of this plan were ALREADY BUILT and installed under docs/plans/one-write-path.md (decided
by the owner 2026-11-20; Delete's crash record and settle 79266423/15b222f8/0e95c35a, `POST /api/photos/write`,
the page's one client; installed at d330b713). The first draft was written from a review dated 2026-11-19 and not checked
against the trunk; the cut 1 worker found it stale (2026-11-23). Only the owner's two additions remain:

4. **Data + Files, ~500:** Migration(40) `queued_actions`, resume at start (`settle_once` starts queued actions after
   finishing `planned` changes), the never-resume-Delete-after-unclean-stop rule (already true of a planned Delete,
   #3256; extend it to a queued one), the path arm for a photo with no row. Installed already: cuts 1-3, so migration
   40 is the only one in flight; the owner approves its run on the three libraries.
5. **Views + Integration, ~800, may be two merges:** the server serves per photo the pending ops, a settled/failed
   signal per group token and a change feed (`GET /api/library/actions`); the page reads it and drops `write-queue.js`
   state, `photoWrites`, the pending map, `shownTags` and `pendingOf` as its own rules.

Each cut removes at least as many concepts as it adds. Each worker cites
`Plan: docs/plans/one-write-path-and-durable-queue.md`.

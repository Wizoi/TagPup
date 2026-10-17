# Files and sync: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-files.md`: its Scope, the contract the other domains rely on, its
invariants and guards, and its scenarios F1 to F11. This page states each of the seven conditions of health
(docs/OWNERS.md) with its evidence and date. A condition not met is the domain's queue.

## What the domain holds (counted 2026-10-15, read-only)

| Library | Photos | Folders | With a DocumentID | Faces | Changes of photo files in the journal |
|---|---|---|---|---|---|
| kr-track | 1,499 | 21 | 1,490 | 9,850 | 274 |
| photo_index | 59,519 | 2,617 | 546 | 187,784 | 40 |
| renton_parkrun | 1,309 | 9 | 1,309 | 3,328 | 1,185 |
| all three | 62,327 | 2,647 | 3,345 | 200,962 | 1,499 |

One library's root is on a network share (1,309 rows). No row is stamped (0.0, 0), none has a NULL stamp, and none holds
XMP PersonInImage or RegionName (all 0 in the three).

## The seven conditions (2026-10-15)

1. **No reach-ins.** Met. Every use of a Files module by another domain is a row of the Integration charter's contract
   table: Files is the callee of rows 6, 7, 38, 46, 47, 48 and 50 to 59. Rows 50 to 59 are this pass's: inspect, tags,
   person_tags, identify, box_shape, the naming jobs' folder scope, the journal's undo and dating, the thumbnails' rows,
   and face_people's write through `file_changes.batch_exclusively`. Each names its guard test; box_shape is review only.
   face_people's own ExifTool session went (#1474).
2. **No rule written twice.** Met.
   - One batch reader, `field_values.read`: the indexer's `MetadataExtractor.batch_read` and sync's pairing read
     through it (#1473). A timeout ends the read and leaves every photo not read unread (#1490).
   - One writer of a photo's fields, `field_values.write`; the identity mint writes through it (`ensure_document_id`).
   - One answer to "is this file there to be written", `file_changes.file_states`, with `write_fields` the one place
     that leaves out a photo on a share asleep (#1492).
   - One cap and its sentence, `file_changes.too_many`, asked by `write_fields`, `batch_exclusively`, Smart Rename, the
     selection and a bulk face assignment (#1337, #1494).
   - One `store.photos.stamps` (#1090).
   - Left by rule: `names.read_for_renaming` reads one photo per command and refuses the whole set (#1419).
3. **No hidden coupling.** Met. `tests/test_rules_have_one_owner.py` fails each of these outside `files/field_values.py`:
   a `set_tags(`, an `-overwrite_original` and an `execute` of a `-TAG=value`. It also fails a `field_values.write`
   outside `file_changes` and `file_only`. The cap and the away rule each have one function.
4. **No concept that could collapse.** Met. Gone in this pass: `stamps_with_vectors`, `libraries.not_in`,
   `sync.folder_state` (now `sync.watch_states`), `bulk_edit._reachable`, `MetadataExtractor._read_one_by_one`,
   `face_people.CHUNK`, `face_people.TIMEOUT` and the route's `_too_many`.
5. **No branch without an instance.** Met.
   - Deleted, 0 live rows each: the (0.0, 0) stamp of a failed stat, `_structure(stamp=None)`, `record_indexed`'s 0.0
     defaults and Apply All's `.get("people") or []`.
   - Kept, with the user action that makes the case:
     - an unstamped record: a share that stops answering during an index (one root is on a share), an ExifTool that
       will not start, a file whose stat fails;
     - a stop during the planning read: Cancel or a hand-over;
     - a Ctrl+C part-way.
   - `tests/photo_rows.stamp_of`'s (0.0, 0) is a fixture's, by design (its docstring).
6. **Scenario tests, each file owned and mapped.** Met. F1 to F11 each have files in `tests/owners.py`, and
   `test_bulk_delete` is F8. `test_service_save_photo` and `test_save_reply_says_rename_failed` were lifted into F1
   (`tests/test_tag_a_photo_read_it_back_and_undo.py`) with mutation proofs, and the restatements deleted.
7. **No open data or wrong finding, and every low fixed or by design.** Met once this branch is merged:
   - #1490 (wrong) is fixed;
   - #1491 to #1496 are fixed;
   - the lows below are by design.

## The journaled path and its two named exceptions

A photo file is written only through `file_changes.write_fields` and `rename` (the charter's contract, point 1). The two
exceptions, each unjournaled by decision:

- **The DocumentID the indexer mints**: `files/field_values.ensure_document_id`, through `field_values.write`. It is
  one command, made again with `-m` only on ExifTool's minor refusal, and then carries the keyword fields as they hold
  (#1355). It is never written into a photo whose picture did not decode.
- **Just look**: `services/file_only.py`, the files of a folder the library does not hold (#527).

## By design (each row closed `decided:`)

- **The indexer's per-photo reads have no deadline** (the header for the read-ahead's estimate, the decodes, the stamp,
  the zero tail; review of AI pipeline 1b, 2026-10-17): a share that sleeps mid-index holds the run until it answers.
  Nothing becomes missing and nothing is written meanwhile. A run from the CLI stops at Ctrl+C. A run the page started
  has no stop of its own: Cancel drops only folders not begun and a run still waiting for the graphics card
  (`IndexQueue.cancel`), and a hand-over waits for an index under way (`Lifecycle.drain`); it ends when the share
  answers, or with the server. A full decode cannot be bounded
  without giving the photo up as unread, which the next index reads again anyway, and `shares.bounded` is for deciding
  "not looked at" before a write or a report, which an index in progress does not do.

- **#1215**: an undo of a rotation leaves the XMP `tiff:Orientation` the turn set. No instance: `raw_metadata` records no
  Orientation in the three libraries (counted 2026-10-15) and nothing has been reported.
- **#1216**: a TIFF's box turn needs Pillow and the mirrored values are untested; the sideways-photo face-box
  redesign is tabled (DECISIONS 2026-10-07).
- **#1384**: a walk that hangs part-way. No hang mid-walk has been seen, and bounding each listing costs a thread per
  folder. A listing that fails or drops is "not looked at" (#1356).
- **#1444**: the watcher across a share's outage. No case has been seen; the one network root holds 1,309 rows.
- **#1419**: an ExifTool refusal names no photo, by rule
  (`tests/test_exiftool_says_why.py`, `test_the_error_says_what_exiftool_said_and_names_no_photo`). The log names it.
- **#1199**: Smart Rename and the routes that tell the page's records in order (bulk tags, Apply All) hold the lock
  through their whole run.
  - `face_people.write_tags` holds it through a whole batch too (`batch_exclusively`).
  - Letting others in between chunks would mean moving the records' ordering into the write, which is a redesign.
  - Cancel and a hand-over already stop them at a chunk.
- **#338**: POST /api/sync runs in the request thread. Making it a background job is new machinery under the freeze;
  the request is its place in line, as for a batch (DECISIONS 2026-10-11, "One batch model").
- **#1324**: a second applied sync from a stale plan records "not in step". None of the 57 sync records kept in the
  three libraries shows it (counted 2026-10-15), and the next sync records in step. The fix would be the journal's skip
  semantics (Data's) and a re-plan.
- **#1438**: the doctor asks each row's stamp through the share's bound. Its whole report on photo_index took 111 s
  read-only (2026-10-15, 0 rules broken), of which the stamps part measured 3.1 s against 2.3 s over 59,519 rows. It is
  a check the owner runs by hand.
- **The index queue's run once more of a sync's batch** (#1565's review): a sync's `together` batch of N folders that a
  whole-library sync asks for again while it runs is run again whole, N folders scanned once more; the stamp rule
  skips the unchanged files. It is once, not a loop (a re-run repeats only when something asks during it), and
  splitting the batch would need the sync to say which folder received files, which nothing records.

## Known, and closed

- **#885**: obsolete. The size-change re-read that left a decided face's vector stale was deleted (a4a4a42).
- **#360**: decided by design. A watcher sync during a slow bulk write may re-read a file whose row is not recorded yet;
  the journal's preconditions settle it, and only reads are wasted.

## History

- 2026-10-15, the domain pass:
  - one batch reader; a photo ExifTool did not answer for is left unread (#1473, #1490, #1491, #1495);
  - the mint through the one writer, under guard (#1473, #1496);
  - a held file's stamp taken before its read (#1462); a dead `row.after` gone (#1286);
  - no person fields read (#1429); `not_in` gone (#1446); one `stamps` (#1090); one away rule in `write_fields`
    (#1180, #1492);
  - the cap in `file_changes` (#1337, #1494); `sync.watch_states`;
  - face_people's person tags as one batch (#1474), and Ctrl+C keeping what was written (#1493);
  - the folders read from the derived table (#501, 0.6 s down to 0.01 s on photo_index);
  - the CLI's held-folder wording (#917); a stoppable planning read (#1200);
  - F1 lifting the save tests (#1475); contract rows 50 to 59.

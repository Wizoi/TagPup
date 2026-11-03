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
   table: Files is the callee of rows 6, 7, 38, 46, 47, 48, 50, 51 and 53 to 59. Those from 50 are this pass's: inspect,
   tags, identify, box_shape, the naming jobs' folder scope, the journal's undo and dating, the thumbnails' rows,
   and face_people's write through `file_changes.batch_exclusively`. Each names its guard test; box_shape is review only.
   face_people's own ExifTool session went (#1474).
2. **No rule written twice.** Met.
   - One batch reader, `field_values.read`: the indexer's `MetadataExtractor.batch_read` and sync's pairing read
     through it (#1473). A timeout ends the read and leaves every photo not read unread (#1490).
   - One writer of a photo's fields, `field_values.write`; the identity mint writes through it (`ensure_document_id`).
     It writes a file one of two ways: a journaled write of a JPEG whose fields fit is patched in place
     (`in_place.patch_jpeg`, called from nowhere else; `PATCH_IN_PLACE`), every other write is ExifTool's rewrite.
     It writes every photo beside itself and moves the new file over it only once its picture is the photo's
     (`_landed`, #2377, DECISIONS 2026-11-03): the one check of a rewrite, the patch in place's verify the other
     mechanism of the same rule (INVARIANTS), not merged with it: a patch checks only the bytes it changed
     and reads no other byte of the file (DECISIONS 2026-11-03).
   - One answer to "is this file there to be written", `file_changes.file_states`, with `write_fields` the one place
     that leaves out a photo on a share asleep (#1492).
   - One cap and its sentence, `file_changes.too_many`, asked by `write_fields`, `batch_exclusively`, Smart Rename, the
     selection and a bulk face assignment (#1337, #1494).
   - One `store.photos.stamps` (#1090).
   - Left by rule: `names.read_for_renaming` reads one photo per command and refuses the whole set (#1419).
3. **No hidden coupling.** Met. `tests/test_rules_have_one_owner.py` fails each of these outside `files/field_values.py`:
   a `set_tags(` and an `execute` of a `-TAG=value`; an `-overwrite_original` it fails anywhere, field_values included
   (a photo is written beside itself, #2377). It also fails a `field_values.write` outside `file_changes` and
   `file_only`. The cap and the away rule each have one function.
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
  one command, made with `-m` as every write is (`field_values.WRITE_OPTIONS`, #2009, DECISIONS 2026-10-27) (#1355).
  It is never written into a photo whose picture did not decode. Its write is wrapped by `file_changes.OutsideWrites`,
  which carries a done rename's fingerprint over it as `_record` carries the journal's own writes (one helper,
  `_follow_rename`; #1957): the exception stays unjournaled, but no longer leaves a rename knowing a file by a
  fingerprint the mint changed. Forward only (#1959); a renamed file minted before 2026-10-29 stays refused.
- **Just look**: `services/file_only.py`, the files of a folder the library does not hold (#527).

Every photo's file is baked with no command (owner, 2026-10-28): a current vector with no fingerprint row
(`photo_fingerprints.NO_FINGERPRINT`: Suggest's or a re-embedding pass's) is work sync owes the index, whose bake after
the index writes the file with no model (the row follows the write) or, for a file holding its bake or one marked
read-only (`file_changes`' `read_only`, the one failure no retry changes), records the decode's row
(`photo_fingerprints.adopt`); any other photo stays owed: the bake takes away the index's rows of this run's photos
before it plans, so only those three give one back, however the bake ends; an index that ends before its bake (an error, a Ctrl+C) takes them away too (`tests/test_the_index_writes_the_bake.py`).

## Rows removed by a whole sync (DECISIONS 2026-10-28)

The one place is `sync.look`; nothing else removes a row for a missing file (the charter's contract, point 4).

- **Who removes:** a sync of the whole library, applied: the CLI's `sync --apply`, the daily job and the watcher's
  catch-up. The CLI's dry run says the count first; the automatic whole syncs remove unasked (the owner's "removed by
  the syncer"), and the watcher's log line says the rows removed and the named faces among them.
- **Never:** a sync of one folder (a file moved out of it may not have been looked for where it went), nor any sync
  that could not look at every folder, nor a row whose root is not confirmed there (`_removable`).
- **Two looks:** when the first walk leaves a row to remove, the whole library is walked again (`_gone_again`) and a
  row is removed only if that walk also looked at every folder, did not find its file, did not pair it as a move nor
  hold it back. A file dragged between folders during the first walk is in neither of its listings (#2153). A library
  with nothing to remove is walked once.
- **A rename in its folder:** `pair_by_stamp` pairs a missing row with a new file of its own folder with its size and
  modified time, one to one, as a move: the row follows with its faces (#2155). Two such files is ambiguous, held
  back, and nothing is removed. A file renamed and saved again, or moved and renamed, with no identity, is not paired.
- Guards: `tests/test_sync.py` (MissingFiles: the two walks, a share dropping in the second, a file moved mid-walk,
  a rename in place, ambiguity, a different time, the command's count, the removal said once in the sync's closing
  line with its named faces).

## Roots and machines: a share away

- `tagpup/files/shares.py` is the one owner of a share being away and back: `away_since` (when it stopped answering),
  `on_back` (a callback on the look that hears it answer), `wait_back` (the one patient wait, up to a time or a stop).
- Every look at a photo or its folder is shares': `stamp_of`, `there`, `known_stamp`, `folder_state`; `shares.listing` is
  the listing every walk makes; `read_stamp` a reader's own stamp. The pages and jobs look through `services.photos`
  (`file_stamp`, `folder_state`, `subfolders`), which they may import (`tests/test_photo_looks_have_one_owner.py`).
- The catch-up sync (`sync._scan`) waits once for a root's share, up to `FOLDER_WAIT`; a root still away is not looked
  at, and nothing under it is called missing.
- The watcher resumes a share within a tick of its being heard back (`watching._heard_back`); its 30 s `RECHECK` is the
  safety net. A folder back is wanted since the share was first found not answering (`away_at`).
- One walker per library: the watcher's whole sync goes through the job claim (`recurring.run_claimed`, `sync_whole`), so
  the daily job and the watcher never walk one library together.
- A sync that could not look at its roots ends failed (`Job.failed`, `sync.could_not_look`) and is retried in an hour,
  not a day later.
- The Activity page shows a root on such a share as "share not answering since hh:mm".
- Tests: `tests/test_a_share_asleep.py`, `tests/test_a_share_that_wakes.py`, `tests/test_one_walk_of_a_library_at_a_time.py`.

## JPEG segment census, 2026-11-03

Counted this session, read-only (`scripts/census_segments.py --sample 3000`, `tagpup/services/segment_census.py`, seed
20261103; M1 of docs/PLAN_OWN_METADATA.md): photo_index a sample of 3,000 of its 59,505 JPEGs; kr-track (2,065) and
renton_parkrun (1,308) every JPEG. Every file read: no share away, none gone, none cut short. Columns: photo_index /
kr-track / renton_parkrun.

- Kinds: photo_index 59,486 .jpg, 19 .jpeg, 17 .tif, 1 .png; kr-track 2,065 .jpg; renton_parkrun 1,308 .jpg, 1 .png. No
  HEIC, no video row in any.
- XMP toolkit: ExifTool in every file read (3,000 / 2,065 / 1,308). One XMP segment in each; two `rdf:about` values,
  a target property twice or as an attribute: 0 / 0 / 0. No packet wrapper 2 / 7 / 0; begin BOM in the rest.
- XMP slack (padding before `<?xpacket end`): 2,048 bytes or more in 2,996 / 2,049 / 1,306; none in 4 / 16 / 2.
- Extended XMP: 111 / 80 / 5 (as many packets name `xmpNote:HasExtendedXMP`). MPF: 100 / 14 / 0. Bytes after the last EOI:
  10 / 3 / 0.
- EXIF: two segments 4 / 0 / 0, none 2 / 0 / 3; slack 0 bytes in 1,652 / 1,120 / 926, 1 to 63 in 1,350 / 945 / 379,
  64 or more in none. IFD1 thumbnail 2,747 / 2,062 / 1,304.
- MakerNotes: 1,613 / 1 / 0. photo_index by maker: Canon 466 moved with an OffsetSchema saying by how much, 764 moved
  with one that does not, 6 TIFF-relative as written, 3 outside; Sony 1 moved and said, 18 moved and not, 3 outside, 233
  no IFD that parses, 3 inline; Nikon 13 own header; Apple 6, Fujifilm 7, Samsung 3 note-relative; no IFD that parses:
  Nokia 48, Google 15, other 11, Kodak 5, LG 3, Casio 2, Apple 1, Microsoft 1. kr-track: one Canon, TIFF-relative.
  ("Moved": every pointer fits the note only shifted by one amount; the classes are the census module's.)
- IPTC: two APP13 segments 0 / 2 / 3; bytes after the last 8BIM 1 / 4 / 6; no 0x0404 block in 1 / 2 / 3; padded with
  PAD_RESOURCE 0 / 0 / 0. Charset 1:90 UTF-8 in 2,194 / 2,057 / 1,305, absent in 746 / 4 / 0; values outside ASCII 6 / 0 / 0
  in 2:xx datasets; datasets cut short 12 / 0 / 0.
- IPTCDigest (0x0425 against the MD5 of 0x0404): matches 2,093 / 153 / 463, differs 112 / 1,902 / 839, absent 735 / 6 / 3.
  The comparison is ExifTool's: on copies of 8 kr-track JPEGs the census's MD5 equalled ExifTool's CurrentIPTCDigest each
  time, and the stored digests are stale. An ExifTool IPTC write as TagPup makes one (`-IPTC:Keywords+=`) changes the
  block and leaves 0x0425 as it was, so a digest that matched stops matching (findings, this branch).
- Segment orders, the commonest: photo_index `JFIF EXIF XMP IPTC` 1,403 and `JFIF Adobe EXIF IPTC ICC XMP` 611;
  kr-track and renton_parkrun `EXIF IPTC ICC XMP Adobe` (with the tables) 1,939 and 1,297.
- Header read (bytes before the first scan, the first 50 reads), median / p90 / max in ms: photo_index 22.6 / 27.6 / 33.5
  (40 / 59 / 67 on a first, cold run), kr-track 41.7 / 54.2 / 86.1, renton_parkrun 42.5 / 54.0 / 60.9.
- The journal's files: photo_index 51,312 done and 139 conflict, kr-track 4,696 done, renton_parkrun 4,318 done; none
  holds a patch record.

Against docs/PLAN_OWN_METADATA.md: extended XMP is 111 of 3,000 in photo_index (the plan relays 127 of 3,000); TIFF is
17 rows and PNG 2 (relayed 18 and 1); HEIC 0, as relayed. The plan's 2 KB XMP reserve is already there in nearly every
file (ExifTool's padding), and EXIF has under 64 bytes of slack everywhere. MakerNotes moved from where their offsets
count are most of photo_index's Canon notes, a class the plan's "absolute or relative offsets" does not name. ExifTool
does not refresh the IPTCDigest, so "refresh to match ExifTool's" has nothing to match. A header read is tens of ms, so
M6 (an own read for the journal's field reads) has little to win from the read itself.

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
  redesign is tabled (DECISIONS 2026-10-07). Since DECISIONS 2026-10-27 faces are detected on the picture as shown, boxes kept stored (#1975).
- **#1384**: a walk that hangs part-way: closed by #2104, each listing is bounded (`shares.listing`). A listing that fails or
  drops is "not looked at" (#1356).
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
- **The index's re-read of the photos it gives an identity** (#1599's review): `tagpup_cli._read_again` starts an
  ExifTool session for each batch of 100 and asks `face_refs` once for each such photo (`refresh_rows.read_files`). If
  every photo without a DocumentID is indexed (58,973 of photo_index's 59,519, the Files owner's count, 2026-10-17),
  that is about 590 session starts and about 59,000 indexed queries, against a second CLIP and face-detection pass for
  each of those photos, which is what the next index did without the re-read.

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

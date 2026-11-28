# Files and sync: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-files.md`: its Scope, the contract the other domains rely on, its
invariants and guards, and its scenarios F1 to F11 (the clip flows, F12 to F19 until 2026-11-13, are
Media's C1 to C7: docs/domains/media.md). This page states each of the seven conditions of health
(docs/OWNERS.md) with its evidence and date. A condition not met is the domain's queue.

## What the domain holds (counted 2026-11-08, read-only)

Photos and clips recounted 2026-11-13 (read-only, `db.readonly_uri`; the other columns are not recounted): photo_index
62,229 photos of which 2,910 clips, kr-track 2,186 of which 5, renton_parkrun 1,309 of which 0. The libraries are
three: the fourth `.db` in `data/` is `kr-track.before-people-merge-20261102-122703.db`, a backup, not a library (#2932).

| Library | Photos | Of them clips | Folders | With a DocumentID | Faces | Changes of photo files in the journal |
|---|---|---|---|---|---|---|
| kr-track | 2,070 | 5 | 35 | 2,065 | 12,664 | 1,846 |
| photo_index | 61,797 | 2,179 | 2,618 | 980 | 183,669 | 2,953 |
| renton_parkrun | 1,309 | 0 | 9 | 1,309 | 3,315 | 1,202 |
| all three | 65,176 | 2,184 | 2,662 | 4,354 | 199,648 | 6,001 |

The photos without a DocumentID are photo_index's 60,817 and kr-track's 5 (its clips); the journal's files are
95,231 done and 139 conflict in photo_index, 4,696 done in kr-track and 4,319 in renton_parkrun. One library's root is on
a network share (renton_parkrun's 1,309 rows, the share counted 2026-10-15). No row is stamped (0.0, 0), none has a NULL
stamp, and none holds XMP PersonInImage or RegionName (all 0 in the three, 2026-11-08).

## The seven conditions (restated 2026-11-08; the first pass was 2026-10-15)

What the pass of 2026-10-15 held is below with today's evidence; what the work since added (the own metadata writer and
its editors, the clips, the bake) is stated where it belongs, and the long sections after this one (the census, the
editors) are the evidence those scenario tests cite.

1. **No reach-ins.** Met. Every use of a Files module by another domain is a row of the Integration charter's contract
   table: Files is the callee of rows 6, 7, 38, 46, 47, 48, 50, 51 and 53 to 59, and of rows 69 to 72 and 74 (a video's
   bytes through `read_span`; the web server's settling of a half-done change; the page record's `writes`; the delete
   check and the Recycle Bin's keeping; the thumbnail cache and a clip's poster). Files is the caller of
   rows 60, 61, 62 (vocabulary, the people vocabulary, the faces store), 65 and 66 (the index's matching of the faces it
   made and its reading of a bake) and 68 (`sync._said` into `tagpup.logs`). Rows 50 to 59 are the 2026-10-15 pass's:
   inspect, tags, identify, box_shape, the naming jobs' folder scope, the journal's undo and dating, the thumbnails'
   rows, and face_people's write through `file_changes.batch_exclusively`. Each names its guard test; box_shape is review
   only. face_people's own ExifTool session went (#1474).
2. **No rule written twice.** Met.
   - One batch reader, `field_values.read`: an operation's reads (`Fields.read`: sync's pairing, Smart Rename's
     captions, Apply All's people, a caption's rule; `Fields.whole`: the indexer's) and the write sessions' read
     through it (#1473); ExifToolSession is constructed in field_values alone, but for the write sessions L0b moves. A timeout ends the read and leaves every photo not read unread (#1490).
   - One writer of a photo's fields, `field_values.write`; the identity mint writes through it (`ensure_document_id`).
     It writes a file one of two ways: a journaled write of a JPEG whose fields fit is patched in place
     (`in_place.patch_jpeg`, called from nowhere else; `PATCH_IN_PLACE`, True), every other write is ExifTool's rewrite.
     A patch's new segments are made by `segment_edits`, the own writer (the one caller of the three block editors,
     `tests/test_the_block_editors_have_one_caller.py`; on since 4d, 2026-11-16, ExifTool's header-copy patch deleted).
     `file_only`, `ensure_document_id` and the bake fields do not reach the own writer (no `saving`, or not in
     `fields.EDITED_AS`, so `segment_edits` refuses): they are ExifTool's rewrite. A clip is patched by `clip_patch`
     (a clip patch is outside `field_values.write`; Media's Convert reaches Files' packet only through
     `field_values.packet_held` / `packet_given`, `PACKET_UNREAD`, `NotAnswered`: contract row 77).
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
   - Deleted, 0 live rows each (still 0, 2026-11-08): the (0.0, 0) stamp of a failed stat, `_structure(stamp=None)`, `record_indexed`'s 0.0
     defaults and Apply All's `.get("people") or []`.
   - Kept, with the user action that makes the case:
     - an unstamped record: a share that stops answering during an index (one root is on a share), an ExifTool that
       will not start, a file whose stat fails;
     - a stop during the planning read: Cancel or a hand-over;
     - a Ctrl+C part-way.
   - `tests/photo_rows.stamp_of`'s (0.0, 0) is a fixture's, by design (its docstring).
6. **Scenario tests, each file owned and mapped.** Met. F1 to F11 each have files in `tests/owners.py`
   (Files' test files, counted 2026-11-13 after the clip files went to Media: 50 scenario, 46 unit, 2 guard), and `test_bulk_delete` is F8.
   `test_service_save_photo` and `test_save_reply_says_rename_failed` were lifted into F1
   (`tests/test_tag_a_photo_read_it_back_and_undo.py`) with mutation proofs, and the restatements deleted. The clip flows
   (F12 to F19 until 2026-11-13, #2736) are Media's C1 to C7 (#2926).
7. **No open data or wrong finding, and every low fixed or by design.** Met for data and wrong
   (`tools/add_findings.py --open`, 2026-11-08: 20 open, all low). Files' open lows are #2754 and
   #2755 (the watcher), #2756 (an unreachable arm of the bulk edit) and #2735 (this page, closed by this restatement).
   Closed: #1490 (wrong), #1491 to #1496, and the lows below, by design.

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

A photo's Delete (`services/photos.delete`) is not a write and is journaled too, as an INTERNAL change
(`file_journal.DELETE`: never listed by History nor undone; UNDO_CHECKS says the Recycle Bin is the way back): planned,
its file marked `writing` with the stamp of the file there, sent to the Bin, and done in the transaction that removes the
photo's rows (`store.photos.forget_photo`'s `also`), all under the lock of changes of photo files. A stop between them is
settled by `photos.settle_delete` (`file_changes._finish`): a file still `planned` is withdrawn, nothing deleted; one left
`writing` is forgotten if gone, sent again if it is still the file stamped, withdrawn if another file is at its path, and
waited for on a share that does not answer. A Just-look photo's Delete has no record. A library view's bulk Delete
(`photos.delete_in_chunks`) is ONE such change, each file planned with the look the start took of it, carried out a chunk
at a time: stopped by Cancel, the rest are withdrawn; stopped by an update (`Batch.for_an_update`, a Cancel in the same
chunk winning), they stay `planned` and the change says how many in its summary (`LEFT_FOR_THE_NEXT_START`, never its
`owner`, #3256), which the next start's `settle_delete` deletes, each only while it is the file looked at; one gone by then
(removed, its folder renamed, its drive not connected), unreadable or on a share that does not answer is withdrawn with a
warning, its row and faces kept: it was never begun, so gone is the Delete's doing only for a `writing` file. The count is
taken off before the first is begun, so a kill during that resume withdraws the rest (owner's answer 4).

Every photo's file is baked with no command (owner, 2026-10-28): a current vector with no fingerprint row
(`photo_fingerprints.NO_FINGERPRINT`: Suggest's or a re-embedding pass's) is work sync owes the index, whose bake after
the index writes the file with no model (the row follows the write) or, for a file holding its bake or one marked
read-only (`file_changes`' `read_only`, the one failure no retry changes), records the decode's row
(`photo_fingerprints.adopt`); any other photo stays owed: the bake takes away the index's rows of this run's photos
before it plans, so only those three give one back, however the bake ends; an index that ends before its bake (an error, a Ctrl+C) takes them away too (`tests/test_the_index_writes_the_bake.py`).

## The copy the index reads, and a write that leaves copies stale (M4, merge 4a)

A photo's tags and captions are read from XMP when it holds them, IPTC's copy only when XMP holds none
(`fields.KEYWORD_COPIES`, `CAPTION_COPIES`; DECISIONS 2026-11-03, XMP IS THE COPY THE INDEX READS). Each value is
read from its own pair (Subject or Keywords, Description or Caption-Abstract, Title or ObjectName), and a field present
but blank holds none: 12,469 photo_index rows hold an empty XMP description beside an IPTC caption, 12,473 an XMP title
and only an IPTC caption, 151 IPTC keywords and no XMP ones (4 of them with an XMP packet) (counted 2026-11-03). A write
answers the fields it left as they were (`field_values.write`; `fields.XMP_ONLY`, the non-XMP fields a keyword or
caption write sets), and the file is verified, recorded and journaled as `fields.as_patched` says it holds them
(`file_changes._carry`). Every write still writes every copy, so that answer is always empty until merge 4b.

A photo indexed or saved is derived by this rule (the indexer, `follow_fields`). The rows stored before it (photo_index
383 rows; kr-track and renton_parkrun none; counted 2026-11-03) were derived again from their own raw_metadata by a
one-off, deleted once 0 rows were apart in the three libraries (#2734); photo_index's journal holds its changes,
"rederive tags and captions", undone from History as any change of rows (`journal.RETIRED`). The page's title is
`fields.title_of` over the row's raw_metadata, the rule the save compares by, whatever order the row stores (#2582).

A photo's title and its caption are two values (DECISIONS 2026-11-06). The title is XMP:Title, else IPTC:ObjectName
(`fields.TITLE_COPIES`, written by `fields.title_fields`: the photo panel's save); the caption is the Description,
XMP's else IPTC's Caption-Abstract (`DESCRIPTION_COPIES`; EXIF:ImageDescription and XPComment are written copies, never
read), which no page writes. The title shown is `fields.title_of`: the title, else the Description, so the index's
`captions` hold the title first. A save of a photo with no title, its Description shown as the title, moves it: the
Title gets the title saved and every caption field holding the text shown is cleared in the same write
(`tagging._caption_moved`), whether the title saved is that text, another, or none: one rule, so a cleared title stays
cleared. A photo with a title of its own keeps its Description. Rows stored before the rule put the Description first
(photo_index 8,263, kr-track and renton_parkrun none; counted 2026-11-06) were ordered again by the same one-off, as
above. A library card carries no caption of its own (`store.library_view.card_rows`): the title a
card shows is the panel's record's (`web/tagpup/library-source.js`), `title_of`, as a folder's page records are; only
the order by caption (`caption_sql`) reads the stored `captions`.

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
  the daily job and the watcher never walk one library together; a run of the job and the watcher's sync of one folder
  (`recurring.sync_folder`) take the library's walk in the process (`recurring.walk`), the second told "running" and the
  folder asked again `RECHECK` later (`Watcher._folder_later`).
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
  The packet's size: under 16 KB 1,828 / 9 / 4, 16 to 48 KB 1,138 / 1,931 / 1,286, 48 KB or more 34 / 125 / 18.
- Extended XMP: 111 / 80 / 5 (as many packets name `xmpNote:HasExtendedXMP`). MPF: 100 / 14 / 0. Bytes after the last EOI:
  10 / 3 / 0.
- EXIF: two segments 4 / 0 / 0, none 2 / 0 / 3; slack 0 bytes in 1,652 / 1,120 / 926, 1 to 63 in 1,350 / 945 / 379,
  64 or more in none. IFD1 thumbnail 2,747 / 2,062 / 1,304. Orientation (of the EXIF segments): 1 in 1,950 / 15 / 0, 6 in
  12 / 0 / 0, 0 in 1 / 0 / 0, absent 1,039 / 2,050 / 1,305.
- MakerNotes: 1,613 / 1 / 0. photo_index by maker: Canon 1,239, every one ending in the Canon footer (its byte order
  and the offset it was written at) and every one moved from there: 1,236 by an amount that lands every pointer inside
  the note, 3 by one that does not; the Exif IFD's OffsetSchema equals the footer's shift in 15 and differs in 1,224.
  Sony 258: 251 moved by an amount nothing in the file says (233 of them little-endian notes in a big-endian TIFF
  whose last 4 of 12 entries are of no TIFF type, skipped), 1 TIFF-relative as written, 3 outside, 3 inline. Nikon 13
  own header; note-relative Apple 7, Fujifilm 7, Samsung 3, Casio 2; no IFD that parses Nokia 48, Google 15, other 11,
  Kodak 5, LG 3, Microsoft 1; inline Google 1. kr-track: one Canon, its footer saying it lies where it was written.
  ("Moved" and the other classes are the census module's.) ExifTool agrees (its warnings, read-only, on photo_index's
  sample): on 11 Canon notes the census calls moved (the 3 included), its "Adjusted MakerNotes base by N" equalled the
  footer's shift each time; on 8 Sony notes called "moved, shift unknown" and the one called TIFF-relative it warns
  "Possibly incorrect maker notes offsets (fix by N?)". So a note with no footer whose pointers land inside it as
  written may have moved too, and nothing but a Canon footer says by how much.
- IPTC: two APP13 segments 0 / 2 / 3; bytes after the last 8BIM 1 / 4 / 6; no 0x0404 block in 1 / 2 / 3; padded with
  PAD_RESOURCE 0 / 0 / 0. Charset 1:90 UTF-8 in 2,194 / 2,057 / 1,305, absent in 746 / 4 / 0; values outside ASCII 6 / 0 / 0
  in 2:xx datasets; datasets cut short 12 / 0 / 0.
- IPTCDigest (0x0425 against the MD5 of 0x0404): matches 2,093 / 153 / 463, differs 112 / 1,902 / 839, absent 735 / 6 / 3.
  The comparison is ExifTool's: on copies of 8 kr-track JPEGs the census's MD5 equalled ExifTool's CurrentIPTCDigest each
  time, and the stored digests are stale. An ExifTool IPTC write as TagPup makes one (`-IPTC:Keywords+=`) changes the
  block and leaves 0x0425 as it was, so a digest that matched stops matching (findings, this branch).
- Segment orders, the commonest: photo_index `JFIF EXIF XMP IPTC` 1,403 and `JFIF Adobe EXIF IPTC ICC XMP` 611;
  kr-track and renton_parkrun `EXIF IPTC ICC XMP Adobe` (with the tables) 1,939 and 1,297.
- Header read (bytes before the first scan, the first 50 reads), median / p90 / max in ms: photo_index 23 / 28 / 31
  (40 / 59 / 67 on a first, cold run), kr-track 40 / 51 / 59, renton_parkrun 39 / 47 / 55. ExifTool's read of the
  journal's fields (`fields.METADATA_FIELDS`, `field_values.read` through `ExifToolSession`) of the same 50 photo_index
  photos, warm, right after the census read them again in 23.5 / 30.3 / 43.7: one command a photo 51.9 / 59.6 / 75.8, one
  batch of the 50 in 1,396 (27.9 a photo).
- The journal's files: photo_index 51,312 done and 139 conflict, kr-track 4,696 done, renton_parkrun 4,318 done; none
  holds a patch record.

Against docs/PLAN_OWN_METADATA.md: extended XMP is 111 of 3,000 in photo_index (the plan relays 127 of 3,000); TIFF is
17 rows and PNG 2 (relayed 18 and 1); HEIC 0, as relayed. The plan's 2 KB XMP reserve is already there in nearly every
file (ExifTool's padding), and EXIF has under 64 bytes of slack everywhere. Every Canon note and nearly every Sony note of
photo_index was moved from where its offsets count, a class the plan's "absolute or relative offsets" does not name; M3
trusts no stored shift but a Canon footer, for Canon only (OffsetSchema is wrong in 1,224 of 1,239), and never moves a
note. ExifTool does not refresh the IPTCDigest, so "refresh to match ExifTool's" has nothing to match. M6 (an own read for
the journal's field reads): a photo read alone takes about half ExifTool's time (23 against 52 ms warm); against a batch,
little (23 against 28 ms a photo).

The census reads a TIFF IFD and an 8BIM resource through `tagpup.files.containers` (`ifd`, `TIFF_TYPE_SIZES`,
`Resource.data` and `.length`), the one statement of their layout; what a MakerNote's entries mean (the footer, the
classes: `maker_note`, `canon_footer`) and the walk of an EXIF block's IFDs (`exif_tiff`) are containers.py's since M3,
read by the census and the EXIF block editor alike, never a third copy. The
phase 6 XMP census (`inspect.xmp_census`, `scripts/census_xmp.py`) is gone: this census counts what it alone answered of
a JPEG (the XMP packet's size, EXIF Orientation; regions, extended XMP and two XMP segments it counted already), and its
17 TIFF and 2 PNG rows are no feature's count.

## The XMP packet editor

`tagpup/files/xmp_packet.py` (M2 of docs/PLAN_OWN_METADATA.md; its one caller `segment_edits`, for a JPEG's packet and, as `segment_edits.new_packet`, a clip's): pure, a packet's bytes in
and out, for a JPEG's segment and a clip's box alike. It owns nine properties (`PROPERTIES`, found by namespace URI):
dc:subject and lr:hierarchicalSubject (a Bag), dc:description and dc:title (an x-default Alt), exif:DateTimeOriginal,
xmp:CreateDate, tiff:Orientation, xmpMM:DocumentID and xmpMM:PreservedFileName (text). `read` answers their values;
`edit` sets them and keeps every other byte: a change is paid for out of the padding before `<?xpacket end`, so the
packet keeps its length, and only `grow=True` lets it grow, laying `RESERVE` (2,425 bytes, ExifTool's padding) afresh.
The padding is one rule: `containers.XMP_PADDING_LEAST` (one line break) is the least an edit, the container's
slack (`containers._xmp_slack`) and the own writer's packet fit (`segment_edits`) leave of it, and
`xmp_packet.padding(size)` lays the bytes (lines of 100 spaces) for the editor and the patch alike.
All three editors answer `containers.Edited(body, length_changed)` or `containers.Refusal(reason)`, one pair.
An edit is parsed again and read back before it is answered. Scenario tests: how a laid-out packet reads and that a
write changes its targets only, F12 (`tests/test_a_photos_fields_read_and_written.py`, on the corpus's `tagged` and
`premiere_mp4`); the decisions inside a packet, `tests/test_xmp_packet_edits_what_it_owns.py` (F1).

Counted this session, read-only, through `scripts/census_segments.py`'s reads (seed 20261103): photo_index 1,000 of its
59,598 JPEGs sampled, kr-track's 2,065 and renton_parkrun's 1,308 every one; 4,373 XMP packets, every one read, none
refused. Each was edited in memory (a caption, a keyword added, an Orientation), nothing written.

- **Refused, each with no instance** (0 of the 4,373, nor of the 80 clips' packets): a property written twice (two
  elements, or an attribute and an element), a Bag or an Alt written as an attribute, two `rdf:about`
  values, a DTD, a comment, a CDATA section, a processing instruction or text beside an element (an edit that clears a
  property splices from the element before it), a byte order mark or UTF-16, a NUL, an Alt not one x-default item, bytes after the end PI, `end='r'`
  (all 4,366 wrapped are `end='w'`). CRLF: none in the packets. A value XML cannot carry (NUL, another C0) is refused,
  and so is an empty keyword (a Bag item ''; nothing real writes one and ExifTool drops it);
  `&`, `<`, `>` are escaped and a CR written `&#xD;`. No size is refused here; a JPEG packet is bounded by its segment, a clip's by `PATCH_CAP`.
- **Handled instances**: the empty caption `<rdf:li xml:lang='x-default'/>` (photo_index 264 of 1,000, renton_parkrun 1)
  reads '' and '' or None writes nothing; no wrapper, 7 in kr-track (an edit at its length, or growing with no padding
  laid; the 7 grow); a single line break of padding in 11 (kr-track 9, renton_parkrun 2): an edit needing room is
  refused without `grow` (a JPEG's, whose segment is fixed: the rewrite's); with `grow` the packet is laid `RESERVE` of padding and grows (a clip's, placed by `clip_patch`); a namespace URI holding `xmlns/` (Iptc4xmpCore), 2
  in photo_index. ExifTool's padding, a line break and 24 lines of 100 spaces (`padding(2425)`), in 4,355 of the 4,366
  wrapped packets.
- **What it does differently from ExifTool**: a property of a namespace the packet does not declare is declared on the
  first Description (ExifTool makes a Description for it); Descriptions keep their order; a cleared property's
  namespace declaration (and an emptied Description) stays. On the 4,373: every edit kept the length where the padding
  allowed (4,355), every byte outside the owned elements and the declarations added was equal, an edit of the values
  held changed no byte, and a caption and keyword undone gave the bytes back wherever dc was declared before (a
  declaration added stays). The previous worker compared it with ExifTool on 5 sidecar copies: the owned and all other
  tags equal.
- **Not verified**: an ExifTool edit of the same change inside a JPEG, byte for byte (M4's shadow comparison).
- **A text property written as an attribute of rdf:Description** (Adobe's tools write a clip's packet so) reads as its
  element does; an edit changes the attribute's value where it stands, its quote kept (`&`, `<`, `>`, the quote, a tab,
  CR and LF written as references, since a parser reads a plain tab or line break in an attribute as a space), and a
  cleared one goes with the whitespace before it; it is never turned into an element. A property added to a
  self-closing `<rdf:Description .../>` opens it (`/>` becomes `>`, its children indented one space past its line, its
  end tag on a line of its own), its namespace declared inside the start tag. Counted this session, read-only: of the
  2,184 clips in the three libraries' 2,603 photo folders, 80 hold an XMP packet: 40 wrapped by Adobe Premiere or Media
  Encoder (`xmp:CreateDate`, `tiff:Orientation`, `xmpMM:DocumentID` attributes, padding of 2,000 B or more), 39 bare
  368-byte "Adobe XMP Core 5.1.0-jc003" packets (a self-closing Description, `exif:DateTimeOriginal` an attribute, no
  wrapper), 1 of 206,986 B (refused then; read and edited since). 79 read; each was edited in memory (a date, a keyword, a caption,
  the dates cleared), nothing written: the 40 every one at its length, the 39 only with `grow` (no padding) but for a
  value of the same length, and each edit made twice gave the bytes of once. JPEGs: 300 of photo_index's 59,600 sampled
  (seed 20261105) hold 300 packets, every one ExifTool's, none with a property as an attribute or a self-closing
  Description; ExifTool writes the attribute form only under its `-api Compact=Shorthand` option, which TagPup never
  passes. Read back by ExifTool (`-validate` clean) and ffprobe in a clip of each shape:
  `tests/test_an_adobe_clip_packet_edited_and_read_back.py`.
- **A clip's Date Taken**: docs/domains/media.md.

## The EXIF block editor

`tagpup/files/exif_block.py` (M3 of docs/PLAN_OWN_METADATA.md; its one caller `segment_edits`): pure, an EXIF segment's
body (`Exif\0\0` and the TIFF block) in and out. It owns nine fields (`FIELDS`): IFD0's ImageDescription, Orientation,
XPComment and XPKeywords; the Exif IFD's DateTimeOriginal, CreateDate, SubSecTime, SubSecTimeOriginal and
SubSecTimeDigitized. The block's layout is `containers.exif_tiff`'s, the one walk of its IFDs (IFD0, IFD1 and its
thumbnail, Exif, GPS, Interoperability: every byte range an IFD points at, `used`, the slack after it), and what a
MakerNote's entries mean is `containers.maker_note`'s and `canon_footer`'s, which the census reads too. `read` answers
the fields' values; `edit(body, changes, grow=False, limit=65533)` answers `containers.Edited(body, length_changed)`,
`DoesNotFit(needed)` (this module's) or `containers.Refusal(reason)`. A value of the same length or shorter is written where it was, the rest
zeroed; a longer one goes after `used` at an even offset, its old bytes zeroed; a cleared field loses its entry (the
table shrinks, its last 12 bytes zeroed). The block keeps its length; `grow` only adds bytes at its end, after
everything. Bytes no IFD points at are taken only with no MakerNote, or one whose pointers land inside it (TIFF- or
note-relative, its own header, none, moved by the Canon footer's shift, moved by a shift unknown). An edit is walked
again and read back, and every byte it changed is checked to be an owned table's, an owned value's or zero, before it
is answered. The editor takes one segment: a file with two (photo_index 2 of the 1,500 below) is its caller's to
refuse. Scenario tests: how a laid-out block reads and that a write changes its targets only, F12
(`tests/test_a_photos_fields_read_and_written.py`), which fails on a copy of the module that reads one byte order
alone, writes a value that fits its old place into the slack, answers a save that moves nothing a byte short, or keeps
a cleared field's entry (T1c, 2026-11-27); the decisions inside a block, `tests/test_exif_block_edits_what_it_owns.py`
(F1), which fails on broken copies such as a value laid over the thumbnail, a MakerNote byte moved with the checks
skipped, a note that is no IFD taken as contained, grow ignored, old values not zeroed (11 such copies counted
2026-11-03, before T1c moved four of its tests to F12; not counted again), and a copy that takes no MakerNote as
contained, which F12 passes.

Counted this session, read-only, in memory, nothing written: a seeded sample (seed 7) of photo_index 1,500, kr-track
400 and renton_parkrun 400 JPEGs; 2,297 with one EXIF segment, every one read, none refused, and writing back the
values each held changed no byte in all 2,297. Big-endian 1,433 / 6 / 3, little-endian 64 / 394 / 397 (photo_index /
kr-track / renton_parkrun). MakerNotes: none 617 / 400 / 400; moved by the Canon footer's shift 669, shift unknown 138,
own header 12, note-relative 9, not an IFD 46, the Canon footer wrong 1 (photo_index).

- **Refused, with instances** (each grown or not): a new tag, since data follows its IFD's table in every absent case
  of the sample (ImageDescription 415 / 396 / 399, Orientation 433 / 397 / 400, XPComment 87 / 396 / 399,
  DateTimeOriginal 28 / 0 / 0; moving the table into room made under `grow` would allow it, M5's choice); a new tag in
  an IFD out of tag order (IFD0 8, Exif 2, photo_index); a longer value beside a MakerNote that is no IFD (46) or whose
  Canon footer's shift lands a pointer outside it (1). The one new tag that fits is one a clear has just made room for. A new entry is laid only over bytes of zero
  or in the slack: 12 non-zero bytes no IFD points at (13 of 400 photo_index blocks hold some before `used`) refuse it.
- **Refused, no instance in the sample**: not an EXIF segment; over 65,533 bytes, read or made; a structure the walk
  cannot follow (an IFD or a value outside the block, an entry of no TIFF type); strips, tiles, SubIFDs or an entry of
  type IFD; an owned tag twice, of a type TagPup does not read (Orientation SHORT or LONG, count 1; an XP tag BYTE or
  ASCII; the rest ASCII), or text not UTF-8; an owned IFD or value sharing bytes with another; a date with no Exif IFD.
  Values: Orientation outside 1 to 8 (one held, 0 in 1 of the census's 3,000, given back changes no byte), a NUL, a date
  not `YYYY:MM:DD HH:MM:SS`, SubSec not digits, a lone surrogate, a field it does not own.
- **Fit**, photo_index's 1,500, each change alone (a 34-character caption; one keyword added; a date, an Orientation, a
  SubSec changed), of the tags present: dates, Orientation and SubSec always fit; ImageDescription 128 fit, 910 need
  `grow`, 36 refused; XPComment 34 / 1,322 / 46; XPKeywords 0 / 1,087 / 44. kr-track and renton_parkrun: every date and
  SubSec present fits, every caption and keyword present needs `grow` (369 and 249 changes).
- **XP tags**: read as UCS-2 little-endian whatever their type (type 2 in 1,392 of 1,402 photo_index XPComments; 337 of
  those hold printable ASCII bytes up to their first NUL, read, as ExifTool reads them, as UCS-2 characters), written as type 1 (BYTE) as ExifTool
  writes them. ImageDescription is written as UTF-8, as ExifTool writes it.
- **What it does differently from ExifTool**: ExifTool rebuilds the segment, TagPup keeps every unowned byte and
  offset and an IFD's order (ExifTool sorts it). On 12 copies the previous worker made of sampled headers (fictional
  values, the same change by each, TagPup's grown), recounted this session: TagPup's segment 14 to 176 bytes longer
  than ExifTool's in 10, the same length in 2; every EXIF tag ExifTool reads from the two equal, but ThumbnailOffset
  (ExifTool moves the thumbnail; TagPup keeps it) in 8.
- **Not verified**: Windows' own reading (Windows Photo Gallery, the Windows property system) of an XP tag TagPup
  wrote; a file with MPF when the EXIF block grows (MPF's offsets count from its own segment, which then moves); a
  rewrite in a file, byte for byte against ExifTool's (M4's shadow comparison).

## The IPTC block editor

`tagpup/files/iptc_block.py` (M3 of docs/PLAN_OWN_METADATA.md; its one caller `segment_edits`): pure, an APP13 segment's
body (`Photoshop 3.0` and its 8BIM resources) in and out. It owns three datasets (`DATASETS`): 2:05 ObjectName (the
title), 2:25 Keywords and 2:120 Caption-Abstract. The layout is containers.py's, the one statement of it, read by the
census, the in-place patch and this editor alike: `photoshop_resources`, `resource`, `pad_resource` (the dummy resource
the patch lays, `PAD_RESOURCE`, `LEAST_RESOURCE`) and `iptc_records`, the one walk of a block's datasets. `read` answers
the values; `edit(body, changes)` answers `containers.Edited(body, length_changed)` or `containers.Refusal(reason)` (a value with a lone surrogate
is refused). Only the 0x0404
resource's data changes, and in it only the owned datasets (one held is rewritten where its first one was, a new one goes
at the end of its record), the character set 1:90 and a missing version dataset; every other dataset and resource keeps
its bytes, the IPTCDigest (0x0425) among them, left stale (DECISIONS 2026-11-03). The dummy resource at the end takes up
or gives the room, so the body keeps its length when the change fits; `length_changed` says it did not, for the caller to
choose a patch or a rewrite. No 1:90 reads as cp1252, as ExifTool reads it; a block stays so while every owned value fits
cp1252, and is else given 1:90 UTF-8 and its owned values written in UTF-8. An edit is walked again and read back, its
values, character set and every kept dataset and resource compared, before it is answered. A file with two APP13
segments is its caller's to refuse (`containers.Jpeg.iptc` is None). Scenario test:
`tests/test_iptc_block_edits_what_it_owns.py` (F1), which failed on each of 7 deliberately broken copies of the module
(2026-11-03: the read-back skipped, the `kept` check skipped, each with and without a fault of the layout behind it; no
version dataset added; zero bytes after the last dataset kept). T1c (2026-11-27) moved only its save-that-moves-nothing
test to F12 (`tests/test_a_photos_fields_read_and_written.py`); the tests of the editor's own checks, the version
datasets and the zero bytes stay.

Counted read-only, in memory, nothing written: single-APP13 bodies 1,467 / 2,059 / 1,302 (photo_index's seed-7 1,500,
every kr-track and renton_parkrun JPEG); photo_index's recounted this session (1,467, 6 refused, 1,461 held values,
the fit rates and the version and zero counts below came out the same), the rest the building worker's.

- **Refused, with instances**: 6 photo_index blocks whose 0x0404 data does not start with a dataset.
- **Refused, no instance**: stray bytes after the last resource, two 0x0404 resources, an extended length, a 1:90 other
  than UTF-8, an owned title or caption written twice, a value not text in the block's character set; text outside
  ASCII elsewhere in a block with no 1:90 when a change needs UTF-8 (giving it 1:90 would change how that text reads);
  over 65,533 bytes, read or made; a NUL or a C0 control but tab, line feed and carriage return in a value.
- **Held values change no byte**: 1,461 / 1,461, 2,059 / 2,059, 1,302 / 1,302.
- **Fit**, a first edit, the length kept: a keyword added 4 / 1,461, 0 / 2,059, 0 / 1,302; a 34-character caption 127,
  3, 0; a title 368, 9, 0. Once an edit and its undo have laid the dummy resource, redoing a keyword or a caption keeps
  the length in every block.
- **Against ExifTool** (the building worker's 12 copies of sampled headers, an ASCII and a non-cp1252 value each): 22
  of 24 APP13 bodies byte-identical; the 2 that differ by design: for a value outside cp1252 in a block with no 1:90,
  ExifTool writes `?` and no 1:90, TagPup adds 1:90 UTF-8 (and 1:00) and writes the letter.
- **Decided here, as ExifTool does** (the own writer does as ExifTool's rewrite does): the version datasets 1:00 and 2:00 added to
  a record that lacks one (photo_index: 1,441 of 1,460 blocks with records lack 1:00, 1,109 lack 2:00); zero bytes after
  the last dataset dropped (754 of 1,461); a trailing NUL read off a value (1,041 photo_index captions end in one); a
  value cut by `fields.as_written`, which every write of a value passes today before ExifTool, so the file holds what an
  ExifTool write of TagPup's leaves (ExifTool with `-m` cuts nothing itself; without it, it cuts at 64 bytes: probed
  this session on a made-up JPEG).
- **Not verified**: Windows Photo Gallery or Lightroom reading a block whose IPTCDigest is stale after TagPup's edit
  (#2408); a rewrite in a file, byte for byte against ExifTool's (M4's shadow comparison).

## segment_edits: a write's values made into a header's segments

`tagpup/files/segment_edits.py` (M4 of docs/PLAN_OWN_METADATA.md; `in_place.patch_jpeg` calls it
for every journaled write of a JPEG, since 4d; `services/metadata_check.py` is the shadow comparison on
copies, its ExifTool side always ExifTool's rewrite, kept for the first weeks): pure, the journal's values of a write ({field: [texts]}) and a JPEG header (its bytes to the
first scan's header's end, `in_place._header_of`'s) in; `new_header` answers the header with only the EXIF, XMP and IPTC
bodies changed (every marker and length byte the header's) and the fields it left stale, or None: not a patch, the
caller rewrites the file, nothing written. It is the one caller of the three editors
(`tests/test_the_block_editors_have_one_caller.py`; one named exception, `in_place`'s `xmp_packet.padding`).

- **What maps to what** (`fields.EDITED_AS`): XMP:Subject, XMP:HierarchicalSubject, XMP:Description, XMP:Title,
  XMP:DateTimeOriginal, XMP:CreateDate, XMP-xmpMM:PreservedFileName to the packet's dc:subject,
  lr:hierarchicalSubject, dc:description, dc:title, exif:DateTimeOriginal, xmp:CreateDate, xmpMM:PreservedFileName; EXIF
  DateTimeOriginal, CreateDate, ImageDescription, XPComment, XPKeywords and Orientation to the EXIF block's (not the
  three SubSecTime: no write names them, a Date Taken is set to the second); IPTC:Keywords, Caption-Abstract and ObjectName to the IPTC block's. EXIF:Orientation is also written to a
  tiff:Orientation the packet holds, never one made (ExifTool's ungrouped "Orientation"). Not mapped, so no patch:
  the DocumentID (a rewrite mints it), a bake's fields.
- **The XMP-only rule** (DECISIONS 2026-11-03; `fields.XMP_ONLY`): a keyword save goes to XMP alone while its
  XMP:Subject is not empty, a title save while its XMP:Title is not, a caption save while its XMP:Description is
  not; IPTC:Keywords and EXIF:XPKeywords, IPTC:ObjectName, or IPTC:Caption-Abstract, EXIF:ImageDescription and
  EXIF:XPComment, are answered stale and keep their bytes. The last
  keyword taken off, or a caption cleared, writes every copy (#2433: XMP holding none, the reader would take IPTC's and
  the value would come back), a patch where they all fit, else a rewrite. XMP is given the planner's whole set
  (`tagging.keywords_planner` reads XMP and IPTC), so a photo whose keywords were in IPTC alone (151 in photo_index,
  4 of them with a packet, DECISIONS 2026-11-03) gets them all in XMP; segment_edits never infers a set.
  Known, by this design (DECISIONS 2026-11-03, 2026-11-05 (1)): the undo of a caption cleared through the own writer
  leaves its copies outside XMP as the clear left them, empty: IPTC:Caption-Abstract, EXIF:ImageDescription and EXIF:XPComment
  (the XMP copy returns; the others are answered stale, fields.XMP_ONLY), pinned by
  `test_a_write_records_what_the_file_holds_and_an_undo_finds_it` in `tests/test_a_file_holds_what_exiftool_kept.py`.
- **Spelling**: a text field takes one text (two: no patch); Orientation a number; an EXIF date only
  `YYYY:MM:DD HH:MM:SS` (a zone: the editor refuses, a rewrite; a Date Taken carries no fraction: 0 rows whose `taken`
  holds one in 59,618 / 2,065 / 1,309, counted 2026-11-05, and the page's picker is to the second); an XMP date in ISO 8601 with the value's own
  fraction and zone, none added. The live rows' XMP:DateTimeOriginal all take that spelling: photo_index 52,825,
  kr-track 52, renton_parkrun 3; XMP:CreateDate 20,222 / 2,049 / 1,304, and 1 photo_index value that does not (a
  rewrite); EXIF dates not of the one shape (a rewrite): DateTimeOriginal 2, CreateDate 5, photo_index only (counted
  2026-11-03, read-only). ExifTool spells the five shapes (plain, zone, fraction, both, Z) as segment_edits does,
  byte for byte, compared on a made-up JPEG.
- **No patch**: an unmapped field; a value it cannot spell; any editor's Refusal or DoesNotFit or `length_changed`
  (never `grow`); a change for a segment the photo lacks (ExifTool makes one: a rewrite), a clear of one it lacks
  changing nothing; a photo holding two segments of a patched kind (two EXIF 4 / 0 / 0, two APP13 0 / 2 / 3 in the census
  above), refused before this module by `in_place._header_of`, whatever the write changes; a photo whose EXIF IFD0 holds
  an XMP packet of its own (tag 0x02BC, Windows Live Photo Gallery's; ExifTool's read answers that copy, so a patch of
  the APP1 packet alone reads back unwritten; #3108), whatever the write changes, IFD0 read only to its tag list; a rotation of a photo with no EXIF Orientation, since the IFD0 table is followed by data in every
  absent case counted (docs above, "The EXIF block editor"), refused by the editor, not by a rule here.
- **Not verified here**: a patch in a real file against ExifTool's (the shadow comparison is
  `services/metadata_check.py`; its result is not recorded on this page).

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

- 2026-11-08: the page restated: counts, the seven conditions and the contract rows (69 to 74 as callee, 60 to 68 as
  caller) as of today (#2735); the lines that called the editors callerless and the two re-derives live were corrected.

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

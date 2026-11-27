# T1 of the own metadata library: one corpus and one conformance suite (a build brief)
Plan: docs/plans/own-metadata-library.md (section 4 "T1", section 5, the conformance items named in sections 1, 3 and 6).
Domain: Files. Design read on trunk d1af4b58 (2026-11-27; L0a-c and R merged). Tests only, but two small product edits in
T1a (section 6), asked in section 8 (Q1). Live counts: the plan's census of 2026-11-26 unless marked "counted
2026-11-27" (read-only, `db.readonly_uri`, counts only). Rows: photo_index 60,355 / kr-track 2,186 / renton_parkrun 1,309 /
mu_dance 1,284 (counted 2026-11-27, as the plan's); JPEG 57,392 / 2,181 / 1,308 / 1,280; PNG 25 / 0 / 1 / 0; TIFF 17 /
0 / 0 / 0; JPEG rows holding IPTC:Keywords and no XMP:Subject 149 / 0 / 0 / 0 (counted 2026-11-27).

Steady state: nothing in the product changes. From T1 every later stage (M, G, J1-J3c, P1, C1) is judged by one suite
that calls `field_values.Fields` and nothing below it, with nothing changed in the suite but one line of its `ANSWERS`
table when a format moves. What the owner sees: nothing; the suite's time (section 7).

## 1. The corpus (`tests/corpus.py`, extended; decision 1)

**Where and how.** Every item is a recipe in `tests/corpus.py` made through `corpus.made` (:170), so it inherits what the
module already settles: one item per recipe digest, made under the stripe lock into `<key>-part-making<ext>` and
`os.replace`d into its name (`_make` :195), read-only, copied per test (`shutil.copyfile`, never a link). Two kinds:
- **Kept** (machine-wide, `%LOCALAPPDATA%\TagPupTestCache\corpus`): a picture PIL makes, then ExifTool's or ffmpeg's
  write. Their build calls PIL, ExifTool, ffmpeg and `tests/corpus.py` / `tests/damaged_photos.py` only, never a
  `tagpup` module, because only `MAKERS` (:75) decides the generation: a tagpup change would leave a kept item stale.
  `MAKERS` gains the two fixture files a recipe reads (`tests/fixtures/xmp_in_ifd0.json`, `tests/fixtures/clip_headers.json`).
- **Laid** (per run, `keep=False`): bytes laid by Python (a second APP13, a NUL segment, a captured header) over a kept
  item's bytes; milliseconds. Any recipe that calls tagpup code (`containers.exif_tiff` in the IFD0-packet layout) is laid.
- **Synthetic only.** Pictures are `damaged_photos.whole_jpeg` noise (64 x 48: the picture bytes the picture check
  compares are real entropy, a file is about 5 KB) or a solid colour for clips' ffmpeg `testsrc` (64 x 48, 1 s); every
  text is fictional (metadata_check's `KEYWORD`, `PERSON`, `CAPTION`, `PRESERVED`, :82-84, which carry `&`, `<`, letters
  outside cp1252 and outside ASCII: Q10's faults). No owner file, no checked-in photo. A captured layout (the two
  fixtures that exist) holds structure and fictional texts of the same length only.
- **Each recipe names its live count** in its docstring (the census row of plan section 3, or the user action that makes
  it); no count and no user action, no item.

**The items** (op names: section 2; "all" = every operation of the format; path today = which writer a journaled write
takes: P the own patch, R ExifTool's rewrite, `-` refused).

| Item | Made by | Live count or user action | Ops | Path today |
|---|---|---|---|---|
| `camera` | noise + ExifTool EXIF (Make, Model, DateTimeOriginal, Orientation 1, IFD1 thumbnail); no XMP, no IPTC | user action: a camera's photo added, before its bake (no XMP: 0 in all four after it) | all | R (no XMP to patch) |
| `tagged` | `camera` + every owned field by ExifTool (XMP, IPTC with no 1:90, XP copies; keywords with a person path, caption, title, dates, DocumentID, PreservedFileName, Rating, GPS) | every JPEG holds XMP (62,161); no 1:90 15,444 / 4 / 0 / 0; Rating 55,695 / 172 / 4 / 817; GPS 1,552 / 1 / 0 / 0 | all | P where it fits |
| `tagged_ii` | `tagged`, `-ExifByteOrder=II` | the census's byte orders (T1a recounts) | dates, turn, keyword | P |
| `iptc_alone` | `camera` + IPTC Keywords, an XMP packet with no dc:subject | 149 / 0 / 0 / 0 (counted 2026-11-27) | keyword, last keyword off | P |
| `iptc_utf8` | `tagged` with a caption outside cp1252 (ExifTool adds 1:90) | 124 / 0 / 0 / 0 | caption set, cleared, keyword | P |
| `app13_without_iptc` | laid: `tagged`'s APP13 holding only a 0x03ED resource | 18 / 2 / 3 / 0 | caption set, keyword | P / R |
| `two_app13` | laid as test_the_two_writers_compared_on_copies :112-118 lays it (lifted) | 0 / 2 / 3 / 0 | last keyword off, caption cleared | R |
| `two_exif` | laid: `tagged`'s Exif APP1 once more after it | 73 / 0 / 0 / 0 | dates, turn | R |
| `two_xmp` | laid: two APP1 XMP | 0: the refusal arm's one item (J2, J3a) | keyword | R |
| `extended_xmp` | `tagged` + a bake of 40 faces by ExifTool (it splits the packet) | 2,488 / 85 / 5 / 7 | keyword, bake | R |
| `big_xmp` | `tagged` + a hierarchy of fictional keywords to a 60 KB packet, no extension | 202 / 40 / 5 / 9 | keyword | R |
| `mpf` | PIL's MPO of two noise frames + ExifTool's fields | 2,811 / 14 / 0 / 0 | keyword, dates | P / R |
| `trailer` | laid: `tagged` + 64 bytes after the EOI | 46 / 3 / 0 / 0 | keyword, dates | P |
| `maker_note` | laid: `tagged`'s EXIF with a Canon-footed note IFD (lifted `note_ifd`, test_exif_block_edits_what_it_owns :87) | Canon moved 10,716, Sony moved 5,108 (photo_index) | dates, turn | P |
| `no_taken` | `camera` without DateTimeOriginal | 60 / 0 / 3 / 0 | Date Taken set | R (adds an entry) |
| `ifd0_xmp` | laid: the captured header (lifted `photo_with_xmp_in_ifd0`, test_a_photo_written_by_its_own_editors :92) | 39 / 0 / 0 / 0 (#3108) | keyword, shift, turn, last keyword off | R |
| `nul_xmp` | laid: an XMP segment of NUL bytes | 3 / 0 / 0 / 0 (#2349) | keyword | R, fails (Invalid XMP) until J3a |
| `no_wrapper` | laid: a packet with no `<?xpacket` | 52 / 7 / 0 / 0 | keyword | P |
| `other_appn` | laid: `tagged` + APP11, APP15, COM | 1,622 / 1,049 / 2,468 (photo_index) | keyword, dates | P |
| `thumb_turn` | `tagged` + IFD1 Orientation and XMP-tiff:Orientation (as :394-405 of test_a_photo_written_by_its_own_editors) | thumbnail 52,306 / 2,178 / 1,304 / 1,280 | turn, mirror | P |
| `png` | PIL PNG + ExifTool XMP (iTXt), EXIF (eXIf), IPTC | 24 of the 26 | all | R |
| `png_xmp_only` | PIL PNG + ExifTool XMP alone | 2 | keyword, turn | R |
| `tiff` | PIL TIFF (II) + ExifTool XMP (700), DateTimeOriginal, XPComment, Rating, Orientation | 17 of 17 | all | R |
| `mp4` | ffmpeg mp4 + ExifTool XMP (uuid box after moov) | .mp4 1,745 | clip: tags, title, date | P (outside: `-`) |
| `mov` | ffmpeg mov + ExifTool XMP (inside moov) | .mov 1,173 | clip | P |
| `mp4_bare` | ffmpeg mp4, no XMP (the patch appends one) | user action: a phone's clip added | clip tags | P |
| `premiere_mp4` | laid: `mp4_bare` given test_xmp_packet_edits_what_it_owns's `PREMIERE` packet (properties as attributes, about 200 KB; lifted) | user action: a clip exported by Adobe Premiere (C2's test_an_adobe_clip_packet_edited_and_read_back) | clip tags, date | P |
| `clip_headers` | laid: every shape of `tests/fixtures/clip_headers.json` (lifted `clip_of`, test_a_clip_dated_through_the_journal :101) | 2,930 clips' header layouts, as captured | clip date | P |
| `cut_short` | `damaged_photos.truncated` | user action: a copy that stopped | keyword (step 3) | R |
| `no_picture` | laid: `tagged`'s header with no scan | 0; user action: a copy cut short | keyword (step 3) | `-` (`NO_PICTURE`) |
| `zeros`, `text_named_jpg` | `damaged_photos.all_zeros`; a text file named .jpg (#46) | user action: a file misnamed or zeroed | read only | `-` |

Conditions put on a copy, not items: **held open** (`lock_owners.opened(copy, lock_owners.NO_SHARING)` held across the
call, as test_partial_writes_leave_rows_unread :310 does; user action: an editor holding it); **read-only**
(`os.chmod(copy, stat.S_IREAD)`; user action: a photo copied off a disc, `field_values.NOT_MOVED` :117); **away** (section 2,
S3.6).

**Not in T1, each its stage's captured layout** (a class that exists only in owner files: its brief captures one file's
layout, texts replaced): an EXIF block the walk cannot follow (J1), IPTC datasets cut short and bytes after the last 8BIM
(J1 read, J3b write), a GUID/MD5 mismatch (0: J2's refusal item), PNG caBX and TIFF SubIFDs (P1), Apple's keys, udta
items, CNTH, MVTG, a clip of 4 GB or more, a .wmv's ASF XMP (C1), WebP (0 rows: J2's refusal item). T1 writes no recipe
for them: a recipe with no caller is a branch with no instance.

## 2. The conformance cases (`tests/test_a_photos_fields_read_and_written.py`; decision 2)

`before` = `Fields.read([copy], targets)` before the write; `oracle(p)` = `field_values.oracle(EXIFTOOL).whole([p])`; a
field's oracle value = the first key of the oracle's answer whose family-0 group and name are the field's (an item where
two copies of one field disagree names the one read: `two_exif` the first). Values compared through
`fields.field_values`. Every other tag = the oracle's keys but `metadata_check.SET_ASIDE` (:67), `KNOWN` (:75), the
targets' keys and the item's `also` (TagPup's own rule: `IPTC:CodedCharacterSet` when the charset rule gives UTF-8).

| Case | Setup | Call through Fields | Expectation | Turns from ExifTool to the library at |
|---|---|---|---|---|
| S1.1 named fields | every item | `read([copy], fields.METADATA_FIELDS)` | each field equals its oracle value, or Unreadable with the item's sentence | JPEG J2 (J1 judged in between by the same case on `segment_edits.held`), PNG/TIFF P1, clips C1 (header dates own: built) |
| S1.2 the whole record | every item | `whole([copy], bakes=True)` | stamped, no `read_error`; `SourceFile` the oracle's; `raw_metadata` the S1.1 values; a bake item's `baked` parses (`bake.parse`) to the bake written (TagPup's canonical text: hand-written); from G its keys are grouped only (G flips one assertion) | as S1.1 |
| S1.3 a batch is its files | every item of a format | one `read` of all, then each alone | the same answers, in the order given | as S1.1 |
| S2.1 a write lands as the oracle reads it | each item x its ops x two sides: `journaled` (`saving` given, a recorder) and `outside` (none: `file_only`'s and the index's) | `write(copy, after, saving=...)`, then `read_back(copy, targets)` | the path taken (recorder or `scratch_written` called, as `metadata_check._written` :166 tells them) is the item's; targets read by the oracle as `fields.as_patched(before, after, stale)`; every other tag as before; picture bytes and every non-target segment byte-equal (`metadata_check._outside` :158); `read_back` equals the oracle | patch: built (4d, M7 for clips); rewrite J3a, its layouts J3b, the bake J3c; PNG/TIFF P1 |
| S2.2 the same write twice | as S2.1 | `write` again | no byte changes; on the patch path the recorder is not called (a patch with no byte to write saves no record) | as S2.1 |
| S2.3 its undo | as S2.1 | `write(copy, before)` | the oracle reads every tag as the original but KNOWN; an XMP-only op (`fields.XMP_ONLY`) undone on the patch path gives the original bytes back | as S2.1 |
| S3.1 cut short | `cut_short`, `no_picture` | `read`, `write` | `cut_short`: read as the oracle reads it, a keyword written, the picture's bytes as they were (DECISIONS 2026-10-15); `no_picture`: the write raises with `NO_PICTURE`'s words, bytes unchanged | J2 / J3a |
| S3.2 not a photo | `zeros`, `text_named_jpg` | `read`, `whole`, `write` | `read` Unreadable; `whole` stamped with `read_error` (a lasting failure, R); `write` raises, bytes unchanged | J2 |
| S3.3 one bad file in a batch | `[tagged, zeros, camera]` | one `read`, one `whole` | the good ones answer as alone, stamped with their own stamps; only `zeros` Unreadable; a batch of good files alone is one ExifTool command (counted) | J2 (the count becomes 0) |
| S3.4 held open | any item, held with no sharing | `whole`, `read`, `write` | `whole`: unread `metadata.HELD`, no stamp (R, built); `read`: Unreadable; `write`: raises, the file's bytes unchanged once let go, nothing beside it; a clip: Refused with `in_place.OPEN_ELSEWHERE` (:83) | R built; JPEG write J3a (Refused, OPEN_ELSEWHERE) |
| S3.5 read-only | `tagged`, `mp4` | `read`, `write` both sides | read as the oracle; the write raises with `NOT_MOVED`'s words (JPEG) or the clip patch's open error, bytes unchanged, nothing beside it | J3a |
| S3.6 a share that does not answer | `[tagged, X, camera]`, X away | `read`, then `read([camera])`; the same inside `together()` | X and every later file of THAT call NotAnswered, the next call answers; inside `together()` every later call fails at once; a write of X raises NotAnswered, the file as it was (or settled by its bytes, patch) | J2 (the stand-in's own half answers) |
| S4 a moved format refused starts no ExifTool | every `ANSWERS` entry "own" with a refusal: today a clip written outside the journal (`Refused(images.CLIP_NOT_EDITED)`, `_clip_written` :690) and a clip turned (`clip_refused` :647) | `write` | `ExifToolSession` patched to raise at construction: the refusal is the item's sentence, nothing raised from ExifTool | each stage adds its format's refusals (J2: `two_xmp`, `nul_xmp` reads) |

**The operations** (decision 3): `metadata_check.OPERATIONS` (:110; the nine: keyword added, last keyword taken off,
person added, caption set, caption cleared, Date Taken shifted, turned right, mirrored, preserved name set) imported, not
spelt again, plus four of the suite's own: title set (`fields.title_fields`), Date Taken set (`fields.date_taken_fields`),
a mint (`{"XMP-xmpMM:DocumentID": id}`, decision 5), a bake of 0, 1 and 40 faces (`baked_files.record`, its values made as
`baked_files.write` :62 makes them: that function split into `values_of(held)` and the write, one spelling). The keyword
planners take a library: the test class is `test_file_journal.FilesCase` (a home and a library of its own, as
test_the_two_writers_compared_on_copies). A clip's ops are its `fields.CLIP_WRITES`; an op an item does not hold
(a last keyword off of a photo with none) is skipped as `metadata_check` skips it (`fields.reads_same`).

## 3. One suite for every backend, deterministic under replay

**Decision 4: the parametrization point is one table, `ANSWERS`, at the top of the suite, and the backend is observed,
never chosen.** The suite always calls `Fields`; what answers is whatever `Fields` routes to. `ANSWERS` states who
answers each (format, call) today: `("jpeg", "read"): EXIFTOOL`, `("jpeg", "patch"): OWN`, `("jpeg", "rewrite"):
EXIFTOOL`, `("png" | "tiff", "read" | "write"): EXIFTOOL`, `("clip", "read"): EXIFTOOL`, `("clip", "write"): OWN`. Every
call of S1-S3 counts `ExifToolSession.execute` (exiftool_session.py :408, every command, a replayed hit too) while it
runs (the oracle's commands outside the window) and asserts: OWN, 0 commands; EXIFTOOL, at least one. A stage that moves
a format flips its line in the merge that moves it; the suite then fails if the stage forgot a caller (commands seen) and
if anything falls back to ExifTool (Q1: never a fallback), and S4 makes the refusals of that format its cases. Reason:
one suite, no copy per backend, no switch the product reads, and the stage's "done" is visible in one line. The
expectations need no backend: every one is the oracle's answer, or a sentence the item names.

**Determinism** (decision 5):
- The oracle's whole read is not kept today: `field_values.WHOLE_READ` (:460, `-all:all`) answers `File:System:Directory`
  and the file's dates, and the keeper never keeps an answer naming a folder or a file's dates (exiftool_replay.py :107,
  :471). Probed 2026-11-27 on a synthetic JPEG in a scratch cache: record and replay both "not kept: its answer names a
  folder", ExifTool started on replay; with `--System:all` added: kept, then a hit, no start. T1a adds it (section 6;
  finding in docs/findings_pending/own-metadata-t1-design.md). `metadata_check` sets the File group aside already
  (`SET_ASIDE`), so nothing it compares changes. `READER_READ` is kept as it is.
- The mint: the suite's mint op takes its id from `exiftool_replay.seeded_mint("conformance:" + item)()` (:499) in every
  mode, not `identity.mint_document_id`, so a `--real-exiftool` run records answers the next replayed run hits. The
  mint's own randomness is `test_photo_identity`'s (STAYS_REAL); M's gate is the write of an id, which this is.
- Every item's bytes are a function of its recipe and the writers' identity (corpus key); the copies' paths are new each
  run and the keeper marks paths, so a replayed run answers every command but the never-kept kinds: a command whose
  status is not 0 (S3.2, S3.3's bad file, S3.4's held file, which the keeper cannot read to key) and the recipes that read
  a file by name (`-ThumbnailImage<=`, made once per generation). Those start one ExifTool a test method at most: S3's
  cases run in one `Fields` a method.
- The suite is not in `exiftool_replay.STAYS_REAL`: its claim is TagPup's against ExifTool's answer, an input keyed by
  ExifTool's version.

## 4. The granular tests lifted, and what stays (decision 6)

A test goes when its claim is "this layout reads as these fields" or "this write changes its targets only, and twice
nothing"; it stays for a decision inside a segment, a refusal's reason, a damage loop. Each successor below must fail on
the break the old test failed on, tried on a copy of the code (T1c's gate).

| File (tests) | Go: test -> successor case | Stay |
|---|---|---|
| test_container_layout (26) | `AClip.test_an_mp4_tagged_by_exiftool_has_its_xmp_box_after_moov_...` -> S1.1/S2.1 `mp4`; `test_an_xmp_box_appended_after_mdat_or_after_a_last_moov_is_found` -> S2.1/S2.2 `mp4_bare`; `test_a_mov_tagged_by_exiftool_has_its_xmp_inside_moov` -> S1.1/S2.1 `mov`; `AJpeg.test_a_jpeg_with_no_xmp` -> S2.1 `camera` (path R); `test_two_xmp_segments_name_no_one_xmp` -> S2.1 `two_xmp` (path R); `test_extended_xmp_is_its_own_kind` -> S1.1/S2.1 `extended_xmp`; `test_a_jpeg_cut_before_its_picture` -> S3.1 `no_picture` (7) | 19: a skip box, 64-bit and size-0 boxes, the > 4 GB sparse clip, moof, the 3 header-version tests, the 7 clip damage walks, the slack of each segment, stray zeros after 8BIM, the unfollowable EXIF's slack, garbage and fill bytes |
| test_xmp_packet_edits_what_it_owns (23) | `test_every_owned_property_reads_back_as_written` -> S2.1 `tagged` read_back; `test_all_nine_set_at_the_packets_length_and_every_other_byte_kept` -> S2.1 every op on `tagged`; `test_writing_what_it_holds_changes_no_byte` -> S2.2; `test_an_empty_caption` -> S2.1 caption cleared; `test_a_bag_keeps_the_callers_order_and_a_keyword_added_then_removed_gives_the_bytes_back` -> S2.1 + S2.3 (bytes back); `AClipsPacket...test_an_attribute_reads_as_its_element_does`, `test_a_date_changed_in_the_attribute_and_every_other_byte_kept`, `test_a_premiere_packet_of_200_kb_is_read_and_a_keyword_paid_for_out_of_its_padding` -> S1.1/S2.1 on a clip laid with the file's `PREMIERE` packet (moved to corpus as `premiere_mp4`; live: the Adobe clips of C2) (8) | 15: padding, the reserve, namespaces declared, no wrapper growth, escaping and what XML cannot carry, refusals, attribute bytes, self-closing descriptions, the 3 damage loops |
| test_exif_block_edits_what_it_owns (15) | `test_every_owned_field_reads_in_either_byte_order` -> S1.1 `tagged`, `tagged_ii`; `test_all_nine_set_in_place_keep_every_other_byte_and_go_back` -> S2.1 + S2.3; `test_writing_what_it_holds_changes_no_byte` -> S2.2; `test_a_cleared_field_loses_its_entry_and_set_again_takes_back_its_place` -> S2.1 caption cleared + S2.3; `test_a_maker_note_that_keeps_its_pointers_inside_it_leaves_the_slack_free` -> S2.1 `maker_note` (5) | 10: slack and growth, type-2 XP tags, where a new tag is laid, Orientation's range, refusals, the IFD loop, the 2 damage loops |
| test_iptc_block_edits_what_it_owns (16) | `test_every_owned_dataset_reads_in_its_character_set` -> S1.1 `tagged`, `iptc_utf8`; `test_a_keyword_added_changes_only_the_iptc_block_...` -> S2.1 last keyword off on `iptc_alone` (non-target segments); `test_writing_what_it_holds_changes_no_byte` -> S2.2; `test_a_dataset_held_is_rewritten_in_its_place_and_a_cleared_one_goes` -> S2.1 caption cleared (4) | 12: the charset rule (3), the dummy resource, version datasets, zero bytes dropped, values cut, a segment given a block, refusals (2), the lone surrogate, the hostile-bytes loop |
| test_segment_edits_choose_what_to_write (25) | `AKeywordOrCaptionSave`: keyword added XMP alone + stale copies -> S2.1 keyword (`as_patched`); keywords in IPTC alone -> S2.1 `iptc_alone`; the last keyword off every copy -> S2.1; caption to XMP alone, cleared to every copy -> S2.1; no XMP segment or a packet refused -> S2.1 `camera`, `nul_xmp` (path); a kind twice -> S2.1 `two_exif`, `two_app13` (path); a save made twice -> S2.2; `AClipsSave`: the bare packet grown, Adobe's edited where it stands, a clip with no packet -> S2.1 `mp4_bare`, `premiere_mp4`, `mp4`; `ADateOrARotation`: a date in every copy, a rotation -> S2.1; `TheEditedHeader` -> S2.1 non-target segments (13) | 12: the map covers every writer (2), the no-patch refusals (copy cannot take it, does not fit, two values, header length), a cleared clip value (#2433), a clip's refusals, an XMP date's zone, an EXIF date with a zone, a turn beside an unreadable packet, no Orientation tag |
| test_metadata_extraction (70) | `TestBatchReadSurvivesOneBadFile`: the good files keep their metadata, the bad one is the only one blanked, every file keeps its stats, a clean batch reads in one call -> S3.3 (4) | 66: TagPup's decisions over a record; ExifTool not starting (#1495, until X) and a stat that fails stay |
| test_a_photo_written_by_its_own_editors (scenario) | `test_a_turn_reads_as_exiftools_write_of_it_reads` -> S2.1 `thumb_turn`; the IFD0-packet pair -> S2.1 `ifd0_xmp` (path R, reads as asked; #3108 cited by the item) (3) | the rest (crashes, undo, chunks, the journal) |

**44 tests go** (7 + 8 + 5 + 4 + 13 + 4 + 3), not the plan's "about 45" of the six files alone: the six give 41. Lines:
those tests and the helpers only they use are about 560 lines (counted from their line spans, 2026-11-27); about 200 more
move to `corpus.py` (net 0: the builders lifted with their callers). Added: `corpus.py` about +280 (recipes and their
docstrings), the suite about +450, the maps and charter about +10, T1a's guard and determinism tests about +40: about
**+780 added, -560 deleted, net +220**, not the plan's -100. Why it still pays: the suite is the gate of ten later stages,
each of which deletes its format's ExifTool arm against it, and 44 test methods become about 12. The extra lifts that
would close the gap are in section 8 (Q3).

## 5. Scenario F12 and the maps (decision 8)

- `tests/owners.py` SCENARIOS (:38), after F11 (:71): `"F12": ("files", "A photo's or a clip's fields read and written as
  the oracle reads them: every corpus item read, each operation written, read back, written again and undone, and a file
  cut short, not a photo, held open, read-only or on a share that does not answer.")`; `_SCENARIO_FILES["F12"] =
  "test_a_photos_fields_read_and_written"`; `_SIZES` "subprocess" (it reaches `ExifToolSession` through `Fields` and the
  oracle; tests/test_every_test_file_has_its_size.py derives the same and prints the line).
- `.claude/agents/tagpup-owner-files.md` :94: `Scenario ids: F1 ... F11 F12.`, in the same commit
  (`test_a_charter_lists_the_scenarios_the_map_gives_its_owner`).
- `tests/tiers.py`: not named, so **fast** (decision 9; section 7). `test_the_tier_file_and_the_map_agree_...` checks only
  that a SCENARIO-tier file is kind scenario; a fast-tier scenario file is allowed (F1's test_tag_a_photo_read_it_back_and_undo).
- The deleted tests' files keep their owners and sizes; `tools/run_tests.py --accept-fewer` records their new counts.
  Findings cited only by a deleted test: none (#3108 is cited also by test_a_file_holds_what_exiftool_kept :62, #2433
  stays); the suite cites #3108, #2349 and #46 in its items all the same (`tests/findings_cited.txt` ratchet).
- `docs/ARCHITECTURE.md` and docs/INVARIANTS.md: unchanged (neither names the granular tests; grep before T1c).

## 6. The cut: three branches, in order, each leaving the trunk green (decision 7)

**T1a `own-metadata-t1a-corpus`** (about +360 / -230, of it about 200 moved; Files with apps for corpus.py). Files:
`tests/corpus.py` (the recipes of section 1, `_written` taking raw `params` for `-ExifByteOrder` and
`-ThumbnailImage<=`, MAKERS + 2 fixtures); the builders lifted with every caller repointed in the same commit
(`tests/test_a_photo_written_by_its_own_editors.py` `photo_with_xmp_in_ifd0` + `XMP_IN_IFD0`,
`tests/test_a_clip_dated_through_the_journal.py` `clip_of` + `_box`/`_boxes`, `tests/test_container_layout.py` `segment`
/ `xmp_segment` / `iptc_segment` / `exif_segment`, `tests/test_exif_block_edits_what_it_owns.py` `block` / `note_ifd` /
`layout_tiff`, `tests/test_the_two_writers_compared_on_copies.py`'s two-APP13 lay, `tests/test_a_file_holds_what_exiftool_kept.py`'s NUL
segment, `tests/test_xmp_packet_edits_what_it_owns.py` `PREMIERE` / `premiere`, `tests/baked_files.py` `values_of`); two
product edits, one commit each: `tagpup/files/field_values.py` `WHOLE_READ` gains `--System:all` (a test in
`tests/test_exiftool_answers_kept_for_tests.py`: the oracle's whole read of a copy kept and answered from the cache,
failing on the old code), and `tagpup/services/metadata_check.py` `_outside` / `_written` renamed `outside` / `written`
(their one caller each). `tests/test_a_photo_made_once_is_copied_for_each_test.py` gains "every recipe made twice into
two empty corpus folders holds the same bytes". Gate: each touched file alone, `affected_tests.py`, ruff, `--fast`
once, one `--real-exiftool` run of the touched ExifTool files (the oracle's command changed: CLAUDE.md), the census
recount of the byte orders (`scripts/census_segments.py`, read-only, counts only) named in `tagged_ii`'s docstring.

**T1b `own-metadata-t1b-suite`** (about +470). Files: `tests/test_a_photos_fields_read_and_written.py` (S1-S4, `ANSWERS`,
the ops, the `away` stand-in: `ExifToolSession.execute` raising `exiftool_session.ExifToolTimeout` for a command naming
X and `in_place._Lent.bounded` / `shares.bounded` raising `in_place.Away` / `core.result.Unavailable` for X, so either
backend meets the same share); `tests/owners.py`; `.claude/agents/tagpup-owner-files.md`. Gate: the file alone twice
under replay (the second's misses only the kinds of section 3 named), once under `--real-exiftool`; two mutations on a
copy (`git archive HEAD | tar -x -C <outside>`): `segment_edits.new_header` writing one byte more into a segment it does
not own fails S2.1, `in_place.patch_jpeg` returning `()` with no byte written fails S2.1's targets; ruff; `--fast` once;
the time baseline (section 7).

**T1c `own-metadata-t1c-lift`** (deletion only, about -560: never over budget). Files: the seven test files of section
4, their docstrings' "breaks that fail these" lines re-pointed at F12. Each commit deletes one file's tests and names each
successor case; before it, the break the deleted test's docstring names (or one chosen for it) tried on a copy fails the
suite. Gate: `--fast` once with `--accept-fewer`, tests/test_the_test_map.py, ruff.

## 7. How it fails, and the time

- **A corpus file half-written** (a run killed mid-make): only `<key>-part-making<ext>` holds it, never read; the next
  maker discards it under the item's stripe lock; `evict` removes one older than `STALE` (600 s). A laid item is made the
  same way. A kept item a tagpup change would make otherwise cannot exist (kept recipes call no tagpup code).
- **Two runs at once sharing the corpus** (two worktrees, the main session beside a worker): the run's folder is its own
  (`ENV`); the kept folder is the machine's: the stripe lock, the look again after it, the os.replace. The copies are in
  each test's own home (FilesCase), so two runs never share a copy; replay entries of one key from two processes: the
  later replaces the earlier whole (exiftool_replay `write_entry`, under its stripe lock).
- **The replay cache missing an answer** (cold, trimmed past CAP, a new item's bytes): a miss runs the real ExifTool and
  keeps its answer; with no ExifTool on the machine the suite is skipped (`requires_exiftool`), never judged from a
  missing answer. The never-kept kinds of section 3 always run it: counted, named, one start a method.
- **A real-ExifTool run on another version**: the corpus key and the replay folder both carry ExifTool's identity, so
  every item is made again and every command runs once for real. Before and after are read by the same version, so a
  case fails only where the new version reads a write otherwise, or names a KNOWN tag otherwise (then metadata_check's
  KNOWN and the suite fail alike: one constant to change). That run is CLAUDE.md's "after an ExifTool version change".
- **Interrupted mid-test**: every write is to a copy in the test's home, reaped by `own_home`; nothing of the owner's.
- **The time** (decision 9: the fast tier, because the suite is every later stage's loop): budget 20 s for the file on a
  second replayed run on this machine (estimate: about 200 item x op x side cases at about 40 ms, plus S3's starts; the
  recorded durations put the slowest fast-tier files at 20-43 s). Over it, an item's ops are narrowed to the segment it
  exists for (never the plain items' `all`), not the tier changed. **Baseline procedure** (T1b's report): on the trunk at
  the branch point, `tools/run_tests.py --fast` twice, the second's wall time and `--slowest 10`; on the branch, the same
  twice; report both second-run walls, the suite's own seconds, and the replay counts the runner sums (hits, misses and
  each "not kept" reason). A first run after a cache miss is not a measurement.

## 8. Not decided here (for the main session; none is the owner's alone)

- **Q1: product edits in a "tests only" stage.** T1a changes `field_values.WHOLE_READ` (the oracle must be replayable,
  or every gate's oracle reads run ExifTool for real: a finding) and renames two `metadata_check` helpers public so the
  suite imports rather than spells the picture/segment comparison and the path probe again. Both revert cleanly. Agree,
  or keep T1 tests-only and have the suite import `_outside` / `_written` as private names?
- **Q2: the tier.** Fast (my reading of "a measured fast-tier time") with a 20 s budget, or the scenario tier with real
  ExifTool's contract tests (tiers.py's own words), keeping the edit loop of J1-C1 faster?
- **Q3: the line budget.** 44 tests and about 560 lines go, not about 800: net +220. More could go to S2.1's bake op:
  `ABakeIsWritten.test_a_structure_reads_back_as_the_text_written` and `test_a_text_with_blanks_at_its_edges_reads_back_as_written`
  (test_a_file_holds_what_exiftool_kept :421-443), but the plan keeps the three scenario files. Accept the net, or lift those?
- **Q4: `ANSWERS` lives in the suite** until J2, whose `_format_of` might expose the product's own (format -> reader).
  Then J2's brief decides whether the suite reads it (one place) or keeps its table as the independent statement a test
  makes of the router. I lean to keeping it in the suite: a test that reads the router's own answer cannot catch it.
- **Q5: `maker_note` is synthetic** (the lifted `note_ifd`), not a captured Canon or Sony note. Maker notes exist only in
  camera files, so section 5's rule would capture one file's note layout; that is J1's or J3a's census work, whose
  item would replace this one. Accept the synthetic item for T1?
- **Q6:** the `outside` side of S2.1 for clips is always Refused (`_clip_written` :690): kept as a case (it is the rule
  that a clip is never written outside the journal) or dropped as restating `clip_refused`'s tests?

## 9. Answers (main session, 2026-11-27)

Q1 agreed: T1a edits the two product files (the `--System:all` oracle fix, #3464, and the public names), the suite never imports a private name. Q2: the fast tier. Q3: lift the two read-back tests in `ABakeIsWritten` too, but only where the successor case fails when the lifted test's break is made (T1c's rule); otherwise keep them; net lines are reported, not targeted. Q4: keep `ANSWERS` in the suite after J2. Q5: a synthetic `maker_note` item is enough; a captured Canon/Sony layout is J1's or J3a's. Q6: keep one outside-side clip case that asserts Refused (it is the real behaviour) and drop the rest.

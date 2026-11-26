# Sweep for #3376: an own reader of a JPEG's metadata (Files owner, 2026-11-24, read-only)

Scope as corrected by the owner: a tool TagPup owns that replaces ALL file-level JPEG reading, not only the owned fields.
Counted this session unless a date says otherwise. No name or path of the libraries appears here.

## 0. Where every JPEG read goes today

One entry: `field_values.read` (tagpup/files/field_values.py:157-233), one batch of 25 (`READ_BATCH`) per `et.get_tags`,
`-G -n` (exiftool_session.py:67), every field asked by its ExifTool name plus `File:MIMEType`; `_held` (:147) keeps the
fields wanted, `records` the whole row. `metadata.batch_read` (tagpup/files/metadata.py:86-141) is one caller of it.
One bypass: `names.read_for_renaming` (tagpup/files/names.py:44-54) calls `et.get_tags` itself, ONE COMMAND PER PHOTO.
`metadata_check._whole` (metadata_check.py:190) calls `execute` itself: the oracle. No other module drives ExifTool
(grep over tagpup/: exiftool_session, field_values, metadata, names, file_changes, file_only, metadata_check,
relink_photos, tagging).

## 1. FIELDS: site | tags asked | owned? | used for what

| # | site | tags | owned? | used for |
|---|---|---|---|---|
| 1 | pre-write plan, `file_changes._write_fields` :517 | the caller's `read`: a save `tagging.SAVE_READ` (:629: TAG_SOURCE_FIELDS + keyword_fields + PRESERVED_NAME) plus the caption/title/date fields it writes; a rotation `fields.ORIENTATION`; a bake `bake.WRITTEN` | owned | `before` of each file; the precondition (`_holds_already` :440) |
| 2 | read-back, `file_changes._read_back` :910 | `row.after`'s fields | owned | `changed` count, `follow_fields` |
| 3 | read whole after a save, `read_whole` :1244 | fields_read + `METADATA_FIELDS` (`also`) | mixed: the index's record | the row's raw_metadata/tags/captions/taken after a save |
| 4 | undo check, `_undoable` :1927 | `row.after` | owned | refuse "no longer holds what the change left" |
| 5 | settle, `_settle_files` :1727 -> `_carry` :965 -> `read_one` :1077/:1224, `_read_whole` :1231 | as 1-3; a patched file first by bytes (`in_place.held` :140, no ExifTool) | owned/mixed | finish a change a crash left |
| 6 | bake plan, `baking.FolderRead` :173 | `bake.WRITTEN` values: XMP-tagpup PhotoModel/FaceModel/Fingerprint/PhotoVector/FacesFound/FaceVectors + `XMP-mwg-rs:RegionInfo` (`-struct`) | owned (TagPup's namespace) | to bake / current / cannot |
| 7 | bake read-back | site 2; the index: `batch_read(bakes=True)` :114 | owned | `bake_reading` |
| 8 | `names.read_for_renaming` :52 | PRESERVED_NAME, XMP:PreservedFileName, CAPTION_FIELDS | owned | Smart Rename's name |
| 9 | relink, `relink_photos._read_files` :41 | DocumentID, PreservedFileName (`MATCHED_BY` :26) | owned | pairing a dead row |
| 10 | just-look, `file_only._write_fields` :103 and `_read_back` :199 | `dates.SHIFTED_FIELDS`, the fields written | owned | time shift of unheld photos |
| 11 | Apply All, `tagging` :596 | `KEYWORD_READ` :624 | owned | who the file already names |
| 12 | the index, `metadata.batch_read` :114 | `METADATA_FIELDS` (fields.py:17-65) + `bake.FIELDS` + MIMEType | mixed | the whole row |
| 13 | `refresh_rows.read_files` :41, `services.photos._read` :241 (page records), `photos` :795 (time-shift records), `inspect` :202 (MCP row-vs-file) | batch_read | mixed | rows, page records |
| 14 | `metadata_check` :268/:316 + `_whole` :190 | READ (:90) + `-j` whole | oracle | the writers' gate; stays ExifTool |

What `METADATA_FIELDS` asks (a bare name brings EVERY group holding it, fields.py:68-79): keywords (IPTC:Keywords,
XMP:Subject, XMP:HierarchicalSubject), caption (IPTC:Caption-Abstract, XMP:Description), title (XMP:Title,
IPTC:ObjectName), dates (EXIF/XMP DateTimeOriginal, EXIF/XMP CreateDate, QuickTime:CreationDate), location (XMP City,
State, Country; IPTC Province-State, Country-PrimaryLocationName), GPS (Composite lat/long, signed decimal under -n),
camera (EXIF Make, Model), lens (EXIF/XMP LensModel, EXIF LensMake, Composite:LensID), XMP:Rating, DocumentID.
NOT asked, so never in a row: EXIF:Orientation (read only by a rotation, site 1), XPKeywords, XPComment,
ImageDescription (written by a save, never read: fields.py:76-78), OffsetTime*, SubSecTime*, exposure, dimensions
(photos.width/height are PIL's, `images.picture_size` :207), maker notes (except what a bare name drags in, below).

## 2. LIVE COUNTS (this session, db.readonly_uri; photo_index fields on a random 3,000 of its photo rows, the other two whole)

Rows / JPEG / TIFF / PNG / video: photo_index 60,270 / 57,315 / 17 / 25 / 2,913; kr-track 2,186 / 2,181 / 0 / 0 / 5;
renton_parkrun 1,309 / 1,308 / 0 / 1 / 0. HEIC 0. Every row read (mtime not null): all.
Columns the index fills from the read, non-null: tags 60,246 / 2,015 / 828; captions 59,511 / 52 / 0; taken 60,196 /
2,186 / 1,305; year all; document_id 1,155 / 2,181 / 1,309; raw_metadata all.

Held per field (photo_index of 3,000 / kr-track of 2,181 / renton of 1,309), and who reads it:
- XMP:Subject 2,993 / 2,015 / 828; IPTC:Keywords 2,880 / 2,015 / 828; both 2,873, XMP only 120, IPTC only 7 (photo_index). Reader: `vocabulary.extract_tags` :243 via `fields.keywords_held` (XMP first). XMP:HierarchicalSubject 150 / 2,014 / 828.
- XMP:Title 2,940 / 51 / 0; IPTC:ObjectName 2,901 / 51 / 0; XMP:Description 20 / 52 / 0 (881 keys present, 20 non-blank); IPTC:Caption-Abstract 442 / 45 / 0. Reader: `fields.extract_captions`, `title_of`.
- EXIF:DateTimeOriginal 2,995 / 2,181 / 1,305; XMP:DateTimeOriginal 2,818 / 52 / 3; EXIF:CreateDate 2,852 / 2,181 / 1,304; XMP:CreateDate 939 / 2,165 / 1,304. Reader: `dates._dates_given` :163 in `DATE_TAKEN_FIELDS` order (EXIF:DTO, bare DTO, XMP:DTO, EXIF:CreateDate, bare CreateDate, XMP:CreateDate).
- EXIF:Make 2,844 / 2,180 / 1,304; EXIF:Model 2,736 / 2,180 / 1,304; EXIF:LensModel 37 / 1,412 / 2; XMP:LensModel 16 / 1,296 / 1; Composite:LensID 43 / 1,412 / 2; EXIF:LensMake 15 / 5 / 0; XMP:Make/Model 17. Reader: `photo_meta.gear` :111 (Image Details, the search's gear index `search_index.GEAR_FIELDS` :242, the camera a Shift Date Taken groups by).
- XMP:DocumentID 57 / 2,137 / 1,309. Reader: `fields.read_document_id`, relink.
- STORED, NO READER FOUND (grep over tagpup/ and web/): XMP:Rating 2,905 / 172 / 4 (and EXIF:Rating 2,911, dragged in by the bare name); Composite:GPSLatitude/Longitude 67 / 1 / 0 (EXIF:GPS* beside them); City/State/Country and the IPTC location pair 0 / 0 / 0 everywhere; QuickTime:CreationDate (clips only). Droppable from a JPEG read: Rating, GPS, location (the owner's call: Rating is held by 97% of photo_index).
- Dragged in by bare names, a reader must know of: MakerNotes:CreateDate 121 / 13 / 0, MakerNotes:Rating 10, MakerNotes:LensModel 4 / 1, APP12:DateTimeOriginal 1. `structured()` (metadata.py:46) puts the LAST answered group under the bare name, so a bare CreateDate may be the maker note's: `dates` reads bare CreateDate fifth, bare DateTimeOriginal second (APP12: 1 row). Low exposure, counted.
- Distinct prefixed keys in a row: 31 / 27 / 20 in all. Nothing else is stored.

Segment census (docs/domains/files.md, "JPEG segment census, 2026-11-03", 3,000 / 2,065 / 1,308): one XMP segment each,
extended XMP 111 / 80 / 5, MPF 100 / 14 / 0, bytes after EOI 10 / 3 / 0, two EXIF 4 / 0 / 0, no EXIF 2 / 0 / 3, maker
notes 1,613 / 1 / 0 (Canon 1,239 moved; Sony 258), IPTC two APP13 0 / 2 / 3, bytes after the last 8BIM 1 / 4 / 6, no
0x0404 1 / 2 / 3, charset 1:90 UTF-8 2,194 / 2,057 / 1,305 and absent 746 / 4 / 0, non-ASCII IPTC 6 / 0 / 0, datasets cut
short 12 / 0 / 0, XMP in IFD0 24 of 10,515 (own-writer-flip.md, 2026-11-16). Not censused: XMP attribute-form or
multi-language values beyond the 0 "target property twice or as attribute", UTF-16 packets, EXIF byte-order splits,
OffsetTime, XP* tags held (XPKeywords 0 in rows because never asked).

## 3. WHAT A READER MUST HANDLE

Precedence TagPup applies itself (not ExifTool's MWG): keywords XMP then IPTC (`KEYWORD_COPIES`), title XMP:Title then
IPTC:ObjectName, caption XMP:Description then IPTC:Caption-Abstract (fields.py:251-257), dates by `DATE_TAKEN_FIELDS`,
DocumentID by `DOCUMENT_ID_KEYS`. ExifTool's part today is only: which group answers a bare name last (`structured`),
-n numbers (Rating, Orientation, LensID a number, GPS signed decimal from ref+DMS: Composite), a date spelled
YYYY:MM:DD HH:MM:SS[.fff][+HH:MM|Z] with XMP's ISO form respelled (segment_edits `_READ_DATE` :55 shows the form),
IPTC decoding by 1:90 (cp1252 without), EXIF XP* as UCS-2 (exif_block `_decoded` :73 already mirrors it),
JSON booleans/numbers (`fields._as_read` :587), MIMEType from magic, ExifTool:Error/Warning keys
(`READ_ERROR_KEYS`), -struct RegionInfo as a dict that `bake.region_text` :229 canonicalises, extended XMP joined by
GUID (HasExtendedXMP; 111 / 80 / 5 files), XMP in IFD0 (24 rows), two EXIF segments (4; ExifTool reads the first).

Already parsed by tagpup/files (reads): `containers.read` (JPEG layout, EXIF IFDs `exif_tiff` :626, maker-note classes,
8BIM resources, IPTC records); `xmp_packet.read` :243 for 9 owned properties (dc:subject/description/title,
lr:hierarchicalSubject, exif:DateTimeOriginal, xmp:CreateDate, tiff:Orientation, xmpMM:DocumentID/PreservedFileName;
refuses attribute form and a multi-language Alt, `_value` :173); `exif_block.read` :133 for 9 fields (ImageDescription,
Orientation, XPComment, XPKeywords, DateTimeOriginal, CreateDate, SubSecTime*); `iptc_block.read` :105 for ObjectName,
Keywords, Caption-Abstract with the charset; `in_place.held` (bytes of a patch); clips' `headers_held` :351 (Media's).
Missing for a read: Make/Model (IFD0 0x010F/0x0110), LensModel/LensMake (ExifIFD 0xA434/0xA433), Rating (0x4746), GPS
IFD, XMP xmp:Rating, aux:Lens, exif:GPS*, tiff:Make/Model, Composite:LensID (maker-note tables: not worth owning),
extended XMP joining, XMP in IFD0, MIME magic, the error/warning words, the bare-name shape of a record, the -struct
dict of mwg-rs regions and the XMP-tagpup Bags (FaceVectors), relaxed forms the editors refuse (attribute-form
properties: census 0; multi-language Alt: not counted).

What the shadow comparison proved about READS: nothing. metadata-check (4d gate, 166 differences on 3,000) compared
ExifTool's read (`field_values.read` and `_whole`) of two copies written by the two writers: ExifTool was the reader on
both sides. The only own-read-vs-ExifTool evidence is tests/test_a_photo_written_by_its_own_editors.py on test files,
and `_edited`'s `followed` read (segment_edits :150) on live writes, which reads a value only to decide whether to write
a copy. An own reader needs its own gate (stage S0 below).

## 4. WHERE IT LIVES

One place: a new tagpup/files/jpeg_read.py (the owned fields' reads of the three editors, plus the camera fields,
one record in the shape field_values.read gives), called ONLY from field_values.read, which stays the one entry for
its 14 callers and dispatches: a JPEG whose every wanted field the reader answers -> own; a clip, TIFF, PNG, a
Refusal, a struct or an unowned field -> ExifTool as today. Callers change nothing. Guard: tests/test_rules_have_one_
owner.py :120 already pins ExifTool writes to field_values; add the read rule (no get_tags outside field_values),
which fails today on names.py:52 (a second reader, one command per photo: fold it into site 8 first, the cheapest
deletion). The independent ExifTool oracle stays in tests (ExifToolSession( in 70 test files, 51 opening one): a test
reading with the real tool to confirm an own read is the oracle a byte-level claim needs; it moves to the periodic
tier only when replay (section 6) is built.

Stages (each merges whole, each measurable):
- S0 (read-only, about 400-600 lines): jpeg_read for the owned fields + Make/Model/Lens*/Rating, and
  metadata-check --reads (reuse its sandbox and sampling; copies per CLAUDE.md though reads are safe): per library
  --sample 3000, own record vs batch_read's on METADATA_FIELDS, differences counted by tag. Gate: 0 unexplained;
  KNOWN grown only from output seen. Measures: ms per photo own vs ExifTool (census 2026-11-03: 23 vs 28 batched).
- S1: field_values.read dispatches for the owned-field sites 1, 2, 4, 5, 9, 10, 11. Tests: the scenario files
  already there, their oracle real ExifTool. Removes the bulk of product starts (80% of 537 in the undo matrix).
- S2: sites 6/7 bake fields (XMP-tagpup Bags, mwg-rs regions to the dict region_text takes) and site 8 folded in.
- S3: the index (sites 12/13): the whole record (bare names, MIME, read_error) from jpeg_read; ExifTool for clips and
  TIFF/PNG. Gate re-run on the three libraries; the measure: a dry re-read of 3,000 rows whose file did not change must
  change 0 of taken/tags/captions/document_id.
- S4: drop Rating/GPS/location from the read if the owner agrees (live: 0 readers; rows keep what they hold).
- S5: retire ExifTool's JPEG rewrite fallback: needs M5 of PLAN_OWN_METADATA (own whole-file rewrite: make a missing
  segment, grow one, keep extended XMP, XMP in IFD0, two segments, bytes after 8BIM/EOI kept) plus the DocumentID
  mint (ensure_document_id :605) and the bake write (RegionInfo struct, XMP-tagpup) in the own writer. Live classes
  it must take or refuse with a sentence: no EXIF 2 / 0 / 3, two EXIF 4, extended XMP 111 / 80 / 5, IFD0 XMP 24 of
  10,515, two APP13 0 / 2 / 3, bytes after 8BIM 1 / 4 / 6, MPF 100 / 14 / 0 (length-stable or refused).
ExifTool leaves the JPEG path entirely when: S0's gate reads 0 unexplained on 3,000 x 3; S3's re-read changes 0 rows;
S5 covers the classes above or refuses them with a sentence the owner accepts; bakes and the mint are own. It stays
installed for clips (2,913 + 5 video rows read by batch_read: Media's), TIFF 17 and PNG 26 (43 rows: or refused, PIL
reading their size), and the tests' oracle.

## 5. RISKS

- One-way: S3 changes what the index records for 60,270 rows at the next sync or re-read; a disagreement is found on
  copies (S0 gate) before any row is written, and the first live run is a dry count, never a sync.
- Data-loss path: a plan read that misses a field (site 1) records a before without it; the own writer's _edited
  followed arm and ExifTool's rewrite then write from that plan. A keyword missed in IPTC is not lost (XMP is written,
  IPTC left stale by design), but a DATE or TITLE misread is overwritten and the undo's before is wrong. So S1 goes
  owned-fields-first with the oracle in every scenario test, never the index first.
- A read that refuses (attribute-form XMP, a type the editor does not read) must fall to ExifTool, never answer holds
  nothing: nothing is a plan that clears a field.
- Must stay ExifTool: metadata_check (both sides), non-JPEG, clips' QuickTime, TIFF/PNG writes, the rewrite until S5.
- Not verified: ExifTool's exact -n spelling of every date and offset form on live files (the gate measures it); how
  many packets hold multi-language Alts or UTF-16 (not censused); the live count of JPEGs holding a bake's RegionInfo
  (0 in the sample because batch_read keeps bakes out of raw_metadata).

## 6. Record/replay at the ExifToolSession boundary (tests only)

(a) Determinism. A read (-G -n -j tags files) is deterministic for the same bytes+args+version except SourceFile (the
path, in every row) and any File: group tag asked (none is: MIMEType is magic, not stat). Stderr carries paths
(unnamed :183 strips them). A write (-o target -m tags) is deterministic in bytes: ExifTool writes no wall-clock
value unless asked (XMPToolkit carries the version: in the key). But the DocumentID the mint writes is uuid.uuid4()
(identity.py:38) in the ARGS, so every mint is a miss and every later read of that file too, until tests seed the
mint. Normalise: each file argument to sha256(bytes); -o's target to a placeholder plus whether it exists (ExifTool
fails when it does: the scratch-in-the-way tests, #2180); the -execute N / {readyN} / postN framing is added AFTER
params (execute :255-264) so it is outside the key; the session's common_args and -config file (the bake namespace,
field_values.session :130) are process-start arguments and belong in the key (content-hashed).
(b) Interception: _DrainingExifTool.execute (exiftool_session.py:247), before cmd is built: on a hit set
_last_stdout/_last_stderr/_last_status, write the recorded target bytes at -o's target, return stdout; a miss runs the
real command and records (stdout, stderr, status, target bytes). One owner: the class every ExifTool goes through
(test_rules_have_one_owner :113-116). But a hit must not START the process: 0.14 s a start is the cost (notes 12-14),
so run() :371 and pyexiftool's auto-start inside execute must be lazy too, the version keyed from the executable's stat
(as tests/corpus.py :82 keys its items), the real -ver asked only on a miss. _landed :568 then runs as today on the
replayed target: picture check, os.replace. scratch_written ordering unchanged. A guard that the product never replays:
the switch reads tagpup.ml.under_test's rule (a run whose __main__ is unittest), plus a test that the flag is absent
outside it.
(c) Not caught: a hidden input outside bytes+args (the target existing; a file open in a player; disk full; a share
away: those tests use fakes and StaysDead already); a changed -config content without a path change (hash it); agree
that a changed arg list is a miss and a TagPup change to the handed bytes is a miss; a stale hit after an ExifTool
upgrade is excluded by the version key, after a pyexiftool upgrade not (key its version too).
(d) Real every time: test_exiftool_session, test_exiftool_encoding, test_exiftool_says_why (framing, draining,
timeouts, CSTR), the scratch-in-the-way and minor-error tests whose claim IS ExifTool's behaviour, metadata-check, and
the periodic uncached tier re-running a sample of hits. Replayable: product reads and writes in the scenario tests (the
undo matrix's 537 starts, the bake files' 253 + 158, tag_a_photo's 161: 1,109 starts, about 157 file-seconds).
(e) Share: of those four files, near all once the mint is seeded and the chain is deterministic (bake vectors are
CPU floats of a fixed picture: deterministic on one machine, a miss across machines); without the seed, every file an
index touched misses on its later reads (my estimate: under half). Whole-suite start count: not measured (the notes
count four files; the scenario tier is 2,507 file-seconds). Order matters: S1-S3 remove the product reads (80% of the
starts) with no cache and no test-only mechanism; replay then pays only for the remaining writes and the oracle, so
build the reader first and size the replay after. A disk cache of whole output files across runs is tens to hundreds
of MB (gitignored). Unsafe only if the key omits a hidden input: a test then passes against a recording the live tool
would not produce; the periodic uncached tier is the catch.

## Findings for docs/findings.md
| ? | low | tagpup/files/names.py:52 | read_for_renaming reads with its own et.get_tags, one command per photo, beside field_values.read (the one batch reader): a second reader and the per-item command checklist item 9 forbids. live: every Smart Rename and preserve_names run. Fold into field_values.read before S1; the one-owner guard then pins it. | confirmed |
| ? | low | tagpup/files/metadata.py:46 structured + core/fields.py:28-31 | the bare CreateDate / DateTimeOriginal a row keeps is whichever group ExifTool answered LAST; MakerNotes:CreateDate is in 121 of 3,000 photo_index rows, APP12:DateTimeOriginal in 1: an own reader that does not read maker notes changes those bare values; dates reads bare DTO second and bare CreateDate fifth, so taken changes only where EXIF:DTO is absent (5 of 3,000). live: counted; measure in S0's gate. | likely |

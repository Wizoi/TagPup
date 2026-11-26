# Own metadata library: every read and write of a photo's fields goes through one interface, proved by one test corpus
Live count (counted read-only 2026-11-26, `db.readonly_uri`): photo_index 60,355 rows: 57,392 JPEG (.jpg 57,373, .jpeg 19), 2,921 clips (.mp4 1,745, .mov 1,173, .m4v 3), 25 PNG, 17 TIFF; kr-track 2,186: 2,181 JPEG, 5 clips; renton_parkrun 1,309: 1,308 JPEG, 1 PNG; mu_dance 1,284: 1,280 JPEG, 4 clips. Unread rows 0 in all four. JPEG 62,161 of 65,134 rows (95.4%); clips 2,930; TIFF/PNG 43. Field and segment counts: docs/work/3376-sweep.md sections 2 and "Segment census" (2026-11-24, three libraries; mu_dance not censused).
Domain: Files
Domains: Files (the library), Media (clip reads, stage L7 only), Data (the journal's patch record and the index row shape, stages L4-L6); integration reviews the seams

Work row #3376. Inputs: docs/work/3376-sweep.md (the Files owner's sweep), docs/work/3376.md, docs/PLAN_OWN_METADATA.md (M1-M7),
docs/plans/own-writer-flip.md (4d), DECISIONS 2026-11-03 (TagPup owns its JPEG reads and writes, ExifTool retires by stages).
Owner's requirement (2026-11-26): if ExifTool is replaced, the replacement CENTRALIZES all tag reading and writing (one
library, one interface, one access path) AND the tests (one corpus, one oracle, one conformance suite).

## 0. Where it stands (counted by grep on trunk 5261f0a4)

**There is already one read entry and one write entry, but not one interface.** `field_values.read` / `read_one` and
`field_values.write` / `written_once_more` are the only ExifTool reads and writes (guards in
`tests/test_rules_have_one_owner.py`: `get_tags` and `set_tags` outside field_values fail; #3377 folded Smart Rename in).
What is not centralized:

- **The session leaks.** Every caller opens ExifTool itself and threads `et` through: `ExifToolSession(` constructed in 7
  places outside field_values (metadata.py :113 and :257, names.py :51, file_changes.py :1930, file_only.py :92,
  relink_photos.py :40, tagging.py :284 and :597, metadata_check.py :352), `field_values.session(` opened in 8 more
  (bake_after_index :149, convert :344/:559/:659, file_changes :463/:1742/:2011, metadata_check :351). `exiftool_path` is
  spelled 213 times in 28 files, a parameter of 82 functions. A caller knows it is talking to ExifTool: `-struct` params
  (metadata.BAKE_PARAMS), `StaysDead`, `ExifToolNotRunning`, `MIME_TYPE`, `READ_ERROR_KEYS`, ExifTool's record shape.
- **Two record shapes.** `field_values.read` answers `{field: [texts]}`; the index wants ExifTool's whole record made into
  a row (`metadata.MetadataExtractor.batch_read` -> `structured`, bare names, `read_error`). Callers of the whole shape:
  `metadata.batch_read` (tagpup_cli :547, refresh_rows :41, photos :241 and :795, inspect :202) and
  `file_changes.read_whole` (:1247; convert :344, :659).
- **The writers** (all reach `field_values.write`): `file_changes._carry` :1049 (journaled: every save, bulk edit, undo,
  bake, rotation, rename's PreservedFileName), `file_only` :167 (just-look time shift), `metadata_check` :184 (sandbox
  copies), `field_values.ensure_document_id` :605 via `metadata.IdentityWriter.give` (the index's mint, unjournaled).
  `file_changes.write_fields` has 18 calls in 7 modules (bake_after_index, convert, face_assignment, file_changes,
  file_only, photos, tagging): it stays the one journaled writer and is not touched by this plan.
- **Inside the write there are already two engines**: the own writer (`segment_edits.new_header` over `xmp_packet`,
  `exif_block`, `iptc_block`, laid by `in_place.patch_jpeg`) for a journaled JPEG write that fits, and ExifTool's rewrite
  (`-o` beside the photo, the picture check `_landed`) for everything else. Clips are never ExifTool's to write
  (`_clip_written`, `in_place.patch_clip`).
- **Inside the read there is already a second engine**: a clip's QuickTime header dates are TagPup's own walk
  (`_read_headers` -> `in_place.headers_held`), put over ExifTool's answer. Every other field of every format is ExifTool's.
- **Own byte readers with no guard**: `containers.read(` is called by field_values._picture_at :540, in_place :233 and
  :368, metadata_check :172, segment_census :84. `metadata_check._whole` (:195, :197) sends `whole_et.execute(...)` itself,
  outside field_values: the one ExifTool command no guard sees (a read: the write rule only catches `-TAG=`).
- **Tests**: `ExifToolSession(` in 52 test files, `fake_exiftool` (a stand-in at the ExifToolSession boundary that holds
  a table of fields, no bytes, and turns `PATCH_IN_PLACE` off) in 26, `corpus` in 24, `field_values.session(` in 9. The
  format tests are granular, one file per module: test_container_layout (26 tests), test_xmp_packet_edits_what_it_owns
  (23), test_exif_block_edits_what_it_owns (15), test_iptc_block_edits_what_it_owns (16),
  test_segment_edits_choose_what_to_write (25), test_metadata_extraction (70), test_a_photo_written_by_its_own_editors
  (16), test_the_two_writers_compared_on_copies (20), test_a_file_holds_what_exiftool_kept (28). Record/replay
  (#3375, `tests/exiftool_replay.py`, keyed at `_DrainingExifTool.execute`) already answers ExifTool from kept answers;
  the suite is 297 s with it.

So the "library" exists in pieces. This plan makes it one thing with one face before any reader is replaced, so that
replacing ExifTool for a format is a change behind the face, proved by a suite written against the face.

## 1. Mechanisms: the ONE place each lives

| Mechanism | The ONE place (module, function) | Current owners (all of them today) | Guard that fails on a second |
|---|---|---|---|
| Open the library for a run (which engine, lazy ExifTool start, deadline, dead-after-timeout) | `tagpup/files/field_values.py` `opened(exiftool_path)` -> `Fields` (new, L0) | 7 `ExifToolSession(` + 8 `field_values.session(` in services; `StaysDead` wrapped by callers | new LINE_RULE "ExifToolSession constructed outside field_values" (owner `field_values.py`; `exiftool_session.py` keeps its class) |
| Read named fields of many files (`{key: {field: [texts]}}` or Unreadable / NotAnswered) | `Fields.read` (today `field_values.read`) | field_values.read; 22 call sites | existing rule "an ExifTool read outside field_values" (`.get_tags(`) |
| Read a file's whole record as the index keeps it (row shape: tags, captions, taken, document_id, raw_metadata, read_error, baked) | `Fields.whole` (new, L0; absorbs `MetadataExtractor.batch_read` and `file_changes.read_whole`) | metadata.MetadataExtractor, file_changes.read_whole, metadata.structured | new rule: `MetadataExtractor(` / `structured(` named nowhere (deleted, L0) |
| Which engine answers a file and field (own or ExifTool), and the fall-back on a refusal | `field_values._engine_for(path, wanted)` (new, L2) | implicit today: `_read_headers` for clip header dates; `PATCH_IN_PLACE` + `new_header` refusal for writes | conformance suite (`tests/test_a_photos_fields_read_and_written.py`, new T1): every engine in `field_values.ENGINES` answers every corpus item alike or refuses it |
| Write fields into a file (patch in place, else rewrite; picture check; clears; minor errors) | `Fields.write` (today `field_values.write` / `written_once_more`) | field_values.write | existing rules: `set_tags`, `-TAG=` execute, `patch_jpeg(`/`patch_clip(` outside field_values; `field_values.write(` outside file_changes (file_only, metadata_check exempt) |
| Mint a photo's identity | `Fields.identity(path, record)` (today `ensure_document_id`, called by `IdentityWriter`) | field_values.ensure_document_id, metadata.IdentityWriter | existing (it calls `write`); `IdentityWriter` folds into the index's `Fields` (L0) |
| Map a field name to where it lives in a JPEG, both ways (read and edit) | `tagpup/files/segment_edits.py` (`new_header` today; `held` new, L1) | segment_edits (write side); the read side is ExifTool's | existing `tests/test_the_block_editors_have_one_caller.py` (only segment_edits imports xmp_packet, exif_block, iptc_block): covers the read side too |
| JPEG container: segments, picture offset, EXIF IFDs, maker notes, 8BIM, IPTC records | `tagpup/files/containers.py` `read`, `exif_tiff`, `photoshop_resources`, `iptc_records` | containers; called from field_values, in_place (2), metadata_check, segment_census | extend test_the_block_editors_have_one_caller: `containers.read(` only from `in_place` (the bounded open) and `segment_edits` (L1 moves the other three) |
| PNG / TIFF / HEIC containers | ExifTool, behind `Fields` (stays: 43 rows, HEIC 0) | ExifTool | conformance suite: own engine refuses them |
| Video boxes (moov, udta, uuid XMP, Xtra, mvhd/tkhd/mdhd dates) | `containers.Video` (layout), `in_place.headers_held` / `packet_held` (reads), `clip_patch` (edits) | Media, beside Files | existing patch_clip rule; L7 adds clip reads to the engine table |
| XMP packet: owned properties read and edited as text | `tagpup/files/xmp_packet.py` `read`, `edit` | xmp_packet (9 properties); ExifTool for the rest | block-editors guard |
| EXIF block: owned entries read and edited in place | `tagpup/files/exif_block.py` `read`, `edit` | exif_block (9 fields); ExifTool for camera and lens | block-editors guard |
| IPTC: 2:05, 2:25, 2:120 and 1:90 charset | `tagpup/files/iptc_block.py` `read`, `edit` | iptc_block | block-editors guard |
| Windows tags: JPEG XPKeywords/XPComment (UCS-2), a clip's Xtra "Windows Tags" | `exif_block` (`_decoded`, JPEG), `clip_patch` + `containers.Video.xtra` (clip) | same | block-editors guard; patch_clip rule |
| Field precedence (keywords XMP then IPTC, title, caption, DATE_TAKEN_FIELDS, DocumentID keys) | `tagpup/core/fields.py`, `tagpup/core/dates.py` (unchanged: TagPup's, not an engine's) | core | `tests/test_keyword_fields.py`, `test_vocabulary.py` (unchanged) |
| Batching (many files a command; a refused batch read one at a time) | `Fields.read` (ExifTool engine: `READ_BATCH` 200) | field_values.read | conformance suite case "a batch with one bad file" |
| Deadline and pipes | ExifTool engine: `exiftool_session._DrainingExifTool` (both pipes drained, a deadline a command); own engine: `in_place._Lent.bounded` / `shares.bounded` (a look at a share bounded) | exiftool_session; in_place; shares | existing pyexiftool rule; `tests/test_photo_looks_have_one_owner.py` |
| Record/replay of ExifTool for tests | `tests/exiftool_replay.py` at `_DrainingExifTool.execute` | same | `tests/test_exiftool_answers_kept_for_tests.py` (the product never replays) |
| The independent ExifTool read used as an oracle (`-j` whole, `-struct`) | `field_values.oracle(paths)` (new, L0; today `metadata_check._whole`'s `execute`) | metadata_check | new LINE_RULE "an ExifTool command outside field_values" (`\.execute\(` on a session) |
| Shadow comparison on copies (writers today, readers from L1) | `tagpup/services/metadata_check.py` (`metadata-check`, `--reads` new in L1) | metadata_check | existing `tests/test_the_two_writers_compared_on_copies.py` |

Concepts this removes when L0 lands: `MetadataExtractor`, `IdentityWriter`, `metadata.structured` as a caller-visible
step, `field_values.session` and `read_one` (a `Fields` method each), `StaysDead` wrapped by hand, `et` as a parameter of
services, `BAKE_PARAMS` / `-struct` named outside field_values. Concepts it adds: `Fields`, `opened`, `whole`, `oracle`.

## 2. The public interface

```python
# tagpup/files/field_values.py -- the one face. Nothing else in tagpup/ reads or writes a photo's fields.

def opened(exiftool_path, *, namespace=True, timeout=exiftool_session.DEFAULT_TIMEOUT) -> ContextManager[Fields]:
    """The library for one run. ExifTool starts only when an engine asks for it (a file the own engine refuses, a write
    it cannot patch); after one command times out every later command answers the same NotAnswered at once (StaysDead,
    inside). `namespace`: knows TagPup's bake namespace (today's session(); every write opens one)."""

class Fields:
    def read(self, paths, wanted, *, stop=None) -> dict[key, dict[field, list[str]] | Unreadable]
        """The fields `wanted` of each file, by paths.key. A struct field (bake regions) answered as its one canonical
        text (bake.region_text). NotAnswered: the share did not answer; every file after it unread."""
    def read_one(self, path, wanted) -> dict[field, list[str]]                    # raises Unreadable
    def whole(self, paths, *, people=None, bakes=False) -> list[Record]
        """Each file as the index records it, in order: stamp before the read, tags, people, captions, document_id,
        raw_metadata (the row's shape, bare names included), read_error, unread; `bakes`: record["baked"]."""
    def write(self, path, values, *, saving=None, scratch_written=None, again=None) -> tuple[field, ...]
        """`values` into the file (today's written_once_more when `again` is given). Returns the fields left stale."""
    def identity(self, record, *, decoded) -> str | None                           # today's IdentityWriter.give
    def left_as_they_were(self, path, values) -> tuple[field, ...]                 # unchanged rule, now a method

def oracle(exiftool_path, paths, *, struct=False) -> list[dict]
    """ExifTool's whole -j read, never an engine's answer: metadata_check's both sides and the conformance suite's
    oracle. The one ExifTool command outside Fields."""

# unchanged module-level names callers keep: Unreadable, NotAnswered, PictureChanged, CHANGED_TWICE, writes(path),
# clip_refused, clip_date_refused, packet_held, packet_given (Convert's), PATCH_IN_PLACE.
```

**A caller never sees**: ExifToolSession, `et`, pyexiftool exceptions, `-struct` / `-G -n` / `-m` / `-o`, `File:MIMEType`,
`READ_ERROR_KEYS`, which engine answered, a container layout, a segment, a scratch file's name (file_changes keeps
`exiftool_session.scratch_of` for its scratch rules, #2180: named once, read through field_values), CSTR.
**A caller still gives**: `exiftool_path` to `opened` (threading it out of 82 functions is a separate, optional
deletion-only cut, not this plan's; see open question 9).

Engines (private): `_ExifToolEngine` (today's read and rewrite), `_OwnJpegEngine` (L1-L6: `segment_edits.held` /
`new_header` over `in_place`), `_OwnClipEngine` (today's `_read_headers` and `_clip_written`; L7 widens its reads).
`ENGINES` lists them for the conformance suite. An engine answers a file whole or refuses it whole: never half a record.

## 3. What stays ExifTool, and why

| What | Live count | Why it stays |
|---|---|---|
| PNG and TIFF reads and writes | 43 rows (25 + 1 PNG, 17 TIFF) | Under the 20-a-format threshold for owning a parser each; writes are unchecked today (DECISIONS 2026-11-02). Owner may instead refuse their writes (question 6). |
| HEIC | 0 | No live instance: no branch is written for it (CLAUDE.md). A HEIC added later is ExifTool's. |
| Clips' reads other than the header dates | 2,930 rows | Media's; L7 only if the owner wants ExifTool out of the clip path too (question 7). Clip writes are already own. |
| A JPEG the own engine refuses (attribute-form Bag, a target property twice, UTF-16 packet, unparsable segment) | counted by the L1 gate; census: attribute form 0, two EXIF 4, IFD0 XMP 24 of 10,515 | A refusal falls to ExifTool, never "holds nothing" (a plan from nothing clears a field: sweep, risks). |
| The JPEG rewrite (a write that does not fit, the mint, bakes, just-look) until L6 | every write not patched; extended XMP 111/80/5 per 3,000/2,065/1,308; MPF 100/14/0 | M5 of PLAN_OWN_METADATA not built. |
| Composite:LensID (maker-note lens tables) | 43 / 1,412 / 2 rows (sweep) | Not worth owning; question 4 (drop, or keep from EXIF LensModel). |
| The oracle | every conformance item, metadata-check both sides | An independent reader is what a byte-level claim needs. Tests and metadata-check only, never a product answer. |

ExifTool stays installed for as long as any row above stays; the plan does not remove the dependency, it removes ExifTool
from the JPEG path (95.4% of rows).

## 4. Stages (each one merge, about 1,200 lines or fewer, trunk whole after each)

Each stage names its gate (what must be true before it merges), its rollback, and what it deletes.

**L0. One face, no behaviour change** (about 900 lines, most of them call sites shrinking). `field_values.opened` /
`Fields` / `whole` / `identity` / `oracle`; every service opens `Fields` instead of an ExifToolSession; `MetadataExtractor`,
`IdentityWriter`, `file_changes.read_whole` and `field_values.session` deleted with every importer in the same commit
(tests/test_layers.py); the two new LINE_RULEs. ExifTool still answers everything. Gate: the full suite green under
replay AND one `--real-exiftool` run (it changes code that shapes commands: CLAUDE.md, owner 2026-11-24); `fake_exiftool`
moves to stand in for `Fields`' ExifTool engine (one fake, below). Rollback: a revert; nothing on disk changed.
Removes 4 concepts for 4 added (section 1). If over budget: L0a reads (`opened`, `read`, `whole`), L0b writes and mint.

**T1. One corpus and one conformance suite, ExifTool the only engine** (about 700 lines added, granular tests lifted:
net negative, section 5). Written against `Fields` before any own reader exists, so the suite is proved on the engine it
will judge the others by. Gate: green under replay and `--real-exiftool`. Rollback: revert (tests only).

**L1. Own JPEG reader, shadow only** (about 600 lines; sweep stage S0). `segment_edits.held(layout, header, wanted)`
reading through the three block modules, which gain: EXIF Make/Model (IFD0 0x010F/0x0110), LensModel/LensMake
(0xA434/0xA433), Rating (0x4746); XMP xmp:Rating, aux:Lens, tiff:Make/Model, extended XMP joined by GUID, XMP in IFD0;
MIME by magic. `metadata-check --reads`: on copies in a sandbox home, `--sample 3000` per library (all four), own record
against `oracle`'s on METADATA_FIELDS + bake fields, differences counted by tag; KNOWN grown only from output seen.
`containers.read`'s other callers move behind in_place/segment_edits. Gate: 0 unexplained differences on 3,000 x 4;
the conformance suite passes with `_OwnJpegEngine` in ENGINES. No product read changes. Rollback: revert; nothing on disk.
Measures (told the owner only as counted): ms a photo, own against ExifTool batched (2026-11-03: 23 vs 28).

**L2. Own reads for the owned fields** (about 400 lines). `_engine_for` sends a JPEG read whose every wanted field the
own engine answers to it: the pre-write plan, the read-back, the undo check, the settle, Smart Rename, relink, just-look,
Apply All (sweep sites 1, 2, 4, 5, 8, 9, 10, 11). A refusal falls to ExifTool in the same call. Gate: the L1 gate green
on trunk; the scenario files of F1, F3, F8 and D2/D3 green with the oracle on (T1). Rollback: one constant,
`OWN_READS = False`, for one release, then deleted (it is a flag with no second instance once the gate holds: the merge
after says `dry run 0 left` style "0 refusals unexplained"). Deletes: the clip header special case
`_read_headers` becomes `_OwnClipEngine`'s answer for those fields (one dispatch, not two).

**L3. Bake fields** (about 500 lines; sweep S2). XMP-tagpup values and `mwg-rs:RegionInfo` read to the structure
`bake.region_text` canonicalises; FaceVectors from extended XMP. Gate: conformance items for a bake of 0, 1 and 40
faces (the 40 forces extended XMP); L1's `--reads` with `bakes`; the live count of baked JPEGs counted before the brief
(not counted for this plan: batch_read keeps bakes out of raw_metadata). Rollback: the bake fields leave `_OwnJpegEngine`'s
answered set (ExifTool reads them again).

**L4. The index's whole read** (about 600 lines; sweep S3). **ONE-WAY, the owner's** (question 3): what 65,134 rows record
at their next re-read. `Fields.whole` builds the row from the own record: the bare-name shape, read_error, MIME.
Gate: a dry re-read of 3,000 unchanged files per library changes 0 of taken / tags / captions / document_id / year,
and raw_metadata differs only in tags the owner accepted (MakerNotes:CreateDate under a bare name: 121 of 3,000 in
photo_index; APP12: 1). Never first run as a sync: a dry count, then the owner's yes. Rollback: `whole` back to ExifTool;
rows already re-read keep the own shape (they read the same by the gate), so no migration and no backfill.

**L5. Drop unread fields** (deletion only; sweep S4; the owner's). Rating, GPS, location: no reader in tagpup/ or web/
(sweep), Rating held by 97% of photo_index. Rows keep what they hold. Skipped if the owner keeps them.

**L6. Own rewrite** (PLAN_OWN_METADATA M5; two merges, L6a and L6b, about 1,000 lines each). L6a: the mint and the bake
write as own writes (the mint needs a record to be patched: question 5); L6b: a whole-file JPEG rewrite beside the photo
(make a missing segment, grow one, keep extended XMP, IFD0 XMP, two EXIF, bytes after 8BIM/EOI; MPF length-stable or
refused) with the picture check unchanged. Gate: the 4d gate (`metadata-check --sample 3000`) on four libraries, 0 own-side
differences; each live class taken or refused with a sentence the owner accepts. Then `_ExifToolEngine`'s JPEG write
arm is deleted. Rollback: before the deletion, the engine table; after, a revert.

**L7. Clip reads (Media; optional)**. MP4/MOV XMP, QuickTime title, Xtra, keys, from `containers.Video`. Only on the
owner's yes (question 7).

One migration or stamp: none. One-way choices: L4 (row shape), L5 (dropped fields), L6a (journaling the mint), L6b
(files grow about 2 KB at a rewrite: PLAN_OWN_METADATA's open item). Each is asked before its brief.

## 5. Tests: one corpus, one oracle, one conformance suite

**One corpus** (`tests/corpus.py`, extended, not a second module). Items named by recipe, made by the real writer and kept
across runs (as today). New: a **shape recipe** for each live class the census found, made by laying the class's segment
layout over a synthetic picture (PIL noise, `damaged_photos.whole_jpeg`), never an owner photo: extended XMP; MPF; bytes
after EOI; two EXIF; no EXIF; Canon and Sony maker notes moved; IPTC with no 1:90 and with UTF-8; two APP13; bytes after
the last 8BIM; IFD0 XMP; XMP attribute-form text; a bake of 0/1/40 faces; a 64 KB packet; plus clips (moov first and last,
uuid XMP, Xtra). Where a class exists only in owner files, its box layout is captured from one file as the fixture
(CLAUDE.md), every text replaced by fictional values of the same shape and length. Each recipe names the live count that
justifies it (no count, no item). About 25 items.

**One oracle**: ExifTool, through `field_values.oracle`, replayed (`exiftool_replay`) in the normal run and real under
`--real-exiftool`. An item's expected answer is never written by hand: it is what the oracle reads. Hand-written
expectations stay only where TagPup decides (precedence, `fields.as_patched`, a refusal's sentence).

**One conformance suite** (`tests/test_a_photos_fields_read_and_written.py`, owner Files, scenario F12 "a photo's fields
read and written the same by every engine"): for every corpus item x every engine in `ENGINES`:
1. read every METADATA_FIELD and bake field: equals the oracle's, or the engine refuses the file whole;
2. for each operation (add/remove a keyword, caption, title, Date Taken, a shift, a turn, mint, PreservedFileName, a bake,
   an undo of each): write, then the oracle reads the target fields as `fields.as_patched` says, every other field as
   before; picture bytes equal; every non-target segment byte-equal; idempotent (write twice: no changed range);
3. the failure cases once per engine: a truncated copy, a file held open, a share that does not answer (StaysDead/Away
   stand-ins already in the tests), a batch with one bad file.
A new engine is done when it passes, or refuses with a sentence, every item.

**What is lifted and deleted.** Each granular test whose claim is "this layout reads/edits to this" becomes a corpus item
row and is deleted: most of test_container_layout (26), test_xmp_packet_edits_what_it_owns (23),
test_exif_block_edits_what_it_owns (15), test_iptc_block_edits_what_it_owns (16), test_segment_edits_choose_what_to_write
(25) and the reads of test_metadata_extraction (70). Kept: tests of a refusal's reason, of TagPup's own decisions
(XMP-only, the IPTC charset rule, padding), and the scenario files (crash settles, undo matrix, bulk paths). Estimate to
verify in T1's brief: about 100 of the 175 granular tests go. `fake_exiftool` (26 users, a table with no bytes) becomes the
stand-in for `Fields`' engine only where a test needs a hook between read and write (`on_read`, `on_write`); a test that
only needed fields cheaply uses a corpus copy and the own engine, which needs no process.

**Effect on the 297 s suite**: not claimed. Replay already answers most ExifTool commands; what the own engine saves in
the normal run is the misses (new bytes) and process starts. What it saves is real in `--real-exiftool` runs (sweep: 80% of
the 537 starts in the undo matrix are product reads). The conformance suite must stay in the fast tier on the own
engine with the oracle replayed; T1's brief sets a budget from a measured run, baseline first (DEVELOPMENT.md,
"Performance work").

## 6. Failure modes

- **Interrupted part way**: reads write nothing. A patch keeps today's rule (record committed before the first byte,
  settled by bytes: INVARIANTS 44-45); L6b's rewrite keeps `-o` beside the photo and the picture check: a kill leaves the
  photo whole and a scratch file the scratch rules settle (#2180).
- **Two at once**: two runs reading one file: harmless. A read during another process's patch can see half a header:
  the own engine reads the header in one bounded open, and a header that does not parse is a refusal that falls to
  ExifTool, never an empty record. Writes keep the per-file lock and the "ExifTool's new file beside it" rule.
- **Unreadable file**: Unreadable with ExifTool's or the own engine's reason; the index records `read_error` and stamps it
  (not read every run), as today. An own refusal is never Unreadable: it is a fall-back.
- **Real data's shape**: every census class is a corpus item with its live count; a class not censused (multi-language
  Alt, UTF-16 packets, OffsetTime, mu_dance as a whole) is counted by the L1 gate's run before L2's brief.
- **A share that does not answer**: own reads go through the bounded open (`in_place._Lent.bounded` / `shares`): NotAnswered
  for that file and every file after it in the call (one read waits one deadline), as ExifTool's StaysDead does now.
- **What the owner sees**: nothing at L0-L3 (same answers, proved). At L4 the row of a file whose bare CreateDate came
  from a maker note may change at its next re-read (counted first). A refusal is invisible (ExifTool answers). A write
  refused by L6b's rules says a sentence in History, as a refusal does now.

## 7. Risks, owner questions, recommendation

**Risks.** (1) A plan read that misses a field records a wrong `before`, and the write and its undo use it: a date or
title misread is overwritten (sweep, risks). Hence L2 only after a gate on four libraries, owned fields first, the index
last. (2) The corpus agreeing with the code that made it: shapes are laid from census classes and checked against the
oracle, never against the reader under test. (3) A stale replay answer after an ExifTool upgrade: excluded by the version
key; the oracle's real run is the gate. (4) L0 touches 15 session sites and the index; it is mechanical but wide: one
reviewer pass plus the Data owner for `whole`'s row shape. (5) Gains are mostly structural; per-photo speed was measured
close (23 vs 28 ms batched), so the case for L2-L6 is ownership and removing the rewrite, not speed.

**Open questions only the owner can answer.**
1. Is the own reader still wanted after replay brought the suite to 297 s (work row 2026-11-24: "the own reader may not
   be needed")? L0 and T1 stand either way.
2. mu_dance (1,284 rows) is a fourth library: in every gate?
3. L4: may the index's raw_metadata change shape where ExifTool's "last group answers the bare name" differs (maker-note
   CreateDate, 121 of 3,000)? Or keep raw_metadata ExifTool's forever?
4. Composite:LensID (maker-note tables; 43 / 1,412 / 2 rows): drop it, or keep EXIF LensModel as the lens?
5. The mint: journaled (so the own writer can patch it), or stays ExifTool's rewrite?
6. PNG/TIFF (43 rows): keep ExifTool for them, or refuse their writes with a sentence?
7. Clips' reads (2,930 rows): own (Media, L7), or ExifTool's?
8. L5: drop Rating/GPS/location from the read (no reader; Rating in 97% of photo_index)?
9. Thread `exiftool_path` out of 82 functions (a later deletion cut), or leave it?

**Recommendation.** Build **L0** first: one face (`field_values.opened` -> `Fields`), every session and `MetadataExtractor`
behind it, the two new guards. It is the cheapest correct stage, changes no answer, and is what the owner's "one library,
one interface, one access path" means whether or not ExifTool ever leaves. Then **T1** (the corpus and the conformance
suite on ExifTool alone), which turns the existing granular format tests into one suite and gives any later engine its
bar. Ask questions 1-3 before L1; build L1-L3 only on a yes to question 1; L4-L6 each wait for their one-way answer.

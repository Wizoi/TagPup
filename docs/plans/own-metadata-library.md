# Own metadata library: every read and write of a photo's fields goes through one interface, proved by one test corpus
Live count (counted read-only 2026-11-26, `db.readonly_uri`, verified again by the completing design pass the same day): four
libraries, 65,134 rows, unread 0 in all four. photo_index 60,355: 57,392 JPEG (.jpg 57,373, .jpeg 19), 2,921 clips (.mp4
1,745, .mov 1,173, .m4v 3), 25 PNG, 17 TIFF; kr-track 2,186: 2,181 JPEG, 5 clips; renton_parkrun 1,309: 1,308 JPEG, 1 PNG;
mu_dance 1,284: 1,280 JPEG, 4 clips. JPEG 62,161 of 65,134 rows (95.4%); clips 2,930; TIFF/PNG 43. JPEGs holding a bake
(the bake namespace in an APP1 segment, every JPEG header read read-only, 2026-11-26): 62,158 of 62,161 (photo_index 57,389 of 57,392, kr-track 2,181, renton_parkrun 1,308, mu_dance 1,280: all). JPEGs holding a DocumentID (the
row's document_id): photo_index 1,138 of 57,392, the other three all. Field and segment counts: docs/work/3376-sweep.md
section 2 and "Segment census" (2026-11-24, three libraries, photo_index sampled 3,000; mu_dance not censused).
Domain: Files
Domains: Files (the library), Media (clip reads, stage L7 only), Data (the journal's patch record and the index row shape,
stages L4-L6); integration reviews the seams

Work row #3376. Inputs: docs/work/3376-sweep.md (the Files owner's sweep; where it is stale, section 0 here is the
trunk's), docs/work/3376.md, docs/PLAN_OWN_METADATA.md (M1-M7), docs/plans/own-writer-flip.md (4d), DECISIONS 2026-11-03
("TagPup owns its JPEG metadata reads and writes; ExifTool retires by stages").
Owner's requirement (2026-11-26): if ExifTool is replaced, the replacement CENTRALIZES all tag reading and writing (one
library, one interface, one access path) AND the tests (one corpus, one oracle, one conformance suite).

Steady-state writer: none new. Every read and write of a photo's fields goes through `Fields` (section 2); a photo added or
changed afterwards is read by the index and written by `file_changes.write_fields` as today. One-offs: none. No command,
backfill, migration or stamp: a row takes L4's shape at its next ordinary re-read.

## 0. Where it stands (counted by grep and AST on trunk 3badca98, 2026-11-26)

**There is already one read entry and one write entry, but not one interface.** `field_values.read` / `read_one` and
`field_values.write` / `written_once_more` are the only ExifTool reads and writes (guards in
`tests/test_rules_have_one_owner.py`: `.get_tags(` and `.set_tags(` outside field_values fail; #3377 folded Smart Rename
in, so the sweep's "one bypass, names.py:52" is gone: `names.read_for_renaming` now opens its own session and reads through
`field_values.read` with `also=RENAME_FIELDS, records=...`, one batch). `READ_BATCH` is 200 (the sweep's 25 is stale).
What is not centralized:

- **The session leaks.** Callers open ExifTool themselves and thread `et` through: `ExifToolSession(` constructed in 9
  places in 7 modules outside field_values (metadata.py :113 and :257, names.py :51, file_changes.py :1938, file_only.py
  :92, relink_photos.py :40, tagging.py :284 and :597, metadata_check.py :352, the oracle's), and `field_values.session(`
  opened in 8 more in 4 modules (bake_after_index :149, convert :344/:559/:659, file_changes :463/:1750/:2044,
  metadata_check :351). `et` is a parameter of 27 service functions (132 uses in 10 files outside field_values). `exiftool_path` is a parameter
  of 81 functions, 216 uses in 27 files (tagpup/ and tagpup_cli.py; none in scripts/ or tools/). A caller knows it is
  talking to ExifTool: `-struct` params (metadata.BAKE_PARAMS), `StaysDead` wrapped by hand (metadata, bake_after_index,
  file_changes), `ExifToolNotRunning`, `MIME_TYPE`, `READ_ERROR_KEYS` (metadata, file_changes), ExifTool's record shape.
- **Two record shapes.** `field_values.read` answers `{field: [texts]}`; the index wants ExifTool's whole record made into
  a row (`metadata.MetadataExtractor.batch_read` -> `structured`, bare names, `read_error`). Callers of the whole shape:
  `MetadataExtractor.batch_read` (tagpup_cli :547, refresh_rows :41, photos :241 and :795, inspect :202) and
  `file_changes.read_whole` (defined :1240; called by `_read_whole` :1237 and convert :347 and :660). Smart Rename also
  reads records (`also` + `records`), the one caller of that shape outside the two.
- **The writers** (all reach `field_values.write`): `file_changes._written` :1049 under `_carry` (journaled: every save,
  bulk edit, undo, bake, rotation, rename's PreservedFileName), `file_only` :167 (just-look time shift and saves of unheld
  photos), `metadata_check._written` :184 (sandbox copies), `field_values.ensure_document_id` :605 via
  `metadata.IdentityWriter.give` (the index's mint, unjournaled: the charter's named exception, #1355).
  `file_changes.write_fields` has 10 calls in 5 modules (bake_after_index, convert, face_assignment, photos, tagging) and
  `file_only.write_fields` 5 (photos, tagging): both stay the writers they are and are not changed by this plan beyond `et`.
- **Inside the write there are already two engines**: the own writer (`segment_edits.new_header` over `xmp_packet`,
  `exif_block`, `iptc_block`, laid by `in_place.patch_jpeg`) for a journaled JPEG write that fits, and ExifTool's rewrite
  (`-o` beside the photo, the picture check `_landed`) for everything else. Clips are never ExifTool's to write
  (`_clip_written`, `in_place.patch_clip`).
- **Inside the read there is already a second engine**: a clip's QuickTime header dates are TagPup's own walk
  (`_read_headers` -> `in_place.headers_held`), put over ExifTool's answer. Every other field of every format is ExifTool's.
- **Own byte readers with no guard**: `containers.read(` is called 5 times: field_values._picture_at :540, in_place :233
  and :368, metadata_check._outside :172, segment_census :84. `metadata_check._whole` (:190; its commands :195 and :197)
  sends `whole_et.execute(...)` itself, outside field_values: the one ExifTool command no guard sees (a read; the write
  rule only catches `-TAG=`).
- **Tests**: `ExifToolSession(` constructed in 53 test files (70 name it), `fake_exiftool` (a stand-in at the
  ExifToolSession boundary that holds a table of fields, no bytes, and turns `PATCH_IN_PLACE` off) used by 26, `corpus` named
  in 24, `field_values.session(` in 9, `MetadataExtractor` or `IdentityWriter` in 24 (78 lines; `tests/photo_rows.py`
  seeds rows through `MetadataExtractor._structure` and `_stamp`). The format tests are granular, one file per module:
  test_container_layout (26 tests), test_xmp_packet_edits_what_it_owns (23), test_exif_block_edits_what_it_owns (15),
  test_iptc_block_edits_what_it_owns (16), test_segment_edits_choose_what_to_write (25), test_metadata_extraction (70):
  175; beside them the scenario files test_a_photo_written_by_its_own_editors (16), test_the_two_writers_compared_on_copies
  (20), test_a_file_holds_what_exiftool_kept (28). Record/replay (#3375, `tests/exiftool_replay.py`, at
  `_DrainingExifTool.execute`, the process started only at the first command it cannot answer) already answers ExifTool
  from kept answers; the suite is 297 s with it.

So the "library" exists in pieces. This plan makes it one thing with one face before any reader is replaced, so that
replacing ExifTool for a format is a change behind the face, proved by a suite written against the face.

## 1. Mechanisms: the ONE place each lives

| Mechanism | The ONE place (module, function) | Current owners (all of them today) | Guard that fails on a second |
|---|---|---|---|
| Open the library for a run (which engine, ExifTool started at its first command, deadline, dead after a timeout) | `tagpup/files/field_values.py` `opened(exiftool_path)` -> `Fields` (new, L0) | 9 `ExifToolSession(` + 8 `field_values.session(` in services; `StaysDead` wrapped by 3 callers | new LINE_RULE "ExifToolSession constructed outside field_values" (owner `field_values.py`; `exiftool_session.py` exempt for its class and its docstring's example) |
| Read named fields of many files (`{key: {field: [texts]}}` or Unreadable / NotAnswered) | `Fields.read` / `read_one` (today `field_values.read` / `read_one`) | field_values.read: 14 call sites, read_one: 6, in 9 modules | existing rule "an ExifTool read outside field_values" (`.get_tags(`) |
| Read a file's whole record as the index keeps it (row shape: tags, captions, taken, document_id, raw_metadata, read_error, unread, baked) | `Fields.whole` (new, L0; the session and read of `MetadataExtractor.batch_read` and `file_changes.read_whole`); the row built by `metadata`'s module functions (today `_structure`, `_stamp`, `_empty`, `structured`, `raw_metadata`), which `tests/photo_rows.py` keeps calling | MetadataExtractor, file_changes.read_whole, metadata.structured | the session rule: no reader outside field_values can open ExifTool; the deleted names leave nothing behind (`tests/test_layers.py`) |
| Which engine answers a file and field (own or ExifTool), and the fall-back on a refusal | `field_values._engine_for(path, wanted)` (new, L2) | implicit today: `_read_headers` for clip header dates; `PATCH_IN_PLACE` + `new_header` refusal for writes | conformance suite (`tests/test_a_photos_fields_read_and_written.py`, new T1): every engine in `field_values.ENGINES` answers every corpus item alike or refuses it whole |
| Write fields into a file (patch in place, else rewrite; picture check; clears; minor errors) | `Fields.write` (today `field_values.write` / `written_once_more`) | field_values.write | existing rules: `.set_tags(`, `-TAG=` execute, `patch_jpeg(` / `patch_clip(` outside field_values; `field_values.write(` outside file_changes (file_only, metadata_check exempt) |
| Mint a photo's identity | `Fields.identity(record, decoded=...)` (today `ensure_document_id`, called by `IdentityWriter.give`) | field_values.ensure_document_id, metadata.IdentityWriter | `tests/test_photo_identity.py` "the indexer mints and nothing else does", re-keyed from the name `IdentityWriter` to a call of `.identity(` (only `tagpup_cli.index`) |
| Map a field name to where it lives in a JPEG, both ways (read and edit) | `tagpup/files/segment_edits.py` (`new_header` today; `held` new, L1) | segment_edits (write side); the read side is ExifTool's | existing `tests/test_the_block_editors_have_one_caller.py` (only segment_edits imports xmp_packet, exif_block, iptc_block): covers the read side too |
| JPEG container: segments, picture offset, EXIF IFDs, maker notes, 8BIM, IPTC records | `tagpup/files/containers.py` `read`, `exif_tiff`, `photoshop_resources`, `iptc_records` | containers; `read` called from field_values (the picture check), in_place (2), metadata_check (1), segment_census (1) | extend test_the_block_editors_have_one_caller (L1): `containers.read(` only in tagpup/files (in_place, segment_edits, field_values' picture check) and in the two read-only layout tools, each exempt by name with its reason (metadata_check compares segments outside the metadata ones; segment_census counts layouts) |
| PNG / TIFF / HEIC containers | ExifTool, behind `Fields` (stays: 43 rows, HEIC 0) | ExifTool | conformance suite: the own engine refuses them |
| Video boxes (moov, udta, uuid XMP, Xtra, mvhd/tkhd/mdhd dates) | `containers.Video` (layout), `in_place.headers_held` / `packet_held` (reads), `clip_patch` (edits) | Media, beside Files | existing patch_clip rule; L7 adds clip reads to the engine table |
| XMP packet: owned properties read and edited as text | `tagpup/files/xmp_packet.py` `read`, `edit` | xmp_packet (9 properties); ExifTool for the rest | block-editors guard |
| EXIF block: owned entries read and edited in place | `tagpup/files/exif_block.py` `read`, `edit` | exif_block (9 fields); ExifTool for camera and lens | block-editors guard |
| IPTC: 2:05, 2:25, 2:120 and 1:90 charset | `tagpup/files/iptc_block.py` `read`, `edit` | iptc_block | block-editors guard |
| Windows tags: JPEG XPKeywords/XPComment (UCS-2), a clip's Xtra "Windows Tags" | `exif_block` (`_decoded`, JPEG), `clip_patch` + `containers.Video.xtra` (clip) | same | block-editors guard; patch_clip rule |
| Field precedence (keywords XMP then IPTC, title, caption, DATE_TAKEN_FIELDS, DocumentID keys) | `tagpup/core/fields.py`, `tagpup/core/dates.py` (unchanged: TagPup's, not an engine's) | core | `tests/test_keyword_fields.py`, `test_vocabulary.py` (unchanged) |
| Batching (many files a command; a refused batch read one at a time) | `Fields.read` (ExifTool engine: `READ_BATCH` 200) | field_values.read | conformance suite case "a batch with one bad file" |
| Deadline and pipes | ExifTool engine: `exiftool_session._DrainingExifTool` (both pipes drained, a deadline a command); own engine: `in_place._Lent.bounded` / `shares.bounded` (a look at a share bounded) | exiftool_session; in_place; shares | existing pyexiftool rule; `tests/test_photo_looks_have_one_owner.py` |
| Record/replay of ExifTool for tests | `tests/exiftool_replay.py` at `_DrainingExifTool.execute` | same | `tests/test_exiftool_answers_kept_for_tests.py` (the product never replays) |
| The independent ExifTool read used as an oracle (`-j -G0:1 -a -all:all`, and `-validate` for what ExifTool says of a file, under `-n`) | `field_values.oracle(exiftool_path)` -> `Oracle.whole(paths)` (new, L0; today `metadata_check._whole` over its own `whole_et`) | metadata_check | the session rule above: with no session outside field_values, no command can be sent outside it (no `\.execute\(` rule is needed, and one would match every SQL call) |
| Shadow comparison on copies (writers today, readers from L1) | `tagpup/services/metadata_check.py` (`metadata-check`, `--reads` new in L1) | metadata_check | existing `tests/test_the_two_writers_compared_on_copies.py` |

Concepts L0 removes: `MetadataExtractor`, `IdentityWriter`, `file_changes.read_whole`, `field_values.session`, the hand
wraps of `StaysDead`, metadata_check's second session `whole_et`, and `et` as a parameter of 27 service functions (it
becomes `fields`, a `Fields`). Concepts it adds: `opened`/`Fields`, `Fields.whole`, `Fields.identity` (it replaces
`ensure_document_id` as the name callers see), `oracle`/`Oracle`. Four or more out, four in.

## 2. The public interface

```python
# tagpup/files/field_values.py -- the one face. Nothing else in tagpup/ reads or writes a photo's fields.

def opened(exiftool_path, *, timeout=exiftool_session.DEFAULT_TIMEOUT) -> ContextManager[Fields]:
    """The library for one run. ExifTool starts at the first command an engine sends it (a file the own engine refuses,
    a write it cannot patch), with the bake namespace's configuration (today's session(); a session without it
    could not read or write a bake); after one command times out every later command answers the same NotAnswered at
    once (StaysDead, inside). A start that fails is each file's Unreadable or `unread` (today's NOT_STARTED), never
    an exception out of `opened`."""

class Fields:
    def read(self, paths, wanted, *, stop=None) -> dict[key, dict[field, list[str]] | Unreadable]
        """The fields `wanted` of each file, by paths.key. A struct field (bake regions) answered as its one canonical
        text (bake.region_text). NotAnswered: the share did not answer; every file after it unread."""
    def read_one(self, path, wanted) -> dict[field, list[str]]                    # raises Unreadable
    def whole(self, paths, *, people=None, bakes=False) -> list[Record]
        """Each file as the index records it, in order: stamp before the read, tags, people, captions, document_id,
        raw_metadata (the row's shape, bare names included), read_error, unread; `bakes`: record["baked"]. The record
        is built by tagpup.files.metadata's functions (structured, raw_metadata), as tests/photo_rows.py builds one."""
    def write(self, path, values, *, saving=None, scratch_written=None, again=None) -> tuple[field, ...]
        """`values` into the file (today's written_once_more when `again` is given). Returns the fields left stale."""
    def identity(self, record, *, decoded) -> str | None                           # today's IdentityWriter.give
    def left_as_they_were(self, values, path) -> tuple[field, ...]                 # unchanged rule, now a method

def oracle(exiftool_path) -> ContextManager[Oracle]
class Oracle:
    def whole(self, paths) -> dict[key, dict[tag, value]]
        """ExifTool's whole read (-j -G0:1 -a -all:all under -n, the bake configuration) with what it says of each file
        (-validate's warnings and errors): metadata_check's both sides and the conformance suite's expected answers,
        never a product answer. One process a run, not a call. The one ExifTool command outside Fields."""

# unchanged module-level names callers keep: Unreadable, NotAnswered, PictureChanged, CHANGED_TWICE, writes(path),
# clip_refused, clip_date_refused, packet_held, packet_given (Convert's), PATCH_IN_PLACE.
```

`also`, `records` and `params` leave the face: `whole` is their one user. Smart Rename (`names.read_for_renaming`, today
`also` + `records`) asks `RENAME_FIELDS` as `wanted` and takes its title from the texts (`fields.title_of` reads lists as
`_read_copy` does); L0's brief proves it on the Smart Rename tests, else Smart Rename keeps `records` until L4.
**A caller never sees**: ExifToolSession, `et`, pyexiftool exceptions, `-struct` / `-G -n` / `-m` / `-o`, `File:MIMEType`,
`READ_ERROR_KEYS`, which engine answered, a container layout, a segment, a scratch file's name (file_changes keeps
`exiftool_session.scratch_of` for its scratch rules, #2180: named once, read through field_values), CSTR.
**A caller still gives**: `exiftool_path` to `opened` (threading it out of 81 functions is a separate, deletion-only
cut, not this plan's; see open question 9).

Engines (private): `_ExifToolEngine` (today's read and rewrite), `_OwnJpegEngine` (L1-L6: `segment_edits.held` /
`new_header` over `in_place`), `_OwnClipEngine` (today's `_read_headers` and `_clip_written`; L7 widens its reads).
`ENGINES` lists them for the conformance suite. An engine answers a file whole or refuses it whole: never half a record.
A refusal is one arm ("not wholly understood: ExifTool reads it"), never a branch a shape, so a shape with no live
instance costs no code.

## 3. What stays ExifTool, and why

| What | Live count | Why it stays |
|---|---|---|
| PNG and TIFF reads and writes | 43 rows (26 PNG: 25 + 1; 17 TIFF) | A parser each for 26 and 17 rows: each format is near CLAUDE.md's line of about 20 below which work is a one-off, not a feature. Their writes are made unchecked (DECISIONS 2026-11-03, "every photo write lands by write beside, compare, move", item 2). Owner may instead refuse their writes (question 6). |
| HEIC | 0 | No live instance: no branch is written for it (CLAUDE.md). A HEIC added later is ExifTool's. |
| Clips' reads other than the header dates | 2,930 rows | Media's; L7 only if the owner wants ExifTool out of the clip path too (question 7). Clip writes are already own. |
| A JPEG the own engine refuses (a target property twice, UTF-16 packet, unparsable segment, two EXIF segments) | counted by the L1 gate; census: attribute form 0, two EXIF 4 of 3,000, IFD0 XMP 24 of 10,515 | A refusal falls to ExifTool, never "holds nothing" (a plan from nothing clears a field: sweep, risks). |
| The JPEG rewrite (a write that does not fit, the mint, bakes, just-look) until L6 | every write not patched; extended XMP 111 / 80 / 5 of 3,000 / 2,065 / 1,308 sampled; MPF 100 / 14 / 0 | M5 of PLAN_OWN_METADATA not built. |
| Composite:LensID (maker-note lens tables) | 43 / 1,412 / 2 of the sweep's samples (EXIF:LensModel 37 / 1,412 / 2) | Not worth owning; question 4 (drop, or keep EXIF LensModel). |
| The oracle | every conformance item, metadata-check both sides | An independent reader is what a byte-level claim needs. Tests and metadata-check only, never a product answer. |

ExifTool stays installed for as long as any row above stays; the plan does not remove the dependency, it removes ExifTool
from the JPEG path (95.4% of rows).

## 4. Stages (each one merge, about 1,200 added lines or fewer, trunk whole after each)

Each stage names its gate (what must be true before it merges), its rollback, and what it deletes.

**L0. One face, no behaviour change** (about 550-650 added lines, counted: 165 product lines name a session, `et` or a
deleted class, in 15 files; about 160 test lines (65 call field_values with an `et`, 78 name MetadataExtractor or
IdentityWriter, 13 patch `metadata.ExifToolSession`); about 150 new in field_values (`opened`, `Fields`, `whole`'s
session and read, `identity`, `oracle`); about 30 of guard; the docs that name the removed classes: ARCHITECTURE,
INVARIANTS' ExifToolSession row, the Files charter's checklist item 9). Under the budget: no cut. If the worker's own count
passes 1,200, it cuts L0a (`opened`, `read`, `whole`, `oracle`: the reads) and L0b (`write`, `identity`: the writes and
the mint), each leaving the trunk whole. Every service opens `Fields` instead of an ExifToolSession; `MetadataExtractor`,
`IdentityWriter`, `file_changes.read_whole` and `field_values.session` deleted with every importer in the same commit
(tests/test_layers.py); metadata's record-building functions stay and `tests/photo_rows.py` keeps calling them (a test's
row made by the code that makes the real one); the one new LINE_RULE. Tests that patch
`tagpup.files.exiftool_session.ExifToolSession` keep working (the engine looks the class up at its start); the 13 lines
patching `metadata.ExifToolSession` move to it. ExifTool still answers everything. Gate: the full suite green under replay
AND one `--real-exiftool` run (it changes code that shapes commands: CLAUDE.md, owner 2026-11-24; sessions that had no
bake configuration now carry it, so their kept answers are keyed anew); `fake_exiftool` keeps standing in at the
ExifToolSession boundary, below `Fields`, so its 26 users do not change. Rollback: a revert; nothing on disk changed.

**T1. One corpus and one conformance suite, ExifTool the only engine** (about 700 added lines; about 45 granular tests
lifted and deleted, about 800 lines: section 5). Written against `Fields` before any own reader exists, so the suite is
proved on the engine it will judge the others by. Gate: green under replay and `--real-exiftool`; a measured fast-tier
time, baseline first. Rollback: revert (tests only).

**L1. Own JPEG reader, shadow only** (about 600 lines; sweep stage S0). `segment_edits.held(layout, header, wanted)`
reading through the three block modules, which gain: EXIF Make/Model (IFD0 0x010F/0x0110), LensModel/LensMake
(0xA434/0xA433), Rating (0x4746); XMP xmp:Rating, aux:Lens, tiff:Make/Model, extended XMP joined by GUID, XMP in IFD0;
MIME by magic. `metadata-check --reads`: on copies in a sandbox home, `--sample 3000` per library (all four; every JPEG
of the three smaller ones), own record against the oracle's on METADATA_FIELDS + bake fields, differences counted by tag;
KNOWN grown only from output seen. The `containers.read` guard (section 1). Gate: 0 unexplained differences on the four
samples; the conformance suite passes with `_OwnJpegEngine` in ENGINES. No product read changes. Rollback: revert;
nothing on disk. Removes nothing: a read-only shadow, the fact L2 is decided on (section 8). Measures (told the owner only
as counted): ms a photo, own against ExifTool batched (2026-11-03: 23 vs 28).

**L2. Own reads for the owned fields** (about 400 lines). `_engine_for` sends a JPEG read whose every wanted field the
own engine answers to it: the pre-write plan, the read-back, the undo check, the settle, Smart Rename, relink, just-look,
Apply All (sweep sites 1, 2, 4, 5, 8, 9, 10, 11). A refusal falls to ExifTool in the same call. Gate: the L1 gate green
on trunk; the scenario files of F1, F3, F8 and D2/D3 green with the oracle on (T1). Rollback: a module constant
`OWN_READS`, True; set False, every read is ExifTool's again and nothing on disk changed. It is deleted in the first merge
after L2 has been installed for a release with no own-read difference reported: a switch kept beside a gate that holds is
a branch with no instance. Deletes: the clip header special case: `_read_headers` becomes `_OwnClipEngine`'s answer for
those fields (one dispatch, not two).

**L3. Bake fields** (about 500 lines; sweep S2). XMP-tagpup values and `mwg-rs:RegionInfo` read to the structure
`bake.region_text` canonicalises; FaceVectors from extended XMP. Live: 62,158 of 62,161 JPEGs hold a bake, so every index read of a JPEG asks these fields: L3 is what takes ExifTool off the index's bake read, not a corner case. Gate: conformance items for a
bake of 0, 1 and many faces (enough to force extended XMP); L1's `--reads` with `bakes`. Rollback: the bake fields leave
`_OwnJpegEngine`'s answered set (ExifTool reads them again).

**L4. The index's whole read** (about 600 lines; sweep S3). **ONE-WAY, the owner's** (question 3): what 62,161 JPEG rows
record at their next re-read. `Fields.whole` builds the row from the own record: the bare-name shape, read_error, MIME.
Gate: a dry re-read of 3,000 unchanged files per library (every JPEG of the three smaller ones) changes 0 of taken / tags /
captions / document_id / year, and raw_metadata differs only in tags the owner accepted (MakerNotes:CreateDate under a bare
name: 121 of 3,000 in photo_index; APP12: 1). Never first run as a sync: a dry count, then the owner's yes. Rollback:
`whole` back to ExifTool; rows already re-read keep the own shape (they read the same by the gate), so no migration and no
backfill.

**L5. Drop unread fields** (deletion only; sweep S4; the owner's, question 8). Location (City/State/Country, the IPTC
location pair: 0 held in every sample), and Rating / GPS if the owner says so: no reader in tagpup/ or web/ (sweep).
A row loses a dropped field from its raw_metadata at its next re-read (the read replaces raw_metadata whole); nothing
reads it. Skipped if the owner keeps them.

**L6. Own rewrite** (PLAN_OWN_METADATA M5; two merges, L6a and L6b, about 1,000 lines each). L6a: the mint and the bake
write as own writes (the mint needs a record to be patched: question 5; live: 56,254 photo_index JPEGs hold no DocumentID
and each is minted when the index next reads it); L6b: a whole-file JPEG rewrite beside the photo (make a missing segment, grow one,
keep extended XMP, IFD0 XMP, two EXIF, bytes after 8BIM/EOI; MPF length-stable or refused) with the picture check
unchanged. Gate: the 4d gate (`metadata-check --sample 3000`) on four libraries, 0 own-side differences; each live class
taken or refused with a sentence the owner accepts. Then `_ExifToolEngine`'s JPEG write arm is deleted. Rollback: before
the deletion, the engine table; after, a revert.

**L7. Clip reads (Media; optional)**. MP4/MOV XMP, QuickTime title, Xtra, keys, from `containers.Video`. Only on the
owner's yes (question 7).

One migration or stamp: none. One-way choices: L4 (row shape), L5 (dropped fields), L6a (journaling the mint), L6b
(files grow about 2 KB at a rewrite: PLAN_OWN_METADATA's open item). Each is asked before its brief.

## 5. Tests: one corpus, one oracle, one conformance suite

**One corpus** (`tests/corpus.py`, extended, not a second module). Items named by recipe, made by the real writer and kept
across runs (as today). New: a **shape recipe** for each live class the census found, made by laying the class's segment
layout over a synthetic picture (PIL noise, `damaged_photos.whole_jpeg`), never an owner photo: extended XMP (111 / 80 /
5); MPF (100 / 14 / 0); bytes after EOI (10 / 3 / 0); two EXIF (4); no EXIF (2 / 0 / 3); Canon and Sony maker notes moved
(1,239; 258); IPTC with no 1:90 (746 / 4 / 0) and with UTF-8 (2,194 / 2,057 / 1,305); two APP13 (0 / 2 / 3); bytes after
the last 8BIM (1 / 4 / 6); IFD0 XMP (24 of 10,515); a bake of 0, 1 and many faces (62,158 JPEGs hold one); plus clips (moov
first and last, uuid XMP, Xtra; Adobe's attribute-form packet, live in clips only). Where a class exists only in owner
files, its box layout is captured from one file as the fixture (CLAUDE.md), every text replaced by fictional values of the
same shape and length. Each recipe names the live count that justifies it (no count, no item: a JPEG attribute-form
packet (census 0) and a 64 KB packet (not counted) are not items until counted). About 22 items.

**One oracle**: ExifTool, through `field_values.oracle`, replayed (`exiftool_replay`) in the normal run and real under
`--real-exiftool`. An item's expected answer is never written by hand: it is what the oracle reads. Hand-written
expectations stay only where TagPup decides (precedence, `fields.as_patched`, a refusal's sentence).

**One conformance suite** (`tests/test_a_photos_fields_read_and_written.py`, owner Files, a new scenario F20 "a photo's
fields read and written the same by every engine": F12 to F19 were the clip flows until 2026-11-13, now Media's C1-C7, so
the next unused id): for every corpus item x every engine in `ENGINES`:
1. read every METADATA_FIELD and bake field: equals the oracle's, or the engine refuses the file whole;
2. for each operation (add/remove a keyword, caption, title, Date Taken, a shift, a turn, mint, PreservedFileName, a bake,
   an undo of each): write, then the oracle reads the target fields as `fields.as_patched` says, every other field as
   before; picture bytes equal; every non-target segment byte-equal; idempotent (write twice: no changed range);
3. the failure cases once per engine: a truncated copy, a file held open, a share that does not answer (StaysDead/Away
   stand-ins already in the tests), a batch with one bad file.
A new engine is done when it passes, or refuses with a sentence, every item.

**What is lifted and deleted** (counted by test name, 2026-11-26; T1's brief confirms each before deleting). A granular
test goes when its claim is "this layout reads as these fields" or "this write changes the target fields and no other,
and twice changes nothing": a corpus item and suite steps 1-2 make that claim for every engine. A test stays when its
claim is TagPup's own decision inside a segment (padding, slack, where a new entry goes, a spelling, a character set), a
refusal's reason, or bytes damaged anywhere (the fuzz loops: one truncated copy an engine is given does not replace them).

| File | Tests | Go | Stay | What goes, and why |
|---|---|---|---|---|
| test_container_layout | 26 | about 8 | 18 | The clip layouts ExifTool and an append leave (mp4 XMP after moov, appended after mdat, mov XMP inside moov, a skip box first, a 64-bit box) and the JPEG layouts no XMP / two XMP / extended XMP: corpus items whose reads the suite checks. Stay: header versions, the > 4 GB sparse clip, fragmented clips, the slack accounting the writer's room uses, and the 9 damage walks ("none raises"). |
| test_xmp_packet_edits_what_it_owns | 23 | about 10 | 13 | Owned properties read back, all nine set with every other byte kept, writing what it holds changes no byte, an empty caption, a keyword added then removed, an attribute read as its element, a date changed in an attribute, a keyword inside the description, a self-closing description, the 200 KB Premiere packet: suite steps 1-2 on items. Stay: padding paid and grown, ExifTool's padding lines, a namespace declared, a packet with no wrapper, escaping and character references, an attribute cleared with its space, what is refused, the 3 damage loops. |
| test_exif_block_edits_what_it_owns | 15 | about 6 | 9 | Fields read in either byte order, all nine set and put back, writing what it holds, XP tags as UCS-2, a cleared field and set again, maker notes that keep their pointers: items and steps. Stay: slack and grow, where a new tag is laid, Orientation's range, refusals, the IFD loop and the 2 damage loops. |
| test_iptc_block_edits_what_it_owns | 16 | about 5 | 11 | Datasets read in their character set, a keyword added changes only the block, writing what it holds, a dataset rewritten in place or cleared, a segment given its first 0x0404: items and steps. Stay: the dummy resource's room, the cp1252 / UTF-8 rule, version datasets, zero bytes dropped, values cut, refusals, the hostile-bytes loop. |
| test_segment_edits_choose_what_to_write | 25 | about 12 | 13 | A keyword or caption to XMP alone with the others stale, an IPTC-only photo given the set in XMP, the last keyword off every copy, a save twice, a date patched in every copy, an XMP date keeping its zone, a rotation's copies, the clip packets grown or edited or given, only the bodies changed: `fields.as_patched` is the suite's expectation for each. Stay: the map covers every writer (2), every "no patch" refusal (8), the header-length check, a clip's copies outside XMP left stale and its refusals (2). |
| test_metadata_extraction | 70 | about 4 | 66 | The batch reads that survive one bad file (good files keep theirs, the bad one blanked, a clean batch is one call, ExifTool that will not start): suite step 3. Stay: 66 test TagPup's own decisions over a record (people, years, captions, file names, clean_metadata_value), not a format; the earlier "the reads of test_metadata_extraction (70)" was wrong. |
| **the six** | **175** | **about 45** | **130** | The earlier estimate "about 100 of 175" counted whole files; by test name it is about 45. |

The three scenario files (test_a_photo_written_by_its_own_editors 16, test_a_file_holds_what_exiftool_kept 28,
test_the_two_writers_compared_on_copies 20) stay: crashes, undo, two at once, the journal, metadata-check. Of them only
"a turn reads as ExifTool's write of it reads" and the IFD0-packet pair (3) restate a conformance claim and may go.
`fake_exiftool` (26 users, a table with no bytes) stays the stand-in at the ExifToolSession boundary where a test needs a
hook between read and write (`on_read`, `on_write`); a test that only needed fields cheaply may move to a corpus copy and
the own engine (from L2), which needs no process.

**Effect on the 297 s suite**: not claimed. Replay already answers most ExifTool commands; what the own engine saves in
the normal run is the misses (new bytes) and process starts. What it saves is real in `--real-exiftool` runs (sweep: 80% of
the 537 starts in the undo matrix are product reads). The conformance suite must stay in the fast tier on the own
engine with the oracle replayed; T1's brief sets a budget from a measured run, baseline first (DEVELOPMENT.md,
"Performance work").

## 6. Failure modes

- **Interrupted part way**: reads write nothing. A patch keeps today's rule (record committed before the first byte,
  settled by bytes: INVARIANTS, "Writes", the two rows on a photo write and a patch in place); L6b's rewrite keeps `-o`
  beside the photo and the picture check: a kill leaves the photo whole and a scratch file the scratch rules settle (#2180).
- **Two at once**: two runs reading one file: harmless. A read during another process's patch can see half a header:
  the own engine reads the header in one bounded open, and a header that does not parse is a refusal that falls to
  ExifTool, never an empty record. Writes keep the per-file lock and the "ExifTool's new file beside it" rule.
- **Unreadable file**: Unreadable with ExifTool's or the own engine's reason; the index records `read_error` and stamps it
  (not read every run), as today. An own refusal is never Unreadable: it is a fall-back.
- **ExifTool will not start** (L0's lazy start): each file is `unread` NOT_STARTED in `whole` and Unreadable in `read`, as
  `MetadataExtractor` and `IdentityWriter` answer today; the mint is skipped, never a failed index.
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
key; the oracle's real run is the gate. (4) L0 touches 17 session sites, 27 functions' `et` and the index: mechanical but
wide: one reviewer pass plus the Data owner for `whole`'s row shape. (5) Gains are mostly structural; per-photo speed was
measured close (23 vs 28 ms batched), so the case for L2-L6 is ownership and removing the rewrite, not speed.

**Open questions only the owner can answer.** Each: what each answer changes, and the plan's recommendation.

1. Is the own reader still wanted after replay brought the suite to 297 s (work row 2026-11-24: "the own reader may not
   be needed")? L0 and T1 stand either way.
   Yes: L1 (read-only shadow) is built, and L2-L3 follow on its counted differences. No: the plan ends at T1, ExifTool the
   only engine behind the face, DECISIONS 2026-11-03 amended to say so. **Recommended: yes to L1 only**, decide L2 on its
   gate's numbers: the 2026-11-03 decision stands, L1 changes no product read, and it turns "may not be needed" into a
   counted answer.
2. mu_dance (1,284 rows, 1,280 JPEG) is a fourth library: in every gate?
   Yes: every gate reads all 1,280 of its JPEGs (about 1,300 more file reads a gate). No: a shape only it holds meets L2
   unproved (it was never censused). **Recommended: yes**, it is cheaper than a census and it is live.
3. L4: may the index's raw_metadata change shape where ExifTool's "last group answers the bare name" differs (maker-note
   CreateDate, 121 of 3,000)? Or keep raw_metadata ExifTool's forever?
   Yes: L4 is built; those rows' bare CreateDate becomes the EXIF/XMP one at their next re-read (taken changes only where
   EXIF:DateTimeOriginal is absent: 5 of 3,000 sampled). No: ExifTool reads every JPEG at every index for good; L4 is
   not built, L6 can still retire the rewrite. **Recommended: yes, a bare name answered by EXIF or XMP, never a maker
   note**, asked again with L4's dry count in hand.
4. Composite:LensID (maker-note tables; 43 / 1,412 / 2 of the samples): drop it, or keep EXIF LensModel as the lens?
   Drop: the gear search loses a lens name only where LensID is text and no LensModel is held (photo_index sample: at
   most 6 of 3,000; a LensID number is already ignored by `photo_meta.gear`). Keep: ExifTool reads those files' lens for
   good, or TagPup owns maker-note tables. **Recommended: drop LensID, the lens is EXIF/XMP LensModel**.
5. The mint: journaled (so the own writer can patch it), or stays ExifTool's rewrite?
   Journaled: L6a builds it; every mint is a journaled change (56,254 photo_index JPEGs hold none, each minted when the
   index next reads it), the charter's named exception #1355 (an unjournaled write) is deleted. Stays: ExifTool keeps a JPEG write
   for good, and L6 cannot retire the rewrite. **Recommended: journaled**: a patch needs a record committed before its
   first byte (INVARIANTS), and it removes an exception.
6. PNG/TIFF (43 rows): keep ExifTool for them, or refuse their writes with a sentence?
   Keep: nothing changes. Refuse: 43 files can no longer be tagged from TagPup, and ExifTool stays installed anyway for
   clips and the oracle. **Recommended: keep**: refusing saves no dependency.
7. Clips' reads (2,930 rows): own (Media, L7), or ExifTool's?
   Own: Media builds MP4/MOV XMP, QuickTime title, Xtra and keys reads (one more merge and gate). ExifTool's: no change;
   clip writes stay own. **Recommended: ExifTool's for now**; reopen after L4 has proved the whole-read shape on JPEGs.
8. L5: drop Rating/GPS/location from the read (no reader; Rating in 97% of photo_index)?
   Drop: L1 need not read Rating (EXIF 0x4746, xmp:Rating) or the GPS IFD; rows lose them at their next re-read. Keep:
   L1 reads them. **Recommended: drop location only** (0 held in every sample: a field with no instance); keep Rating
   and GPS, held and the owner's data, cheap to read.
9. Thread `exiftool_path` out of 81 functions (a later deletion cut), or leave it?
   Thread out: after L0, `opened` is its one reader; a deletion-only cut (216 uses in 27 files, 77 test lines) lets
   `opened` find ExifTool itself. Leave: nothing changes. **Recommended: yes, as its own deletion-only cut after L0**
   (never over budget), not inside L0.

**Recommendation.** Build **L0** first: one face (`field_values.opened` -> `Fields`), every session and `MetadataExtractor`
behind it, the one new guard. It is the cheapest correct stage, changes no answer, and is what the owner's "one library,
one interface, one access path" means whether or not ExifTool ever leaves. Then **T1** (the corpus and the conformance
suite on ExifTool alone), which turns about 45 granular format tests into rows of one suite and gives any later engine its
bar. Ask questions 1-3 before L1; build L1 on a yes to question 1 and L2-L3 on L1's numbers; L4-L6 each wait for their
one-way answer.

## 8. Checked against (the completing design pass, 2026-11-26)

Verified on trunk 3badca98 by grep, AST and reading the code; live numbers counted read-only this session:
- Every count in sections 0-1 (sessions 9 + 8, `et` in 27 functions, `exiftool_path` 81 / 216 / 27, `containers.read` 5,
  `_whole`'s two commands, `READ_BATCH` 200, test files 53 / 26 / 24 / 9 / 24, the six granular files 175 tests) and every
  line number cited; corrected where the first draft was wrong (sessions said 7, file_changes lines 1930/1742/2011,
  "18 calls in 7 modules" of write_fields, 22 read call sites, 52 test files, 213 / 28 / 82).
- The live rows of all four libraries, the JPEG and clip totals, the bake count and the DocumentID count (section header).
- Every module, function and test file the plan names exists (the new ones are marked new).
- CLAUDE.md, "one owner for each rule, with its guard": each mechanism of section 1 has one place and a guard; the
  session rule makes the oracle's own `\.execute\(` rule unnecessary (it would also have matched every SQL call). The
  identity guard (`test_photo_identity`) is re-keyed in L0, not lost. INVARIANTS' ExifToolSession row and CLAUDE.md's
  "never construct ExifToolHelper or ExifTool" line gain "and ExifToolSession only in field_values" in L0 (CLAUDE.md is the
  main session's edit).
- CLAUDE.md, "a special case needs a real instance": every engine class, corpus item and stage cites a live count; HEIC
  (0) and JPEG attribute-form packets (0) get no branch and no item; refusals are one arm, not one per shape. Not met yet:
  multi-language Alt and UTF-16 packets are uncounted (L1's gate counts them before L2).
- CLAUDE.md, "each branch removes at least as many concepts as it adds": L0 (section 1, four or more out, four in), T1
  (about 45 tests out for one suite and one corpus extension), L2 (the clip header special case, then `OWN_READS`), L5
  and L6 (deletions) meet it. **L1 and L3 add a reader and remove nothing**: L1 says why (a shadow is the fact L2 is
  decided on); L3 removes ExifTool's `-struct` read of bakes from the product only when its gate holds; both are flagged
  for the owners' review.
- INVARIANTS, "a test's data is made by the code that makes the real thing" and "seed rows as the indexer stores them":
  metadata's record functions stay and photo_rows keeps calling them. "A write reports what it changed": unchanged (reads
  are what move; `changed` still counts a read-back that differs). "A share that does not answer is not missing" and every
  look through `shares`: the own engine reads through `in_place`'s bounded open. "One migration in flight": none here.
- The branch budget: L0 about 550-650, T1 about 700, L1 600, L2 400, L3 500, L4 600, L6a / L6b about 1,000 each: each under
  1,200 added lines.

Not verified (by design or not countable read-only): the per-test "goes / stays" split is by name, T1's brief confirms it;
L0's line estimate is from counted call sites, not a diff; the 3,000-photo field samples are the sweep's (2026-11-24); the
23 vs 28 ms measurement is from 2026-11-03.

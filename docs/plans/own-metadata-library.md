# Own metadata library: every read and write of a photo's fields goes through one interface, proved by one test corpus
Live count (counted read-only 2026-11-26, `db.readonly_uri`, verified again by the completing design pass the same day): four
libraries, 65,134 rows, unread 0 in all four. photo_index 60,355: 57,392 JPEG (.jpg 57,373, .jpeg 19), 2,921 clips (.mp4
1,745, .mov 1,173, .m4v 3), 25 PNG, 17 TIFF; kr-track 2,186: 2,181 JPEG, 5 clips; renton_parkrun 1,309: 1,308 JPEG, 1 PNG;
mu_dance 1,284: 1,280 JPEG, 4 clips. JPEG 62,161 of 65,134 rows (95.4%); clips 2,930; TIFF/PNG 43. JPEGs holding a bake
(the bake namespace in an APP1 segment, every JPEG header read read-only, 2026-11-26): 62,158 of 62,161 (photo_index 57,389 of 57,392, kr-track 2,181, renton_parkrun 1,308, mu_dance 1,280: all). JPEGs holding a DocumentID (the
row's document_id): photo_index 1,138 of 57,392, the other three all. Field and segment counts: docs/work/3376-sweep.md
section 2 and "Segment census" (2026-11-24, three libraries, photo_index sampled 3,000; mu_dance not censused).
Row shapes (counted read-only 2026-11-26 by the review pass, every row of the four): photo_index 54,690 of 60,355 rows hold
no bare key in raw_metadata (grouped names only); kr-track 17 of 2,186, mu_dance 0 of 1,284, renton_parkrun 4 of 1,309.
All four libraries are at schema 39, nothing in flight; the plan adds no migration.
Domain: Files
Domains: Files (the library), Media (clip reads, stage L7 only), Data (the journal's patch record, the index row shape and
the mint's journal shape: stages M, L4-L6); integration reviews the seams

Work row #3376. Inputs: docs/work/3376-sweep.md (the Files owner's sweep; where it is stale, section 0 here is the
trunk's), docs/work/3376.md, docs/PLAN_OWN_METADATA.md (M1-M7), docs/plans/own-writer-flip.md (4d), DECISIONS 2026-11-03
("TagPup owns its JPEG metadata reads and writes; ExifTool retires by stages"). Reviewed 2026-11-26 by the Files,
integration and Data owners: their points are folded in below and listed in the Review log (section 9).
Owner's requirement (2026-11-26): if ExifTool is replaced, the replacement CENTRALIZES all tag reading and writing (one
library, one interface, one access path) AND the tests (one corpus, one oracle, one conformance suite).

Steady-state writer: none new. Every read and write of a photo's fields goes through `Fields` (section 2); a photo added or
changed afterwards is read by the index and written by `file_changes.write_fields` as today, and (stage M, on question 5's
yes) its identity is minted by the index's bake, the one writer that already writes a field of every indexed photo. One-offs:
none. No command, backfill, migration or stamp: a row takes L4's shape at its next ordinary re-read.

## 0. Where it stands (counted by grep and AST on trunk 3badca98, 2026-11-26; re-checked on d98a2a47 by the review pass)

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
  metadata_check :351). Only `field_values.session` and metadata_check's oracle carry the bake configuration
  (`bake.config_path()`); the other 9 sessions, the index's read among them (metadata.py :113), do not. `et` is a parameter
  of 27 service functions (132 uses in 10 files outside field_values). `exiftool_path` is a parameter
  of 81 functions, 216 uses in 27 files (tagpup/ and tagpup_cli.py; none in scripts/ or tools/). A caller knows it is
  talking to ExifTool: `-struct` params (metadata.BAKE_PARAMS), `StaysDead` wrapped by hand (metadata, bake_after_index,
  file_changes), `ExifToolNotRunning`, `MIME_TYPE`, `READ_ERROR_KEYS` (metadata, file_changes), ExifTool's record shape.
  A session starts its process at `with` (ExifToolHelper), so today ExifTool that will not start fails a write before
  its change is recorded.
- **Two record shapes.** `field_values.read` answers `{field: [texts]}`; the index wants ExifTool's whole record made into
  a row (`metadata.MetadataExtractor.batch_read` -> `structured`, bare names, `read_error`). Callers of the whole shape:
  `MetadataExtractor.batch_read` (tagpup_cli :547, refresh_rows :41, photos :241 and :795, inspect :202) and
  `file_changes.read_whole` (defined :1240; called by `_read_whole` :1237 and convert :347 and :660). Two more read
  ExifTool's records through `also` + `records`: Smart Rename (`names.read_for_renaming`, names.py :52) and the save's
  caption check (`tagging._caption_problem`, tagging.py :405, every field a caption is held in, read only when a caption
  breaks a rule).
- **raw_metadata is shaped in four places**: `metadata.structured` (the bare copy of every grouped name, metadata.py
  :53-60), `store.photos.follow_fields` (the bare copy kept in step on a follow, photos.py :258-271),
  `fields.record_keyword_fields` (fields.py :435-438, the same rule for keywords) and `IdentityWriter.give` (metadata.py
  :251, `XMP:DocumentID` put into the record by hand). The bare-copy rule is written twice. Every reader reads grouped names
  first (core/fields.py :251-253 and :83, core/dates.py :28-31, core/photo_meta.py :30-38, store/search_index.py :246);
  `inspect._by_field` (inspect.py :176-179) already compares a row with a fresh read by field name alone because most rows
  hold no bare name. The SourceFile a row holds is `@<root>/...` (photo_index 59,968 of 60,355; kr-track 1,799 of 2,186;
  renton_parkrun 1,309 of 1,309; mu_dance 0), converted by `store.roots.raw_to_row` (roots.py :351-363, :391-394) only from
  `paths.exiftool_spelling`'s form.
- **The writers** (all reach `field_values.write`): `file_changes._written` :1049 under `_carry` (journaled: every save,
  bulk edit, undo, bake, rotation, rename's PreservedFileName), `file_only` :167 (just-look time shift and saves of unheld
  photos), `metadata_check._written` :184 (sandbox copies), `field_values.ensure_document_id` :605 via
  `metadata.IdentityWriter.give` (the index's mint, unjournaled: the charter's named exception, #1355; INVARIANTS :44,
  exception (1)), carried over a done rename by `file_changes.OutsideWrites.around` (:1373-1420, #1957; built at
  tagpup_cli.py :620) and read again by `tagpup_cli._read_again` (:1227) before its batch is recorded. The mint runs per
  photo mid-batch (tagpup_cli.py :848), before the photo's row is recorded (:880-881). `file_changes.write_fields` has 10
  calls in 5 modules (bake_after_index, convert, face_assignment, photos, tagging) and `file_only.write_fields` 5 (photos,
  tagging): both stay the writers they are and are not changed by this plan beyond `et`.
- **The read-back is independent today.** After a patch in place, `file_changes._verified` (:1066) reads the file back
  through `_read_whole` -> `read_whole` -> `field_values.read`, which is ExifTool: the own writer's work is checked by
  another author (INVARIANTS, "a write reports what it changed"; `tests/test_a_file_holds_what_exiftool_kept.py`).
- **Inside the write there are already two engines**: the own writer (`segment_edits.new_header` over `xmp_packet`,
  `exif_block`, `iptc_block`, laid by `in_place.patch_jpeg`) for a journaled JPEG write that fits, and ExifTool's rewrite
  (`-o` beside the photo, the picture check `_landed`) for everything else. `xmp_packet` already owns `xmpMM:DocumentID`
  (xmp_packet.py :43). Clips are never ExifTool's to write (`_clip_written`, `in_place.patch_clip`).
- **Inside the read there is already a second engine**: a clip's QuickTime header dates are TagPup's own walk
  (`_read_headers` -> `in_place.headers_held`), put over ExifTool's answer. Every other field of every format is ExifTool's,
  but for the Orientation Pillow reads at decode (`images._boxes_turn`, images.py :84, which `orientation.boxes_turn`
  documents, orientation.py :39): a field reader outside the face, which stays (section 3).
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
  from kept answers; the suite is 297 s with it. A kept answer is keyed by its arguments, common arguments, the session's
  configuration file and the tool (exiftool_replay.py :223-226); the mint is seeded under replay (:499-507).

So the "library" exists in pieces. This plan makes it one thing with one face before any reader is replaced, so that
replacing ExifTool for a format is a change behind the face, proved by a suite written against the face.

## 1. Mechanisms: the ONE place each lives

| Mechanism | The ONE place (module, function) | Current owners (all of them today) | Guard that fails on a second |
|---|---|---|---|
| Open the library for an operation (which engine, ExifTool started at its first command, deadline) | `tagpup/files/field_values.py` `opened(exiftool_path)` -> `Fields` (new, L0a), opened per operation, never held by the Runtime; an entry point reaches it through `runtime.fields(library)` (new, beside `runtime.exiftool`, runtime.py :348-351; the CLI, web and MCP may not import `files`) | 9 `ExifToolSession(` + 8 `field_values.session(` in services | new LINE_RULE "ExifToolSession constructed outside field_values" (owner `field_values.py`; `exiftool_session.py` exempt for its class and its docstring's example) |
| Dead after a timeout (a share that does not answer costs one deadline, not one a file) | `Fields`: every `read` / `read_one` / `whole` / `write` call is dead for good after its first timeout and the next call answers again (I1); `Fields.together()` holds that across the calls of one journaled write (`file_changes._write_session`, today :464-466) and one bake run (`bake_after_index.bake_folders`, today :149), its two users | `StaysDead` wrapped by hand in metadata :114, bake_after_index :150, file_changes :467 | the session rule (no `StaysDead(` outside field_values: one more name in the same LINE_RULE); conformance step 3 (section 5) |
| ExifTool cannot start: a write refused before its record | `Fields.ready_to_write()` (new, L0b), called by `file_changes.write_fields` before `file_journal.plan`: starts ExifTool (the start every write pays today at `with`) and refuses the change whole with one sentence when it cannot | implicit today: the session's start at `with` raises before the plan | new scenario test, the pattern of `tests/test_ffmpeg_is_a_prerequisite.py`: ExifTool that will not start leaves no change record and says one sentence (fails on old code, which raises) |
| Read named fields of many files (`{key: {field: [texts]}}` or Unreadable / NotAnswered) | `Fields.read` / `read_one` (today `field_values.read` / `read_one`) | field_values.read: 14 call sites, read_one: 6, in 9 modules | existing rule "an ExifTool read outside field_values" (`.get_tags(`) |
| Read a write back (the check before `done`, the row's whole read back, the settle's read of a file its patch reached) | `Fields.read_back(path, wanted, *, patched)` (new, L0b): answered by an engine that did not write the file (ExifTool after an own patch; from L2 the own engine may read back ExifTool's rewrite), through L6b at least (question 10) | `file_changes._verified` :1066 and `_read_whole` :1234 over `field_values.read`: ExifTool always | conformance step 2 (every write's read-back engine differs from its writer's); `tests/test_a_file_holds_what_exiftool_kept.py` end to end |
| Read a file's whole record as the index keeps it (row shape: tags, captions, taken, document_id, raw_metadata, read_error, unread, baked) | `Fields.whole` in `field_values` (new, L0a: the session and read of `MetadataExtractor.batch_read` and `file_changes.read_whole`), over `tagpup.files.metadata`'s record functions (`structured`, `raw_metadata`, `_structure`, `_empty`, `_stamp`, made module functions), which `tests/photo_rows.py` keeps calling. `metadata` stops importing `field_values` (its uses leave with MetadataExtractor and IdentityWriter; the MIME filter moves into `whole`), so field_values -> metadata is one way | MetadataExtractor, file_changes.read_whole, metadata.structured | the session rule: no reader outside field_values can open ExifTool; the deleted names leave nothing behind (`tests/test_layers.py`); an import cycle fails at import |
| Shape a row's raw_metadata | ONE owner per path: `tagpup/files/metadata` (`structured` / `raw_metadata`) for a whole read; `store.photos.follow_fields` for a follow. `fields.record_keyword_fields`' bare copy deleted at L4, `IdentityWriter.give`'s at stage M | four places (section 0) | at L4 (question 3, grouped names only) the bare-copy rule is deleted, leaving nothing to write twice; until then `tests/test_tag_writes_keep_the_index_true.py` |
| SourceFile in a whole record | `Fields.whole` emits `SourceFile = paths.exiftool_spelling(path)` from every engine, which `store.roots.raw_to_row` turns into the row's `@<root>/...` | ExifTool's own spelling | conformance item: each engine's SourceFile equals the oracle's |
| Which engine answers a file and field (own or ExifTool), and the fall-back on a refusal | `field_values._engine_for(path, wanted)` (new, L2) | implicit today: `_read_headers` for clip header dates; `PATCH_IN_PLACE` + `new_header` refusal for writes | conformance suite (`tests/test_a_photos_fields_read_and_written.py`, new T1): every engine in `field_values.ENGINES` answers every corpus item alike or refuses it whole |
| Write fields into a file (patch in place, else rewrite; picture check; clears; minor errors) | `Fields.write` (today `field_values.write` / `written_once_more`) | field_values.write | existing rules: `.set_tags(`, `-TAG=` execute, `patch_jpeg(` / `patch_clip(` outside field_values; `field_values.write(` outside file_changes (file_only, metadata_check exempt) |
| Mint a photo's identity | **Recommended (question 5): a field of the index's bake**: `services/baking.plan_photo` plans `XMP-xmpMM:DocumentID` for a photo the bake is handed that holds none, written by `file_changes.write_fields` under `baking.INDEX_OPERATION` (stage M). Alternative (question 5, the other answer): `Fields.identity(record, decoded=, around=)` with `file_changes` (the owner of OutsideWrites) its one caller (L0b) | field_values.ensure_document_id, metadata.IdentityWriter, file_changes.OutsideWrites, tagpup_cli._read_again | `tests/test_photo_identity.py` "the indexer mints and nothing else does", re-keyed from the name `IdentityWriter` to a call of `identity.mint_document_id(` only in `services/baking.py` (M) or only in `field_values` (alternative) |
| Map a field name to where it lives in a JPEG, both ways (read and edit) | `tagpup/files/segment_edits.py` (`new_header` today; `held` new, L1) | segment_edits (write side); the read side is ExifTool's | existing `tests/test_the_block_editors_have_one_caller.py` (only segment_edits imports xmp_packet, exif_block, iptc_block): covers the read side too |
| JPEG container: segments, picture offset, EXIF IFDs (IFD0, Exif, GPS), maker notes, 8BIM, IPTC records | `tagpup/files/containers.py` `read`, `exif_tiff`, `photoshop_resources`, `iptc_records` | containers; `read` called from field_values (the picture check), in_place (2), metadata_check (1), segment_census (1) | extend test_the_block_editors_have_one_caller (L1): `containers.read(` only in tagpup/files (in_place, segment_edits, field_values' picture check) and in the two read-only layout tools, each exempt by name with its reason (metadata_check compares segments outside the metadata ones; segment_census counts layouts) |
| PNG / TIFF / HEIC containers | ExifTool, behind `Fields` (stays: 43 rows, HEIC 0) | ExifTool | conformance suite: the own engine refuses them |
| A picture's Orientation as its decode declares it (face boxes, shown shape) | `images._boxes_turn` (Pillow `getexif`, images.py :84) over `orientation.boxes_turn` (stays: section 3) | images | the rotation scenarios (`tests/test_rotate_keeps_the_photo.py`, `test_rotate_is_journaled.py`, `test_a_face_box_turned_upright_and_back.py`) |
| Video boxes (moov, udta, uuid XMP, Xtra, mvhd/tkhd/mdhd dates) | `containers.Video` (layout), `in_place.headers_held` / `packet_held` (reads), `clip_patch` (edits) | Media, beside Files | existing patch_clip rule; L7 adds clip reads to the engine table |
| XMP packet: owned properties read and edited as text | `tagpup/files/xmp_packet.py` `read`, `edit` | xmp_packet (9 properties, `xmpMM:DocumentID` among them); ExifTool for the rest | block-editors guard |
| EXIF block: owned entries read and edited in place | `tagpup/files/exif_block.py` `read`, `edit` | exif_block (9 fields); ExifTool for camera, lens and GPS | block-editors guard |
| IPTC: 2:05, 2:25, 2:120 and 1:90 charset | `tagpup/files/iptc_block.py` `read`, `edit` | iptc_block | block-editors guard |
| Windows tags: JPEG XPKeywords/XPComment (UCS-2), a clip's Xtra "Windows Tags" | `exif_block` (`_decoded`, JPEG), `clip_patch` + `containers.Video.xtra` (clip) | same | block-editors guard; patch_clip rule |
| Field precedence (keywords XMP then IPTC, title, caption, DATE_TAKEN_FIELDS, DocumentID keys) | `tagpup/core/fields.py`, `tagpup/core/dates.py` (unchanged: TagPup's, not an engine's; every list reads grouped names first) | core | `tests/test_keyword_fields.py`, `test_vocabulary.py` (unchanged) |
| Batching (many files a command; a refused batch read one at a time) | `Fields.read` (ExifTool engine: `READ_BATCH` 200) | field_values.read | conformance suite case "a batch with one bad file" |
| Deadline and pipes | ExifTool engine: `exiftool_session._DrainingExifTool` (both pipes drained, a deadline a command); own engine: `in_place._Lent.bounded` / `shares.bounded` (a look at a share bounded) | exiftool_session; in_place; shares | existing pyexiftool rule; `tests/test_photo_looks_have_one_owner.py` |
| Record/replay of ExifTool for tests | `tests/exiftool_replay.py` at `_DrainingExifTool.execute` | same | `tests/test_exiftool_answers_kept_for_tests.py` (the product never replays) |
| The independent ExifTool read used as an oracle (`-j -G0:1 -a -all:all`, and `-validate` for what ExifTool says of a file, under `-n`) | `field_values.oracle(exiftool_path)` -> `Oracle.whole(paths)` (new, L0a; today `metadata_check._whole` over its own `whole_et`) | metadata_check | the session rule above: with no session outside field_values, no command can be sent outside it (no `\.execute\(` rule is needed, and one would match every SQL call) |
| Shadow comparison on copies (writers today, readers from L1) | `tagpup/services/metadata_check.py` (`metadata-check`, `--reads` new in L1, with its sunset) | metadata_check | existing `tests/test_the_two_writers_compared_on_copies.py` |

Concepts L0a, M and L0b remove: `MetadataExtractor`, `IdentityWriter`, `OutsideWrites`, `_read_again`,
`field_values.ensure_document_id`, `file_changes.read_whole`, `field_values.session`, the hand wraps of `StaysDead`,
metadata_check's second session `whole_et`, and `et` as a parameter of 27 service functions (it becomes `fields`, a
`Fields`). Concepts they add: `opened`/`Fields`, `Fields.whole`, `Fields.read_back`, `Fields.together`, `oracle`/`Oracle`,
`runtime.fields`. Ten out, six in. On question 5's other answer, `OutsideWrites` and `_read_again` stay and
`Fields.identity` is added: eight out, seven in.

### Contract-table and charter diff (L0a's and L0b's briefs carry it)

Changed: the integration charter's row 50 (`.claude/agents/tagpup-owner-integration.md` :83) names
`files/metadata.MetadataExtractor.batch_read`, deleted by L0a: its callee becomes `files/field_values.Fields.whole`, its
meaning unchanged. Row 1 names "identity minting" among the exceptions to the journaled path: stage M deletes the
exception (the mint is a field of the index's bake). Unchanged in meaning, re-checked by the brief: rows 1 (but for M),
16, 24 and 41, 56, 66, 70 (the settle thread's `field_values.session` at file_changes.py :1750 and :2044 becomes
`opened`), 77. Also updated in the same commits: the Files charter's checklist item 9 (`.claude/agents/tagpup-owner-files.md`
:72, "Through `refresh_rows.read_files` / `MetadataExtractor`") and its `Scenario ids:` line (:94; F12 added, see section 5;
`tests/test_the_test_map.py` :220-224 fails a charter that does not list its ids), `.claude/agents/tagpup-worker.md` :79
("ExifTool through `ExifToolSession`" becomes "through `field_values.opened`"), docs/INVARIANTS.md :20 (the ExifToolSession
row gains "and ExifToolSession only in field_values") and :44 (exception (1) deleted at M, the "indexer writes no identity
into a photo that does not decode" row re-owned to `services/baking.py`), ARCHITECTURE, and CLAUDE.md's "Never construct
ExifToolHelper or ExifTool" line (the main session's edit). `tests/test_layers.py` :53 PROGRAMS_TODAY `tagpup_cli.py` goes
from 6 to 5 when its `from tagpup.files.metadata import IdentityWriter, MetadataExtractor` (:66) leaves (the CLI opens
`Fields` through `runtime.fields`, imports nothing new from `files`). `tests/test_photo_looks_have_one_owner.py` :47
(file_changes' os.stat exemption) loses "and the identity mint's carry (OutsideWrites)" at M; :48 (metadata's, for
`_stamp`) stays while `_stamp` stays in metadata, and moves with it in the same commit if a brief moves it.

## 2. The public interface

```python
# tagpup/files/field_values.py -- the one face. Nothing else in tagpup/ reads or writes a photo's fields.

def opened(exiftool_path, *, timeout=exiftool_session.DEFAULT_TIMEOUT) -> ContextManager[Fields]:
    """The library for one operation (a batch read, a journaled write, a bake run, a look): opened by the service or
    through runtime.fields(library) by the entry point, never kept on the Runtime. `exiftool_path` is required: there is
    no None arm (inspect.photo_against_file's default None has no product caller: the MCP passes runtime.exiftool,
    tagpup/mcp/server.py :162; L0a makes the parameter required). ExifTool starts at the first command an engine sends
    it (a file the own engine refuses, a write it cannot patch, a read-back), always with the bake namespace's
    configuration (today only field_values.session's carries it: the index's read gains it, stage L0a's gate). A start
    that fails is each file's Unreadable or `unread` (today's NOT_STARTED) in a read, never an exception out of
    `opened`; a write is refused before its record (ready_to_write)."""

class Fields:
    # Dead for good after a timeout, for ONE call: a call whose command times out answers NotAnswered for the file and
    # every later file of that call at once; the next call starts ExifTool again and answers (I1). A long-lived Fields
    # never leaves every later photo unread after one share hiccup.
    def together(self) -> ContextManager[Fields]
        """The calls inside wait one deadline between them: dead for good from the first timeout to the block's end (what
        StaysDead(et) does today). Two users: file_changes._write_session (one journaled write) and
        bake_after_index.bake_folders (one bake run). The caller's own later calls answer again (file_changes.py
        :464-466 today)."""
    def read(self, paths, wanted, *, stop=None) -> dict[key, dict[field, list[str]] | Unreadable]
        """The fields `wanted` of each file, by paths.key. A struct field (bake regions) answered as its one canonical
        text (bake.region_text). NotAnswered: the share did not answer; every file after it in this call unread."""
    def read_one(self, path, wanted) -> dict[field, list[str]]                    # raises Unreadable
    def read_back(self, path, wanted, *, patched) -> dict[field, list[str]]
        """A file a write just made (or, `patched`, one a patch's record says it reached: the settle), read by an engine
        that did not write it: ExifTool after an own patch; after ExifTool's rewrite, the own engine where it answers
        (from L2), else ExifTool. The caller knows only whether it patched (its `saving` was called): never an engine."""
    def whole(self, paths, *, people=None, bakes=False) -> list[Record]
        """Each file as the index records it, in order: stamp before the read, tags, people, captions, document_id,
        raw_metadata, read_error, unread; `bakes`: record["baked"]. SourceFile is paths.exiftool_spelling(path) from every
        engine. raw_metadata holds ExifTool's grouped names; until L4 it also holds the bare copies (structured), from
        L4 on grouped names only (question 3). Built by tagpup.files.metadata's functions, as tests/photo_rows.py builds
        one."""
    def ready_to_write(self) -> None                                               # raises Refused, one sentence
    def write(self, path, values, *, saving=None, scratch_written=None, again=None) -> tuple[field, ...]
        """`values` into the file (today's written_once_more when `again` is given). Returns the fields left stale."""
    def left_as_they_were(self, values, path) -> tuple[field, ...]                 # unchanged rule, now a method
    # Only on question 5's other answer (no stage M):
    # def identity(self, record, *, decoded, around) -> str | None   # today's IdentityWriter.give; `around` is
    #     file_changes.OutsideWrites.around (#1957), and file_changes is its one caller (the CLI asks the service)

def oracle(exiftool_path) -> ContextManager[Oracle]
class Oracle:
    def whole(self, paths) -> dict[key, dict[tag, value]]
        """ExifTool's whole read (-j -G0:1 -a -all:all under -n, the bake configuration) with what it says of each file
        (-validate's warnings and errors): metadata_check's both sides and the conformance suite's expected answers,
        never a product answer. One process a run, not a call. The one ExifTool command outside Fields."""

# unchanged module-level names callers keep: Unreadable, NotAnswered, PictureChanged, CHANGED_TWICE, writes(path),
# clip_refused, clip_date_refused, packet_held, packet_given (Convert's), PATCH_IN_PLACE.
```

`also`, `records` and `params` leave the face. They have three users: `whole` (inside the face), Smart Rename
(`names.read_for_renaming`, names.py :52) and the save's caption check (`tagging._caption_problem`, tagging.py :405). Smart
Rename asks `RENAME_FIELDS` as `wanted` and takes its title from the texts (`fields.title_of` reads lists as `_read_copy`
does); the caption check asks `fields.HELD_CAPTION_FIELDS`' grouped names as `wanted` and `fields.captions_held` reads the
`{field: [texts]}` answer (it reads a mapping by field name either way). L0a's brief proves both on the Smart Rename and
caption-rule tests (`tests/test_tags_are_checked_where_they_are_set.py`), else that caller keeps `records` until L4.
**A caller never sees**: ExifToolSession, `et`, pyexiftool exceptions, `-struct` / `-G -n` / `-m` / `-o`, `File:MIMEType`,
`READ_ERROR_KEYS`, which engine answered or wrote, a container layout, a segment, a scratch file's name (file_changes keeps
`exiftool_session.scratch_of` for its scratch rules, #2180: named once, read through field_values), CSTR.
**A caller still gives**: `exiftool_path` to `opened`, until question 9's cut collapses it with `et` into one parameter
`fields` (section 4, L0c).

Engines (private): `_ExifToolEngine` (today's read and rewrite), `_OwnJpegEngine` (L1-L6: `segment_edits.held` /
`new_header` over `in_place`), `_OwnClipEngine` (today's `_read_headers` and `_clip_written`; L7 widens its reads).
`ENGINES` lists them for the conformance suite. An engine answers a file whole or refuses it whole: never half a record.
A refusal is one arm ("not wholly understood: ExifTool reads it"), never a branch a shape, so a shape with no live
instance costs no code. A read-back never goes to the engine that made the write it checks.

## 3. What stays ExifTool (or Pillow), and why

| What | Live count | Why it stays |
|---|---|---|
| PNG and TIFF reads and writes | 43 rows (26 PNG: 25 + 1; 17 TIFF) | A parser each for 26 and 17 rows: each format is near CLAUDE.md's line of about 20 below which work is a one-off, not a feature. Their writes are made unchecked (DECISIONS 2026-11-03, "every photo write lands by write beside, compare, move", item 2). The Files owner's answer to question 6: keep. |
| HEIC | 0 | No live instance: no branch is written for it (CLAUDE.md). A HEIC added later is ExifTool's. |
| Clips' reads other than the header dates | 2,930 rows | Media's; L7 only if the owner wants ExifTool out of the clip path too (question 7). Clip writes are already own. |
| A JPEG the own engine refuses (a target property twice, UTF-16 packet, unparsable segment, two EXIF segments) | counted by the L1 gate; census: attribute form 0, two EXIF 4 of 3,000, IFD0 XMP 24 of 10,515 | A refusal falls to ExifTool, never "holds nothing" (a plan from nothing clears a field: sweep, risks). |
| The JPEG rewrite (a write that does not fit, bakes that do not fit, just-look) until L6b | every write not patched; extended XMP 111 / 80 / 5 of 3,000 / 2,065 / 1,308 sampled; MPF 100 / 14 / 0 | M5 of PLAN_OWN_METADATA not built. |
| The read-back of an own-engine write | every patch in place | Question 10 (recommended: keep): an independent author checks the own writer's escaping and character sets, through L6b at least. |
| Composite:LensID (maker-note lens tables) | 43 / 1,412 / 2 of the sweep's samples (EXIF:LensModel 37 / 1,412 / 2) | Not worth owning; question 4 (drop, or keep EXIF LensModel), decided before L4. |
| The Orientation a decode declares (Pillow `getexif`, images.py :84; `orientation.boxes_turn`, orientation.py :39) | every photo decoded | Read by Pillow as it opens the picture, the decode's own answer, never a field read of the index; judged by the rotation scenarios (section 1), not the conformance suite. Not moved behind `Fields`. |
| The oracle | every conformance item, metadata-check both sides | An independent reader is what a byte-level claim needs. Tests and metadata-check only, never a product answer. |

ExifTool stays installed for as long as any row above stays; the plan does not remove the dependency, it removes ExifTool
from the JPEG path (95.4% of rows) but for the read-back (question 10).

## 4. Stages (each one merge, about 1,200 added lines or fewer, trunk whole after each)

Each stage names its gate (what must be true before it merges), its rollback, and what it deletes. Order: L0a, M (on
question 5's yes), L0b, L0c (on question 9's yes), T1, L1, L2, L3, L5, L4, L6b, L7. L5 and question 4 come before L4,
because L4's whole read asks every METADATA_FIELD (50, the GPS pair, the location pair and LensID among them); question 8
comes before L1, because it decides L1's list.

**L0a. One face for the reads, no behaviour change but the configuration** (about 350 added lines, decided up front:
L0 as one branch was 17 session sites, 27 functions, the CLI and the MCP at once). `opened`, `Fields.read` / `read_one` /
`whole` (per-call dead for good), `oracle`, `runtime.fields`; every read site opens `Fields` (metadata :113, names :51,
relink_photos :40, tagging :597, refresh_rows, inspect, photos, the CLI's index read, metadata_check's oracle);
`MetadataExtractor` deleted with every importer in the same commit (tests/test_layers.py); metadata's record functions
stay as module functions and `tests/photo_rows.py` keeps calling them (a test's row made by the code that makes the real
one); metadata imports nothing of field_values; `photo_against_file`'s `exiftool_path` required. The contract diff above
for row 50 and the Files charter's item 9. Tests that patch `tagpup.files.exiftool_session.ExifToolSession` keep working
(the engine looks the class up at its start); the lines patching `metadata.ExifToolSession` move to it. Behaviour change,
named: every session gains the bake configuration (`-config`), the index's read (metadata.py :113) among them.
Gate: `tests/test_an_index_reads_a_bake.py` under `--real-exiftool` with a bake of 0, 1 and many faces (the index read
answers the bake fields as before under the configuration); the full suite green under replay. Replay keys include the
configuration file (exiftool_replay.py :223-226), so the first replay run after L0a on each machine records anew and is
slow: the suite's time is read from the second run, never the first. Rollback: a revert; nothing on disk changed.

**M. The mint as a field of the index's bake** (Files and Data; on question 5's yes; about 300 added lines, more deleted;
before L0b, so that L0b never builds `Fields.identity`). `baking.plan_photo` plans `XMP-xmpMM:DocumentID` (the bake's
read, `FolderRead`, asks it beside `bake.WRITTEN`): a photo the bake is handed (recorded this run, or owed with no
fingerprint row: the set `owe_first` makes owed, so a run stopped before the bake leaves it owed and the next index mints
it) that holds none is planned with a minted id; one whose bake is `Current` and holds none is planned with the id alone
(its bake fields as held, so a current bake is still never rewritten: DECISIONS 2026-10-27). The write is the folder's
existing internal change (`baking.INDEX_OPERATION`, `file_journal.INTERNAL`: one change per folder, never per file, in no
History, never undone, its UNDO_CHECKS row journal.py :103; erased when the folder finishes), recorded in the row by
`follow_fields` in the transaction that marks the file done (`DOCUMENT_ID_KEYS` -> `document_id`, photos.py :274-279), and
carried over a done rename by `_record`'s `_follow_rename` (file_changes.py :876), as every journaled write is. The
mint's own rules move with it: never into a photo that did not decode in full (the bake writes only after its decode,
`_Decode`, or the index's handed one for an unchanged stamp) and never into one whose tail is zeros (#407: today's
`incomplete` check, tagpup_cli.py :840-844, is the index loop's; `plan_photo` asks `images.tail_check` for a photo it
would mint, and only for it). `xmp_packet` already owns `xmpMM:DocumentID`, so a mint that fits is patched in place
from M on (today every mint is ExifTool's rewrite, a new file id and size: #1954), the rest is ExifTool's rewrite under
the picture check. Deletes: `IdentityWriter`, `field_values.ensure_document_id`, `OutsideWrites` (file_changes.py
:1373-1420), `_read_again` (tagpup_cli.py :1227) and `given`, the index loop's `give` (:848), INVARIANTS exception (1)
(docs/INVARIANTS.md :44), the CLI's `metadata` import. Behaviour changes, named for the owner: the mint is written after
the folder's batches are recorded, not before; an index by hand with `--skip-faces` mints nothing (it bakes nothing; the
server's index never passes it: no caller in tagpup/services or tagpup/jobs); a photo the bake cannot plan (no vector,
never examined, unreadable) is minted when it can be. Gate: `tests/test_a_rename_stopped_part_way.py`
(`AnIdentityTheIndexGivesTheRenamedPhotos`, today run with `--skip-faces`: run with faces stood in) green: the rename's
undo finds the minted photos; `tests/test_no_writes_to_damaged_photos.py` re-pointed at the bake and failing on a bake
that mints a zero-tailed copy; `tests/test_photo_identity.py` re-keyed; the undo matrix's bake writer
(`tests/test_undo_after_every_later_kind.py` :224, `a_bake`) given a photo with no DocumentID, so every earlier kind is
undone after a mint (no new kind: no UNDO_CHECKS row, no new WRITERS entry); a bake dry run on a copy of each library
counting the photos it would mint (told the owner as counted). Rollback: a revert (the mints already made are ordinary
DocumentIDs, read as any other). The two designs, costed: M adds one planned field to an existing internal change and
deletes five concepts; the alternative (`Fields.identity` journaled per mint) needs a journal shape for a photo with no
row yet (file_changes.py :572 records `photo_id` None; `follow_fields` returns None for no row, photos.py :243), a new
kind with its UNDO_CHECKS row (journal.py :89) and an undo-matrix writer, `OutsideWrites` threaded through `identity`
(F2), and one History change a mint (56,254 photo_index JPEGs hold none; History holds 11,633 changes and 108,105
change_files today) or a per-run batching the journal does not have.

**L0b. One face for the writes** (about 300 added lines). `Fields.write`, `read_back`, `together`, `ready_to_write`;
every write site opens `Fields` (file_changes :463/:1750/:1938/:2044, file_only :92, tagging :284, bake_after_index :149,
convert :344/:559/:659, metadata_check :351-352); `file_changes.read_whole` and `field_values.session` deleted with their
importers; the hand wraps of `StaysDead` become `together()` (file_changes, bake_after_index) or the per-call default
(metadata's went with L0a); the new LINE_RULE. The brief lists the callers whose one session spans several operations
today and what each keeps: `tagging.py :284` (a save: its read, the caption check, then `write_fields` given its session:
the write's `together()` ends at the write, the save's later calls answer again, as file_changes.py :464-466 says today),
`bake_after_index :149` (one `together()` for the run: a share away ends the run at that folder, as `baked.stalled or
et.dead` does today), `file_changes :463` (one `together()` a journaled write). The contract diff for rows 70 and 1, the
worker charter :79, INVARIANTS :20. On question 5's other answer, L0b also builds `Fields.identity(record, decoded=,
around=)` with `file_changes` its one caller (a service owning OutsideWrites: the CLI asks it), and its gate adds
`AnIdentityTheIndexGivesTheRenamedPhotos`. Gate: the full suite green under replay AND one `--real-exiftool` run (it
changes code that shapes commands: CLAUDE.md, owner 2026-11-24); the new ExifTool-will-not-start scenario test fails on
the old code and passes; `fake_exiftool` keeps standing in at the ExifToolSession boundary, below `Fields`, so its 26
users do not change. Rollback: a revert; nothing on disk changed.

**L0c. `et` and `exiftool_path` collapsed into `fields`** (deletion only, on question 9's yes; never over budget). The 27
functions taking `et` and the 81 taking `exiftool_path` take one parameter `fields`, opened per operation by the service
that starts the operation or by the entry point through `runtime.fields(library)` (= `field_values.opened(
runtime.exiftool(library))`; `Runtime.exiftool`, runtime.py :635-637, and `web/state.exiftool`, state.py :38-42, stay its
resolvers: `files` may import only `core`, tests/test_layers.py :30-40, so `opened` cannot find ExifTool itself). Never a
long-lived `Fields` on the Runtime (the drain at hand-over, and I1's per-call scope). Gate: suite green; the diff adds no
line beyond the parameter's rename. Rollback: revert.

**T1. One corpus and one conformance suite, ExifTool the only engine** (about 700 added lines; about 45 granular tests
lifted and deleted, about 800 lines: section 5). Written against `Fields` before any own reader exists, so the suite is
proved on the engine it will judge the others by. The brief states that every conformance write is deterministic: the
mint is seeded under REPLAY (`exiftool_replay.seeded_mint`, :499-507), and no item writes a time or a random value the
seed does not give, so its kept answers stay hits. Gate: green under replay and `--real-exiftool`; a measured fast-tier
time, baseline first (read from a second run). Rollback: revert (tests only).

**L1. Own JPEG reader, shadow only** (about 650 lines; sweep stage S0). `segment_edits.held(layout, header, wanted)`
reading through the three block modules, which gain: EXIF Make/Model (IFD0 0x010F/0x0110), LensModel/LensMake
(0xA434/0xA433), Rating (0x4746); the GPS IFD (pointer 0x8825; GPSLatitudeRef/GPSLatitude 0x0001/0x0002,
GPSLongitudeRef/GPSLongitude 0x0003/0x0004, rationals and refs answered as Composite:GPSLatitude/GPSLongitude's signed
decimals); XMP xmp:Rating, aux:Lens, tiff:Make/Model, extended XMP joined by GUID, XMP in IFD0; MIME by magic. Not the
location fields (question 8: dropped, 0 held). `metadata-check --reads`: on copies in a sandbox home, `--sample 3000` per
library (all four; every JPEG of the three smaller ones), own record against the oracle's on METADATA_FIELDS + bake fields,
differences counted by tag; KNOWN grown only from output seen. The `containers.read` guard (section 1). Gate: 0
unexplained differences on the four samples; the conformance suite passes with `_OwnJpegEngine` in ENGINES. No product
read changes. Rollback: revert; nothing on disk. Removes nothing: a read-only shadow, the fact L2 is decided on. Measures
(told the owner only as counted): ms a photo, own against ExifTool batched (2026-11-03: 23 vs 28). **Sunset, written**: a
shadow with no product caller is a branch with no instance; if no product read is sent to `_OwnJpegEngine` (L2 merged)
within one release of L1's install, `segment_edits.held`, the block modules' new read arms and `--reads` are deleted in
one deletion-only merge, and the gate's numbers are kept in docs/history.

**L2. Own reads for the owned fields** (about 400 lines). `_engine_for` sends a JPEG read whose every wanted field the
own engine answers to it: the pre-write plan, the undo check, the settle's plan, Smart Rename, relink, just-look, Apply
All (sweep sites 1, 4, 5, 8, 9, 10, 11). Not the read-back (sweep site 2): `read_back` stays with the engine that did not
write (question 10). A refusal falls to ExifTool in the same call. Gate: the L1 gate green on trunk; the scenario files of
F1, F3, F8 and D2/D3 green with the oracle on (T1). Rollback: a module constant `OWN_READS`, True; set False, every read
is ExifTool's again and nothing on disk changed. It is deleted in the first merge after L2 has been installed for a
release with no own-read difference reported: a switch kept beside a gate that holds is a branch with no instance.
Deletes: the clip header special case: `_read_headers` becomes `_OwnClipEngine`'s answer for those fields (one dispatch,
not two). What the owner sees: a save still starts ExifTool, for its read-back (question 10).

**L3. Bake fields** (about 500 lines; sweep S2). XMP-tagpup values and `mwg-rs:RegionInfo` read to the structure
`bake.region_text` canonicalises; FaceVectors from extended XMP. Live: 62,158 of 62,161 JPEGs hold a bake, so every index
read of a JPEG asks these fields. **L3 changes a product read**: the bake plan's read of every folder the index recorded
a photo in (`baking.FolderRead.of`, baking.py :173, one read of bake.WRITTEN's fields a folder, over the 62,158 baked
files as each folder is next indexed) and the index's own read of the bake (`whole(bakes=True)`) go to the own engine. It
is what takes ExifTool off the index's bake read, not a corner case. Gate: conformance items for a bake of 0, 1 and many
faces (enough to force extended XMP); L1's `--reads` with `bakes`; `tests/test_an_index_reads_a_bake.py` and
`tests/test_a_bake_is_planned_from_the_library.py` green with the oracle on. Rollback: the bake fields leave
`_OwnJpegEngine`'s answered set (ExifTool reads them again).

**L5. Drop unread fields** (deletion only; sweep S4; before L4; the owner's, questions 4 and 8). The location fields (XMP
City/State/Country, the IPTC location pair Province-State and Country-PrimaryLocationName: 0 held in every sample) and, on
question 4's drop, Composite:LensID / LensID, leave METADATA_FIELDS: no reader in tagpup/ or web/ (sweep; no page reads
raw_metadata, grep 2026-11-26). Rating and GPS stay (question 8). A row loses a dropped field from its raw_metadata at
its next re-read (the read replaces raw_metadata whole); nothing reads it.

**L4. The index's whole read** (about 600 lines; sweep S3). **ONE-WAY, the owner's** (question 3): what 62,161 JPEG rows
record at their next re-read. `Fields.whole` builds the row from the own record: grouped names only (question 3's
recommended answer), read_error, MIME, SourceFile in `paths.exiftool_spelling`'s form. On that answer it deletes
`structured`'s bare loop (metadata.py :58-59), the bare branch of `follow_fields` (photos.py :258-271) and of
`record_keyword_fields` (fields.py :435-438), and then `inspect._by_field`'s reason to exist (inspect.py :176-179);
METADATA_FIELDS' bare entries stay as what ExifTool is ASKED (a bare name asked is answered under every group).
Gate: **two fresh reads of the same file, never a read against a row** (54,690 of 60,355 photo_index rows hold no bare
key, so a stored row measures shape noise): for 3,000 unchanged files per library (every JPEG of the three smaller ones),
own `Fields.whole` against ExifTool's `Fields.whole`, both fresh, compared per field by `_by_field`'s rule (field name
alone); 0 differences in taken / tags / captions / document_id / year, and in raw_metadata only those the owner accepted
(MakerNotes:CreateDate under a bare name: 121 of 3,000 in photo_index; APP12: 1; both moot under grouped names only).
Never first run as a sync: a dry count, then the owner's yes. Rollback: `whole` back to ExifTool; rows already re-read keep
the own shape (they read the same by the gate: every reader reads grouped names first), so no migration and no backfill.

**L6b. Own rewrite** (PLAN_OWN_METADATA M5; about 1,000 lines, one merge or two cut by segment kind). A whole-file JPEG
rewrite beside the photo (make a missing segment, grow one, keep extended XMP, IFD0 XMP, two EXIF, bytes after 8BIM/EOI;
MPF length-stable or refused) with the picture check unchanged; the bake that does not fit and the mint that does not fit
are ordinary journaled writes by then (M), so there is no separate L6a. Gate: the 4d gate (`metadata-check --sample
3000`) on four libraries, 0 own-side differences; each live class taken or refused with a sentence the owner accepts;
every own rewrite read back by ExifTool (question 10). Then `_ExifToolEngine`'s JPEG write arm is deleted. Rollback:
before the deletion, the engine table; after, a revert. (On question 5's other answer, an L6a remains: the mint as an own
write needs its journaled record first, the alternative's costs in stage M.)

**L7. Clip reads (Media; optional)**. MP4/MOV XMP, QuickTime title, Xtra, keys, from `containers.Video`. Only on the
owner's yes (question 7).

One migration or stamp: none. One-way choices: M (the mint moved into the bake, patched where it fits), L4 (row shape),
L5 (dropped fields), L6b (files grow about 2 KB at a rewrite: PLAN_OWN_METADATA's open item). Each is asked before its
brief.

## 5. Tests: one corpus, one oracle, one conformance suite

**One corpus** (`tests/corpus.py`, extended, not a second module). Items named by recipe, made by the real writer and kept
across runs (as today). New: a **shape recipe** for each live class the census found, made by laying the class's segment
layout over a synthetic picture (PIL noise, `damaged_photos.whole_jpeg`), never an owner photo: extended XMP (111 / 80 /
5); MPF (100 / 14 / 0); bytes after EOI (10 / 3 / 0); two EXIF (4); no EXIF (2 / 0 / 3); Canon and Sony maker notes moved
(1,239; 258); IPTC with no 1:90 (746 / 4 / 0) and with UTF-8 (2,194 / 2,057 / 1,305); two APP13 (0 / 2 / 3); bytes after
the last 8BIM (1 / 4 / 6); IFD0 XMP (24 of 10,515); a bake of 0, 1 and many faces (62,158 JPEGs hold one); a GPS IFD
(Rating and GPS kept: question 8); a keyword holding `&` and one outside cp1252 (the escaping and character-set faults a
same-author read-back would hide, question 10); plus clips (moov first and last, uuid XMP, Xtra; Adobe's attribute-form
packet, live in clips only). Where a class exists only in owner files, its box layout is captured from one file as the
fixture (CLAUDE.md), every text replaced by fictional values of the same shape and length. Each recipe names the live count
that justifies it (no count, no item: a JPEG attribute-form packet (census 0) and a 64 KB packet (not counted) are not
items until counted). About 24 items.

**One oracle**: ExifTool, through `field_values.oracle`, replayed (`exiftool_replay`) in the normal run and real under
`--real-exiftool`. An item's expected answer is never written by hand: it is what the oracle reads. Hand-written
expectations stay only where TagPup decides (precedence, `fields.as_patched`, a refusal's sentence).

**One conformance suite** (`tests/test_a_photos_fields_read_and_written.py`, owner Files, mapped in `tests/owners.py` as a
scenario file, a new scenario **F12** "a photo's fields read and written the same by every engine": F1 to F11 are the
Files ids in `tests/owners.py` SCENARIOS today, so F12 is the next unused; added to SCENARIOS and to the Files charter's
`Scenario ids:` line in the same commit, which `tests/test_the_test_map.py` :220-224 checks): for every corpus item x
every engine in `ENGINES`:
1. read every METADATA_FIELD and bake field: equals the oracle's, or the engine refuses the file whole; `whole`'s SourceFile
   equals the oracle's (`paths.exiftool_spelling`);
2. for each operation (add/remove a keyword, caption, title, Date Taken, a shift, a turn, a mint (a bake field from M),
   PreservedFileName, a bake, an undo of each): write, then the oracle reads the target fields as `fields.as_patched`
   says, every other field as before; picture bytes equal; every non-target segment byte-equal; idempotent (write twice:
   no changed range); the write's `read_back` was answered by an engine other than the writer;
3. the failure cases once per engine: a truncated copy, a file held open, a batch with one bad file, and a share that does
   not answer (StaysDead/Away stand-ins already in the tests): it fails the file and every later file in THAT call, and the
   next call on the same `Fields` answers (inside `together()`, every later call of the block fails at once).
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
test_the_two_writers_compared_on_copies 20) stay: crashes, undo, two at once, the journal, metadata-check, and the
independent read-back. Of them only "a turn reads as ExifTool's write of it reads" and the IFD0-packet pair (3) restate a
conformance claim and may go. `fake_exiftool` (26 users, a table with no bytes) stays the stand-in at the ExifToolSession
boundary where a test needs a hook between read and write (`on_read`, `on_write`); a test that only needed fields cheaply
may move to a corpus copy and the own engine (from L2), which needs no process.

**Effect on the 297 s suite**: not claimed. Replay already answers most ExifTool commands; what the own engine saves in
the normal run is the misses (new bytes) and process starts. What it saves is real in `--real-exiftool` runs (sweep: 80% of
the 537 starts in the undo matrix are product reads; the read-backs stay ExifTool's under question 10's recommended
answer). The conformance suite must stay in the fast tier on the own engine with the oracle replayed; T1's brief sets a
budget from a measured run, baseline first (DEVELOPMENT.md, "Performance work"); after L0a's re-keying, the time is read
from the second run.

## 6. Failure modes

- **Interrupted part way**: reads write nothing. A patch keeps today's rule (record committed before the first byte,
  settled by bytes: INVARIANTS, "Writes", the two rows on a photo write and a patch in place); L6b's rewrite keeps `-o`
  beside the photo and the picture check: a kill leaves the photo whole and a scratch file the scratch rules settle (#2180).
  A mint (M) is a field of the folder's internal bake change: a kill mid-folder is settled at the next start as a bake is
  (by bytes for a patch, the scratch rules for a rewrite); the photos not reached stay owed and the next index mints them.
- **Two at once**: two runs reading one file: harmless. A read during another process's patch can see half a header:
  the own engine reads the header in one bounded open, and a header that does not parse is a refusal that falls to
  ExifTool, never an empty record. Writes keep the per-file lock and the "ExifTool's new file beside it" rule. A mint
  races a save as a bake does: the bake reads each file again just before its write, and one changed since is a conflict.
- **Unreadable file**: Unreadable with ExifTool's or the own engine's reason; the index records `read_error` and stamps it
  (not read every run), as today. An own refusal is never Unreadable: it is a fall-back.
- **ExifTool will not start** (L0's lazy start): in a read, each file is `unread` NOT_STARTED in `whole` and Unreadable in
  `read`, as `MetadataExtractor` answers today. In a write, `ready_to_write` refuses the whole change with one sentence
  before its record is committed: a lazy start must not move the failure after the record (INVARIANTS: record before the
  first byte), where it would leave a half-done change the settle thread retries at every start. This matters from L2 on,
  when the plan's read no longer starts ExifTool, and already at L0b for callers that pass `held`. In the bake (M), the
  folder's write is refused and its photos stay owed: no mint, never a failed index.
- **Real data's shape**: every census class is a corpus item with its live count; a class not censused (multi-language
  Alt, UTF-16 packets, OffsetTime, mu_dance as a whole) is counted by the L1 gate's run before L2's brief. raw_metadata's
  stored shape is not the gate's reference (L4: two fresh reads).
- **A share that does not answer**: per call (I1): own reads go through the bounded open (`in_place._Lent.bounded` /
  `shares`) and ExifTool's through the deadline; NotAnswered for that file and every file after it in the call (one read
  waits one deadline); the next call answers again, so a CLI index run that met one hiccup reads its next batch. Inside
  `together()` (a journaled write, a bake run) every later call of the block fails at once, as `StaysDead` does now.
- **A fault both of one author's sides share** (an `&` in a keyword, a character outside cp1252): the read-back is another
  engine's (question 10), and the corpus holds both cases.
- **What the owner sees**: nothing at L0a-L3 (same answers, proved), but at M: identities appear after the folder's batches
  are recorded, patched in place where they fit (no new file id for those), none from a `--skip-faces` index. At L4 a
  row's raw_metadata loses its bare copies at its next re-read (5,665 photo_index rows hold them, 2,169 kr-track, 1,284
  mu_dance, 1,305 renton_parkrun; every reader reads grouped names first, so taken, tags, captions and search read the
  same: 0 photos of the four hold a bare date with no EXIF:DateTimeOriginal). A refusal is invisible (ExifTool answers).
  A write refused by L6b's rules says a sentence in History, as a refusal does now.

## 7. Risks, owner questions, recommendation

**Risks.** (1) A plan read that misses a field records a wrong `before`, and the write and its undo use it: a date or
title misread is overwritten (sweep, risks). Hence L2 only after a gate on four libraries, owned fields first, the index
last. (2) The corpus agreeing with the code that made it: shapes are laid from census classes and checked against the
oracle, never against the reader under test; a write is never read back by its own author (question 10). (3) A stale
replay answer after an ExifTool upgrade: excluded by the version key; the oracle's real run is the gate. (4) L0a and L0b
touch 17 session sites, 27 functions' `et` and the index: mechanical but wide, hence cut in two up front, each with one
reviewer pass plus the Data owner for `whole`'s row shape. (5) Gains are mostly structural; per-photo speed was measured
close (23 vs 28 ms batched), so the case for L2-L6 is ownership and removing the rewrite, not speed. (6) M moves an
automatic write on the owner's photos (the mint) into another writer: its population and timing are named in stage M and
counted by a dry run before its merge.

**Open questions only the owner can answer.** Each: what each answer changes, the plan's recommendation, and whether the
owners' review changed it.

1. Is the own reader still wanted after replay brought the suite to 297 s (work row 2026-11-24: "the own reader may not
   be needed")? L0a-L0b and T1 stand either way.
   Yes: L1 (read-only shadow) is built, and L2-L3 follow on its counted differences. No: the plan ends at T1, ExifTool the
   only engine behind the face, DECISIONS 2026-11-03 amended to say so. **Recommended: yes to L1, with its written
   sunset** (deleted in a deletion-only merge if no product read takes it within one release); decide L2 on its gate's
   numbers. Changed by the Files owner: the sunset is added, since a shadow with no product caller is a branch with no
   instance.
2. mu_dance (1,284 rows, 1,280 JPEG) is a fourth library: in every gate?
   Yes: every gate reads all 1,280 of its JPEGs (about 1,300 more file reads a gate). No: a shape only it holds meets L2
   unproved (it was never censused). **Recommended: yes** (unchanged; the Files owner agrees): cheaper than a census,
   and live.
3. L4: what does raw_metadata record? (a) grouped names only; (b) bare names answered by EXIF or XMP, never a maker note;
   (c) ExifTool's shape for good.
   (a): L4 deletes `structured`'s bare loop, the bare branches of `follow_fields` and `record_keyword_fields`, and later
   `inspect._by_field`; a row loses its bare copies at its next re-read (5,665 / 2,169 / 1,284 / 1,305 rows hold them;
   54,690 of 60,355 photo_index rows hold none already); readers read grouped names first, so nothing the owner sees
   changes (0 photos hold a bare date without EXIF:DateTimeOriginal; the clips' bare CreateDate is QuickTime's and the
   clip rule reads `QuickTime:CreateDate`); the maker-note CreateDate question is moot. (b): the bare names stay and need
   an order ExifTool does not give: where a bare name exists today it is XMP's copy (bare CreateDate = XMP:CreateDate in
   kr-track 2,148 of 2,169, mu_dance 1,280 of 1,284, renton_parkrun 1,304 of 1,304, photo_index 1,006 of 5,562), so "EXIF
   wins" would change about 5,700 rows; the bare-copy rule stays written twice. (c): ExifTool reads every JPEG at every
   index for good; L4 is not built, L6b can still retire the rewrite. **Recommended: (a) grouped names only, the gate two
   fresh reads of the same file**. Changed by the Data owner: the earlier recommendation (b) was unordered, and its gate
   compared a fresh read with stored rows that mostly hold no bare name. Asked again with L4's dry count in hand.
4. Composite:LensID (maker-note tables; 43 / 1,412 / 2 of the samples): drop it, or keep EXIF LensModel as the lens?
   Drop: the gear search loses a lens name only where LensID is text and no LensModel is held (photo_index sample: at
   most 6 of 3,000; a LensID number is already ignored by `photo_meta.gear`); L5 removes it from METADATA_FIELDS. Keep:
   ExifTool reads those files' lens for good, or TagPup owns maker-note tables. **Recommended: drop LensID, the lens is
   EXIF/XMP LensModel** (unchanged), now asked before L4, whose whole read would otherwise ask it.
5. The mint: where is it journaled?
   (a) As a field of the index's bake (stage M): one internal change per folder, never per file, in no History, never
   undone; deletes `IdentityWriter`, `ensure_document_id`, `OutsideWrites`, `_read_again` and INVARIANTS' exception (1);
   no new kind, no new UNDO_CHECKS row; a mint that fits is patched in place at once. It changes when a photo is minted
   (after its folder's batches are recorded), mints nothing in a `--skip-faces` index or in a photo the bake cannot plan
   until it can, and mints only the photos the bake is handed or owes (today's population: a photo the index records), not
   the rest of each folder (the 56,254 photo_index JPEGs without an id are minted as each is next recorded, as today).
   (b) Journaled per mint, by `Fields.identity` (L0b, L6a): a new kind, its undo check and matrix writer, a journal shape
   for a photo with no row yet, `OutsideWrites` threaded through it, and one History change a mint unless the journal
   gains a per-run batch. (c) Stays ExifTool's unjournaled rewrite: L6b cannot retire the rewrite. **Recommended: (a), a
   field of the index's bake**. Changed by the Data and Files owners: the earlier "journaled" named no journal shape, and
   a journaled mint before the row exists records nothing; both owners answer "one internal change per folder, never per
   file". Sub-choice, the owner's: mint every photo of a touched folder that holds no id (more writes now, up to 56,254 in
   photo_index as folders are indexed) instead of today's population; recommended: today's population.
6. PNG/TIFF (43 rows): keep ExifTool for them, or refuse their writes with a sentence?
   Keep: nothing changes. Refuse: 43 files can no longer be tagged from TagPup, and ExifTool stays installed anyway for
   clips and the oracle. **Recommended: keep** (unchanged; the Files owner's answer).
7. Clips' reads (2,930 rows): own (Media, L7), or ExifTool's?
   Own: Media builds MP4/MOV XMP, QuickTime title, Xtra and keys reads (one more merge and gate). ExifTool's: no change;
   clip writes stay own. **Recommended: ExifTool's for now** (unchanged); reopen after L4 has proved the whole-read shape on
   JPEGs.
8. L5: drop Rating/GPS/location from the read (no reader; Rating in 97% of photo_index)?
   Drop all: L1 need not read Rating (EXIF 0x4746, xmp:Rating) or the GPS IFD; rows lose them at their next re-read. Keep:
   L1 reads them (the GPS IFD's rationals and refs are on its list). **Recommended: drop location only** (0 held in every
   sample: a field with no instance); keep Rating and GPS, held and the owner's data, cheap to read. Unchanged; the Files
   and Data owners both answer so. Asked before L1, whose list it decides.
9. `et` and `exiftool_path` (27 and 81 functions): collapse them, or leave them?
   Collapse (L0c, deletion only): one parameter `fields`, opened per operation by the service or by the entry point
   through `runtime.fields(library)` (the resolver stays `config.exiftool_path(settings.exiftool)` via `runtime.exiftool`
   / `Runtime.exiftool` / `web/state.exiftool`); never a long-lived `Fields` on the Runtime. Leave: two parameters stay
   threaded beside each other. **Recommended: collapse, as its own deletion-only cut after L0b**. Changed by the
   integration owner: the earlier "let `opened` find ExifTool itself" cannot be built (`files` may import only `core`,
   tests/test_layers.py :30-40), and a library's own ExifTool setting is read through the runtime (set in 0 of the 4
   libraries today: `paths.exiftool` is empty in all four).
10. (New, the Files owner's.) The read-back after an own-engine write: keep it cross-engine (ExifTool reads back what
   the own writer patched), or accept the conformance suite and metadata-check as the only independent check?
   Keep: `read_back` never asks the writer; a save, a bulk edit and a bake still start ExifTool for their read-back
   through L6b (L2's gain is the plan and undo reads, not the process start); a shared escaping or character-set fault
   (an `&` in a keyword) is caught on the owner's photos before `done`, and the patch is put back. Accept: the own engine
   reads back its own patch, ExifTool leaves the save path at L2, and such a fault reaches the owner's files whenever the
   corpus lacks its case. **Recommended: keep, through L6b at least**, asked again at L6b's gate with its numbers.

**Recommendation.** Build **L0a** first: one face for the reads (`field_values.opened` -> `Fields`, `whole`, `oracle`),
`MetadataExtractor` behind it. Ask question 5 now: on (a), build **M** next (the mint into the bake, deleting
`IdentityWriter` and `OutsideWrites`), then **L0b** (the writes, `read_back`, `together`, `ready_to_write`, the guard).
Then **L0c** on question 9's yes, and **T1** (the corpus and the conformance suite on ExifTool alone), which turns about
45 granular format tests into rows of one suite and gives any later engine its bar. Ask questions 1, 2, 8 and 10 before
L1; build L1 on a yes to question 1 with its sunset, and L2-L3 on L1's numbers; ask question 4 before L5 and question 3
before L4; L4-L6b each wait for their one-way answer.

## 8. Checked against (the completing design pass and the review pass, 2026-11-26)

Verified on trunk 3badca98 by grep, AST and reading the code; live numbers counted read-only this session:
- Every count in sections 0-1 (sessions 9 + 8, `et` in 27 functions, `exiftool_path` 81 / 216 / 27, `containers.read` 5,
  `_whole`'s two commands, `READ_BATCH` 200, test files 53 / 26 / 24 / 9 / 24, the six granular files 175 tests) and every
  line number cited; corrected where the first draft was wrong (sessions said 7, file_changes lines 1930/1742/2011,
  "18 calls in 7 modules" of write_fields, 22 read call sites, 52 test files, 213 / 28 / 82).
- The live rows of all four libraries, the JPEG and clip totals, the bake count and the DocumentID count (section header).
- Every module, function and test file the plan names exists (the new ones are marked new).
- CLAUDE.md, "one owner for each rule, with its guard": each mechanism of section 1 has one place and a guard; the
  session rule makes the oracle's own `\.execute\(` rule unnecessary (it would also have matched every SQL call). The
  identity guard (`test_photo_identity`) is re-keyed, not lost. INVARIANTS' ExifToolSession row and CLAUDE.md's "never
  construct ExifToolHelper or ExifTool" line gain "and ExifToolSession only in field_values" (CLAUDE.md is the main
  session's edit).
- CLAUDE.md, "a special case needs a real instance": every engine class, corpus item and stage cites a live count; HEIC
  (0) and JPEG attribute-form packets (0) get no branch and no item; refusals are one arm, not one per shape; `opened` has
  no None arm (no product caller passes None); L1 carries a sunset. Not met yet: multi-language Alt and UTF-16 packets are
  uncounted (L1's gate counts them before L2).

Re-verified by the review pass on trunk d98a2a47 (the three owners' points, section 9), by reading the code and counting
every row of the four libraries read-only (`db.readonly_uri`):
- The read-back: `_verified` (file_changes.py :1066) -> `read_back` -> `_read_whole` (:1234) -> `read_whole` (:1240) ->
  `field_values.read`: ExifTool.
- The mint: `IdentityWriter` built at tagpup_cli.py :620 with `OutsideWrites(...).around`; `give` at :848 mid-batch;
  `_read_again` at :880 and :1227; `give` puts `XMP:DocumentID` into the record at metadata.py :251; `OutsideWrites` at
  file_changes.py :1373; `_record` calls `_follow_rename` (:876) for every journaled write, internal changes too;
  `follow_fields` returns None for a path with no row (photos.py :243) and sets `document_id` from `DOCUMENT_ID_KEYS`
  (:274-279); `file_journal.plan` takes `photo_id` None for a path with no row (file_changes.py :572);
  `baking.INDEX_OPERATION` is `file_journal.INDEX_BAKE`, in `INTERNAL`, with its UNDO_CHECKS row (journal.py :103); the
  undo matrix's `a_bake` writes the retired `baking.OPERATION` (tests/test_undo_after_every_later_kind.py :189-236);
  `xmp_packet.PROPERTIES` holds `xmpMM:DocumentID` (:43); the index bakes nothing with `--skip-faces` (tagpup_cli.py
  :1027), and no service passes `--skip-faces`; `AnIdentityTheIndexGivesTheRenamedPhotos` runs the index with
  `--skip-faces` (tests/test_a_rename_stopped_part_way.py :362); the incomplete-copy check is the index loop's
  (tagpup_cli.py :840-844) and the bake has none of its own today (no `tail_check` or `ZERO_TAIL` in baking or
  bake_after_index).
- The sessions: only `field_values.session` and metadata_check's oracle pass `bake.config_path()`; metadata.py :113 does
  not; `ExifToolSession` starts at `with` (exiftool_session.py :382-409); `StaysDead` (:424) wrapped at metadata :114,
  bake_after_index :150, file_changes :467.
- The layers: `files` may import only `core` (tests/test_layers.py :30-40); `cli` may not import `files`; PROGRAMS_TODAY
  `tagpup_cli.py` 6, one of them tagpup_cli.py :66; `runtime.exiftool` (runtime.py :348-351), `Runtime.exiftool`
  (:635-637), `web/state.exiftool` (state.py :38-42) over `config.exiftool_path` (config.py :103-113); metadata.py :15
  imports field_values.
- `also` + `records` users: names.py :52, tagging.py :405-406, and `read_whole`.
- Scenario ids: tests/owners.py SCENARIOS holds F1-F11 for files; the Files charter's `Scenario ids:` line (:94);
  tests/test_the_test_map.py :220-224.
- `inspect.photo_against_file(..., exiftool_path=None)` (inspect.py :186, :202); its one product caller passes
  `runtime.exiftool` (tagpup/mcp/server.py :162).
- METADATA_FIELDS has 50 entries, among them Composite:GPSLatitude/GPSLongitude, the IPTC location pair and
  Composite:LensID; Pillow reads Orientation at images.py :84.
- Counted, every row: schema 39 in all four; `paths.exiftool` empty in all four; photo_index changes 11,633, change_files
  108,105; rows with no bare key 54,690 / 17 / 0 / 4 (photo_index / kr-track / mu_dance / renton_parkrun); XMP:Subject
  without bare Subject 54,819 / 165 / 2 / 3; bare CreateDate equal to XMP:CreateDate 1,006 of 5,562 / 2,148 of 2,169 /
  1,280 of 1,284 / 1,304 of 1,304 (photo_index's equal to EXIF:CreateDate: 2,239); photos (kind photo) holding a bare date
  with no EXIF:DateTimeOriginal: 0 in all four (clips: 1,934 / 5 / 4 / 0, dated by the clip rule's grouped fields);
  SourceFile in the `@<root>/` form 59,968 of 60,355 / 1,799 of 2,186 / 0 of 1,284 / 1,309 of 1,309; photo_index JPEGs
  with no document_id 56,254, the other three 0.
- Not verified: the about-5,700 rows "EXIF wins" would change (summed from the XMP-equal counts, not counted against the
  EXIF copy); the count of photos stage M would mint at the next index (its dry run counts it); whether a handed digest
  of a photo whose vector was kept by its stamp is "decoded in full" in #407's sense (the Files owner's call, in M's
  brief); the line estimates of L0a, M, L0b (each brief counts its own).

## 9. Review log (2026-11-26: the Files, integration and Data owners)

Files owner:
- F1 (read-back made by the writer at L2): folded. `Fields.read_back` (section 1, 2), L2 excludes the read-back, question
  10 added (recommended keep), corpus items for `&` and non-cp1252 keywords, conformance step 2.
- F2 (`Fields.identity` drops `OutsideWrites.around`): folded, then made moot by D3 on question 5 (a): the mint is a
  journaled bake field carried by `_record`'s `_follow_rename`. On answer (b), `identity` takes `around` and the test
  gate stays `AnIdentityTheIndexGivesTheRenamedPhotos`, now M's gate too.
- F3 (`_caption_problem` a third `also`+`records` user): folded (sections 0 and 2).
- F4 (L4 before L5/Q4; GPS IFD missing from L1): folded. Order L5 before L4, questions 4 and 8 asked first; the GPS IFD on
  L1's list; location dropped from it.
- F5 (Pillow Orientation outside the face): folded (sections 0, 1, 3).
- F6 (import cycle for `whole`): folded. `whole` in field_values over metadata's module functions; metadata stops
  importing field_values; the os.stat exemptions named (:47 changes at M, :48 stays with `_stamp`).
- F7 (StaysDead inside `opened` ends restart-between-commands): folded with I1: per-call scope, `together()` for the two
  spanning writers, the callers listed in L0b's brief.
- F8 (L3 changes a product read; L1 sunset): folded (L3's text, L1's sunset, question 1).
- Answers Q1, Q2, Q5, Q6, Q9: folded into the questions; Q1 gains the sunset, Q5 changes (with Data), Q9 changes (with
  I3), Q2 and Q6 unchanged.

Integration owner:
- I1 (dead-for-good for a Fields' life): folded. Per call; `together()`; conformance step 3; failure modes.
- I2 (contract-table diff): folded (section 1, "Contract-table and charter diff").
- I3 (Q9 cannot be built as written): folded. L0c and question 9 rewritten: one parameter `fields`, `runtime.fields`.
- I4 (lazy start moves a write's failure after its record): folded. `ready_to_write`, its scenario test, failure modes.
- I5 (L0 gains `-config`; replay keys change): folded into L0a's text and gate.
- I6 (the one caller of `identity` is a service): folded for answer (b); moot on (a) (M deletes it; PROGRAMS_TODAY 6 to 5
  either way, the CLI's metadata import leaving).
- I7 (F12, not F20): folded (section 5).
- I8 (`opened(None)`): folded. No None arm: `exiftool_path` required; the MCP already passes `runtime.exiftool`.
- I9 (L0a/L0b decided up front; T1 deterministic): folded (L0a, L0b, T1's brief).

Data owner:
- D1 (L4's gate against stored rows): folded. Two fresh reads of the same file per field by `_by_field`'s rule; counts
  re-verified (54,690; 165 / 2 / 3; 2,148 / 1,280 / 1,304; 0 photos with a bare date and no EXIF:DateTimeOriginal).
- D2 (grouped names only): folded as question 3's recommended answer (a), with what it deletes and its row counts.
- D3 (the mint as a field of the index's bake): folded as stage M and question 5's recommended answer (a), verified: the
  internal change, its undo row, `_follow_rename` in `_record`, `follow_fields`' `document_id`, the row-less plan. Added
  by the review pass: the zero-tail rule must move into `plan_photo` (the bake has none today), `--skip-faces` mints
  nothing, the gate test runs without `--skip-faces`, the mint population is the bake's handed and owed photos, and a
  Current bake gets the id alone. Both designs costed in stage M.
- D4 (four places shape raw_metadata): folded (section 0, the "Shape a row's raw_metadata" row).
- D5 (SourceFile spelling): folded (section 1 row, `whole`'s docstring, conformance step 1; 59,968 re-counted).
- Answers Q3, Q5, Q8 and "schema 39, no migration": folded; Q3 and Q5 changed, Q8 unchanged, schema re-counted.

Rejected: none. Narrowed: I1's "scope to ONE call" is kept as the default, with `together()` for the two writers whose
batch must wait one deadline in all (a journaled write of many files, a bake run), as `StaysDead` gives them today.

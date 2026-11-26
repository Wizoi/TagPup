# Own metadata library: TagPup reads and writes every photo's and clip's fields itself, ExifTool only in the tests
Live count (counted read-only this pass, 2026-11-26, `db.readonly_uri`; the segment census of every JPEG of the four,
`scripts/census_segments.py --sample 100000`): four libraries, 65,134 rows. photo_index 60,355: 57,392 JPEG, 2,921 clips
(.mp4 1,745, .mov 1,173, .m4v 3), 25 PNG, 17 TIFF; kr-track 2,186: 2,181 JPEG, 5 clips; renton_parkrun 1,309: 1,308 JPEG,
1 PNG; mu_dance 1,284: 1,280 JPEG, 4 clips. JPEG 62,161 (95.4%), clips 2,930, PNG 26, TIFF 17, WebP 0, HEIC 0, a clip of
an old format (.wmv, .mpg, .avi, .mod) 0. What each format holds, and every case the library must take or refuse:
section 3.
Domain: Files
Domains: Files (the library, JPEG, PNG, TIFF), Media (clip reads, stage C1), Data (the row's shape and the mint's journal
shape: stages M and G), integration (the entry points, the settings and the install: stage X); integration reviews the
seams

Work row #3376. Inputs: docs/work/3376.md (the workers' notes and decisions), docs/work/3376-sweep.md (the Files owner's
sweep, 2026-11-24; where it is stale, section 0 here is the trunk's), docs/PLAN_OWN_METADATA.md (M1-M7: the own patch,
built), docs/domains/files.md ("JPEG segment census, 2026-11-03"), and the owner's decisions of 2026-11-26 (section 7).
Owner's requirement (2026-11-26): the library CENTRALIZES all tag reading and writing (one library, one interface, one
access path) AND the tests (one corpus, one oracle, one conformance suite).

**What the owner decided (DECISIONS 2026-11-26).** The library is TagPup's own, for every format the libraries hold: every
partial and whole read and write of a photo's or a clip's fields, XMP first, every other EXIF, IPTC and box byte a file
holds retained untouched. ExifTool leaves the product: it is a development tool for the tests and the checks (the
oracle, the conformance suite, metadata-check on copies), never a fallback. A file the library cannot read or write is
refused with a sentence, never handed to ExifTool. Not urgent: the stages after L0b and T1 are ordered here, none
pressing. A gate is the test corpus and its conformance suite, never a production library.

Steady-state writer: none new. Every read and write of a photo's or clip's fields goes through `Fields` (section 2); a
photo added or changed afterwards is read by the index and written by `file_changes.write_fields` as today, and from
stage M its identity is minted by the index's bake, the one writer that already writes a field of every indexed photo.
One-offs: none. No command, backfill, migration or stamp: a row takes stage G's shape at its next ordinary re-read.

## 0. Where it stands (trunk 9e377388, 2026-11-26; L0a merged)

**L0a is built** (merged a723cb27 and the commits before it, on trunk since b503654b; docs/work/3376.md, two branches,
+455/-395 and +255/-111, no review round): `field_values.opened(exiftool_path)` -> `Fields` (`read`, `whole`), opened per
operation, ExifTool started at its first command (never at `opened`); `runtime.fields(library)` (runtime.py :354) for an
entry point; `field_values.oracle` -> `Oracle.whole` (WHOLE_READ, READER_READ, READER_SAYS moved from metadata_check);
`MetadataExtractor` deleted with every importer (0 names left); metadata's record functions public
(`stamp_before_read`, `structure`, `empty`, `structured`, `raw_metadata`) and metadata imports nothing of field_values;
`IdentityWriter`, `BAKE_FIELDS`, `BAKE_PARAMS` moved into field_values; `inspect.photo_against_file`'s `exiftool_path`
required (inspect.py :186); every read site opens `Fields` (names :50, relink_photos :40, tagging :406 and :599,
refresh_rows :38, photos :238 and :795, inspect :202, the CLI's index read through `runtime.fields`); the rule
"ExifToolSession constructed outside field_values" (`tests/test_rules_have_one_owner.py` :100-124) with its `ALLOWED`
list of the three write sites L0b takes (file_changes :1938, file_only :92, tagging :284), which fails a listed line that
is gone ("take it off the list"). Every read session now carries the bake configuration (`-config`), the index's
among them. Decisions the workers made, kept by this plan: (1) dead for good after a timeout is `read`'s own rule for
ONE call (`stalled` ends the call; the next call restarts ExifTool), so `Fields` holds no `StaysDead`; (2) `Fields.read`
RAISES a failed start (a reader taking "every photo Unreadable" would decide something wrong of each: sync's pairing
would pair nothing), while `whole` answers each file NOT_STARTED, unread; (3) `Fields.read` keeps `also` + `records`
for its users (`whole`, Smart Rename names :50, `_caption_problem` tagging :406, relink_photos :40) until stage G's
grouped names make them answer by field alone; (4) no `Fields.read_one` yet: its 6 callers are all on write sessions
(L0b); (5) the save's read at tagging :284 shares its session with the write (L0b).

**What is still ExifTool's in the product**, by place (grep and AST on trunk 9e377388):
- **Sessions outside `opened`**: `field_values.session(` at 8 sites in 4 modules (bake_after_index :149, convert :344,
  :559, :659, file_changes :463, :1750, :2044, metadata_check :323), the three `ALLOWED` `ExifToolSession(` lines, and
  `IdentityWriter._session` (field_values.py :863, a session without the bake configuration). `StaysDead` wrapped by hand
  at bake_after_index :150, file_changes :467 and :1751. `et` is a parameter of 26 functions outside field_values (120
  uses in 8 files; 34 with field_values); `exiftool_path` of 85 functions (227 uses, in tagpup/ and tagpup_cli.py).
- **Every read of a photo's fields** but a clip's three QuickTime header dates (`_read_headers` ->
  `in_place.headers_held`, put over ExifTool's answer; an old-format clip keeps ExifTool's values). The Orientation a
  decode declares is Pillow's (`images._boxes_turn`, images.py :84): the decode's own answer, not a field read, it stays.
- **The read-back** before `done`: `file_changes._verified` -> `_read_whole` -> `read_whole` -> `field_values.read`:
  ExifTool reads back what the own patch wrote.
- **The JPEG rewrite** (`field_values.write` -> `_command`, `-o` beside the photo, `_refuse_dropped`, the picture check
  `_landed`): every JPEG write the own patch does not take (section 3 lists them; the bake's every write and the mint
  among them, since `fields.EDITED_AS` names neither DocumentID nor a bake field), every write given no `saving` (a
  just-look photo's, `file_only`; the index's mint, `IdentityWriter`; metadata-check's ExifTool side), and every PNG
  and TIFF write (unchecked, DECISIONS 2026-11-03 item 2). A clip is never ExifTool's to write (`_clip_written`).
- **The mint**: `IdentityWriter` (field_values :802) built at tagpup_cli.py :621 around `file_changes.OutsideWrites`,
  given per photo mid-batch, the batch read again by `tagpup_cli._read_again` (:1228); INVARIANTS exception (1).
- **Around the face**: the library setting `paths.exiftool` (core/validation.py :343; stored empty in 4 of 4 libraries)
  and its settings group `exiftool` (:275, "every read and every write of a photo file goes through this program"),
  `config.exiftool_path` / `default_exiftool` (config.py :95-113), `runtime.exiftool` (:348), `Runtime.exiftool` (:641),
  `web/state.exiftool`, settings_routes' `exiftool_found` (:36), the MCP tool's "read with ExifTool" (mcp/server.py
  :155), `processes.py`'s notes on ExifTool's child, `pyexiftool` in requirements.txt :12.

**The own parts already built**: the own patch of a journaled JPEG write that fits (`segment_edits.new_header` over
`xmp_packet`, `exif_block`, `iptc_block`, laid by `in_place.patch_jpeg`; 4d, 2026-11-16), and a clip's whole write
(`segment_edits.new_packet`, `in_place.patch_clip`, `clip_patch`; M7). `containers` lays out a JPEG (segments, EXIF IFDs,
maker notes, 8BIM, IPTC records) and a clip (`containers.Video`). The three block editors read 9 + 9 + 3 fields.

**Tests**: `ExifToolSession(` in 53 test files, `fake_exiftool` (a stand-in at the ExifToolSession boundary, a table of
fields, no bytes; it turns `PATCH_IN_PLACE` off) in 26, `corpus` in 25. The format tests are granular, one file per
module: test_container_layout 26, test_xmp_packet_edits_what_it_owns 23, test_exif_block_edits_what_it_owns 15,
test_iptc_block_edits_what_it_owns 16, test_segment_edits_choose_what_to_write 25, test_metadata_extraction 70 (175);
beside them the scenario files test_a_photo_written_by_its_own_editors 16, test_the_two_writers_compared_on_copies 20,
test_a_file_holds_what_exiftool_kept 28. Record/replay (#3375, `tests/exiftool_replay.py`) answers ExifTool from kept
answers; the suite is 297 s with it.

## 1. Mechanisms: the ONE place each lives

| Mechanism | The ONE place (module, function) | Current owners | Guard that fails on a second |
|---|---|---|---|
| Open the library for an operation | `field_values.opened(...)` -> `Fields` (L0a, built), opened per operation, never kept on the Runtime; an entry point through `runtime.fields(library)`. Takes `exiftool_path` until X, nothing after | built; the write sessions until L0b | LINE_RULE "ExifToolSession constructed outside field_values" (built), its ALLOWED list emptied by L0b; at X a rule that `exiftool` and `exiftool_session` are imported by no product module but the oracle's |
| Which code answers a file | By its format, read from its first bytes (`field_values._format_of`, new J2): JPEG, PNG, TIFF, MP4/MOV. A format's reads are either wholly ExifTool's (not moved yet) or wholly the library's; a JPEG's writes move in three merges (J3a-J3c), routed by the header's layout and the fields named, decided before any write and deleted by J3c. Never an "own, else ExifTool" arm: a moved format's file the library cannot read or write is Unreadable or Refused with a sentence | implicit: ExifTool for all but clip writes and clip header dates | conformance step 4 (section 5): a file of a moved format that the library refuses starts no ExifTool (ExifToolSession patched to raise); a grep rule: no `ExifTool` name in a moved format's arm |
| Dead after a timeout | `read`'s own rule for ONE call (built: `stalled`); `Fields.together()` (L0b) holds it across the calls of one journaled write and one bake run; the library's reads bounded by `in_place._Lent.bounded` / `shares.bounded` | `StaysDead` by hand at bake_after_index :150, file_changes :467, :1751 | no `StaysDead(` outside field_values (one more name in the session rule, L0b); conformance step 3 |
| A write refused before its record | `Fields.ready_to_write()` (L0b), called by `file_changes.write_fields` before `file_journal.plan`: starts ExifTool while a format of the change is ExifTool's, and refuses the change whole, one sentence, when it cannot; deleted at X | implicit: the start at `with` | scenario test on the pattern of `tests/test_ffmpeg_is_a_prerequisite.py` (L0b; fails on old code) |
| Read named fields | `Fields.read` / `read_one` | `field_values.read` / `read_one` | existing `.get_tags(` rule; the session rule |
| Read a file whole, as the index records it | `Fields.whole` (built) over metadata's record functions | built | `tests/test_layers.py` (the deleted names); an import cycle fails at import |
| Read a write back (before `done`; the settle) | `Fields.read_back(path, wanted, *, patched)` (L0b): the library's own reader once the format is moved (Q10, A+C), after the write path validated what it built before any byte (below) | `_verified` / `_read_whole` over ExifTool | `tests/test_a_file_holds_what_exiftool_kept.py` end to end; conformance step 2 (the oracle confirms what the read-back said) |
| Validate what a write built, before a byte | `xmp_packet.checked(packet)` (new, J2), called by `segment_edits` on every packet it gives `in_place`: a strict XML parse, the namespaces declared, every owned property read back as given, `&` `<` `>` escaped, no C0 character, UTF-8; a packet that fails is no write (Refused, one sentence) | `xmp_packet.edit` parses its edit again (the editor's own check) | conformance items `&` in a keyword and a keyword outside cp1252 (section 5); a test that a packet failing the check writes no byte |
| Shape a row's raw_metadata | `tagpup/files/metadata` (`structured` / `raw_metadata`) for a whole read; `store.photos.follow_fields` for a follow; grouped names only from G | four places: metadata.structured, follow_fields, fields.record_keyword_fields, IdentityWriter.give | G deletes the bare-copy rule, leaving nothing to write twice; `tests/test_tag_writes_keep_the_index_true.py` |
| SourceFile in a whole record | `Fields.whole` emits `paths.exiftool_spelling(path)` whatever reads the file; `store.roots.raw_to_row` makes it `@<root>/...` | ExifTool's own spelling | conformance step 1: SourceFile equals the oracle's |
| Write fields into a file | `Fields.write`: the own patch where the change fits (built), else the library's rewrite beside the photo, compared and moved (J3a for JPEG, P1 for PNG and TIFF); a clip's patch (built) | the own patch; ExifTool's rewrite | existing rules (`.set_tags(`, `-TAG=` execute, `patch_jpeg(` / `patch_clip(` outside field_values, `field_values.write(` outside file_changes); J3c deletes `_command` and the rule's last instance |
| The picture check | `field_values._landed` (built): every rewrite's new file compared with the photo from its first picture byte, then moved; a PNG's IDAT and a TIFF's strips from P1 | `_landed` (JPEG only) | `tests/test_a_file_holds_what_exiftool_kept.py`; P1 adds PNG and TIFF items |
| Mint a photo's identity | A field of the index's bake (Q5): `services/baking.plan_photo` plans `XMP-xmpMM:DocumentID`, written by `file_changes.write_fields` under `baking.INDEX_OPERATION` (M) | IdentityWriter, ensure_document_id, OutsideWrites, _read_again | `tests/test_photo_identity.py` re-keyed to `identity.mint_document_id(` only in `services/baking.py` |
| Map a field to where it lives in a file, both ways | `segment_edits` (`new_header`, `new_packet`, `held` new J1): `fields.EDITED_AS` read and written alike; the bake's fields and DocumentID join it (M, J3c) | segment_edits (write side) | existing `tests/test_the_block_editors_have_one_caller.py` |
| A JPEG's container | `containers` (`read`, `exif_tiff`, `photoshop_resources`, `iptc_records`) | containers; `read` called by field_values, in_place (2), metadata_check (1), segment_census (1) | J1 extends the block-editors guard: `containers.read(` only in tagpup/files and the two read-only layout tools, exempt by name |
| A JPEG rewritten whole | `segment_edits.new_file(layout, header, values)` (new, J3a): the header rebuilt with segments grown or made, every other segment and every byte from the first scan copied | ExifTool `-o` | conformance step 2: every non-target segment byte-equal, picture bytes equal |
| Extended XMP | `xmp_packet` (`joined`, J2; `split`, J3b): the extension joined by its GUID on read, a packet over a segment split on write | ExifTool | corpus items with an extension (section 5) |
| PNG and TIFF containers | `containers.Png` and `containers.Tiff` (new, P1): chunks; IFD0 and its XMP (700), Exif IFD | ExifTool | conformance items, P1 |
| A clip's boxes | `containers.Video` (layout), `in_place.headers_held` / `packet_held`, `clip_patch` (built); the clip reader (C1, Media) | Media, beside Files | existing patch_clip rule; C1's items |
| Field precedence (keywords XMP then IPTC, title, caption, DATE_TAKEN_FIELDS, the clip rule, DocumentID keys, gear) | `core/fields.py`, `core/dates.py`, `core/photo_meta.py` (unchanged but for G's grouped names) | core | `tests/test_keyword_fields.py`, `test_vocabulary.py` |
| The oracle (ExifTool's whole read `-j -G0:1 -a -all:all` under `-n`, with `-validate`) | `field_values.oracle` -> `Oracle.whole` (built); at X moved to a development module (`tagpup/files/oracle.py`) imported only by metadata_check, the census and the tests | built | the X rule above; tests/test_exiftool_answers_kept_for_tests.py (the product never replays) |
| The check on copies | `services/metadata_check.py` (`metadata-check`): from J3c one writer, the library, on sandbox copies, the oracle reading each copy before and after (the target fields as `fields.as_patched` says, every other tag as before) | two writers compared | `tests/test_the_two_writers_compared_on_copies.py`, renamed for what it then tests |
| Record/replay of ExifTool for the tests | `tests/exiftool_replay.py` | built | `tests/test_exiftool_answers_kept_for_tests.py` |

Concepts the plan removes: `field_values.session`, `file_changes.read_whole`, `IdentityWriter`, `ensure_document_id`,
`OutsideWrites`, `_read_again`, the hand wraps of `StaysDead`, `et` and `exiftool_path` as parameters, the bare-copy rule
(three places), `_read_headers`' special case, ExifTool's JPEG rewrite (`_command`, `_refuse_dropped`, `_dropped`,
`NOT_WRITTEN`, `WRITE_OPTIONS`), `BAKE_PARAMS`, `MIME_TYPE`, `READ_ERROR_KEYS`, `fake_exiftool`, `PATCH_IN_PLACE`, the
ExifTool settings group and `paths.exiftool`, `runtime.exiftool`, `config.exiftool_path`. Concepts it adds: `Fields`
(built), `Fields.together`, `read_back`, `ready_to_write` (gone again at X), the format readers and the rewrite
(`held`, `new_file`, `Png`, `Tiff`, the clip reader), `xmp_packet.checked`, `oracle.py`.

### Contract-table and charter diff (each stage's brief carries its part)

L0a changed the integration charter's row 50 (`.claude/agents/tagpup-owner-integration.md`, `Fields.whole` for
`MetadataExtractor.batch_read`) and the Files charter's item 9. Still to change: row 1 ("identity minting" among the
exceptions to the journaled path) at M; row 70 (the settle thread's `field_values.session` at file_changes :1750 and
:2044 becomes `opened`) at L0b; the worker charter (`.claude/agents/tagpup-worker.md`, "ExifTool through
`ExifToolSession`" becomes "through `field_values.opened`") at L0b and again at X (no ExifTool); docs/INVARIANTS.md
(the ExifToolSession row at L0b; exception (1) deleted and the "indexer writes no identity into a photo that does not
decode" row re-owned to `services/baking.py` at M; the read-back row, "an engine that did not write it", restated as
Q10's at J2); CLAUDE.md's "Never construct ExifToolHelper or ExifTool" line (the main session's edit, at X:
"ExifTool is the tests' and metadata-check's, through the oracle"); ARCHITECTURE; `tests/test_layers.py` PROGRAMS_TODAY
`tagpup_cli.py` 6 -> 5 at M (its `from tagpup.files.field_values import IdentityWriter`, tagpup_cli.py :66, leaves);
`tests/test_photo_looks_have_one_owner.py` (file_changes' os.stat exemption loses "the identity mint's carry" at M).

## 2. The public interface

```python
# tagpup/files/field_values.py -- the one face. Nothing else in tagpup/ reads or writes a photo's or a clip's fields.

def opened(exiftool_path) -> Fields          # built (L0a); from X: opened(), no argument
    """The library for ONE operation (a batch read, a journaled write, a bake run, a look), in a `with` block, never
    kept on the Runtime. Opening never raises and starts nothing: ExifTool, while a format is still its, starts at the
    first command that format needs, always with the bake configuration."""

class Fields:
    def read(self, paths, wanted, *, also=None, records=None) -> dict[key, dict[field, list[str]] | Unreadable]
        """Built. The fields `wanted` of each file. NotAnswered: the share did not answer; it and every later file of
        THIS call unread; the next call answers again. A start that fails is raised (L0a's decision). `also`/`records`
        leave at G (every user then answers by field)."""
    def read_one(self, path, wanted) -> dict[field, list[str]]                  # L0b; raises Unreadable
    def whole(self, paths, *, people=None, bakes=False) -> list[Record]
        """Built. Each file as the index records it: stamp before the read, tags, captions, taken, document_id,
        raw_metadata (grouped names only from G), read_error, unread; `bakes`: record["baked"]. A file the library
        refuses is recorded with its sentence as read_error, stamped (not read every run)."""
    def read_back(self, path, wanted, *, patched) -> dict[field, list[str]]    # L0b
        """What a write just made (or, `patched`, what a patch's record says it reached: the settle). Answered by the
        library's reader once the format is moved (J2 for JPEG), after the write path's own check of what it built."""
    def together(self) -> ContextManager[Fields]                                # L0b
        """Dead for good from the first timeout to the block's end: one journaled write, one bake run."""
    def ready_to_write(self) -> None                                            # L0b; deleted at X
    def write(self, path, values, *, saving=None, scratch_written=None, again=None) -> tuple[field, ...]
        """L0b (today's write / written_once_more). `values` into the file: a patch where it fits, else a rewrite
        beside, compared, moved; Refused with a sentence where the library does not write the file's shape (section 3).
        Returns the fields left stale."""
    def left_as_they_were(self, values, path) -> tuple[field, ...]              # L0b, the unchanged rule

def oracle(exiftool_path) -> ContextManager[Oracle]      # built; from X in tagpup/files/oracle.py, development only
class Oracle:
    def whole(self, paths) -> dict[key, dict[tag, value]]
        """ExifTool's whole read with -validate's words: the conformance suite's and metadata-check's expected answers,
        never a product answer."""

# unchanged module-level names callers keep: Unreadable, NotAnswered, PictureChanged, CHANGED_TWICE, writes(path),
# clip_refused, clip_date_refused, packet_held, packet_given (Convert's).
```

**Fields keeps working per operation at every stage**: a stage changes what answers behind a method, never its
signature, so its callers are not touched (L0c and X are the two stages that change callers, both deletion only).
**A caller never sees** ExifToolSession, `et`, pyexiftool's exceptions, `-struct` / `-G` / `-n` / `-m` / `-o`,
`File:MIMEType`, `READ_ERROR_KEYS`, which code read or wrote, a container's layout, a scratch file's name (file_changes
keeps `exiftool_session.scratch_of`'s rule for its scratch files, #2180, renamed at X).
**A caller still gives** `exiftool_path` to `opened`, until L0c collapses it with `et` into one parameter `fields`;
`runtime.fields(library)` stays the entry points' door and stops naming ExifTool at X.

## 3. What the library must take, or refuse with a sentence

The rule (DECISIONS 2026-11-26, Q6/Q7): with no fallback, the library takes every case ExifTool takes today, or refuses
it with a sentence. A case with a live instance is taken; a case with none is one refusal arm, never a branch written
for its shape (CLAUDE.md). Counts: the segment census of every JPEG (2026-11-26, this pass, every file read, none away),
photo_index / kr-track / renton_parkrun / mu_dance; the PNG and TIFF files read whole (43, every one); the clips' rows.

**JPEG: the shapes of the file** (all 62,161 read)

| Case | Live count | What ExifTool does today | What the library does (stage) |
|---|---|---|---|
| A change that does not fit its segment (XMP padding short, EXIF slack small, IPTC rarely padded) | XMP slack 0: 302 / 16 / 2 / 2 (under 2 KB: 543 / 16 / 2 / 6); EXIF slack under 64 bytes: 44,864 / 2,181 / 1,305 / 1,280 of the EXIF segments; IPTC padded: 2,787 / 0 / 0 / 0 | rewrites the file, the segment grown, XMP padded 2,425 bytes afresh | rewrite beside, the segment grown, XMP padding laid as ExifTool lays it (`xmp_packet.RESERVE`), EXIF grown by appending (no pointer moves), compared and moved (J3a) |
| A change for a segment the photo lacks | no EXIF 0 / 0 / 3 / 0; no IPTC 853 / 4 / 3 / 0; an APP13 with no IPTC block (0x0404) 18 / 2 / 3 / 0; no XMP 0 in all four | makes the segment | the rewrite makes it where the census's commonest layouts hold it (EXIF first after SOI or JFIF; IPTC and XMP before the tables), the oracle's read of the corpus item the judge (J3a) |
| A rewrite refreshes every copy (DECISIONS 2026-11-03: EXIF and IPTC copies of keywords and captions written only by a rewrite) | every rewrite | writes every copy | the rewrite writes every copy `fields` names, as today (J3a) |
| IPTC with no 1:90 charset; a value outside cp1252 | 1:90 absent 15,444 / 4 / 0 / 0; values outside ASCII 124 / 0 / 0 / 0 | writes UTF-8 and adds 1:90 | `iptc_block`'s rule (built): cp1252 when every value fits and the file had no marker, else 1:90 UTF-8 (J3a for the grown block) |
| IPTC bytes after the last 8BIM | 2 / 2 / 3 / 0 files | rewrites the block | kept byte for byte after the resources the editor rewrites (J3b) |
| IPTC datasets cut short or garbage | 251 / 0 / 0 / 0 | reads what it can | read as the oracle reads them (a corpus item laid from one file's layout); a write keeps every dataset the oracle reads and the unreadable tail byte for byte (J1 read, J3b write) |
| Two APP13 segments | 0 / 2 / 3 / 0 | reads both, writes one | read as the oracle reads them (a corpus item laid from one file's layout), the segment holding 0x0404 edited, the other kept byte for byte (J3b) |
| Two EXIF segments | 73 / 0 / 0 / 0 | reads the first, writes the first | the first read and written, the second kept byte for byte (J3b) |
| An EXIF block the walk cannot follow (`containers.exif_tiff` answers no IFD0) | 7 / 0 / 0 / 0 | reads what it can | J1's brief captures one layout as the item: read as the oracle reads it; a date or a turn of one refused with a sentence ("its EXIF block cannot be followed; nothing was written"), its keywords and captions (XMP's) written (J1 read, J3a write) |
| Extended XMP (an extension segment) | 2,488 / 85 / 5 / 7 | joins it on read; on write keeps it, or splits a packet over a segment into one | read joined by its GUID (J2); main packet edited and the extension kept, a packet over a segment split, GUID and MD5 laid (J3b) |
| XMP packet of 60 KB or more (a bake near the segment's limit) | 202 / 40 / 5 / 9 | splits on growth | J3b's split |
| An EXIF segment of 60 KB or more (little room to grow) | 234 / 0 / 0 / 0 | grows it to the limit | a rewrite whose EXIF copy of a keyword or caption would pass 64 KB leaves that copy stale, XMP holding the value, and answers it as a stale field (`fields.as_patched`), as a patch does today; dates and turns always fit (J3a) |
| An XMP packet in IFD0 (0x02BC) beside APP1's | 39 / 0 / 0 / 0 | reads both; writes both | read as the oracle answers (corpus item laid from one file's layout); written in both in one rewrite (J3b) |
| An XMP segment holding no packet (NUL bytes) | 3 / 0 / 0 / 0 | reads no XMP; refuses a write ("Invalid XMP", #2349) | reads as no XMP; a write lays a new packet in its place (J3a) |
| XMP with no packet wrapper | 52 / 7 / 0 / 0 | reads it | read and edited (the editor takes it today) |
| XMP as UTF-16, with a DTD, a target property twice or as an attribute, two rdf:about values; not well-formed beyond the NUL segments above | 0 in all four (census; its "not well-formed" 3 / 0 / 0 / 0 match the 3 NUL segments in number; J2's brief confirms they are the same files) | reads; writes with `-m` | refused with a sentence: one arm (J2 for reads, J3a for writes) |
| MPF (a second picture after the first's EOI) | with MPF 2,811 / 14 / 0 / 0 | rewrites its offsets | offsets are from the MPF header to bytes after the first EOI: kept as they are when no segment after MPF changes length, adjusted by the length change when one does (J3a); the 4d gate's 120 "MPF offsets by design" were the difference between a patch, which moves nothing, and ExifTool's rewrite |
| Bytes after the last EOI (a trailer, a phone's motion clip) | other bytes 46 / 3 / 0 / 0 | kept | copied byte for byte with the picture (J3a) |
| IFD1 thumbnail, maker notes (Canon footer, Sony moved), GPS and Interop IFDs | thumbnail 52,306 / 2,178 / 1,304 / 1,280; maker notes 32,892 / 1 / 0 / 0 (photo_index: Canon moved by the footer's shift 10,716, the footer wrong 68; Sony moved by a shift nothing says 5,108; entries skipped 5,001) | rewrites the EXIF block, adjusting maker-note offsets | no pointer moves: new values appended at the block's end, the thumbnail, the note and the IFDs left where they are (built for the patch, J3a for growth) |
| Other APPn (JFIF, ICC, Adobe, APP11, COM, unknown) | ICC 19,925 / 2,180 / 1,308 / 1,280; APP11 1,622 / 7 / 0 / 0; COM 2,468; APP15 1,049; APP4 866; APP5 838; APP2 (not ICC or MPF) 456 / 1 / 0 / 0; APP3 136; JFXX 56; APP10 36; APP12 11 (photo_index's, the others 0) | kept | copied byte for byte, in order (J3a) |
| A file whose picture is not found, or not a JPEG under a .jpg | 0 in all four (every file read laid out to its scan); user action: a copy cut short | reads; refuses the write (NO_PICTURE) | read refused with a sentence; write refused as today (`NO_PICTURE`) |

**JPEG: the fields** (every row's raw_metadata, 2026-11-26)

| Case | Live count | Library (stage) |
|---|---|---|
| The owned fields (keywords, captions, title, dates, Orientation, DocumentID, PreservedFileName) | every JPEG | read by the three editors' read arms (`segment_edits.held`, J1) |
| Camera and lens (EXIF Make/Model, LensModel/LensMake; XMP's) | rows with a lens 746 / 1,412 / 2 / 1,280 | J1 (IFD0 0x010F/0x0110, Exif IFD 0xA434/0xA433, XMP tiff:Make/Model, aux:Lens) |
| Rating (Q8 keeps it) | 55,695 / 172 / 4 / 817 | J1 (EXIF 0x4746, xmp:Rating) |
| GPS (Q8 keeps it) | 1,552 / 1 / 0 / 0 | J1 (GPS IFD, refs and rationals answered as the signed decimals readers have) |
| The bake (XMP-tagpup fields, mwg-rs:RegionInfo) | 62,158 of 62,161 JPEGs | J2 read (to `bake.region_text`'s canonical text), J3c write |
| Composite:LensID, MakerNotes:LensModel (Q4 drops them) | the lens they alone name: 6 photos (counted every row, photo_index) | not read: those 6 photos lose a lens name at their next re-read (the cost of Q4, counted) |
| Location (Q8 drops it) | 0 rows | not read (G) |
| MIME, read errors | every file | the format by its first bytes; a refusal's sentence as `read_error` (J2) |

**PNG (26: photo_index 25, renton_parkrun 1) and TIFF (17, photo_index), read whole**

| Case | Live count | Library (P1) |
|---|---|---|
| PNG XMP in an iTXt `XML:com.adobe.xmp` chunk before IDAT | 26 of 26 | read and written by `xmp_packet`, the chunk's CRC laid |
| PNG EXIF in an eXIf chunk | 24 | read by `containers.exif_tiff`; Orientation and dates written into it where it holds them |
| PNG IPTC in a zTXt `Raw profile type iptc` chunk (8BIM in hex, deflated) | 24 (24 hold IPTC:Keywords in their rows) | read; written where the group's XMP value is empty (#2433: the last keyword off must not come back from IPTC), else left stale as a JPEG patch leaves it |
| PNG with a C2PA box (caBX) | 1 | copied byte for byte |
| TIFF, little-endian, one IFD, XMP in tag 700, an Exif IFD, XPComment, Rating, Orientation; SubIFDs in 4; XPKeywords in 5; no IPTC | 17 of 17 | IFD0 and the Exif IFD read; a write appends each new value and repoints its entry, every strip and SubIFD where it is |
| A turn of a PNG with no eXIf | 2 PNGs hold no eXIf | refused with a sentence ("its turn is kept in its EXIF, which it does not hold"): a turn makes no segment in a PNG |
| A PNG or TIFF write's picture | 43 | compared and moved as a JPEG's is: IDAT's bytes, the strips' bytes (open item 2) |

**Clips (2,930: photo_index 2,921, kr-track 5, mu_dance 4; every one .mp4, .mov or .m4v)**

| Case | Live count | Library (C1, Media) |
|---|---|---|
| XMP (uuid box or moov/udta XMP_) | 2,921 + 0 + 4 rows hold XMP fields | read by the patch's own walk (`in_place.packet_held`, built) through `xmp_packet` |
| QuickTime CreateDate, CreationDate and the track and media dates | 2,930; CreationDate 13 | the header walk (built) and the keys item list |
| QuickTime Title | 2,623 | the title the patch frees and gives back (built for writes) |
| Make and Model | QuickTime 746; EXIF inside the clip 987 (254 with no QuickTime copy) | QuickTime's keys; the EXIF a camera puts in a clip after a Media census of its boxes (open item 1) |
| GPS | 93 | the location key (ISO 6709) answered as signed decimals |
| Lens (EXIF LensModel inside the clip) | 647 | with the clip's EXIF (open item 1) |
| Windows Tags (Xtra, `Microsoft:Category`) | asked by a clip save's read of its before (not in a row); written by the patch | `containers.Video.xtra` (built) |
| An old format (.wmv, .mpg, .avi, .mod) | 0 rows; user action: a folder holding one added | refused with a sentence until Convert has made it an MP4 ("TagPup reads the fields of a video once it is converted"); Convert carries no packet from it, as today |

**Neither**: WebP (0 rows, an indexable type) and HEIC (0, not indexable): a WebP's fields are refused with a sentence
("TagPup reads no fields of a .webp file"), its row recorded with it as read_error; no branch for either.

## 4. Stages (each one merge, about 1,200 added lines or fewer, the trunk whole after each)

Order: L0a (done), L0b, M, L0c, T1, G, J1, J2, J3a, J3b, J3c, P1, C1, X. JPEG first, then PNG and TIFF, clips last
(Q6/Q7). None is urgent (DECISIONS 2026-11-26). **Every gate is the corpus**: `tests/corpus.py`'s items, the conformance
suite (section 5) and the scenario files a stage names, under replay, plus one `--real-exiftool` run for a stage that
changes code shaping ExifTool's commands or the oracle's items (CLAUDE.md). The live libraries are census inputs: a
stage's brief counts, read-only, the shapes it meets and turns each into a corpus item, a real file's layout captured as
a fixture where the shape matters (its texts replaced by fictional values of the same length). A sandbox copy of a
library checked before an install (`metadata-check`) is the owner's option, never a merge gate. Each stage names what it
deletes; a stage that moves a format deletes ExifTool's arm for it in the same merge or the one after.

**L0a. One face for the reads: DONE** (section 0).

**L0b. One face for the writes** (Files; about 300 added lines). `Fields.read_one`, `write`, `read_back`, `together`,
`ready_to_write`, `left_as_they_were`; every write site opens `Fields` (file_changes :463, :1750, :1938, :2044,
file_only :92, tagging :284, bake_after_index :149, convert :344, :559, :659, metadata_check :323);
`file_changes.read_whole` and `field_values.session` (as a public name) deleted with their importers; the hand wraps of `StaysDead` become
`together()` (file_changes :467, the settle :1751, bake_after_index :150); the session rule's ALLOWED list emptied (the
rule then holds with no exemption but the class and its docstring). The brief lists the callers whose one session spans
several operations and what each keeps: tagging :284 (a save's read, the caption check, then `write_fields` given its
`Fields`: the write's `together()` ends at the write), bake_after_index :149 (one `together()` a run: a share away ends
the run at that folder, as `baked.stalled or et.dead` does today), file_changes :463 (one a journaled write).
`IdentityWriter` stays inside field_values until M. Contract rows 70; worker charter; INVARIANTS' ExifToolSession row.
Gate: the suite under replay and one `--real-exiftool` run; the new ExifTool-will-not-start scenario test fails on the
old code and passes; `fake_exiftool` keeps standing in below `Fields`. Rollback: a revert; nothing on disk changed.

**M. The mint as a field of the index's bake** (Files and Data; about 300 added lines, more deleted; Q5). `baking.
plan_photo` plans `XMP-xmpMM:DocumentID` (the bake's read, `FolderRead`, asks it beside `bake.WRITTEN`) for a photo the
bake is handed (recorded this run, or owed with no fingerprint row) that holds none; one whose bake is `Current` and
holds none is planned with the id alone (a current bake is never rewritten: DECISIONS 2026-10-27). The write is the
folder's existing internal change (`baking.INDEX_OPERATION`, `file_journal.INTERNAL`: one change a folder, in no History,
never undone; erased when the folder finishes), recorded by `follow_fields` (`DOCUMENT_ID_KEYS` -> `document_id`) in the
transaction that marks the file done, carried over a done rename by `_record`'s `_follow_rename`. The mint's rules move
with it: never into a photo that did not decode in full, never into one whose tail is zeros (#407: `plan_photo` asks
`images.tail_check` for a photo it would mint, and only for it). `fields.EDITED_AS` gains `XMP-xmpMM:DocumentID`
(`xmp_packet` owns it already), so a mint planned alone (a `Current` bake's) is patched where it fits, else ExifTool's
rewrite until J3a; a mint beside the bake's fields is part of the bake's write, ExifTool's rewrite until J3c.
Deletes `IdentityWriter`, `ensure_document_id`, `OutsideWrites`, `_read_again` and `given`, the index loop's `give`,
INVARIANTS exception (1), the CLI's `field_values` import (PROGRAMS_TODAY 6 -> 5). Behaviour changes, named for the
owner (Q5's answer): the mint is written after the folder's batches are recorded; an index with `--skip-faces` mints
nothing (no service passes it); a photo the bake cannot plan is minted when it can be. Before it merges: a read-only dry
count of the photos it would mint, per library, told the owner (a census, Q5's condition, not a pass/fail gate). Gate:
`tests/test_a_rename_stopped_part_way.py` (`AnIdentityTheIndexGivesTheRenamedPhotos`, run with faces stood in, not
`--skip-faces`); `tests/test_no_writes_to_damaged_photos.py` re-pointed at the bake; `tests/test_photo_identity.py`
re-keyed; the undo matrix's `a_bake` given a photo with no DocumentID. Rollback: a revert (the mints made are ordinary
DocumentIDs).

**L0c. `et` and `exiftool_path` collapsed into `fields`** (Files and integration; deletion only, Q9, never over budget).
The 26 functions outside field_values taking `et` and the 85 taking `exiftool_path` take one parameter `fields`, a
`Fields` opened per operation by the service that starts it or by the entry point through `runtime.fields(library)`.
Never a long-lived `Fields` on the Runtime (the drain at hand-over; per-call dead for good). Gate: the suite; the diff
adds no line beyond the rename. Rollback: a revert.

**T1. One corpus and one conformance suite** (Files; about 700 added lines, about 45 granular tests lifted and deleted,
about 800 lines: section 5). Written against `Fields`, so every later stage is judged by the same suite with nothing
changed but what answers behind the face; the oracle is ExifTool, replayed, real under `--real-exiftool`. Every
conformance write is deterministic (the mint seeded under replay, `exiftool_replay.seeded_mint`; no item writes a time
or a random value the seed does not give). Gate: green under replay and `--real-exiftool`; a measured fast-tier time,
baseline first, read from a second run. Rollback: a revert (tests only).

**G. Grouped names only; location and LensID dropped** (Data and Files; about 150 added lines, more deleted; ONE-WAY,
decided: Q3, Q4, Q8). ExifTool is asked grouped names only and raw_metadata records what it answers, so the library
(J2) has one key set to match. `METADATA_FIELDS` becomes the grouped names the readers read (core/fields, dates,
photo_meta) and those held for the owner (XMP:Rating and EXIF:Rating; Composite:GPSLatitude/GPSLongitude), and gains the
grouped names a bare ask brought that a reader reads: QuickTime:CreateDate (the clip rule; 2,930 clips), QuickTime:Title
(2,623), and, for the clips whose camera or lens only QuickTime names, QuickTime:Make and QuickTime:Model in
`photo_meta.MAKE_FIELDS` / `MODEL_FIELDS` (13 clips) and QuickTime:LensModel in `LENS_FIELDS` (5 clips), asked by name
(else those clips lose their camera or lens at their next re-read). Location (0 rows held)
and Composite:LensID / LensID leave. Deletes `structured`'s bare loop, the bare branches of `follow_fields` and
`record_keyword_fields`, `also` + `records` (Smart Rename and the caption check answer by field), and, once a dry count
says no row holds a bare key, `inspect._by_field`'s reason to exist. What the owner sees: nothing, but 6 photos' lens
(Q4) and the bare copies leaving a row at its next re-read (5,665 / 2,169 / 1,305 / 1,284 rows hold them; counted
this pass on every row, under grouped names only no photo's date, title, caption, hierarchy, identity or camera
changes, and a clip's date is its clip rule's,
grouped already). Gate: the conformance suite's step 1
(the whole record's keys are the grouped list); `tests/test_tag_writes_keep_the_index_true.py`; a scenario test that a
clip's camera, title and date read the same from a row read under G. Rollback: a revert (rows read under G read the same
by every reader).

**J1. The JPEG reader** (Files; about 650 added lines; no product caller until J2, the next merge: one feature cut in
two for the budget, the conformance suite its caller in between). `segment_edits.held(layout, header, wanted)` over the
three block modules' read arms, which gain EXIF Make/Model (IFD0 0x010F/0x0110), LensModel/LensMake (0xA434/0xA433),
Rating (0x4746), the GPS IFD (0x8825; refs and rationals answered as signed decimals), XMP xmp:Rating, aux:Lens,
tiff:Make/Model; a record in `Fields.whole`'s shape (grouped names, SourceFile). Not the bake fields, not extended XMP
(J2). The `containers.read` guard. Gate: the conformance suite's step 1 for every JPEG item, the library's reader called
by the suite against the oracle; a census item per shape it reads (section 3). Measures (told the owner only as
counted, baseline first): ms a photo against ExifTool batched (2026-11-03: 23 against 28). Rollback: a revert.

**J2. JPEG reads are the library's** (Files; about 600 added lines; deletes ExifTool's JPEG read arm). The bake's fields
(XMP-tagpup values and Bags, mwg-rs:RegionInfo to the structure `bake.region_text` canonicalises), extended XMP joined
by its GUID (its MD5 checked), the IFD0 packet, every layout of section 3's JPEG table read (two EXIF: the first; two
APP13: as the oracle reads them; bytes after the last 8BIM skipped; an XMP segment of NULs: no XMP), MIME by the first
bytes, the refusal sentences (section 6). Then every
`Fields` read of a JPEG (`read`, `read_one`, `whole`, `read_back`) is the library's: the plan's read, the undo's check,
the settle, Smart Rename, relink, a look, the bake's `FolderRead`, the index's whole read. A JPEG it cannot read is
Unreadable with its sentence, never ExifTool's. With it, `xmp_packet.checked` (Q10's strict check: every packet a write
builds is validated before `saving` is called), and the read-back of a JPEG is the library's (Q10, A+C). Deletes the
JPEG arm of `read` over ExifTool, `BAKE_PARAMS` for JPEG, the MIME filter. What the owner sees: a save of a JPEG that is
patched starts no ExifTool; a save that needs a rewrite still does (J3a). Gate: the conformance suite's steps 1-4 for
every JPEG item; `tests/test_an_index_reads_a_bake.py`, `tests/test_a_bake_is_planned_from_the_library.py`,
`tests/test_a_file_holds_what_exiftool_kept.py` with the oracle on; a packet failing the check writes no byte.
Rollback: a revert (nothing on disk changed: reads only, and the patch writes as before).

**J3a. The JPEG rewrite** (Files; about 700 added lines). `segment_edits.new_file`: a whole-file rewrite beside the photo
(the scratch name and rules of #2180), compared by the picture check and moved, never over the photo: a segment grown
(XMP padded as ExifTool pads it, EXIF grown by appending, IPTC grown), a segment made where the photo has none, every
copy refreshed as a rewrite does today, IPTC's charset rule, MPF offsets kept or adjusted by a length change after the
MPF segment, every other segment and every byte from the first scan copied. It takes every JPEG write of the fields
`EDITED_AS` names that the patch does not fit, every write given no `saving` (a just-look photo's, `file_only`), and an
XMP segment holding no packet. The layouts J3b takes and the bake's fields (J3c) stay ExifTool's rewrite until their
stage, decided by the header's layout and the fields named before any write: they have not moved yet. No write the
library refuses goes to ExifTool. Gate: conformance step 2 for
every JPEG item and operation (picture bytes equal, every non-target segment byte-equal, idempotent);
`tests/test_a_file_holds_what_exiftool_kept.py`; the crash scenarios (`tests/test_a_photo_written_by_its_own_editors.py`).
Rollback: a revert.

**J3b. The JPEG layouts** (Files; about 600 added lines). Extended XMP kept on an edit of the main packet, a packet over
a segment split (`xmp_packet.split`: GUID, MD5, chunk offsets); the IFD0 packet written with APP1's; two APP13 read and
written as the oracle reads them; two EXIF, the first written and the second kept; bytes after the last 8BIM, and an
IPTC block's unreadable tail, kept. Gate: a corpus item for
each, laid from a census class (a real file's layout captured where the shape matters), conformance step 2. Rollback: a
revert.

**J3c. The bake's write; ExifTool leaves the JPEG path** (Files; about 500 added lines, more deleted). `xmp_packet` writes
mwg-rs:RegionInfo (the structure from `bake.region_text`'s text) and TagPup's namespace, so `EDITED_AS` names every field
a writer writes; `metadata_check` becomes one writer (the library) with the oracle's read of each copy before and after.
Deletes ExifTool's JPEG write: `_command`, `_refuse_dropped`, `_dropped`, `NOT_WRITTEN`, `WRITE_OPTIONS`, the
`.set_tags(` rule's last instance, `fake_exiftool`'s JPEG users moved to corpus copies. Gate: conformance step 2 for a
bake of 0, 1 and many faces (enough to force extended XMP); `tests/test_an_index_reads_a_bake.py`, the undo matrix's
`a_bake`. Rollback: before the deletion merge, a revert of J3c alone restores ExifTool's bake write. What the owner sees:
no JPEG read or write starts ExifTool.

**P1. PNG and TIFF** (Files; about 700 added lines; 43 rows). `containers.Png` (chunks) and `containers.Tiff` (IFD0, the
Exif IFD, XMP in tag 700); reads of every field section 3 names; writes beside, compared (IDAT, strips) and moved: a PNG's
iTXt packet rewritten with its CRC, its IPTC profile edited by the #2433 rule, its eXIf where it holds a field; a TIFF's
new values appended and their entries repointed. A turn of a PNG with no eXIf refused with a sentence. Deletes the
PNG/TIFF arm of ExifTool's read and write and "a TIFF or PNG is written unchecked" (DECISIONS 2026-11-03 item 2: open
item 2). Gate: a corpus item per class of section 3's PNG/TIFF table (their layouts captured from one file each, texts
replaced), conformance steps 1-4. Rollback: a revert. What the owner sees: nothing but that a PNG or TIFF write is
checked.

**C1. Clip reads** (Media, beside Files; about 700 added lines). The clip reader over `containers.Video`: XMP (the
patch's walk), QuickTime dates (the header walk, built), Title, the keys item list (Make, Model, location), Xtra, and
the EXIF a camera puts in a clip where the Media owner's census of its boxes finds it (open item 1); `_read_headers`'
special case becomes the reader's answer. Before the brief: a read-only Media census of the 2,930 clips' boxes (kinds
and counts, no paths), each class a corpus item. Deletes ExifTool's clip read: from here ExifTool reads and writes
nothing in the product. Gate: clip items (moov first and last, uuid XMP, udta XMP_, Xtra, a camera's EXIF box),
conformance steps 1 and 3; the clip scenario files (C2, C5, C6). Rollback: a revert.

**X. ExifTool leaves the product** (integration, with Files; deletion only). `opened()` and `runtime.fields(library)`
take no ExifTool; `ready_to_write` goes; `runtime.exiftool`, `Runtime.exiftool`, `web/state.exiftool`,
`config.exiftool_path` / `default_exiftool`, the settings group `exiftool` and the setting `paths.exiftool` (stored empty
in 4 of 4: the Data owner's brief says whether the stored row is left unread or retired), settings_routes'
`exiftool_found`, the MCP tool's "read with ExifTool"; the oracle moves to `tagpup/files/oracle.py` with
`exiftool_session`, imported by `metadata_check`, the census and the tests only (`metadata-check` refuses with a sentence
where no ExifTool is found); `pyexiftool` to a development requirements file; `PATCH_IN_PLACE` and `fake_exiftool`
deleted. A new rule: no product module imports `exiftool` or `exiftool_session` but `oracle.py`. CLAUDE.md's line (the
main session's). Gate: the suite; the rule. Rollback: a revert.

One migration or stamp: none. One-way choices, each decided: M (the mint into the bake), G (the row's shape, the
dropped fields). Each is restated in its brief before it is built.

## 5. Tests: one corpus, one oracle, one conformance suite

**One corpus** (`tests/corpus.py`, extended, not a second module). Items named by recipe, made by the real writer and
kept across runs. A **shape recipe** for each live class of section 3, laid over a synthetic picture (PIL noise,
`damaged_photos.whole_jpeg`), never an owner photo: extended XMP; MPF; bytes after EOI; two EXIF; no EXIF, no IPTC, no
XMP; Canon and Sony maker notes moved; IPTC with no 1:90 and with UTF-8; IPTC datasets cut short; an APP13 with no
IPTC block; two APP13; bytes after the last 8BIM; an EXIF block the walk cannot follow; IFD0 XMP; unknown APPn;
an XMP segment of NULs; a packet with no wrapper; a bake of 0, 1 and many faces; a GPS IFD; a Rating in EXIF and XMP; a
keyword holding `&` and one outside cp1252 (the faults a same-author read-back would hide, Q10); PNG (iTXt XMP, eXIf,
zTXt IPTC, caBX); TIFF (XMP 700, Exif IFD, SubIFDs); clips (moov first and last, uuid XMP, udta XMP_, Xtra, keys,
a camera's EXIF box). Where a class exists only in owner files, its layout is captured from one file as the fixture,
every text replaced by fictional values of the same shape and length. Each recipe names the live count that justifies
it: no count, no item (a JPEG attribute-form packet, census 0, is no item: it is a refusal's case, one item for the
refusal arm).

**One oracle**: ExifTool, through `field_values.oracle` (from X `tagpup/files/oracle.py`), replayed in the normal run and
real under `--real-exiftool`. An item's expected answer is never written by hand: it is what the oracle reads.
Hand-written expectations stay only where TagPup decides (precedence, `fields.as_patched`, a refusal's sentence).

**One conformance suite** (`tests/test_a_photos_fields_read_and_written.py`, owner Files, a scenario file F12 "a photo's
fields read and written as the oracle reads them": F1-F11 are the Files ids in `tests/owners.py` today; added to
SCENARIOS and the Files charter's `Scenario ids:` line in the same commit, `tests/test_the_test_map.py`). For every
corpus item, through `Fields`:
1. read every METADATA_FIELD and bake field: equal to the oracle's, or a refusal whose sentence the item names; `whole`'s
   keys are the grouped list (from G) and its SourceFile the oracle's;
2. for each operation (add or remove a keyword, a caption, a title, Date Taken, a shift, a turn, a mint, PreservedFileName,
   a bake, an undo of each): write, then the oracle reads the target fields as `fields.as_patched` says and every other
   tag as before; the picture bytes equal; every non-target segment byte-equal; idempotent (a second write changes no
   byte); the write's `read_back` agrees with the oracle;
3. the failure cases: a truncated copy, a file held open, a batch with one bad file, a share that does not answer (the
   StaysDead/Away stand-ins): it fails the file and every later file of THAT call, and the next call answers (inside
   `together()`, every later call of the block fails at once);
4. a file of a moved format the library refuses starts no ExifTool (ExifToolSession patched to raise), and its refusal
   is the item's sentence.
A stage that moves a format is done when every item of the format passes, or is refused with the sentence it names.

**What is lifted and deleted** (counted by test name, 2026-11-26; T1's brief confirms each before deleting). A granular
test goes when its claim is "this layout reads as these fields" or "this write changes the target fields and no other,
and twice changes nothing": a corpus item and steps 1-2 make that claim for whatever answers. A test stays when its
claim is TagPup's own decision inside a segment (padding, slack, where a new entry goes, a spelling, a character set),
a refusal's reason, or bytes damaged anywhere (the fuzz loops).

| File | Tests | Go | Stay | What goes |
|---|---|---|---|---|
| test_container_layout | 26 | about 8 | 18 | clip layouts ExifTool and an append leave, the JPEG layouts no XMP / two XMP / extended XMP; stay: header versions, the > 4 GB sparse clip, fragmented clips, slack accounting, the 9 damage walks |
| test_xmp_packet_edits_what_it_owns | 23 | about 10 | 13 | owned properties read back, all nine set with every other byte kept, the 200 KB Premiere packet; stay: padding, namespaces, no wrapper, escaping, refusals, the 3 damage loops |
| test_exif_block_edits_what_it_owns | 15 | about 6 | 9 | fields read in either byte order, XP tags as UCS-2, maker notes that keep their pointers; stay: slack and grow, where a new tag is laid, refusals, the damage loops |
| test_iptc_block_edits_what_it_owns | 16 | about 5 | 11 | datasets read in their charset, a keyword added changes only the block; stay: the dummy resource, the charset rule, refusals, the hostile-bytes loop |
| test_segment_edits_choose_what_to_write | 25 | about 12 | 13 | XMP-only saves, the last keyword off every copy, a save twice, a date in every copy; stay: the map covers every writer, every "no patch" refusal, the header-length check, a clip's refusals |
| test_metadata_extraction | 70 | about 4 | 66 | the batch reads that survive one bad file: step 3; stay: TagPup's own decisions over a record |
| **the six** | **175** | **about 45** | **130** | |

The three scenario files stay (crashes, undo, two at once, the journal, the read-back); of them only "a turn reads as
ExifTool's write of it reads" and the IFD0-packet pair restate a conformance claim and may go. `fake_exiftool` (26
users) stays the stand-in below `Fields` while a format of its users is ExifTool's; its users move to corpus copies and
the library as each format moves, and it is deleted at X.

**Effect on the 297 s suite**: not claimed. Replay answers most ExifTool commands; what the library saves in the normal
run is the misses and process starts, real in `--real-exiftool` runs. The conformance suite stays in the fast tier; T1's
brief sets its budget from a measured run, baseline first (DEVELOPMENT.md, "Performance work").

## 6. Failure modes

- **A share that does not answer**: per call (built): the library's reads go through the bounded open
  (`in_place._Lent.bounded`, `shares.bounded`) and ExifTool's, while a format is still its, through the deadline;
  NotAnswered for that file and every later file of the call (one read waits one deadline); the next call answers, so a
  CLI index run that met one hiccup reads its next batch. Inside `together()` (a journaled write, a bake run) every later
  call of the block fails at once. A rewrite's picture check is bounded (`CHECK_SECONDS`): a share away leaves the photo
  as it was and the new file beside it for the scratch rules.
- **Interrupted part way**: reads write nothing. A patch keeps today's rule (its record committed before the first byte,
  settled by bytes: INVARIANTS, "Writes"). The library's rewrite writes beside the photo and moves the checked file over
  it with one `os.replace`: a kill leaves the photo whole and a scratch file the scratch rules settle (#2180), as
  ExifTool's `-o` does today; an extended-XMP split is part of the one rewrite, never a second write. A mint (M) is a
  field of the folder's internal bake change: a kill mid-folder is settled at the next start as a bake is, the photos
  not reached stay owed.
- **Two at once**: two reads of one file: harmless. A read during another process's patch can see half a header: the
  library reads the header in one bounded open, and a header that does not parse is Unreadable ("its header changed
  while it was read; it is read again when it next changes"), never an empty record; the patch changes the file's
  stamp, so the next index reads it again. Writes keep the per-file lock and the scratch rule (a scratch file beside the
  photo is a rewrite under way: no patch is made). A mint races a save as a bake does: the bake reads each file again
  just before its write, and one changed since is a conflict.
- **A read that fails**: Unreadable with the library's sentence (or ExifTool's, while the format is its); the index
  records `read_error` and stamps the file (not read every run); a plan's read that fails fails that file's write with
  the sentence, never a plan from nothing (a plan from nothing would clear a field).
- **A write the library does not make**: Refused, one sentence, nothing written, the photo as it was; in a journaled
  change the file is failed with that sentence in History, as a clip's refusal is today.
- **ExifTool will not start** (until each format moves): in a read, `read` raises (L0a's decision) and `whole` answers
  each file NOT_STARTED, unread; in a write, `ready_to_write` refuses the change whole before its record (a lazy start
  must not move the failure after the record). For a moved format nothing starts ExifTool, so the case is gone; after X
  it exists only in metadata-check and the tests (the oracle refuses the check with a sentence).
- **The refusal sentences** (each a constant beside its arm, named in the corpus item that proves it; no path in any):
  "Its XMP is not one TagPup reads (written as UTF-16, or not well-formed XML); nothing was read from it" (0 files);
  "Its XMP names the same property twice; nothing was written" (0); "Its EXIF block cannot be followed; nothing was
written" (a date or a turn of the 7 photo_index photos whose EXIF the walk cannot follow); "It starts as a JPEG but
holds no picture data that
  could be found; nothing was written" (today's NO_PICTURE); "TagPup reads no fields of a .webp file" (0 rows); "TagPup
  reads the fields of a video once it is converted to MP4" (0 rows of an old format); "Its turn is kept in its EXIF,
  which this PNG does not hold; nothing was written" (2 PNGs, on a turn); "The packet this write built did not read back
  as written; nothing was written" (`xmp_packet.checked`, 0 expected). Each stage's brief adds the sentences of the
  shapes it refuses, each with its live count.
- **What the owner sees**: nothing at L0b, L0c, T1. At M: identities appear after the folder's batches are recorded,
  patched where they fit, none from a `--skip-faces` index. At G: 6 photos lose a lens name (Q4), and a row's bare copies
  leave at its next re-read. From J2: saves that patch start no ExifTool; from J3c, no JPEG read or write does; from C1,
  nothing does. A refusal says its sentence where the failure says one today (the save's message, History, the index's
  read_error).

## 7. Decisions (the owner's answers, 2026-11-26; docs/DECISIONS.md)

1. **Is the own reader wanted, after replay brought the suite to 297 s?** Yes, and more: the library is TagPup's own for
   every format, all partial and whole reads and writes, XMP first, other EXIF/IPTC/box data retained; ExifTool leaves the
   product (DECISIONS 2026-11-26, "THE METADATA LIBRARY IS OURS, FOR EVERY FORMAT"; supersedes "L1 shadow only" and the
   L1 sunset). ExifTool is development-only, never a fallback: a file the library cannot read or write is refused with a
   sentence (same row).
2. **mu_dance in every gate?** Void (DECISIONS 2026-11-26, "A GATE IS A TEST CORPUS, NEVER A PRODUCTION LIBRARY"): every
   gate is the corpus and its conformance suite; mu_dance, like the other three, is a census input.
3. **What does raw_metadata record?** Grouped names only (XMP:Subject, EXIF:DateTimeOriginal); the bare copies stop being
   recorded and leave a row at its next re-read; no migration, no backfill (a one-way change, chosen). Stage G.
4. **Composite:LensID?** Dropped; the lens is EXIF/XMP LensModel; no maker-note tables are owned. Stage G. Counted this
   pass on every row: 6 photos lose the lens name only LensID or a maker note gave them (5 clips whose lens only
   QuickTime:LensModel names keep it: G names it).
5. **The mint?** A field of the index's bake write (one internal change per folder, never per file, in no History, never
   undone), today's population (the photos the index records), stage M, whose dry run counts the photos it would mint
   before it merges; IdentityWriter, OutsideWrites, _read_again and INVARIANTS exception (1) go with it.
6. **PNG and TIFF (43 rows)?** The library's, after JPEG (stage P1).
7. **Clips' reads (2,930)?** The library's, last (stage C1, the Media owner builds them); their writes are ours already.
8. **Drop Rating, GPS, location?** Location only (0 rows hold it); Rating and GPS stay read, held and written. Stage G.
9. **`et` and `exiftool_path`?** Collapsed into one `fields` parameter opened per operation, as its own deletion-only
   cut L0c after L0b.
10. **The read-back after the library's write?** A+C: the product reads back with the library's own reader after
    validating the packet it built before any byte is written (the strict check, `xmp_packet.checked`, J2); ExifTool
    confirms in the conformance suite and in metadata-check on copies.

Also decided the same day: none of it is urgent (the stages after L0b and T1 are ordered, none pressing); with no
fallback, the library must take every case the ExifTool rewrite takes today or refuse it with a sentence (section 3).

### Open for the owner (new, raised by this rewrite)

1. **A clip's camera, from the EXIF a camera puts inside it** (987 clips hold EXIF:Make, 254 of them with no QuickTime
   copy; 647 a lens; 689 maker notes): C1 reads it (after a Media census of the boxes that hold it), or a clip's camera is
   QuickTime's alone and those 254 clips lose it from Image Details and the gear search at their next re-read.
   **Recommended: read it**: it is shown and searched today, and the box holding it is a census away.
2. **A PNG or TIFF write checked like a JPEG's** (DECISIONS 2026-11-03 item 2 writes them unchecked): the library's P1
   compares the picture bytes (IDAT, strips) before the move, or writes them unchecked as today. **Recommended:
   checked**: the same rule for every photo, no cost worth naming at 43 rows.
3. **The rewrite's padding** (PLAN_OWN_METADATA's open item, "rewrites grow each file about 2 KB"): the library's rewrite
   pads XMP as ExifTool does today (2,425 bytes, what almost every packet holds) and lays no EXIF reserve, or lays an
   EXIF reserve too so later dates and turns patch. **Recommended: XMP's padding as today, no EXIF reserve**: a file grows
   no more than it does now, and dates and turns already patch (they always fit).

**Risks.** (1) A plan read that misses a field records a wrong `before`, and the write and its undo use it: hence J2
only once every JPEG item passes step 1, and the read-back is checked by the oracle in the suite. (2) The corpus
agreeing with the code that made it: shapes are laid from census classes and judged by the oracle, never by the reader
under test; the strict check catches a same-author escaping fault before a byte. (3) A stale replay answer after an
ExifTool upgrade: excluded by the version key; the oracle's real run is the gate's. (4) With no fallback, a shape the
census missed is refused on the owner's photo, not read: its sentence says so and the stage's census is counted again
before the brief. (5) Gains are mostly structural; per-photo speed was measured close (23 against 28 ms batched): the
case is ownership and removing the rewrite and the process, not speed.

## 8. Checked against (this rewrite, 2026-11-26, on trunk 9e377388)

Verified by grep, AST and reading the code, and counted read-only this pass:
- L0a as built (section 0): `opened` and `Fields` (field_values.py :245-344; `read` raises a failed start :288-295,
  `whole` answers NOT_STARTED :316-321), `Oracle` (:369-401), `runtime.fields` (runtime.py :354-357), the nine read sites,
  `MetadataExtractor` 0 names in tracked .py files, metadata.py importing only core and `images`, `photo_against_file`'s
  required `exiftool_path` (inspect.py :186), SESSION_OUTSIDE and its ALLOWED list
  (tests/test_rules_have_one_owner.py :100-170), `IdentityWriter` in field_values (:802; its own session :863) and the
  CLI's import of it (:66, built :621; `_read_again` :1228).
- The sessions left: `field_values.session(` at 8 sites in 4 modules; `ExifToolSession(` outside field_values only on the
  three ALLOWED lines; `StaysDead(` at bake_after_index :150, file_changes :467 and :1751; `et` 26 functions outside
  field_values (120 uses, 8 files), `exiftool_path` 85 functions (227 uses) in tagpup/ and tagpup_cli.py.
- The write path: `field_values.write` (:445-524), the patch's refusals (`in_place._header_of` :230-243: two segments of a
  patched kind, an extension, IPTC stray bytes; `segment_edits.new_header`: a field outside `EDITED_AS`, IFD0 XMP, a
  change that does not fit, a missing segment), `EDITED_AS` naming neither DocumentID nor a bake field (core/fields.py
  :396-413), `NOT_WRITTEN` and `WRITE_OPTIONS` (:70, :92).
- The ExifTool surface around the face: validation.py :275 and :343, config.py :95-113, runtime.py :348 and :641,
  settings_routes.py :36, mcp/server.py :155, requirements.txt :12; `paths.exiftool` stored empty in 4 of 4.
- The census (every JPEG of the four, `scripts/census_segments.py --sample 100000`, every file read) and the extra counts
  of every JPEG (IFD0 XMP, NUL XMP segments, 60 KB packets, missing segments: a scratch script over the census's own
  reads, nothing written); the 26 PNG and 17 TIFF files read whole (chunk kinds, IFD0 tags); the groups and keys of
  every non-JPEG row's raw_metadata; on every row of the four, what a reader answers with and without bare keys (lost:
  video date 1,117 / 5 / 0 / 4, answered by the clip rule's grouped fields; video camera 13 / 0 / 0 / 0; changed: photo
  lens 6) and what Q3 + Q4 cost (lens lost: 6 photos; 5 clips keep theirs by QuickTime:LensModel); Rating 55,695 /
  172 / 4 / 817; GPS photos 1,552 / 1 /
  0 / 0, clips 93; clip camera EXIF 987, QuickTime only 13.
- Relayed from the review pass of the same day, not recounted here: schema 39 in all four; rows holding a bare key
  (5,665 / 2,169 / 1,305 / 1,284); 0 photos with a bare date and no EXIF:DateTimeOriginal; History 11,633 changes; the
  line numbers of `OutsideWrites` (file_changes.py :1373-1420) and of the bake's plan.
- Not verified: the line estimates of each stage (each brief counts its own); the box that holds a clip's EXIF (C1's
  census); how ExifTool answers a file whose IFD0 and APP1 packets differ (J2's corpus item, laid from one file).

## 9. Review log

2026-11-26, the Files, integration and Data owners (the review of the first design; kept as it was):

Files owner:
- F1 (read-back made by the writer at L2): folded then as `Fields.read_back` and question 10; answered since (Q10, A+C):
  the library reads back, after its strict check; the oracle confirms in the suite and metadata-check.
- F2 (`Fields.identity` drops `OutsideWrites.around`): moot on Q5 (a): stage M.
- F3 (`_caption_problem` a third `also`+`records` user): folded (sections 0 and 2); G removes `also`.
- F4 (L4 before L5/Q4; GPS IFD missing from L1): folded; now G (the drops) before J1 (the reader), the GPS IFD on J1's
  list.
- F5 (Pillow Orientation outside the face): folded (section 0: it stays, the decode's own answer).
- F6 (import cycle for `whole`): folded and built in L0a.
- F7 (StaysDead inside `opened` ends restart-between-commands): folded and built (per call; `together()` at L0b).
- F8 (L3 changes a product read; L1 sunset): the sunset is gone with Q1's answer; the bake read moves at J2.

Integration owner:
- I1 (dead-for-good for a Fields' life): built per call; `together()` at L0b.
- I2 (contract-table diff): folded (section 1).
- I3 (Q9 cannot be built as written): folded; L0c, answered.
- I4 (lazy start moves a write's failure after its record): `ready_to_write` at L0b, gone at X.
- I5 (L0 gains `-config`; replay keys change): built in L0a.
- I6 (the one caller of `identity` is a service): moot on Q5 (a).
- I7 (F12, not F20): folded (section 5).
- I8 (`opened(None)`): built: `exiftool_path` required.
- I9 (L0a/L0b decided up front; T1 deterministic): built for L0a; L0b and T1 as written.

Data owner:
- D1 (L4's gate against stored rows): void with the live-library gates (DECISIONS 2026-11-26): G's gate is the corpus.
- D2 (grouped names only): answered (Q3), stage G.
- D3 (the mint as a field of the index's bake): answered (Q5), stage M.
- D4 (four places shape raw_metadata): folded; G deletes the bare-copy rule.
- D5 (SourceFile spelling): folded (section 1).

Rejected: none.

2026-11-26, this rewrite (the design agent, after the owner's answers): the plan follows the owner's decisions of the
day. The library is every format's (renamed throughout: no "JPEG reader" concept, no engine table); ExifTool leaves the
product and is never a fallback, so section 3 ("what stays ExifTool") became what the library must take or refuse, with
counts from a census of every JPEG, the 43 PNG and TIFF files read whole and every row; the `_engine_for` fall-back,
L1's shadow and sunset, the L4 two-fresh-reads gate on live libraries, "mu_dance in every gate" and "the ExifTool
rewrite retired at L6b" are gone. Stages renumbered by format: L0b, M, L0c, T1, G (the decided drops), J1-J3c (JPEG),
P1 (PNG, TIFF), C1 (clips), X (ExifTool out of the product). Every gate is the corpus. The questions section became the
Decisions, with three new items for the owner. Found on the way and folded: G must keep QuickTime:Make/Model (13 clips
would lose their camera) and QuickTime:LensModel (5 clips, their lens), and ask QuickTime:CreateDate and QuickTime:Title
by name; Q4 costs 6 photos a lens name, counted.

# Own metadata library: TagPup reads and writes every photo's and clip's fields itself, ExifTool only in the tests
Live count (counted read-only, 2026-11-26, `db.readonly_uri`; the segment census of every JPEG of the four,
`scripts/census_segments.py --sample 100000`, and a second pass this revision over every JPEG and clip): four libraries,
65,134 rows. photo_index 60,355: 57,392 JPEG, 2,921 clips (.mp4 1,745, .mov 1,173, .m4v 3), 25 PNG, 17 TIFF; kr-track
2,186: 2,181 JPEG, 5 clips; renton_parkrun 1,309: 1,308 JPEG, 1 PNG; mu_dance 1,284: 1,280 JPEG, 4 clips. JPEG 62,161
(95.4%), clips 2,930, PNG 26, TIFF 17, WebP 0, HEIC 0, a clip of an old format (.wmv, .mpg, .avi, .mod) 0. Photos with
no DocumentID: 56,272 (photo_index: 56,254 JPEG, 1 PNG, 17 TIFF; 0 photos in the other three). Rows holding a bare
(ungrouped) key: 10,423 (5,665 / 2,169 / 1,305 / 1,284). What each format holds, and every case the library must take or
refuse: section 3.
Domain: Files
Domains: Files (the library, JPEG, PNG, TIFF), Media (clip reads, stage C1), Data (the row's shape, the mint's journal
shape and the two backfills: stages R, M, B1, G, B2), integration (the entry points, the settings and the install: stage
X); integration reviews the seams

Work row #3376. Inputs: docs/work/3376.md (the workers' notes and decisions), docs/work/3376-sweep.md (the Files owner's
sweep, 2026-11-24; where it is stale, section 0 here is the trunk's), docs/PLAN_OWN_METADATA.md (M1-M7: the own patch,
built), docs/domains/files.md ("JPEG segment census, 2026-11-03"), the owner's decisions of 2026-11-26 (section 7) and
the four owners' second review (section 9). Owner's requirement (2026-11-26): the library CENTRALIZES all tag reading and
writing (one library, one interface, one access path) AND the tests (one corpus, one oracle, one conformance suite).

**What the owner decided (DECISIONS 2026-11-26).** The library is TagPup's own, for every format the libraries hold: every
partial and whole read and write of a photo's or a clip's fields, XMP first, every other EXIF, IPTC and box byte a file
holds retained untouched. ExifTool leaves the product: a development tool for the tests and the checks (the oracle, the
conformance suite, metadata-check on copies, all under `tools/` from X), never a fallback. A file the library cannot
read or write is refused with a sentence, never handed to ExifTool. Not urgent. A gate is the test corpus and its
conformance suite, never a production library. The reviews' decisions D1-D5: section 7.

Steady-state writer: none new. Every read and write of a photo's or clip's fields goes through `Fields` (section 2); a
photo added or changed afterwards is read by the index and written by `file_changes.write_fields` as today, and from
stage M a photo the index records (new or changed, decoded in full) is minted by the index's bake, the one writer that
already writes a field of every indexed photo. Two one-offs, each briefed with its steady state first (CLAUDE.md):
**B1** (after M) mints the 56,272 photos that hold no DocumentID (a photo added or changed afterwards: M's bake mints it,
no command run); **B2** (after G) strips the bare keys from the 10,423 rows that hold them (a row read afterwards: G's
recorders record grouped names only, no command run). Each is a `scripts/` program calling the service, never a copy of
it: dry run by default, a backup, `--apply`, the owner's go-ahead after its counts, deleted when its dry run says 0 left
(the merge's line `<name>: dry run 0 left`). No migration, no stamp.

## 0. Where it stands (trunk c45fd4e6, 2026-11-26; L0a merged)

No product file changed since 9e377388 (the trunk the line numbers below were read on): `git log 9e377388..c45fd4e6`
touches docs, tests and the queue only. **L0a is built** (a723cb27; docs/work/3376.md): `field_values.opened` ->
`Fields` (`read`, `whole`), opened per operation, ExifTool started at its first command; `runtime.fields(library)`
(runtime.py :354); `Oracle.whole`; `MetadataExtractor` deleted; every read site opens `Fields`; the rule
"ExifToolSession constructed outside field_values" (`tests/test_rules_have_one_owner.py` :100-124) with its `ALLOWED`
list of the three write sites L0b takes (file_changes :1938, file_only :92, tagging :284). Kept from its workers: (1)
dead for good after a timeout is `read`'s rule for ONE call; (2) `Fields.read` RAISES a failed start (sync would pair
nothing on "every photo Unreadable") while `whole` answers NOT_STARTED, unread: transitional, gone at X; (3) `also` +
`records` until G; (4) `read_one` and (5) the save's shared session at L0b.

**What is still ExifTool's in the product**, by place:
- **Sessions outside `opened`**: `field_values.session(` at 8 sites in 4 modules (bake_after_index :149, convert :344,
  :559, :659, file_changes :463, :1750, :2044, metadata_check :323), the three `ALLOWED` lines, `IdentityWriter._session`
  (field_values.py :863). `StaysDead` wrapped by hand at bake_after_index :150, file_changes :467 and :1751. `et` is a
  parameter of 26 functions outside field_values (120 uses in 8 files); `exiftool_path` of 85 (227 uses).
- **Every read of a photo's fields** but a clip's three QuickTime header dates (`_read_headers` -> `in_place.headers_held`).
  The Orientation a decode declares is Pillow's (`images._boxes_turn`, images.py :84): the decode's own answer, it stays.
- **The read-back** before `done`: `file_changes._verified` -> `_read_whole` -> `read_whole` -> `field_values.read`.
- **The JPEG rewrite** (`field_values.write` -> `_command`, `-o` beside the photo, `_refuse_dropped`, the picture check
  `_landed`): every JPEG write the own patch does not take (section 3; the bake's every write and the mint among them,
  since `fields.EDITED_AS` names neither DocumentID nor a bake field), every write given no `saving` (`file_only`, the
  index's mint, metadata-check's ExifTool side), and every PNG and TIFF write (unchecked, DECISIONS 2026-11-03 item 2).
  A clip is never ExifTool's to write (`_clip_written`).
- **The mint**: `IdentityWriter` (field_values :802) built at tagpup_cli.py :621 around `file_changes.OutsideWrites`,
  given per photo mid-batch, the batch read again by `tagpup_cli._read_again` (:1228); INVARIANTS exception (1).
- **Around the face and outside `tagpup/files`**: listed by line in X's brief (section 4).

**The own parts already built**: the own patch of a journaled JPEG write that fits (`segment_edits.new_header` over
`xmp_packet`, `exif_block`, `iptc_block`, laid by `in_place.patch_jpeg`; 4d), and a clip's whole write
(`segment_edits.new_packet`, `in_place.patch_clip`, `clip_patch`; M7). `containers` lays out a JPEG (segments, EXIF IFDs,
maker notes, 8BIM, IPTC records) and a clip (`containers.Video`). The three block editors read 9 + 9 + 3 fields.

**Tests**: `ExifToolSession(` in 53 test files, `fake_exiftool` (a table of fields, no bytes; it turns
`PATCH_IN_PLACE` off) in 26, `corpus` in 25; 175 granular format tests in six files (section 5); record/replay (#3375,
`tests/exiftool_replay.py`) answers ExifTool from kept answers; the suite is 297 s with it.

## 1. Mechanisms: the ONE place each lives

| Mechanism | The ONE place (module, function) | Current owners | Guard that fails on a second |
|---|---|---|---|
| Open the library for an operation | `field_values.opened(...)` -> `Fields` (built), opened per operation, never kept on the Runtime; an entry point through `runtime.fields(library)`. Takes `exiftool_path` until X, nothing after | built; the write sessions until L0b | LINE_RULE "ExifToolSession constructed outside field_values" (built), its ALLOWED list emptied by L0b; at X a rule that nothing under `tagpup/` imports `exiftool` or `exiftool_session` |
| Which code answers a file | By its format, read from its first bytes (`field_values._format_of`, new J2, over `containers.FIRST_TYPES` and the JPEG/PNG/TIFF magic, which stay in `containers`): a format's reads are wholly ExifTool's (not moved yet) or wholly the library's; a JPEG's writes move in three merges (J3a-J3c), routed by the header's layout and the fields named, decided before any write, deleted by J3c. Never an "own, else ExifTool" arm | implicit | conformance step 4 (section 5): a file of a moved format the library refuses starts no ExifTool (ExifToolSession patched to raise); no `ExifTool` name in a moved format's arm |
| Dead after a timeout | `read`'s own rule, ONE call (built: `stalled` ends the call, the next call answers again). The one wider scope is `Fields.together()` (L0b), a block its caller opens for one journaled write or one bake run, never a `Fields`' life and never across ffmpeg (Convert opens `Fields` per operation: its reads and its carry are minutes of ffmpeg apart). The library's own reads are bounded by `in_place._Lent.bounded` / `shares.bounded` | `StaysDead` by hand at bake_after_index :150, file_changes :467, :1751 | no `StaysDead(` outside field_values (L0b); conformance step 3 |
| A write refused before its record | `Fields.ready_to_write()` (L0b), called by `file_changes.write_fields` before `file_journal.plan`: starts ExifTool while a format of the change is ExifTool's and refuses the change whole, one sentence, when it cannot; deleted at X | implicit: the start at `with` | a named scenario in the F3 file `tests/test_bulk_edits_stalled_exiftool.py` (L0b; fails on the old code): ExifTool will not start, the whole change refused before its record, nothing journaled |
| A read that fails | `Fields.whole` (R): a transient failure (held open by another program: `lock_owners.looks_held`; a share that does not answer) left unread, no stamp, read at the next index; a lasting one (a refusal, a damaged file) stamped, its sentence logged. `store.photos.record_indexed` (the row's one writer, reached through `services/search.py` `build_or_update` -> `_record`) keeps tags, captions and raw_metadata when the record carries `read_error` | `whole` stamps every failure; `record_indexed` writes the empty record | R's scenario test (Files F6's file): a held file and a refused file each keep their row's fields; fails on the old code |
| Read named fields | `Fields.read` / `read_one` | `field_values.read` / `read_one` | existing `.get_tags(` rule; the session rule |
| Read a file whole, as the index records it | `Fields.whole` (built) over metadata's record functions | built | `tests/test_layers.py`; an import cycle fails at import |
| Read a write back (before `done`; the settle) | `Fields.read_back(path, wanted, *, whole=False)` (L0b): the library's own reader once the format is moved (Q10, A+C), after the write path validated what it built | `_verified` / `_read_whole` over ExifTool | `tests/test_a_file_holds_what_exiftool_kept.py`; conformance step 2 (the oracle confirms what the read-back said) |
| Validate what a write built, before a byte | `xmp_packet.checked(packet)` (new, J2), called by `segment_edits` on every packet it gives `in_place`: a strict XML parse, the namespaces declared, every owned property read back as given, `&` `<` `>` escaped, no C0 character, UTF-8; one that fails is no write (Refused) | `xmp_packet.edit` parses its edit again | conformance items `&` in a keyword and a keyword outside cp1252; a packet failing the check writes no byte |
| Shape a row's raw_metadata | `tagpup/files/metadata` (`structured` / `raw_metadata`) for a whole read; `store.photos.follow_fields` for a follow; grouped names only from G | four places: metadata.structured, follow_fields, fields.record_keyword_fields, IdentityWriter.give | G deletes the bare-copy rule; `tests/test_tag_writes_keep_the_index_true.py` |
| Which names a reader reads | `core/fields.py` (TITLE_COPIES, DESCRIPTION_COPIES, HIERARCHY_FIELDS, KEYWORD_COPIES), `core/dates.py` (DATE_TAKEN_FIELDS), `core/photo_meta.py` (MAKE/MODEL/LENS/LENS_MAKE_FIELDS; `store/search_index.GEAR_FIELDS` is theirs), grouped names only from G | bare names beside grouped in each | G's guard: no reader list names a field without its group |
| SourceFile in a whole record | `Fields.whole` emits `paths.exiftool_spelling(path)` whatever reads the file; `store.roots.raw_to_row` makes it `@<root>/...` | ExifTool's own spelling | conformance step 1: SourceFile equals the oracle's |
| Write fields into a file | `Fields.write`: the own patch where the change fits (built), else the library's rewrite beside the photo, compared and moved (J3a for JPEG, P1 for PNG and TIFF); a clip's patch (built) | the own patch; ExifTool's rewrite | existing rules (`.set_tags(`, `-TAG=` execute, `patch_jpeg(` / `patch_clip(` outside field_values, `field_values.write(` outside file_changes); J3c deletes `_command` |
| The picture check | `field_values._landed` (built, bounded: `CHECK_SECONDS`): every rewrite's new file compared with the photo from its first picture byte, then moved; a PNG's IDAT and a TIFF's strips from P1 | `_landed` (JPEG only) | `tests/test_a_file_holds_what_exiftool_kept.py`; P1 adds PNG and TIFF items |
| A rewrite's scratch file | `exiftool_session.scratch_of` (the name `<photo>_exiftool_tmp`, #2180) until X, then the library's own constant of the same name, so a scratch of an older version is still recognised; read by `file_changes.removed_scratch` (`SCRATCH_STALE`, file_changes.py :1119) and the watcher (jobs/watching.py :434) | ExifTool's convention | J3a's brief names both readers; X keeps the suffix (a test plants an old scratch and sees it settled) |
| Mint a photo's identity | A field of the index's bake (Q5): `bake_after_index` adds `XMP-xmpMM:DocumentID` to the plan of a photo the index recorded this run, once per `PhotoAt`, written by `file_changes.write_fields` under `baking.INDEX_OPERATION` alone (M) | IdentityWriter, ensure_document_id, OutsideWrites, _read_again | `tests/test_photo_identity.py` re-keyed: `identity.mint_document_id(` only in `services/bake_after_index.py` |
| Map a field to where it lives in a file, both ways | `segment_edits` (`new_header`, `new_packet`, `held` new J1): `fields.EDITED_AS` read and written alike; DocumentID joins it at M, the bake's fields at J3c | segment_edits (write side) | `tests/test_the_block_editors_have_one_caller.py` |
| A JPEG's container | `containers` (`read`, `exif_tiff`, `photoshop_resources`, `iptc_records`) | containers; `read` called by field_values, in_place (2), metadata_check (1), segment_census (1) | J1 extends the block-editors guard: `containers.read(` only in tagpup/files and the layout tools under `tools/` |
| A JPEG rewritten whole | `segment_edits.new_file(layout, header, values)` (new, J3a): the header rebuilt with segments grown or made, every other segment and every byte from the first scan copied | ExifTool `-o` | conformance step 2: every non-target segment byte-equal, picture bytes equal |
| Extended XMP | `xmp_packet` (`joined`, J2; `split`, J3b) | ExifTool | corpus items with an extension (section 5) |
| PNG and TIFF containers | `containers.Png` and `containers.Tiff` (new, P1) | ExifTool | conformance items, P1 |
| A clip's boxes | `containers.Video` (layout; its walk, `CONTAINERS` containers.py :36, extended at C1 to moov/meta and udta's items), `in_place.headers_held` / `packet_held`, `clip_patch` (built); the clip reader (C1, Media) | Media, beside Files | existing patch_clip rule; C1's items |
| Field precedence (keywords XMP then IPTC, title, caption, DATE_TAKEN_FIELDS, the clip rule, DocumentID keys, gear) | `core/fields.py`, `core/dates.py`, `core/photo_meta.py` (G's grouped names; nothing else changes) | core | `tests/test_keyword_fields.py`, `test_vocabulary.py` |
| The oracle (ExifTool's whole read `-j -G0:1 -a -all:all` under `-n`, with `-validate`) | `field_values.oracle` -> `Oracle.whole` (built); at X moved to `tools/` with `exiftool_session`, `metadata_check` and the census | built | the X rule; `tests/test_exiftool_answers_kept_for_tests.py` |
| The check on copies | `metadata_check` (a `tools/` program from X; the CLI's `metadata-check` command dropped): from J3c one writer, the library, on sandbox copies, the oracle reading each copy before and after | two writers compared | `tests/test_the_two_writers_compared_on_copies.py`, renamed for what it then tests |
| Record/replay of ExifTool for the tests | `tests/exiftool_replay.py`: reads and writes until J3c, then reads only (the oracle's); its write half deleted with J3c's last ExifTool write | built | `tests/test_exiftool_answers_kept_for_tests.py` |

Concepts the plan removes: `field_values.session`, `file_changes.read_whole`, `IdentityWriter`, `ensure_document_id`,
`OutsideWrites`, `_read_again`, the hand wraps of `StaysDead`, `et` and `exiftool_path` as parameters, the bare-copy rule
(three recorders and every reader list), `inspect._by_field`, the clip rule "a clip's QuickTime Subject is never a
keyword" (core/fields.py :121, :247-249; DECISIONS 2026-11-07: no bare Subject is asked once G lands), `_read_headers`'
special case, ExifTool's JPEG rewrite (`_command`, `_refuse_dropped`, `_dropped`, `NOT_WRITTEN`, `WRITE_OPTIONS`),
`BAKE_PARAMS`, `MIME_TYPE`, `READ_ERROR_KEYS`, `fake_exiftool`, `PATCH_IN_PLACE`, the ExifTool settings group and
`paths.exiftool` as a setting read, `runtime.exiftool`, `config.exiftool_path`, the CLI's `metadata-check`, replay's
write half. Concepts it adds: `Fields` (built), `Fields.together`, `read_back`, `ready_to_write` (gone again at X), the
format readers and the rewrite (`held`, `new_file`, `Png`, `Tiff`, the clip reader), `xmp_packet.checked`.

### Contract-table and charter diff (each stage's brief carries its part)

Integration charter (`.claude/agents/tagpup-owner-integration.md`): row 50 changed at L0a and again at R (an unread
record for a transient failure: `file_read: false`); row 70 (the settle's `field_values.session` at file_changes :1750
and :2044 becomes `opened`) at L0b; row 1 ("identity minting" among the exceptions to the journaled path) at M; row 16
(the CLI index: its arguments unchanged; it mints no identity mid-batch) at M; row 18 (`runtime.library_settings`:
`paths.exiftool` no longer read) and row 24 (the MCP tool: "read with ExifTool" becomes "read as the indexer reads it")
at X; row 12 unchanged (a refusal is a `Result` or `Refused`, never a new exception). Files charter: item 9 changed at
L0a; `Scenario ids:` gains F12 at T1. Worker charter (`.claude/agents/tagpup-worker.md`, "ExifTool through
`ExifToolSession`" becomes "through `field_values.opened`") at L0b and again at X. docs/INVARIANTS.md: :20 (the
ExifToolSession row) at L0b and X; exception (1) deleted and :51 ("the indexer writes no identity into a photo that does
not decode") re-owned to `services/bake_after_index.py` at M; :44 and :45 (the journaled path, the patch's record) at X;
the read-back row restated as Q10's at J2. CLAUDE.md's "Never construct ExifToolHelper or ExifTool" line (the main
session's edit, at X: "ExifTool is the tests' and `tools/`', through the oracle"); ARCHITECTURE; `tests/test_layers.py`
PROGRAMS_TODAY `tagpup_cli.py` 6 -> 5 at M (its `IdentityWriter` import, tagpup_cli.py :66, leaves);
`tests/test_photo_looks_have_one_owner.py` (file_changes' os.stat exemption loses "the identity mint's carry") at M.

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
        THIS call unread; the next call answers again. A start that fails is raised (L0a; until X). `also`/`records`
        leave at G (every user then answers by field)."""
    def read_one(self, path, wanted) -> dict[field, list[str]]                  # L0b; raises Unreadable
    def whole(self, paths, *, people=None, bakes=False) -> list[Record]
        """Built. Each file as the index records it: stamp before the read, tags, captions, taken, document_id,
        raw_metadata (grouped names only from G), read_error, unread; `bakes`: record["baked"]. From R: a file held open
        by another program or on a share that does not answer is UNREAD (no stamp); one refused or damaged carries its
        sentence as read_error, stamped, and its row keeps the fields it had (store.photos.record_indexed)."""
    def read_back(self, path, wanted, *, whole=False) -> dict[field, list[str]]    # L0b
        """What a write just made (or, `patched`, what a patch's record says it reached: the settle). The library's
        reader once the format is moved (J2 for JPEG), after the write path's own check of what it built."""
    def together(self) -> ContextManager[Fields]                                # L0b
        """Dead for good from the first timeout to the block's end: one journaled write, one bake run; never a span
        that holds ffmpeg."""
    def ready_to_write(self) -> None                                            # L0b; deleted at X
    def write(self, path, values, *, saving=None, scratch_written=None, again=None) -> tuple[field, ...]
        """L0b (today's write / written_once_more). `values` into the file: a patch where it fits, else a rewrite
        beside, compared, moved; Refused with a sentence where the library does not write the file's shape (section 3).
        Returns the fields left stale."""
    def left_as_they_were(self, values, path) -> tuple[field, ...]              # L0b, the unchanged rule

def oracle(exiftool_path) -> ContextManager[Oracle]      # built; from X in tools/, development only
class Oracle:
    def whole(self, paths) -> dict[key, dict[tag, value]]
        """ExifTool's whole read with -validate's words: the conformance suite's and metadata-check's expected answers,
        never a product answer."""

# unchanged module-level names callers keep: Unreadable, NotAnswered, PictureChanged, CHANGED_TWICE, writes(path),
# clip_refused, clip_date_refused, packet_held, packet_given (Convert's).
```

**Fields keeps working per operation at every stage**: a stage changes what answers behind a method, never its
signature (L0c and X change callers, both deletion only). **A caller never sees** ExifToolSession, `et`, pyexiftool's
exceptions, `-struct` / `-G` / `-n` / `-m` / `-o`, `File:MIMEType`, `READ_ERROR_KEYS`, which code read or wrote, a
container's layout, a scratch file's name. **A caller still gives** `exiftool_path` to `opened` until L0c collapses it
with `et` into one parameter `fields`; `runtime.fields(library)` stays the entry points' door and stops naming ExifTool
at X.

## 3. What the library must take, or refuse with a sentence

The rule (DECISIONS 2026-11-26, Q6/Q7): with no fallback, the library takes every case ExifTool takes today, or refuses
it with a sentence. A case with a live instance is taken; a case with none is one refusal arm, never a branch written
for its shape (CLAUDE.md). Counts: photo_index / kr-track / renton_parkrun / mu_dance; the segment census of every JPEG
(every file read, none away); the PNG and TIFF files read whole (43); the clips' rows and boxes (every clip read).

**JPEG: the shapes of the file** (all 62,161 read)

| Case | Live count | What ExifTool does today | What the library does (stage) |
|---|---|---|---|
| A change that does not fit its segment (XMP padding short, EXIF slack small, IPTC rarely padded) | XMP slack 0: 302 / 16 / 2 / 2 (under 2 KB: 543 / 16 / 2 / 6); EXIF slack under 64 bytes: 44,864 / 2,181 / 1,305 / 1,280; IPTC padded: 2,787 / 0 / 0 / 0 | rewrites the file, the segment grown, XMP padded 2,425 bytes afresh | rewrite beside, the segment grown, XMP padding laid as ExifTool lays it (`xmp_packet.RESERVE`), EXIF grown by appending (no pointer moves), compared and moved (J3a) |
| A change for a segment or an entry the photo lacks | no EXIF 0 / 0 / 3 / 0; no IPTC 853 / 4 / 3 / 0; an APP13 with no IPTC block (0x0404) 18 / 2 / 3 / 0; no XMP 0 in all four; no EXIF:DateTimeOriginal in the row 60 / 0 / 3 / 0 (20 + 3 of them hold no EXIF field at all) | makes the segment or the entry | the rewrite makes it where the census's commonest layouts hold it (EXIF first after SOI or JFIF; IPTC and XMP before the tables), the oracle's read of the corpus item the judge (J3a); a date or a turn that replaces an entry always patches, one that adds an entry is a rewrite |
| A rewrite refreshes every copy (DECISIONS 2026-11-03) | every rewrite | writes every copy | the rewrite writes every copy `fields` names (J3a) |
| IPTC with no 1:90 charset; a value outside cp1252 | 1:90 absent 15,444 / 4 / 0 / 0; values outside ASCII 124 / 0 / 0 / 0 | writes UTF-8 and adds 1:90 | `iptc_block`'s rule (built): cp1252 when every value fits and the file had no marker, else 1:90 UTF-8 (J3a for the grown block) |
| IPTC bytes after the last 8BIM | 2 / 2 / 3 / 0 | rewrites the block | kept byte for byte after the resources the editor rewrites (J3b) |
| IPTC datasets cut short or garbage | 251 / 0 / 0 / 0 | reads what it can | read as the oracle reads them (a corpus item laid from one file's layout); a write keeps every dataset the oracle reads and the unreadable tail byte for byte (J1 read, J3b write) |
| Two APP13 segments | 0 / 2 / 3 / 0 | reads both, writes one | read as the oracle reads them, the segment holding 0x0404 edited, the other kept byte for byte (J3b) |
| Two EXIF segments | 73 / 0 / 0 / 0 | reads the first, writes the first | the first read and written, the second kept byte for byte (J3b) |
| Two XMP main segments (the patch refuses two of any patched kind: in_place.py :236) | 0 in all four (the census's "XMP segments 1" is every JPEG read: 57,392 / 2,181 / 1,308 / 1,280) | not checked (no file holds one) | refused with a sentence: one arm, no branch (J2 read, J3a write) |
| An EXIF block the walk cannot follow (`containers.exif_tiff` answers no IFD0) | 7 / 0 / 0 / 0 | reads what it can | J1's brief captures one layout as the item: read as the oracle reads it; a date or a turn of one refused with a sentence, its keywords and captions (XMP's) written (J1 read, J3a write) |
| Extended XMP (an extension segment) | 2,488 / 85 / 5 / 7 | joins it on read; on write keeps it, or splits a packet over a segment into one | read joined by its GUID (J2); main packet edited and the extension kept, a packet over a segment split, GUID and MD5 laid (J3b) |
| Extended XMP whose GUID or MD5 does not match (the main packet's `xmpNote:HasExtendedXMP` against the segments' GUID; the joined bytes' MD5 against it) | 0 of the 2,585 files with an extension (2,488 / 85 / 5 / 7): every GUID the main packet names, every MD5 its bytes' | reads it, with a warning | refused with a sentence: one arm, no branch (J2) |
| XMP packet of 60 KB or more (a bake near the segment's limit) | 202 / 40 / 5 / 9 | splits on growth | J3b's split |
| An EXIF segment of 60 KB or more (little room to grow) | 234 / 0 / 0 / 0 | grows it to the limit | a rewrite whose EXIF copy of a keyword or caption would pass 64 KB leaves that copy stale, XMP holding the value, answered as a stale field (`fields.as_patched`), as a patch does today; a date or a turn that replaces an entry always fits (J3a) |
| An XMP packet in IFD0 (0x02BC) beside APP1's | 39 / 0 / 0 / 0 | reads both; writes both | read as the oracle answers (corpus item laid from one file's layout); written in both in one rewrite (J3b) |
| An XMP segment holding no packet (NUL bytes) | 3 / 0 / 0 / 0 | reads no XMP; refuses a write ("Invalid XMP", #2349) | reads as no XMP; a write lays a new packet in its place (J3a) |
| XMP with no packet wrapper | 52 / 7 / 0 / 0 | reads it | read and edited (the editor takes it today) |
| XMP as UTF-16, with a DTD, a target property twice or as an attribute, two rdf:about values; not well-formed beyond the NUL segments | 0 in all four (the census's "not well-formed" 3 / 0 / 0 / 0 match the 3 NUL segments in number; J2's brief confirms they are the same files) | reads; writes with `-m` | refused with a sentence: one arm (J2 for reads, J3a for writes) |
| MPF (a second picture after the first's EOI) | 2,811 / 14 / 0 / 0 | rewrites its offsets | offsets are from the MPF header to bytes after the first EOI: kept when no segment after MPF changes length, adjusted by the length change when one does (J3a) |
| Bytes after the last EOI (a trailer, a phone's motion clip) | 46 / 3 / 0 / 0 | kept | copied byte for byte with the picture (J3a) |
| IFD1 thumbnail, maker notes (Canon footer, Sony moved), GPS and Interop IFDs | thumbnail 52,306 / 2,178 / 1,304 / 1,280; maker notes 32,892 / 1 / 0 / 0 (photo_index: Canon moved by the footer's shift 10,716, the footer wrong 68; Sony moved 5,108; entries skipped 5,001) | rewrites the EXIF block, adjusting maker-note offsets | no pointer moves: new values appended at the block's end, the thumbnail, the note and the IFDs left where they are (built for the patch, J3a for growth) |
| Other APPn (JFIF, ICC, Adobe, APP11, COM, unknown) | ICC 19,925 / 2,180 / 1,308 / 1,280; APP11 1,622 / 7 / 0 / 0; COM 2,468; APP15 1,049; APP4 866; APP5 838; APP2 (not ICC or MPF) 456 / 1 / 0 / 0; APP3 136; JFXX 56; APP10 36; APP12 11 | kept | copied byte for byte, in order (J3a) |
| A file whose picture is not found, or not a JPEG under a .jpg | 0 in all four; user action: a copy cut short | reads; refuses the write (NO_PICTURE) | read refused with a sentence; write refused as today (`NO_PICTURE`) |
| A file another program holds open (no sharing) | user action: an editor holding it; TagPup's own patch holds a photo alone while it writes (`in_place._alone`) | fails the read or the write | a read is left unread, no stamp, read again at the next index (R); a write is Refused (`OPEN_ELSEWHERE`, as the patch's today) |

**JPEG: the fields** (every row's raw_metadata, 2026-11-26)

| Case | Live count | Library (stage) |
|---|---|---|
| The owned fields (keywords, captions, title, dates, Orientation, DocumentID, PreservedFileName) | every JPEG | read by the three editors' read arms (`segment_edits.held`, J1) |
| Camera and lens (EXIF Make/Model, LensModel/LensMake; XMP's) | rows with a lens 746 / 1,412 / 2 / 1,280 | J1 (IFD0 0x010F/0x0110, Exif IFD 0xA434/0xA433, XMP tiff:Make/Model, aux:Lens) |
| Rating (Q8 keeps it) | 55,695 / 172 / 4 / 817 | J1 (EXIF 0x4746, xmp:Rating) |
| GPS (Q8 keeps it) | 1,552 / 1 / 0 / 0 | J1 (GPS IFD, refs and rationals answered as signed decimals) |
| The bake (XMP-tagpup fields, mwg-rs:RegionInfo) | 62,158 of 62,161 JPEGs | J2 read (to `bake.region_text`'s canonical text), J3c write |
| Composite:LensID, MakerNotes:LensModel (Q4 drops them) | the lens they alone name: 6 photos | not read: those 6 lose a lens name at their next re-read (Q4's cost) |
| Location (Q8 drops it) | 3 rows (photo_index; no reader reads it: nothing shown changes) | not asked from G; B2 leaves the stored keys (they are grouped) |
| A DocumentID two rows share | 2 pairs (1 / 1 / 0 / 0) | nothing changes; a doctor count is optional (Data, not built unless asked) |
| MIME, read errors | every file | the format by its first bytes; a refusal's sentence as `read_error` (J2) |

**PNG (26: photo_index 25, renton_parkrun 1) and TIFF (17, photo_index), read whole**

| Case | Live count | Library (P1) |
|---|---|---|
| PNG XMP in an iTXt `XML:com.adobe.xmp` chunk before IDAT | 26 of 26 | read and written by `xmp_packet`, the chunk's CRC laid |
| PNG EXIF in an eXIf chunk | 24 | read by `containers.exif_tiff`; Orientation and dates written into it where it holds them |
| PNG IPTC in a zTXt `Raw profile type iptc` chunk (8BIM in hex, deflated) | 24 | read; written where the group's XMP value is empty (#2433), else left stale as a JPEG patch leaves it |
| PNG with a C2PA box (caBX) | 1 | copied byte for byte |
| TIFF, little-endian, one IFD, XMP in tag 700, an Exif IFD, XPComment, Rating, Orientation; SubIFDs in 4; XPKeywords in 5; no IPTC | 17 of 17 | IFD0 and the Exif IFD read; a write appends each new value and repoints its entry, every strip and SubIFD where it is |
| A turn of a PNG with no eXIf | 2 | refused with a sentence: a turn makes no segment in a PNG |
| A PNG or TIFF write's picture | 43 | compared and moved as a JPEG's is: IDAT's bytes, the strips' bytes (open item 2) |

**Clips (2,930: photo_index 2,921, kr-track 5, mu_dance 4; every one .mp4, .mov or .m4v; boxes read this revision)**

| Case | Live count | Library (C1, Media) |
|---|---|---|
| XMP (uuid box or moov/udta XMP_) | 2,921 + 0 + 4 rows hold XMP fields | read by the patch's own walk (`in_place.packet_held`, built) through `xmp_packet` |
| QuickTime CreateDate, the track and media dates | 2,930 | the header walk (built) |
| Apple's keys (moov/meta, handler `mdta`: keys + ilst; make, model, creationdate, ISO 6709 location) | 1,298 clips (1,289 / 5 / 0 / 4; make and model keys 721, creationdate 13, location 7); QuickTime:Make/Model 746 rows, QuickTime:CreationDate 13 | `CONTAINERS` (containers.py :36) walks moov's children udta and trak only: C1 adds moov/meta and reads the keys' numbered items |
| QuickTime's user data items in udta (0xA9 xyz GPS, mak, mod, day) | xyz 81, day 204, mak 3, mod 3 (photo_index) | C1 reads udta's items beside its meta (today only `udta/meta/ilst` is walked) |
| QuickTime Title | 2,623 rows | the title the patch frees and gives back (built for writes); read from ilst's item (C1) |
| The camera's EXIF inside a clip | EXIF:Make 987 rows (254 with no QuickTime copy), a lens 647; boxes: moov/udta/CNTH holding CNDA (Exif) 920, moov/udta/MVTG 42; 25 rows unaccounted | CNTH/CNDA is a JPEG holding Exif: read by J1's `exif_block` arm; MVTG is a bare IFD, no TIFF header: the same IFD walker given a start and a byte order; the rows left unaccounted: C1's census names their box before the brief |
| GPS | 93 rows; boxes: udta xyz 81, the keys' location 7 (5 rows unaccounted) | the location key or udta's xyz (ISO 6709) answered as signed decimals; C1's census names the 5 |
| Windows Tags (Xtra, `Microsoft:Category`) | asked by a clip save's read of its before; written by the patch | `containers.Video.xtra` (built) |
| A clip of 4 GB or more | 3 (photo_index) | read and patched as any clip: the patch refuses only a free box of 4 GB or more (0; clip_patch.py :38) |
| An old format (.wmv, .mpg, .avi, .mod) | 0 rows; user action: a folder holding one added, then Convert | a .wmv's XMP read from its top-level ASF XMP object (C1, owner's decision 4: Convert's `_ready` reads the source before planning, convert.py :344-349; `tests/test_a_clip_converted.py` `wmv_dated`), so Convert carries its tags, date and kept name; an .mpg, .avi or .mod: open item 4 |

**Neither**: WebP (0 rows, an indexable type) and HEIC (0, not indexable): a WebP's fields are refused with a sentence,
its row recorded with it as read_error; no branch for either.

## 4. Stages (each one merge, about 1,200 added lines or fewer, the trunk whole after each)

Order: L0a (done), L0b, R, L0c, T1, M, B1, G, B2, J1, J2, J3a, J3b, J3c, P1, C1, X. JPEG first, then PNG and TIFF,
clips last (Q6/Q7). B1 and B2 are one-offs the owner runs; no stage after them waits on their `--apply`. None is urgent
(DECISIONS 2026-11-26). **Every gate is the corpus**: `tests/corpus.py`'s items, the conformance suite (section 5) and
the scenario files a stage names, under replay, plus one `--real-exiftool` run for every J stage and any other stage
that changes code shaping ExifTool's commands or the oracle's items (CLAUDE.md). The live libraries are census inputs: a
stage's brief counts, read-only, the shapes it meets and turns each into a corpus item, a real file's layout captured as
a fixture where the shape matters (its texts replaced by fictional values of the same length). A sandbox copy checked
before an install (`metadata-check`) is the owner's option, never a merge gate. Each stage names what it deletes; a
stage that moves a format deletes ExifTool's arm for it in the same merge or the one after.

**L0a. One face for the reads: DONE** (section 0).

**L0b. One face for the writes** (Files; about 300 added lines). `Fields.read_one`, `write`, `read_back`, `together`,
`ready_to_write`, `left_as_they_were`; every write site opens `Fields` (file_changes :463, :1750, :1938, :2044,
file_only :92, tagging :284, bake_after_index :149, convert :344, :559, :659, metadata_check :323);
`file_changes.read_whole` and `field_values.session` (as a public name) deleted with their importers; the hand wraps of
`StaysDead` become `together()` (file_changes :467, the settle :1751, bake_after_index :150); the session rule's ALLOWED
list emptied. The brief lists the callers whose one session spans several operations and what each keeps: tagging :284
(a save's read, the caption check, then `write_fields` given its `Fields`: `together()` ends at the write),
bake_after_index :149 (one `together()` a run: a share away ends the run at that folder, as `baked.stalled or et.dead`
does today), file_changes :463 (one a journaled write); Convert opens one `Fields` per operation (the read at :344, the
read-back at :559, the settle at :659), never one across ffmpeg. "ExifTool will not start" joins the F3 file
`tests/test_bulk_edits_stalled_exiftool.py` as `AnExifToolThatWillNotStart.test_the_change_is_refused_before_its_record`:
`ready_to_write` refuses the whole change, nothing journaled.
`IdentityWriter` stays inside field_values until M. Contract row 70; worker charter; INVARIANTS :20. Gate: the suite
under replay and one `--real-exiftool` run; the new scenario fails on the old code and passes. Rollback: a revert.

**R. A read that fails never empties a row** (Files and Data; about 150 added lines; owner's decision 2). Today
`Fields.whole` stamps every failed read with an empty record (field_values.py :332-334) and `store.photos.record_indexed`
(store/photos.py :572-581) writes its empty tags, captions and raw_metadata over the row's. From R: `whole` asks one
bounded open of a file whose read failed; held by another program (`lock_owners.looks_held`) or not answering, the file
is UNREAD (no stamp; `metadata.HELD`, a new reason beside NOT_ANSWERED, NO_STAT, NOT_STARTED), read at the next index;
else it is a lasting failure, stamped, its sentence in the index's log (the row has no read_error column; no migration).
`services/search._record` passes `read_error` to `record_indexed`, which then keeps the row's tags, captions and
raw_metadata (and builds the derived rows, people and face names from the kept tags); a new row with a lasting failure
is recorded empty, as today. What the owner sees: a photo open in an editor while the index runs keeps its tags and is
read next time. Gate: a scenario test in the F6 file (a held file and a refused file each keep their row's fields;
fails on the old code), the conformance case "a file held open by another program" (T1 adds it to step 3). Contract
row 50. Rollback: a revert.

**L0c. `et` and `exiftool_path` collapsed into `fields`** (Files and integration; deletion only, Q9, never over budget).
The 26 functions outside field_values taking `et` and the 85 taking `exiftool_path` take one parameter `fields`, a
`Fields` opened per operation by the service that starts it or by the entry point through `runtime.fields(library)`.
Never a long-lived `Fields` on the Runtime. `scripts/clear_duplicate_captions.py` (:105 `runtime.exiftool`) is deleted
first, with its line `clear_duplicate_captions: dry run 0 left`, or the brief names it among the callers. Before T1 and
M, so neither is written against a parameter about to go. Gate: the suite; the diff adds no line beyond the rename.
Rollback: a revert.

**T1. One corpus and one conformance suite** (Files; about 700 added lines, measured before it merges; about 45
granular tests lifted and deleted, about 800 lines: section 5). Scenario F12, added to `tests/owners.py` SCENARIOS and
the Files charter's `Scenario ids:` line in the same commit (`tests/test_the_test_map.py`). Written against `Fields`, so
every later stage is judged by the same suite with nothing changed but what answers behind the face; the oracle is
ExifTool, replayed, real under `--real-exiftool`. Every conformance write is deterministic (the mint seeded under replay,
`exiftool_replay.seeded_mint`). Before M, whose gate is its step 2 for a mint. Gate: green under replay and
`--real-exiftool`; a measured fast-tier time, baseline first, read from a second run. Rollback: a revert (tests only).

**M. The mint as a field of the index's bake** (Files and Data; about 300 added lines, more deleted; Q5, owner's
decision 1). The mint is planned by `bake_after_index._planned`, never by `baking.plan_photo`: for a photo decoded in
full that holds no DocumentID, `XMP-xmpMM:DocumentID` is added to its final plan once, after its second `photo.plan`
(`_planned` plans a photo twice, :223-225), so a `PhotoAt` is minted once. The bake's read (`FolderRead`) asks
DocumentID beside `bake.WRITTEN`. A photo the bake writes anyway (`ToBake`) gets the id in that write whether or not the
index recorded it this run (DECISIONS 2026-11-27: no write and no decode more; as first built, a photo a stopped bake
left was baked by the next index, which handed it nothing, and never minted). A photo whose bake is `Current` and holds
no id, not recorded this run (`handed`, decoded by the index), is NOT minted: a write for the id alone is B1's. A photo
recorded this run whose bake is `Current` is planned with the id alone (a current bake is
never rewritten: DECISIONS 2026-10-27) and written with the bake's follower (`baking.follower`) in the transaction that
marks it done, so its fingerprint row is put as a baked photo's is (`_adopt` leaves a photo in `to_bake` to the
follower). The write is the folder's internal change under `baking.INDEX_OPERATION` alone (`file_journal.INTERNAL`: one
change a folder, in no History, never undone), recorded by `follow_fields` (`DOCUMENT_ID_KEYS` -> `document_id`),
carried over a done rename by `_follow_rename`; no other operation journals an identity (the undo matrix's `a_bake`,
which journals a bake under the retired `baking.OPERATION`, writes none). The mint's rules move with it: never into a
photo that did not decode in full; and nothing, neither its bake nor its id, into one whose tail is zeros (#407:
`images.tail_check`, asked of every photo the bake would write, `_incomplete`; as built, asked only of a photo to
mint, it let that photo's bake through). M does not need the own writer's growth path: `fields.EDITED_AS` gains `XMP-xmpMM:DocumentID`, so a
mint alone patches where XMP has room, else ExifTool's rewrite as today until J3a; a mint beside the bake's fields is
part of the bake's write (ExifTool's rewrite until J3c). Deletes `IdentityWriter`, `ensure_document_id`,
`OutsideWrites` with `file_journal.renamed_fingerprints`, `_read_again` and `given`, the index loop's `give`, INVARIANTS
exception (1), the CLI's `field_values` and `refresh_rows` imports (PROGRAMS_TODAY 6 -> 5). Behaviour changes, named
for the owner: the mint is written after the folder's batches are recorded, inside the bake; an index with
`--skip-faces` mints nothing; a photo the bake cannot plan is minted when it can be; a photo ending in zeros gets
neither its bake nor its id, and is counted on the bake's line as a possibly incomplete copy. Before
it merges: a read-only dry count of the photos it would mint per library, told the owner (Q5's condition, a census).
Gate: conformance step 2 for a mint; `tests/test_a_rename_stopped_part_way.py` (`AnIdentityTheIndexGivesTheRenamedPhotos`,
faces stood in); `tests/test_no_writes_to_damaged_photos.py` re-pointed at the bake; `tests/test_photo_identity.py`
re-keyed; `a_bake` asserts no DocumentID journaled. Contract rows 1 and 16. Rollback: a revert (the mints are ordinary
DocumentIDs).

**B1. One-off: mint the photos with no DocumentID** (Files and Data; a `scripts/` program, about 150 lines, deleted at 0
left; owner's decision 1). Live: 56,272 photo_index photos (56,254 JPEG, 1 PNG, 17 TIFF), 0 in the other three. Cause
unknown; likely photos indexed before the mint of 2026-09-21 and never read again since. Steady state: a photo added or
changed after M is minted by M's bake, no command run. The script calls M's service (the folder's bake write under
`INDEX_OPERATION`, the photo decoded in full and its tail checked, as M asks of a recorded photo), never a copy: dry run
by default (per library, per folder: how many it would mint, how many refused as not decoding), a backup, `--apply`
through the CLI (a long run: hours of decoding), the owner's go-ahead after the dry run's counts; a PNG or TIFF by
ExifTool's rewrite until P1, as M's mint of one is. Deleted when its dry run says 0 left. Gate: a scenario test on a
corpus library (a photo with no id minted once; a second run mints nothing). Rollback: none needed (ordinary ids).

**G. Grouped names only; location and LensID dropped** (Data and Files; about 200 added lines, more deleted; ONE-WAY,
decided: Q3, Q4, Q8, owner's decision 3). ExifTool is asked grouped names only, raw_metadata records what it answers,
and every reader reads grouped names only, in one branch. `METADATA_FIELDS` becomes the grouped names the readers read
and those held for the owner (XMP:Rating, EXIF:Rating; Composite:GPSLatitude/GPSLongitude), and gains by name what a
bare ask brought that a reader reads: XMP:CreateDate (DATE_TAKEN_FIELDS, CLIP_XMP_DATE_FIELDS), XMP:Make, XMP:Model,
XMP:LensMake, and the clip's QuickTime:CreateDate (2,930), QuickTime:Title (2,623), QuickTime:Make and QuickTime:Model
(746 rows; 13 clips whose camera only they name), QuickTime:LensModel (5), with QuickTime:CreationDate (13) as today;
the brief counts each. The readers lose their bare names: TITLE_COPIES ("Title", "ObjectName"), DESCRIPTION_COPIES
("Description", "Caption-Abstract"), HIERARCHY_FIELDS ("HierarchicalSubject"), DATE_TAKEN_FIELDS ("DateTimeOriginal",
"CreateDate"), photo_meta's MAKE/MODEL/LENS/LENS_ID/LENS_MAKE_FIELDS (search_index.GEAR_FIELDS with them), which gain
QuickTime:Make/Model/LensModel; `fields.scan_reads` reads its group's name alone. A guard: no reader list names a field
without its group. Location (3 rows) and Composite:LensID / LensID leave. Deletes `structured`'s bare loop, the bare
branches of `follow_fields` and `record_keyword_fields`, `also` + `records` (Smart Rename and the caption check answer
by field), and the clip rule at core/fields.py :121 and :247-249. `tests/photo_rows.py`'s docstring (#247) inverts: a
read records grouped names only. The brief checks what `tests/test_a_clip_converted.py` asserts of an old clip's row
title (a .wmv's ASF title and an .avi's RIFF title come today through the bare "Title"; 0 live rows): the assertion is
restated, no name asked for it. What the owner sees: nothing, but 6 photos' lens (Q4). Counted on every row: under
grouped names no photo's date, title, caption, hierarchy, identity or camera changes, a clip's date is its clip rule's.
Gate: conformance step 1 (the whole record's keys are the grouped list); `tests/test_tag_writes_keep_the_index_true.py`;
a scenario test that a clip's camera, title and date read the same from a row read under G. Rollback: a revert (rows
read under G read the same by every reader, before and after).

**B2. One-off: strip the bare keys from the stored rows** (Data; a `scripts/` program, about 100 lines, deleted at 0
left; owner's decision 3). Live: 10,423 rows (5,665 / 2,169 / 1,305 / 1,284). Steady state: G's recorders record
grouped names only, so no row gains a bare key again, no command run. Rows only, no photo file touched: each row's
raw_metadata rewritten without its keys that hold no group, through the store's writer of the column (the Data owner's
brief names it, never a second spelling of the rule: G's `metadata.raw_metadata`), in one transaction a batch. Dry run
by default (per library: rows and keys it would strip, and that no grouped key changes), a backup, `--apply`, the
owner's go-ahead. Its deletion merge also deletes `inspect._by_field` (decision 3: it goes with G; until B2 has run, a
row still holding bare keys would compare stale against a file read under G). Gate: a scenario test on a corpus library
(a row with bare keys stripped, its grouped keys equal; a second run strips nothing). Rollback: the backup.

**J1. The JPEG reader** (Files; about 650 added lines; no product caller until J2: one feature cut in two for the
budget, the conformance suite its caller in between). `segment_edits.held(layout, header, wanted)` over the three block
modules' read arms, which gain EXIF Make/Model (IFD0 0x010F/0x0110), LensModel/LensMake (0xA434/0xA433), Rating
(0x4746), the GPS IFD (0x8825), XMP xmp:Rating, aux:Lens, tiff:Make/Model; a record in `Fields.whole`'s shape (grouped
names, SourceFile). The IFD walker takes a start and a byte order (C1 reuses it for a clip's MVTG). Not the bake
fields, not extended XMP (J2). The `containers.read` guard. Gate: conformance step 1 for every JPEG item; a census item
per shape it reads (section 3); `--real-exiftool`. Measures (told the owner only as counted, baseline first): ms a photo
against ExifTool batched (2026-11-03: 23 against 28). Rollback: a revert.

**J2. JPEG reads are the library's** (Files; about 600 added lines; deletes ExifTool's JPEG read arm). The bake's fields
(XMP-tagpup values and Bags, mwg-rs:RegionInfo to the structure `bake.region_text` canonicalises), extended XMP joined
by its GUID (the GUID/MD5 arm of section 3), the IFD0 packet, the two-XMP refusal, every layout of
section 3's JPEG table read, MIME by the first bytes, the refusal sentences (section 6). Then every `Fields` read of a
JPEG (`read`, `read_one`, `whole`, `read_back`) is the library's: the plan's read, the undo's check, the settle, Smart
Rename, relink, a look, the bake's `FolderRead`, the index's whole read. A JPEG it cannot read is Unreadable with its
sentence, never ExifTool's: in a journaled write its refusal lands in the reply's `conflicts` ("could not be read: "
and the sentence, file_changes.py :1202-1205), the photo as it was. With it, `xmp_packet.checked` (Q10), and the
read-back of a JPEG is the library's (A+C). Deletes the JPEG arm of `read` over ExifTool, `BAKE_PARAMS` for JPEG, the
MIME filter. What the owner sees: a save of a JPEG that is patched starts no ExifTool; one that needs a rewrite still
does (J3a). Gate: conformance steps 1-4 for every JPEG item; `tests/test_an_index_reads_a_bake.py`,
`tests/test_a_bake_is_planned_from_the_library.py`, `tests/test_a_file_holds_what_exiftool_kept.py` with the oracle on;
a packet failing the check writes no byte; `--real-exiftool`. Rollback: a revert (reads only).

**J3a. The JPEG rewrite** (Files; about 700 added lines). `segment_edits.new_file`: a whole-file rewrite beside the photo,
compared by the picture check and moved, never over the photo: a segment grown (XMP padded as ExifTool pads it, EXIF
grown by appending, IPTC grown), a segment or an entry made where the photo has none (a date or a turn the EXIF lacks),
every copy refreshed, IPTC's charset rule, MPF offsets kept or adjusted, every other segment and every byte from the
first scan copied. The new file is written through one bounded call (`shares.bounded`, as `_landed`'s check is): a share
that hangs leaves the photo as it was and a part-written scratch beside it under the scratch name (`<photo>_exiftool_tmp`,
#2180), which `file_changes.removed_scratch` settles (one younger than `SCRATCH_STALE`, file_changes.py :1119, is left
and the owner told to try again; an older one removed); the brief names `SCRATCH_STALE` (today ExifTool's
`DEFAULT_TIMEOUT`: J3a gives it its own value) and the watcher's rename-onto-a-photo rule (jobs/watching.py :434) as
readers of the writer's convention. It takes every JPEG write of the fields `EDITED_AS` names that the patch does not
fit, every write given no `saving` (`file_only`), and an XMP segment holding no packet. The layouts J3b takes and the
bake's fields (J3c) stay ExifTool's rewrite until their stage, decided by the header's layout and the fields named
before any write. Gate: conformance step 2 for every JPEG item and operation (picture bytes equal, every non-target
segment byte-equal, idempotent); `tests/test_a_file_holds_what_exiftool_kept.py`; the crash scenarios
(`tests/test_a_photo_written_by_its_own_editors.py`); a share that hangs mid-rewrite (the scratch settled);
`--real-exiftool`. Rollback: a revert.

**J3b. The JPEG layouts** (Files; about 600 added lines). Extended XMP kept on an edit of the main packet, a packet over
a segment split (`xmp_packet.split`: GUID, MD5, chunk offsets); the IFD0 packet written with APP1's; two APP13 read and
written as the oracle reads them; two EXIF, the first written and the second kept; bytes after the last 8BIM, and an IPTC block's unreadable tail, kept. Gate: a corpus item for each, laid from a
census class, conformance step 2; `--real-exiftool`. Rollback: a revert.

**J3c. The bake's write; ExifTool leaves the JPEG path** (Files; about 500 added lines, more deleted). `xmp_packet` writes
mwg-rs:RegionInfo and TagPup's namespace, so `EDITED_AS` names every field a writer writes; `metadata_check` becomes one
writer (the library) with the oracle's read of each copy before and after. Deletes ExifTool's JPEG write (`_command`,
`_refuse_dropped`, `_dropped`, `NOT_WRITTEN`, `WRITE_OPTIONS`, the `.set_tags(` rule's last instance), replay's write
half, `fake_exiftool`'s JPEG users moved to corpus copies. Gate: conformance step 2 for a bake of 0, 1 and many faces
(enough to force extended XMP); `tests/test_an_index_reads_a_bake.py`, the undo matrix's `a_bake`; `--real-exiftool`.
Rollback: before the deletion merge, a revert of J3c alone restores ExifTool's bake write. What the owner sees: no JPEG
read or write starts ExifTool.

**P1. PNG and TIFF** (Files; about 700 added lines; 43 rows). `containers.Png` (chunks) and `containers.Tiff` (IFD0, the
Exif IFD, XMP in tag 700); reads of every field section 3 names; writes beside (bounded, as J3a's), compared (IDAT,
strips) and moved: a PNG's iTXt packet rewritten with its CRC, its IPTC profile edited by the #2433 rule, its eXIf where
it holds a field; a TIFF's new values appended and their entries repointed. A turn of a PNG with no eXIf refused with a
sentence. Deletes the PNG/TIFF arm of ExifTool's read and write and "a TIFF or PNG is written unchecked" (open item 2).
Gate: a corpus item per class of section 3's PNG/TIFF table, conformance steps 1-4. Rollback: a revert.

**C1. Clip reads** (Media, beside Files; about 900 added lines, cut in two at the census's classes if it passes the
budget). First step, before the brief: a full read-only census of the 2,930 clips' boxes (kinds and counts, no paths),
each class a corpus item, the rows section 3 leaves unaccounted named by their box. The clip reader over
`containers.Video`: `CONTAINERS` extended to moov/meta (Apple's `mdta` keys and their numbered ilst items: make, model,
creationdate, ISO 6709 location) and udta's items (0xA9 xyz, mak, mod, day); XMP (the patch's walk), QuickTime dates
(the header walk, built), Title, Xtra; the camera's EXIF from CNTH/CNDA (a JPEG holding Exif: J1's `exif_block` arm)
and MVTG (a bare IFD: J1's walker given a start and a byte order); a .wmv's top-level ASF XMP object (decision 4). The
first-bytes rule stays `containers.FIRST_TYPES`. `_read_headers`' special case becomes the reader's answer. Convert:
`_short_of` (convert.py :554-566) reads the copy back before the original is deleted for good; with the library both
writer and reader (A+C), the independent check of that irreversible step is conformance step 2 for every clip item and
`clip_convert.verified` (ffprobe), and C1's brief says so. Deletes ExifTool's clip read: from here ExifTool reads and
writes nothing in the product. Gate: clip items (moov first and last, uuid XMP, udta XMP_, Xtra, keys, udta items, CNTH,
MVTG, a clip of 4 GB or more, a .wmv with ASF XMP), conformance steps 1 and 3; the clip scenario files (C2, C5, C6) and
`tests/test_a_clip_converted.py`. Rollback: a revert.

**X. ExifTool leaves the product** (integration, with Files; deletion only; owner's decision 5). The brief lists by line:
- `opened()` and `runtime.fields(library)` take no ExifTool; `ready_to_write` goes; `Fields.read`'s raised start goes.
- `config.exiftool_path` / `default_exiftool` (config.py :95-113, its docstring :1, :7, :13); `runtime.exiftool`
  (runtime.py :348-351) and its callers (:78, :672), `Runtime.exiftool` (:641-643); `web/state.exiftool` (web/state.py
  :38-42); settings_routes.py :36 `exiftool_found` (docstring :6, :8) and web/common/settings-dialog.js :158-159 (:5);
  the settings group `exiftool` (core/validation.py :275) and the setting `paths.exiftool` (:343): its stored row (empty
  in 4 of 4) left unread, retired by the next migration that rebuilds settings, none made for it; the MCP tool's words
  (mcp/server.py :155, :162); processes.py's notes (:21) and `end_with_this_process` (:83, ExifTool's alone: it moves
  with `exiftool_session` or goes); `pyexiftool` (requirements.txt :12) to a development requirements file.
- `metadata_check`, `segment_census` (the census), the oracle and `exiftool_session` move to `tools/`; the CLI's
  `metadata-check` command (tagpup_cli.py :1350-1367) and its import (:97) dropped; `scripts/census_segments.py`
  follows the census; `bake.py` spells its own read arguments (bake.py :82 `READ_ARGS` from
  `exiftool_session.DEFAULT_COMMON_ARGS`, its import :41). The scratch suffix `_exiftool_tmp` stays recognised (a
  constant of the library's own; section 1).
- Docs: SPEC_TAGPUP_CLI.md :29, :46, :59, :85, :154-158 and :268 (metadata-check); SPEC_TAGTUNER.md :107, :121, :126;
  SPEC_TAGPUP_GUI.md :227; INVARIANTS.md :20, :44, :45, :51; CLAUDE.md's line (the main session's). The installer, the
  launcher and the doctor name no ExifTool (0 references): X does not change them.
- Tests whose claim is ExifTool's own behaviour, deleted or re-owned (to `tools/`' tests or the oracle) with
  `tests/owners.py`, `tests/tiers.py` (:36, :37, :62) and `exiftool_replay.STAYS_REAL` (tests/exiftool_replay.py :79)
  in the same commit: test_exiftool_session, test_exiftool_encoding, test_exiftool_says_why,
  test_bulk_edits_stalled_exiftool (its F3 scenarios kept where they are a write's, not ExifTool's),
  test_minor_errors_in_a_journaled_write, test_bulk_edits_real_exiftool, test_the_two_writers_compared_on_copies.
  `PATCH_IN_PLACE` and `fake_exiftool` deleted. The replay cache stays for the oracle's reads.
- A guard: nothing under `tagpup/` (or `tagpup_cli.py`, `tagpup_web.py`) imports `exiftool` or `exiftool_session`;
  pyexiftool is not in the product's requirements.
Gate: the suite; the guard. Rollback: a revert.

One migration or stamp: none. One-way choices, each decided: M (the mint into the bake), G (the row's shape, the dropped
fields), B1 and B2 (the owner's go-ahead after their counts). Each is restated in its brief before it is built.

## 5. Tests: one corpus, one oracle, one conformance suite

**One corpus** (`tests/corpus.py`, extended, not a second module). Items named by recipe, made by the real writer and
kept across runs. A **shape recipe** for each live class of section 3, laid over a synthetic picture (PIL noise,
`damaged_photos.whole_jpeg`), never an owner photo: extended XMP (and its GUID/MD5 class where the census holds one);
MPF; bytes after EOI; two EXIF; two XMP (one refusal item); no EXIF, no IPTC, no XMP, no DateTimeOriginal entry; Canon and
Sony maker notes moved; IPTC with no 1:90 and with UTF-8; IPTC datasets cut short; an APP13 with no IPTC block; two
APP13; bytes after the last 8BIM; an EXIF block the walk cannot follow; IFD0 XMP; unknown APPn; an XMP segment of NULs;
a packet with no wrapper; a bake of 0, 1 and many faces; a GPS IFD; a Rating in EXIF and XMP; a keyword holding `&` and
one outside cp1252 (the faults a same-author read-back would hide, Q10); PNG (iTXt XMP, eXIf, zTXt IPTC, caBX); TIFF
(XMP 700, Exif IFD, SubIFDs); clips (C1's census classes). Where a class exists only in owner files, its layout is
captured from one file as the fixture, every text replaced by fictional values of the same shape and length. Each recipe
names the live count that justifies it: no count, no item (a shape with census 0 is one item for its refusal arm).

**One oracle**: ExifTool, through `field_values.oracle` (from X in `tools/`), replayed in the normal run and real under
`--real-exiftool`. An item's expected answer is never written by hand: it is what the oracle reads. Hand-written
expectations stay only where TagPup decides (precedence, `fields.as_patched`, a refusal's sentence).

**One conformance suite** (`tests/test_a_photos_fields_read_and_written.py`, owner Files, scenario F12 "a photo's fields
read and written as the oracle reads them"). For every corpus item, through `Fields`:
1. read every METADATA_FIELD and bake field: equal to the oracle's, or a refusal whose sentence the item names; `whole`'s
   keys are the grouped list (from G) and its SourceFile the oracle's;
2. for each operation (add or remove a keyword, a caption, a title, Date Taken, a shift, a turn, a mint, PreservedFileName,
   a bake, an undo of each): write, then the oracle reads the target fields as `fields.as_patched` says and every other
   tag as before; the picture bytes equal; every non-target segment byte-equal; idempotent; the write's `read_back`
   agrees with the oracle;
3. the failure cases: a truncated copy; a file held open by another program (no sharing: unread, no stamp, its row's
   fields kept; a write Refused); a batch with one bad file; a share that does not answer (the Away stand-ins): it fails
   the file and every later file of THAT call, and the next call answers (inside `together()`, every later call of the
   block fails at once);
4. a file of a moved format the library refuses starts no ExifTool (ExifToolSession patched to raise), and its refusal
   is the item's sentence.
A stage that moves a format is done when every item of the format passes, or is refused with the sentence it names.

**What is lifted and deleted** (by test name; T1's brief confirms each). A test goes when its claim is "this layout reads
as these fields" or "this write changes the target fields only, and twice nothing" (steps 1-2 make it); it stays when
its claim is TagPup's own decision inside a segment, a refusal's reason, or damaged bytes (the fuzz loops).

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
users) stays the stand-in below `Fields` while a format of its users is ExifTool's; its users move to corpus copies as
each format moves, and it is deleted at X. The tests whose claim is ExifTool's own behaviour go at X (its list). Effect
on the 297 s suite: not claimed; T1's brief sets the conformance suite's fast-tier budget from a measured run.

## 6. Failure modes

- **A share that does not answer**: per call (built): the library's reads go through the bounded open
  (`in_place._Lent.bounded`, `shares.bounded`) and ExifTool's, while a format is still its, through the deadline;
  NotAnswered for that file and every later file of the call; the next call answers, so a CLI index run that met one
  hiccup reads its next batch. Inside `together()` every later call of the block fails at once. A rewrite's new file is
  written in one bounded call and checked in another (`CHECK_SECONDS`): a share away leaves the photo as it was and the
  scratch beside it for the #2180 rules.
- **Interrupted part way**: reads write nothing. A patch keeps today's rule (its record committed before the first byte,
  settled by bytes: INVARIANTS, "Writes"). The library's rewrite writes beside the photo and moves the checked file over
  it with one `os.replace`: a kill leaves the photo whole and a scratch file the scratch rules settle; an extended-XMP
  split is part of the one rewrite. A mint (M) is a field of the folder's internal bake change: a kill mid-folder is
  settled at the next start as a bake is. B1 and B2 stopped part way leave what they finished; their next dry run counts
  the rest.
- **Two at once**: two reads of one file: harmless. TagPup's own patch opens the photo alone, with no sharing
  (`in_place._alone`): a read during it, TagPup's or another program's, fails to open the file (a sharing violation),
  never sees half a header; from R that read is left unread, no stamp, and read at the next index (the patch changes the
  stamp anyway). Another program writing a file while sharing it may leave a header that does not parse: Unreadable,
  stamped, its row's fields kept (R); that program's write changes the stamp, so the next index reads it again. Writes
  keep the per-file lock and the scratch rule (a scratch file beside the photo is a rewrite under way: no patch is made).
  A mint races a save as a bake does: the bake reads each file again just before its write, and one changed since is a
  conflict.
- **A read that fails** (R): transient (held, not answering) is unread and retried; lasting is stamped with its sentence
  logged, and the row keeps its tags, captions and raw_metadata; a plan's read that fails fails that file's write with
  the sentence, never a plan from nothing (a plan from nothing would clear a field).
- **A file the library refuses, by entry point**: a save refuses with the sentence in its message (tagging :284-295, as a
  changed-on-disk or caption problem is); a bulk edit fails that file with the sentence in History and writes the others;
  the bake counts it among `baked.cannot` under its own kind, once a run, never a failure line repeated each run (the
  stamp keeps the index from reading it again); Smart Rename's read (`names.read_for_renaming`, which raises on ONE
  unreadable photo) refuses the whole selection with that photo's sentence, and a PreservedFileName write the library
  refuses leaves that photo out of the renames; an undo undoes what it can and fails the refused file, a partial undo as
  today; Convert answers its UNREADABLE sentence before ffmpeg runs.
- **A write the library does not make**: Refused, one sentence, nothing written, the photo as it was.
- **ExifTool will not start** (until each format moves): in a read, `read` raises (L0a; until X) and `whole` answers each
  file NOT_STARTED, unread; in a write, `ready_to_write` refuses the change whole before its record. For a moved format
  nothing starts ExifTool; after X it exists only in `tools/` and the tests.
- **The refusal sentences** (each a constant beside its arm, named in the corpus item that proves it; no path in any):
  "Its XMP is not one TagPup reads (written as UTF-16, or not well-formed XML); nothing was read from it" (0 files); "It
  holds two XMP segments; nothing was read from it" (0);
  "Its XMP names the same property twice; nothing was written" (0); "Its EXIF block cannot be followed; nothing was
  written" (a date or a turn of the 7); "It starts as a JPEG but holds no picture data that could be found; nothing was
  written" (NO_PICTURE); "TagPup reads no fields of a .webp file" (0 rows); "Its turn is kept in its EXIF, which this PNG
  does not hold; nothing was written" (2 PNGs, on a turn); "The packet this write built did not read back as written;
  nothing was written" (`xmp_packet.checked`, 0 expected). Each stage's brief adds the sentences of the shapes it
  refuses, each with its live count.
- **What the owner sees**: nothing at L0b, L0c, T1. At R: a photo held open during an index keeps its tags. At M:
  identities appear after the folder's batches are recorded, none from a `--skip-faces` index. B1 and B2: their counts,
  then their runs. At G: 6 photos lose a lens name (Q4). From J2: saves that patch start no ExifTool; from J3c, no JPEG
  read or write does; from C1, nothing does; at X, no ExifTool setting in the settings dialog.

## 7. Decisions (the owner's answers, 2026-11-26; docs/DECISIONS.md)

Q1 the library is TagPup's own for every format; ExifTool development-only, never a fallback. Q2 void: every gate is the
corpus. Q3 raw_metadata records grouped names only (G; widened by D3). Q4 LensID dropped: 6 photos lose a lens name (G).
Q5 the mint a field of the index's bake write, its dry count before M merges (narrowed by D1). Q6 PNG and TIFF after
JPEG (P1). Q7 clips' reads last (C1, Media). Q8 location dropped, Rating and GPS kept (G); the row says location is held
by 0 rows: recounted, 3 photo_index rows hold a location key and no reader reads one, so nothing shown changes (the main
session may amend the row). Q9 `et` and `exiftool_path` collapsed into `fields` (L0c, after L0b). Q10 A+C: the library's
own read-back after the strict check (`xmp_packet.checked`, J2); ExifTool confirms in the suite and metadata-check.

**From the four owner reviews (DECISIONS 2026-11-26, "OWNER DECISIONS FROM THE FOUR OWNER REVIEWS", items 1-5)**:
- **D1** M mints only the photos the index records (new or changed, decoded in full), through the bake's write; it does
  not mint the 56,272 photos with no DocumentID as a side effect of indexing; B1, a separate one-off, does, after M.
- **D2** A read that fails never empties a row (stage R): `record_indexed` keeps tags, captions and raw_metadata when a
  record carries read_error (DECISIONS names the caller, `build_or_update`); a transient failure is unread with no stamp,
  a lasting refusal stamped with its sentence; the conformance suite holds a file held open by another program.
- **D3** G drops the bare names from every reader list as well as the recorders, with a guard; `inspect._by_field` goes;
  B2 strips the bare keys from the 10,423 stored rows (rows only).
- **D4** C1 reads the XMP object of a .wmv's ASF container, so Convert of an old-format clip still carries its tags, date
  and kept name.
- **D5** X moves `metadata_check`, the census, the oracle and `exiftool_session` to `tools/` and drops the CLI's
  `metadata-check`; nothing under `tagpup/` imports exiftool; pyexiftool a development requirement, with a guard;
  `bake.py` spells its own read arguments; the scratch name `_exiftool_tmp` of older versions still recognised.

Also decided: none of it is urgent; with no fallback, the library takes every case the ExifTool rewrite takes today or
refuses it with a sentence (section 3).

### Open for the owner

1. **A clip's camera, from the EXIF a camera puts inside it** (987 clips hold EXIF:Make, 254 with no QuickTime copy; 647
   a lens): C1 reads it from the boxes the census found (CNTH/CNDA, MVTG), or a clip's camera is QuickTime's alone and
   those 254 clips lose it from Image Details and the gear search at their next re-read. **Recommended, and planned so
   in C1: read it**: shown and searched today; the readers are J1's (exif_block, the IFD walker).
2. **A PNG or TIFF write checked like a JPEG's** (DECISIONS 2026-11-03 item 2 writes them unchecked). **Recommended:
   checked**: the same rule for every photo, no cost worth naming at 43 rows.
3. **The rewrite's padding**: XMP's padding as ExifTool lays it (2,425 bytes) and no EXIF reserve, or an EXIF reserve
   too so a date or a turn the EXIF lacks patches next time. **Recommended: XMP's padding as today, no EXIF reserve**: a
   file grows no more than now, and a date or a turn that replaces an entry already patches (63 JPEGs lack the entry).
4. **An .mpg, .avi or .mod after C1** (0 rows of any old format; `tests/test_a_clip_converted.py` converts a .wmv, an .mpg
   and an .avi): with no ExifTool, Convert's `_ready` reads the source through the library. The library answers such a
   file as holding no field TagPup reads (a record with no fields: Convert carries no tags, date or title from it, the
   copy dated by its name) or refuses it (Convert of one refused with its sentence). **Recommended: no fields held**:
   Convert keeps working for the formats it exists for, and no reader is written for a container with no live file.

**Risks.** (1) A plan read that misses a field records a wrong `before`, and the write and its undo use it: hence J2
only once every JPEG item passes step 1, and the read-back is checked by the oracle in the suite. (2) The corpus agreeing
with the code that made it: shapes are laid from census classes and judged by the oracle, never by the reader under
test. (3) A stale replay answer after an ExifTool upgrade: excluded by the version key; the oracle's real run is the
gate's. (4) With no fallback, a shape the census missed is refused on the owner's photo: its sentence says so and the
stage's census is counted again before the brief. (5) Gains are mostly structural (23 against 28 ms batched): the case
is ownership and removing the rewrite and the process, not speed. (6) R keeps a row's fields for a file it could not
read: the row then says what the file last told (F6's "a row never claims more than was read" holds for the stamp, not
the fields; R's brief restates the rule for this case).

## 8. Checked against (this revision, 2026-11-26, trunk c45fd4e6)

Verified by grep and reading the code, and counted read-only this revision:
- No product file the plan names changed since 9e377388 (git log). L0a as built (field_values.py :245-344).
- R: `record_indexed` (store/photos.py :539-598; ON CONFLICT :572-581), called from `services/search.py` `_record` :506
  (`build_or_update` :482); no read_error column (store/schema.py :211); `in_place._alone` :204 (NO_SHARING);
  `lock_owners.looks_held` :314. M: `_planned` plans twice (bake_after_index.py :222-225), `_adopt` :260, `_write` :282;
  `baking.follower` :240, `OPERATION` retired :37; the undo matrix's bake under it (test_undo_after_every_later_kind.py
  :193-218). G: core/fields.py :17-65, :121, :247-261; dates.py :28-52; photo_meta.py :30-38; search_index.py :242;
  inspect.py :176-182; photo_rows.py :3-9. Convert: convert.py :344-349, :554-566; clip_convert.py :356;
  test_a_clip_converted.py :92-102, :200-208. X: every line of its brief read; install_app.py, launcher.py and
  tools/doctor.py name no ExifTool; contract rows 1, 12, 16, 18, 24, 50, 70 read.
- Counts (every row of the four): photos with no DocumentID 56,272 (photo_index: 56,254 JPEG, 1 PNG, 17 TIFF; 0 photos
  elsewhere); rows holding a bare key 5,665 / 2,169 / 1,305 / 1,284; location 3 / 0 / 0 / 0; DocumentIDs two rows share
  1 / 1 / 0 / 0; JPEG rows with no EXIF:DateTimeOriginal 60 / 0 / 3 / 0; clip rows: QuickTime:Make 746, Model 746,
  CreationDate 13, LensModel 5, Title 2,623, EXIF:Make 987 (254 with no QuickTime:Make), EXIF:LensModel 647, GPS 93.
- Files read this revision (a scratch script, read-only, bounded, background priority, counts only): every JPEG's XMP
  segments and extended-XMP GUID/MD5 (62,161 of 62,161 read; no second XMP segment, no GUID or MD5 mismatch in the 2,585 with an extension); every clip's moov/meta, udta items and sizes (2,929 of 2,930 read, one photo_index clip not answered; mdta keys 1,298, CNTH 920, MVTG 42, udta xyz 81, 4 GB or more 3, a free box of 4 GB or more 0).
- Relayed, not recounted: the segment census of the earlier pass (section 3's JPEG table but the two rows above), the 43
  PNG and TIFF files' chunk and IFD kinds, schema 39 in all four, the `et` / `exiftool_path` function counts.
- Not verified: each stage's line estimate (each brief counts its own; T1's measured before it merges); how ExifTool
  answers a file whose IFD0 and APP1 packets differ (J2's corpus item).

## 9. Review log

**First round** (2026-11-26, Files, integration and Data owners, on the first design; every point folded or made moot by
the owner's answers): F1 read-back (Q10, A+C); F2, I6 `Fields.identity` (moot: M); F3 `_caption_problem` (G removes
`also`); F4 the drops before the reader (G before J1; GPS on J1's list); F5 Pillow's Orientation (stays); F6 the import
cycle (built, L0a); F7, I1 dead for good (per call; `together()`); F8 the sunset (gone with Q1); I2 the contract diff;
I3 Q9 (L0c); I4 a lazy start after the record (`ready_to_write`); I5 `-config` (built); I7 F12; I8 `opened(None)`
(built); I9 decided up front; D1 live-library gate (void: corpus); D2, D3 (Q3, Q5); D4 four shapers (G); D5 SourceFile.
Rejected: none. The rewrite after the owner's answers renamed the library for every format, made section 3 what the
library takes or refuses, and set every gate on the corpus.

### Review log (second round)

2026-11-26: the owner's decisions 1-5 and the four owners' reviews of the rewrite. Each point was checked on trunk
c45fd4e6 before it was folded; a count named was counted this revision.

Owner's decisions. D1 folded (M, B1; 56,272 confirmed, 0 photos elsewhere). D2 folded as stage R; the row's writer is
`store.photos.record_indexed` (DECISIONS names its caller, `search.build_or_update`); section 6's "half a header" was
the wrong failure: the patch opens with no sharing (`in_place._alone`), so a read during it fails to open. D3 folded (G,
B2; 10,423 confirmed); `_by_field` goes in B2's deletion merge, since until B2 has run a row holding bare keys would
compare stale. D4 folded (C1, section 3); found: an .mpg, .avi or .mod (0 rows) has no reader after C1 and Convert's
tests convert all three: open item 4. D5 folded (X), `end_with_this_process` ExifTool's alone, the scratch suffix named.

Files owner, all folded: two XMP main segments (section 3 row: 0 live, a refusal arm); extended XMP with a GUID/MD5 mismatch (row
and arm: 0 live, a refusal arm); "dates and turns always fit" reworded (63: 60 + 3, counted); the rewrite's copy over a share
bounded, the #2180 scratch rules and their readers (J3a, section 6); T1 before M (and L0c before both, so neither is
written against a parameter about to go); M's mint-alone write carries `baking.follower`; a refused file per entry
point (section 6); `--real-exiftool` for every J stage; M needs no growth path (said in M).

Data owner, all folded: the mint INDEX_OPERATION's alone, once per `PhotoAt` (planned in `_planned` after its second
plan, never in `plan_photo`), `a_bake` journals no identity (verified: two `photo.plan` calls, the retired operation);
location 3 rows, not 0 (counted 3 / 0 / 0 / 0; sections 3, 7); the doctor count of shared DocumentIDs optional (2
pairs); `paths.exiftool` left unread, retired by the next settings migration (X); photo_rows' docstring inverts (G); the
QuickTime names kept by name (G), with XMP:CreateDate, XMP:Make, XMP:Model, XMP:LensMake found the same way.

Media owner, all folded: `CONTAINERS` walks neither moov/meta nor udta's items (section 3, C1; recounted: keys 1,298, udta xyz 81, CNTH 920, MVTG 42, 25 unaccounted);
the camera's EXIF in clips, a full census first (C1, open item 1); clips of 4 GB or more read and patch (3; a free box of 4 GB or more 0);
`together()` never spans ffmpeg, Convert opens per operation (L0b); `_short_of`'s independence moves to conformance step
2 and `clip_convert.verified` (C1); G deletes the clip Subject rule; `containers.FIRST_TYPES` stays (section 1).

Integration owner, all folded: X's deletions by line, install, launcher and doctor unchanged (X; SPEC_TAGPUP_CLI.md
:268 and `runtime.exiftool`'s callers :78, :672 added); ExifTool's own tests with owners.py, tiers.py and `STAYS_REAL`
in one commit (X; `STAYS_REAL` is in tests/exiftool_replay.py, named there); replay keeps reads, its write half dies at
J3c; "ExifTool will not start" in the F3 file with a named id (L0b); contract rows 24, 18, 16, 50, 12 (section 1; 12
unchanged); clear_duplicate_captions :105 (L0c); the dead-for-good scope ONE call, `together()` the one wider scope, a
block (section 1); `Fields.read`'s raised start transitional until X; J2's refusal lands in `conflicts`
(file_changes.py :1202-1205); T1 is F12 in SCENARIOS and the charter in one commit, its size measured before it merges.

Rejected: none.

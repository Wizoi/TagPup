# Plan: TagPup owns its JPEG metadata reads and writes

Status: planned 2026-11-03 (owner direction and Fable planner). Numbers below marked "relayed" come from older DECISIONS or
findings rows and were not counted for this plan; the M1 census recounts them.

## Why

Photos meet few outside programs: SmugMug, Windows Live Photo Gallery (WLPG) and a few online services. If those stay
happy, TagPup can do the small set of reads and writes ExifTool does for it, while keeping the camera and other fields
ExifTool keeps. Owning the writer removes the ExifTool rewrite (a whole-file copy on the share), its %TEMP% header copy
(#2399), its dropped-field warnings and its scratch file, and lets a rewrite lay a reserve so later edits patch in place.

## Scope (owner 2026-11-03: staged cut)

- **Own writer: JPEG.** Edit only the EXIF APP1, Photoshop APP13 (IPTC) and XMP APP1 segments; copy every other byte.
- **Stays on ExifTool:** every non-JPEG write (TIFF 17 rows, PNG 2 rows, counted 2026-11-03; HEIC 0), the indexer's whole read
  into `raw_metadata` (Composite GPS and LensID exist only there), bakes (namespace config), `-validate`, and a shadow
  comparison while the bar below is met.
- **Unchanged:** `file_changes.write_fields` is the one journaled writer; `field_values.write` the one entry; the patch
  record (`in_place.record` / `held` / `put_back`, `file_journal.save_patch`) and the picture check (`_landed`).

## The fields (today's, `core/fields.py`)

Keywords: XMP:Subject, IPTC:Keywords, EXIF:XPKeywords (UCS-2), XMP:HierarchicalSubject. Captions: XMP:Description,
IPTC:Caption-Abstract, EXIF:ImageDescription, EXIF:XPComment. Dates: EXIF/XMP DateTimeOriginal, EXIF CreateDate,
SubSecTime*, XMP CreateDate on a shift. Orientation (XMP tiff:Orientation follows). Identity: XMP-xmpMM DocumentID,
PreservedFileName. Bake: XMP-tagpup fields and MWG RegionInfo (stay on ExifTool).

## Preservation spec (JPEG)

- Byte for byte: every other segment (JFIF, ICC, MPF, Adobe, DQT/DHT/SOF, COM, unknown APPn) in order, and everything from
  the first SOS to the end (picture, MPF second images, Motion Photo video, trailers).
- EXIF: edit IFD0 and ExifIFD entries only; never move MakerNotes, IFD1 and its thumbnail, the GPS or Interop IFDs, or an
  unknown tag. New value data goes in the slack or segment end; the segment grows only when it has no MakerNote or the census shows its offsets are not affected (M3 decides, from the census classes in docs/domains/files.md).
  A structure that cannot be followed is "not mine" and goes to ExifTool.
- XMP: an XML-preserving textual edit of the properties TagPup owns; other namespaces kept as bytes; UTF-8, keep the
  `<?xpacket>` wrapper and its padding and the first `rdf:about`. A packet where a target property occurs twice or in
  attribute form, or is not well-formed, is refused to ExifTool or with a plain sentence. Extended XMP: main packet edited,
  extension untouched (111 of 3,000 in photo_index, 80 of 2,065 in kr-track, counted 2026-11-03).
- IPTC: rewrite only 8BIM 0x0404 (2:25, 2:120, 2:05, 1:90 charset); the 8BIM 0x0425 IPTCDigest is left alone (owner 2026-11-03, DECISIONS);
  keep every other 8BIM resource; pad with the dummy resource (`PAD_RESOURCE` 0x0DAC).
- Nothing foreign is dropped; TagPup adds, never removes.
- Dates: EXIF as 19-char local wall clock, no zone invented; XMP ISO 8601 keeping an existing offset; OffsetTime* untouched.
- IPTC charset: no marker means cp1252; write cp1252 when every value fits and the file had no marker, else 1:90 UTF-8.

## Verification bar (before any live file is touched)

1. Copies of 3,000 random JPEGs per library, in a sandbox home (`own_home_environment`), never the originals.
2. For each operation (add/remove keyword, caption, date, shift, rotate, mint id, preserved name), own write against
   ExifTool's on a copy: picture bytes equal; every non-target segment equal; EXIF entries other than targets equal with
   MakerNotes offset unchanged; ExifTool `-G -n -struct` read of both equal on the field set plus MakerNotes, ICC, MPF;
   `-validate` adds no warning; Pillow decodes; Windows property system agrees.
3. WLPG round trip on 20 copies; SmugMug upload of 20 copies to a private gallery (the owner's).
4. Fuzz: truncation, flipped segment headers, two XMP, two EXIF, zero slack, IPTC without charset, UTF-16 packet, full
   64 KB XMP: refuse or clean, never bytes.
5. Idempotence: write, read, write: no changed range.
6. Off by default: `OWN_WRITER = False`; the check tool reports counts; the owner turns it on; shadow mode stays on the first weeks.

## Build order (each merge about 1,200 lines or fewer, trunk whole)

- **M1** census and corpus tool (read-only): `scripts/census_segments.py` and a `metadata-check` skeleton; counts into
  docs/domains/files.md. Confirms the format split and the extended-XMP scope.
- **M2** XMP packet editor (pure; shared with the clip patcher).
- **M3** EXIF and IPTC block editors (IPTCDigest, charset).
- **M4** wire into `in_place.patch_jpeg` behind `OWN_WRITER = False` with shadow comparison; the %TEMP% header copy goes.
  Merge 4b-1 built `segment_edits.new_header` (no caller). Wiring hazards for 4b-2 (files owner, 2026-11-05):
  (a) `field_values.write`'s `if patched: return ()` must return new_header's stale tuple, even when the patch changes
  no byte; (b) new_header takes the journal's values keyed by field (`EXIF:Orientation`), not the ExifTool params keyed
  by `WRITTEN_AS`; (c) the shadow comparison includes a packet holding tiff:Orientation and an EXIF with an IFD1
  Orientation. 4b-2 also deletes the %TEMP% header copy, `in_place._fitted` and the ExifTool-copy arm of `jpeg_ranges`.
- **M5** own whole-file JPEG rewrite beside the photo, with the reserve (XMP already carries ExifTool's padding, 2,425 bytes (`xmp_packet.RESERVE`), in 4,355 of 4,366 wrapped packets, counted 2026-11-03, so the reserve is mostly EXIF, which never has 64 bytes of slack: 0 of 6,373 sampled);
  `_refuse_dropped` arms go for JPEG; MPF files kept length-stable or refused. The 512 B reserve branch is closed.
- **M6** optional own read for the journal's field reads, dropped: M1 timed a header read at 23 ms against ExifTool at 52 ms for one photo and 28 ms in a batch (warm cache, 2026-11-03).
- **M7** MP4/MOV clip patcher on the same XMP editor.

## Risks ExifTool protects us from today

MakerNotes (all 1,239 sampled Canon notes and 251 of 258 Sony notes in photo_index were already moved by another program, #2409; M3 trusts no stored shift except the Canon footer, Canon only); MPF offsets; vendor trailers; XMP/RDF variants (a mis-located property
shows two titles); IPTCDigest semantics (wrong either way makes WLPG or Lightroom discard or overwrite); UCS-2/Latin/UTF-8
encodings (mojibake in Explorer); the minor errors `-m` swallows (our writer refuses rather than guesses; the fallback to
ExifTool stays for anything not fully understood); Composite fields (the indexer's read stays on ExifTool). The hardware
fault behind #2377 is addressed by neither writer; the picture check stays.

## Open for the owner

Rewrites grow each file about 2 KB (M5); whether bakes move off ExifTool; the IPTCDigest refresh (new behaviour); the
SmugMug round trip; extended-XMP scope after the census; do stale EXIF/IPTC keyword copies matter (owner 2026-11-03: no,
so an XMP-only keyword write stays an open option).

# A clip's Date Taken in its QuickTime headers: SmugMug (and any reader of mvhd) shows the date TagPup saved
Live count: 681 of photo_index's 2,910 clips hold an XMP Date Taken and a zero or unset header (all .mp4; 669 are the
name-dated clips of change 8261); kr-track 0 of 5, renton_parkrun 0 clips; every row of photo_index is on the share
(counted read-only 2026-11-13; `raw_metadata` agreed with the file's mvhd on zero-or-not in 150 of 150 sampled).
109 more hold a header that disagrees with their XMP date after the UTC shift; the owner reviewed them (2026-11-13,
`reports/clip_date_disagreements.csv`, untracked): the 17 whose header holds a year 1981-1989 and the 81 others (73
within minutes, 7 about 2 h, 1 at 26 h) take the XMP date in their header; the 11 about 9 h later keep their header (the
owner reads them as a UTC difference). The one-off therefore covers 681 + 98 = 779 clips and leaves those 11 alone.
Domains: Media (the patch's ranges, Convert), Files (containers' read-only walk of trak/mdia, the write door, reviewed
beside); integration reviews the seam.

Owner's answers (2026-11-13): (1) the QuickTime dates of a clip are written, reversing 2026-11-08 "never written"
(modification times untouched); (2) the Date Taken's digits are written as if UTC ("local as-is"; SmugMug shows them as
local), though a camera stores true UTC; TagPup's reader keeps preferring XMP; (3) the 681 existing clips are touched by a
dry-run-first, journaled, undoable one-off; (4) of the 109 clips whose header disagrees with their XMP, 98 take the XMP
date in their header and the 11 about 9 h later keep theirs (see the count above); (5) Convert stops carrying creation_time
through ffmpeg; the carry through the clip save is the only writer.

| Mechanism | The ONE place it lives | Its owner | New or existing |
|---|---|---|---|
| Where a clip's creation times are (every mvhd, tkhd, mdhd; v0 = 4 bytes, v1 = 8) and the seconds since 1904-01-01 | `tagpup/files/clip_patch.py` (`_header_ranges`, folded into the one `Patch`) over `containers.Video.headers` (a read-only walk of moov/trak/mdia) | Media (containers: Files) | new |
| Write a clip's Date Taken into XMP and headers in one journaled patch, settled by bytes, undone per box | the existing clip save: `tagging.save_photo` -> `file_changes.write_fields` -> `field_values._clip_written` -> `in_place.patch_clip` | Files' door, Media's ranges | existing: one arm |
| Convert's copy dated | the carry (`convert._carrier`) through the same save; ffmpeg carries no creation_time (`DATED_TYPES` / `UNDATED` deleted) | Media | existing, one branch removed |
| The date the library reads | unchanged: `dates._clip_dates`, XMP first, a camera's header as UTC (2026-11-02 item 8) | Files | existing |

Rules the cuts keep:
- A header TagPup writes is never written without an XMP date beside it in the same patch (so the reader never reaches
  the header of a clip TagPup wrote); an undo that gives back "no XMP date" gives back the header's own bytes.
- Each box is given back its own old bytes (about 1% of clips have tracks of one kind that disagree): the patch records
  each box's old digits, `row.before` holds them per kind in box order; an undo refuses (`CLIP_NOT_PATCHED`) when the
  box count no longer matches.
- Two early returns skip a write whose XMP already holds the date: `in_place.patch_clip` ("nothing to write") and
  `convert._carrier` (skips a copy whose XMP holds the date). The header is part of both conditions, each with a test that
  fails on the old code (it is the whole case of the 681).
- Version 0 cannot spell a date before 1904 or after 2040: the picker refuses it, the patcher has no branch for it.
- ffmpeg is never given the date: `-metadata creation_time=` without a `Z` stores local as UTC + the zone offset
  (measured: 07:00 -> 14:00).
- Fixtures are the headers of real clips (a v0 two-track MP4 with zero headers, a v1 clip, a three-track .mov),
  captured by container, version and track count, not drawn by hand.

Steady-state writer: every Date Taken a clip is given, from the panel or by Convert's carry, writes its XMP and its
headers in one patch, no command run. A clip added with zero headers and a date in its name is dated in the library by
`dates.name_date` and its file is not touched (2026-11-13, never automatic): its header is written when the owner saves
its Date Taken.
One-offs: `scripts/date_clip_headers.py`: lists the rows of kind `video` holding an XMP date and a zero or absent
`QuickTime:CreateDate` (and the 98 named from the CSV: a `--paths` list, the 11 left out), and saves each clip's own
Date Taken through the same journaled clip save (never its own byte writer); dry run by default, `--apply`, `--limit`, TagPup stopped, one undoable change, "wrote N" from the files
changed. Deleted when its dry run says 0 left (the merge saying so in a line `date_clip_headers: dry run 0 left`).
Cuts:
1. Media, with `containers.py` reviewed beside Files (about 700 lines): `containers` walks trak/mdia, `Video.headers`;
   `clip_patch._header_ranges` and the seconds conversion; `fields` puts the three QuickTime date fields in the clip's
   date group and `CLIP_OUTSIDE_XMP`; `patch_clip`'s nothing-to-write condition includes the header; `_clip_written`
   hands the date through; `tagging.save_photo` sends it. Lifts `tests/test_a_clip_dated_through_the_journal.py` (C5)
   (headers set in every box, every other byte unchanged, undo gives each back), a header range in
   `test_a_clip_patch_cut_short`, the real-shape fixtures in `test_container_layout`; the C5 text in `tests/owners.py`,
   the DECISIONS row, `docs/domains/media.md`, the comment in `dates.py`.
2. Media (about 250 lines): Convert: `clip_convert.arguments` always clears creation_time, `DATED_TYPES` / `UNDATED` and
   the module's paragraph deleted; `_carrier.plan_one` compares the header; C7 test: a .mov made smaller and an old
   .wmv with an XMP date come out with the headers set from the carry.
3. Media (about 250 lines): the one-off and its dry-run test, its row in `tests/owners.py`; run by the main session
   after a backup, TagPup stopped, `--limit 1` first.

## Cut 1's design (main session, 2026-11-13, after the first worker's probe; #3004)
ExifTool (`-G -n`, with or without `-a`) answers ONE value per kind of header (the first tkhd, the last mdhd), so a
per-box `row.before` could not be verified or compared through it. Decision A: `field_values.read` answers a clip's three
header fields (QuickTime:CreateDate = every mvhd, TrackCreateDate = every tkhd, MediaCreateDate = every mdhd, each a list
in box order) from TagPup's own walk when they are in `wanted`: a new `in_place.headers_held(path)`, bounded as
`packet_held` is, calling `containers.read` and a pure `clip_patch` function from seconds to date text. When they are only
in `also` (the indexer's `wanted=[]`, `metadata.py:114`) `raw_metadata` stays ExifTool's. So the before, the conflict
check, the verify and the read-back compare the same per-box lists exactly. Not B (patching `row.before` after the fact,
matching by membership: it can record an undo as done without writing).
- containers.py: `CONTAINERS` moov also walks trak; add `"trak": {"mdia"}`, `"mdia": frozenset()`; `Box` gains version and
  the stored creation seconds for mvhd/tkhd/mdhd only (one version byte, three flag bytes, then 4 bytes (v0) or 8 (v1));
  `Video.headers` in file order.
- clip_patch.py: `patch()` takes the dates; `_header_ranges` writes the creation field at payload+4. Strict conversion
  `YYYY:MM:DD HH:MM:SS`; `0000:00:00 00:00:00` is 0 (the 681 clips). A list of one date sets every box of that kind (a
  save cannot know the box count when it plans); a list of n sets each box in turn and must equal the box count, else
  None -> CLIP_NOT_PATCHED.
- in_place.patch_clip ~384 (#3002): the nothing-to-write condition also checks the header dates; boxes already holding the
  date are skipped.
- field_values: `_clip_written` (349-388) passes the dates; `clip_refused` (338) holds the invariant: a write naming a
  header field without XMP:DateTimeOriginal is refused (an undo naming XMP:DateTimeOriginal cleared passes).
- fields.py: the three fields join `_CLIP_WRITE_FIELDS["date"]` (377-379) and `CLIP_OUTSIDE_XMP` (148);
  `date_taken_fields(clip=True)` (456-464) returns them, so `tagging.save_photo` sends them with no change of its own;
  fix the 'never written' wording (34-37). `save_photo`'s `dates_written` then includes them: `record_dates` writes
  QuickTime:CreateDate into the page record, which is what ExifTool reads back.
- Refusal of a date before 1904 or after 2040 (v0): at the date input's validation (find where the panel's Date Taken
  is checked; `core/validation.py` has none).
- dates.py 37-49 'Never written' comment changes; lift tests/test_a_clip_dated_through_the_journal.py (lines 7, 123-149).
- Cut 2: a date with a fraction or time zone (`segment_edits._READ_DATE` allows them) fails the strict conversion
  loudly; Convert's carry hands the date over without them.

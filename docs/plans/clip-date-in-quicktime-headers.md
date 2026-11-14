# A clip's Date Taken in its QuickTime headers: SmugMug (and any reader of mvhd) shows the date TagPup saved
Live count: 681 of photo_index's 2,910 clips hold an XMP Date Taken and a zero or unset header (all .mp4; 669 are the
name-dated clips of change 8261); kr-track 0 of 5, renton_parkrun 0 clips; every row of photo_index is on the share
(counted read-only 2026-11-13; `raw_metadata` agreed with the file's mvhd on zero-or-not in 150 of 150 sampled).
109 more hold a header that disagrees with their XMP date after the UTC shift (decision 4 below, not in this plan).
Domains: Media (the patch's ranges, Convert), Files (containers' read-only walk of trak/mdia, the write door, reviewed
beside); integration reviews the seam.

Owner's answers (2026-11-13): (1) the QuickTime dates of a clip are written, reversing 2026-11-08 "never written"
(modification times untouched); (2) the Date Taken's digits are written as if UTC ("local as-is"; SmugMug shows them as
local), though a camera stores true UTC; TagPup's reader keeps preferring XMP; (3) the 681 existing clips are touched by a
dry-run-first, journaled, undoable one-off; (4) the 109 clips whose header disagrees with their XMP are NOT in this
feature: the owner reviews a list and may decide to overwrite the XMP instead; (5) Convert stops carrying creation_time
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
`QuickTime:CreateDate`, and saves each clip's own Date Taken through the same journaled clip save (never its own byte
writer); dry run by default, `--apply`, `--limit`, TagPup stopped, one undoable change, "wrote N" from the files
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

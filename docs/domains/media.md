# Media (clips): the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-media.md`: its Scope, the contract the other domains rely on, its
invariants and guards, and its scenarios C1 to C9 (C for clips: M2 to M7 are the video milestones' labels). It was
carved out of Files and sync, with two scenarios of Views, on 2026-11-13 (#2926, DECISIONS 2026-11-13): no product code
moved. This page states each of the seven conditions of health (docs/OWNERS.md) with its evidence and date. A
condition not met is the domain's queue.

## What the domain holds (counted 2026-11-13, read-only, `db.readonly_uri`)

| Library | Photos | Clips | .mp4 | .mov | .m4v | Of an old format (.wmv, .mpg, .avi) | Clips never read |
|---|---|---|---|---|---|---|---|
| photo_index | 59,319 | 2,910 | 1,733 | 1,174 | 3 | 0 | 0 |
| kr-track | 2,181 | 5 | 5 | 0 | 0 | 0 | 0 |
| renton_parkrun | 1,309 | 0 | 0 | 0 | 0 | 0 | 0 |
| another library file in `data/` | 2,065 | 5 | 5 | 0 | 0 | 0 | 0 |

A clip is a row of `photos` with `kind = 'video'`; its row, journal and write door are a photo's. No clip of an old
format is left in any library (Convert has run). TODO (2026-11-13): count the clips that hold an XMP packet, and those
a browser cannot play (`clip_plan` asks ffprobe of the file; not counted from the rows).

## What the domain answers

| Rule | Where | Scenario |
|---|---|---|
| A clip's frames and its streams' codecs are read only through ffmpeg in `files/video_frames` (never raising into the caller, no child left running) | `tagpup/files/video_frames.py` | C1, `tests/test_frames_of_a_video.py` |
| A clip's tags, title and Date Taken are patched in place into its XMP (`clip_patch` says where; `in_place.patch_clip` writes), never by ExifTool's rewrite; one the patch cannot place is refused before anything is written | `tagpup/files/clip_patch.py`, Files' `in_place.py` | C2, C5, C6 |
| A video is played by `/api/video-file`: a range at a time, at most `VIDEO_SPAN` (4 MiB), 206 / 416 / 404 / 503 / 409 as the route's docstring says, to this PC only; a stream that cannot go on ends short; a share not answering is one bounded look (`services.photos.video_bytes`) | Files' `services/photos.py`, `files/shares.read_span` | C8 (`tests/test_playing_a_video.py`) |
| A clip the browser does not play (an error, or its sound with no picture: `videoWidth` 0 once its first data is loaded) is asked of `/api/clip/convert-plan`, and the photo panel says the server's sentence while that clip is still the one shown (`web/tagpup/media.js`); the plan is `files/convertible.plan_for` of the codecs one bounded ffprobe run finds (`services.photos.clip_plan`, `video_frames.streams`): NOTHING or REENCODE, an older format always REENCODE; it writes nothing (DECISIONS 2026-11-09) | `tagpup/files/convertible.py`, `web/tagpup/media.js` | C8 (`tests/test_what_a_clip_needs_to_play.py`, `tests/frontend/video-viewer.test.mjs`) |
| A clip's picture in the grid is its poster, the middle frame ffmpeg takes (`files/video_frames.poster`), kept as a photo's thumbnail is; ffmpeg is a prerequisite checked at start (DECISIONS 2026-11-06) | Files' `files/thumbs.py` (`poster`), Views' `services/thumbnails.py` | C9 (`tests/test_a_clip_shows_its_poster.py`) |
| Convert and Make smaller: one journaled change never undone, the copy on its disk before it is recorded, the original deleted for good last | `tagpup/services/convert.py`, `tagpup/files/clip_convert.py`, `web/tagpup/clip-convert.js` | C7 (`tests/test_a_clip_converted.py`, `tests/frontend/clip-convert.test.mjs`) |

- **A clip's Date Taken** (DECISIONS 2026-11-08, A5 (D) REVISED) is written into its exif:DateTimeOriginal, and
  into its xmp:CreateDate only where it holds one (`fields.held_dates`, decided when the write is planned: 41
  photo_index clips held one, counted 2026-11-08); its QuickTime dates are never written. The index dates a clip by its XMP dates
  first (`dates.CLIP_XMP_DATE_FIELDS`), then its QuickTime ones. The rows dated before that rule (photo_index 80
  of its 2,179 clips then, none by its year; kr-track and renton_parkrun none) were dated again from their own raw_metadata
  by a one-off, applied on photo_index as the journal's "rederive clip dates" (undone from History as any change of
  rows: `journal.RETIRED`) and deleted once no clip was apart (#2734). C5:
  `tests/test_a_clip_dated_through_the_journal.py`.
- **A clip whose file holds no date** (its QuickTime dates zeros, no XMP date) is dated by the whole date in its file
  name at midnight (`dates.name_date`: `YYYY-MM-DD`, `YYYY_MM_DD`, `YYYY.MM.DD`, `YYYYMMDD`), never by its folder's
  name (DECISIONS 2026-11-13). Its year is the year of its Date Taken alone (`dates.photo_year`): a clip with no Date
  Taken has no year, neither its folder's nor a year alone in its name, so the owner finds it among the undated to date
  it by hand (DECISIONS 2026-11-13, #2941); a photo keeps its year from its file or folder names. The one-off
  `scripts/date_undated_clips.py` writes the name's date into each such clip's XMP, its --apply first dating the rows
  again and clearing the year of the clips with no date in their name (photo_index, counted read-only 2026-11-13: 669 to
  date, 11 to clear). C5: `tests/test_a_clip_dated_by_its_name.py`, `tests/test_dating_clips_by_their_names.py`.

## The seven conditions (first stated 2026-11-13, at the carve)

1. **No reach-ins.** Met for what the carve moved: Media calls Files' write door (the Integration charter's contract
   row 1) and adds none; its clip modules are reached by Files' index (row 73, `video_frames.frames`), the viewer
   (row 69) and the thumbnails (row 74, the poster), each a row with its guard. The clip parts of Files' modules
   (the charter's "Not yours" line) stay Files' code, reviewed by both owners: a shared review, not a second owner.
2. **No rule written twice.** Met: whether a browser plays a clip is `convertible`'s alone; where a patch goes,
   `clip_patch`'s; ffmpeg runs in `video_frames` (reads) and `clip_convert` (the one write).
3. **No hidden coupling.** Not yet stated with evidence: the next pass of this owner checks that a clip's patch, its
   settle and its read-back change together by test, not by convention.
4. **No concept that could collapse.** Met: a clip is a photo row of another kind, not a second table or write path.
5. **No branch without an instance.** Not yet restated: the old-format arm of Convert has 0 rows left in every library
   (counted above); the next pass says whether it stays (the owner adds clips from old cameras) or goes.
6. **Scenario tests, each file owned and mapped.** Met: C1 to C9 each have files in `tests/owners.py` (Media's test
   files, counted 2026-11-13: 18 scenario, 3 unit), `tests/test_the_test_map.py` green.
7. **No open data or wrong finding.** Met (`tools/add_findings.py --open`, 2026-11-13: 4 open, all low).

## History

- 2026-11-13: the domain carved out of Files (F12 to F17 and F19 became C1 to C7) and Views (V15 and V16 became C8 and
  C9); the clip patch tests moved from F1 to C2; the clip Date Taken section moved here from docs/domains/files.md and
  the play, plan and poster rows from docs/domains/views.md (#2926).

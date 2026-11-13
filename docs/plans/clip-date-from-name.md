# Clip Date Taken from its file name: an MP4 with zeroed date boxes is dated, in the library and in its XMP
Live count: 680 of photo_index's 2,910 clips have no Date Taken (all .mp4, every QuickTime date zeros), counted
read-only 2026-11-13; 669 carry a date in the file name, 11 in the folder name only, none in neither. kr-track and
renton_parkrun not yet counted: the worker counts them read-only first.
Domains: Files and sync (the indexer's read, the journaled save), Views (the sort and year read the same `taken`);
integration reviews the seam.

| Mechanism | The ONE place it lives | Its owner | New or existing |
|---|---|---|---|
| A date in a name (`YYYY-MM-DD`, `YYYY_MM_DD`, `YYYYMMDD`), file name first, then the folder | `tagpup/core/dates.py`, one function | Files | new |
| A clip's `taken` when its file holds no date: the name's date, at 00:00:00 | `dates.date_taken` / `_dates_given`, after the clip's own dates, so a real date always wins | Files | existing, one arm added |
| Write a clip's Date Taken into its XMP | the journaled save the photo panel uses (`tagging.save_photo`, `fields.date_taken_fields(clip=True)`, `in_place.patch_clip`) | Files | existing: reuse |
| The year and the order that read `taken` | unchanged: they read `dates.date_taken` | Views | existing |

Owner decisions (2026-11-13): the file-name date wins over the folder's (415 of the 680 names differ from their
folder's date); the date is written into each clip's XMP; the library reads it where the file has none.
Steady-state writer: a clip with zeroed dates added afterwards is dated by the index from its name (the fallback above),
with no command run; the file is not touched. A real date in the file always wins over the name's.
One-offs: `scripts/date_undated_clips.py`, dry run by default, `--apply`, one library; it lists the clips the index
holds with no `taken`, takes each name's date (`dates.name_date`), and saves it through the same journaled clip save
(never its own XMP writer); a share that does not answer is skipped and said; it reports `wrote N`. Deleted when its dry
run says 0 left on the three libraries.
Cuts: one worker, one concern, about 400 lines: the function and the fallback with their scenario test (a clip of
zeroed dates is dated by its name, a real date wins, the sort and the year follow), then the script. The app is stopped
for the apply (as `fill_picture_sizes` was).

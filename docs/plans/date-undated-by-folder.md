# Undated photos in a full-date folder are dated by the folder: one script, one change in History
Live count: photo_index 777 rows with no Date Taken in a folder named YYYY-MM-DD (767 photos, 10 clips; 344 in 2018 folders), counted read-only 2026-11-15; kr-track 0; renton_parkrun 4 (the owner asked for the 777 only).
Domains: Files (the journaled Date Taken write); no other.

| Mechanism | The ONE place it lives | Its owner | New or existing |
|---|---|---|---|
| The date in a folder's name (`YYYY-MM-DD`, the file's own folder, midnight) | `tagpup/core/dates.py`, one function beside `name_date` | Files | new |
| Write a photo's or clip's Date Taken into its file and follow it in its row | `file_changes.write_fields` with `fields.date_taken_fields` (the panel's save; a clip with `clip=True`), as the deleted `tagging.date_clips_by_name` called it | Files | existing: reuse |

Steady-state writer: none, by decision (DECISIONS 2026-11-15): a photo added undated afterwards is not dated by its folder.
One-offs: `scripts/date_undated_by_folder.py --library photo_index [--apply]` calls the one writer above; dry run by default (counts and ids), `--apply` with TagPup closed, a snapshot first, one change in History (undoable). Deleted when its dry run says 0 left (merge line `date_undated_by_folder: dry run 0 left`).
Cuts: one worker, one branch, under 400 lines.

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: Media: docs/plans/clip-date-in-quicktime-headers.md (One-offs) says the one-off takes the 98 named clips as "a `--paths` list"; it reads the owner's reviewed CSV (`--csv`: file_name, folder relative to the root, difference_hours; the 8 to 10 h rows left out), as cut 3's brief chose, each matched as the clip's path under its root's place on this machine (paths.key), never by the row's spelling. The plan's line is stale. | open |
| B-2 | 2026-11-13 | low: Media: `scripts/date_clip_headers.py`: a clip whose XMP Date Taken no header holds (before 1904, after early 2040) is listed, refused with the save's sentence and counted, and stays in "Left to date", so while one exists the dry run never says 0 left (the deletion rule). 0 such clips on the three live libraries (counted read-only 2026-11-13 through clips_to_date_headers: photo_index 680 listed, 0 refused; kr-track and renton_parkrun 0 listed), so no arm splitting them out was written. | decided: no live instance; the dry run names any such clip as refused |

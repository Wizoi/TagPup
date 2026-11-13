# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: #2941, DECISIONS 2026-11-13 and docs/plans/clip-date-from-name.md call photo_index's 11 clips with no date in their name "dated only in their folder", but 9 of the 11 hold a year alone in their file name (shapes `YYYY-MM ...`, `YYYYMon ...`, `... YYYY-YYYY ...`) and 2 a year in their folder only (counted read-only 2026-11-13). Decided here, not copied from the brief: a clip's year is the year of its Date Taken alone (`dates.name_date` takes a whole date only), so all 11 lose their year, the owner's rule "no Date Taken, no year"; said in `dates.photo_year`'s docstring and the DECISIONS row. `tests/test_a_clip_dated_by_its_name.py` (a `2016-07` clip) failed on the old code. | decided: no Date Taken, no year, for a year alone in the name too |
| B-2 | 2026-11-13 | low: `store.photos.clips_to_redate`'s docstring said "one walk of the clips' own index", but its statement (`kind = ? AND taken IS NULL`) searches `idx_photos_taken` on photo_index (EXPLAIN QUERY PLAN, read-only 2026-11-13); the docstring now says so. Text only, no behaviour. | fixed: this branch |

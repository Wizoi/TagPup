# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: Views: under the Video filter the whole library ordered by caption walks `idx_photos_caption` past every photo to find the clips (`kind` is in no index, and `all` is not `ranged`, so `_walks` never counts it): measured warm on the live photo_index (61,797 rows, 2,179 clips, read-only), `all_ids` 193 ms and `view_ids` 228-244 ms, against 22 / 66 ms for Both; by name 51 / 88-99 ms, by Date Taken 49 / 94-106 ms. A folder with its subfolders, a year and a month under the filter are counted and read by their range (top folder 100-108 ms, a year of 228 clips 8-9 ms). Not changed in this cut (the brief keeps `ranged`); marking a filtered scope `ranged` in `_of_kind` would let `_walks` decide by the clips' count. | open |
| B-2 | 2026-11-08 | low: Views: decisions made building the server half of the Photos / Videos filter: `store.PHOTO` ("photo") added beside `VIDEO` (the brief took it as defined; it was not); `of` is the source's count by its own where without the kind, from the same transaction, and `total` itself (no second count) under Both; the reply's `media` is `null` for Both; `media` that is not exactly `photo` or `video` (a case, a plural, a number, a list) is refused, never read as Both; the spec's page address (`?view=...`, line 191) is left to the page's cut, which adds the control. | decided: server cut of the media filter; the page's cut documents its address |

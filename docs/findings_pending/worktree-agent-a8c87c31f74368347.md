# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 | low: the search box's Clear clicked twice before the first Back has arrived calls history.go(-back) twice (search.js clearSearch reads searchBack from the place it is still on), so the page goes two views back past the one before the search; user action: two quick clicks on Clear (or Enter twice on an empty box). From the advanced filtering cut 1b brief | fixed: c9146d00 (library-view.js leaveSearch with the leaving guard; the V29 test of Clear clicked twice failed on 489691da: history.go [-1, -1] where [-1] was due) |
| B-2 | 2026-11-19 | low: a filter made by clicking rows in Filter mode is also put in the search box's Filters lists (search.js searchFollows fills All of and None of from the open search), so the same filter shows twice: as chips above the grid and as chips under Filters; user action: turn Filter on, click two rows, open Filters. Left open: whether the search box hides a filter or edits it is a design choice for the plan (docs/plans/advanced-filtering.md), not a quick fix | open |
| B-3 | 2026-11-19 | low: filter-bar.js refused a mark once the filter held MAX_MEMBERS rows in all, stricter than the server, which caps each list of a search and each union in it (services.library_view search_of); user action: a filter of 1,000 rows marked not, then one more row marked any of. Fixed by library-source.js listOverCap, which the search box's own check now uses too; the V29 cap test failed on the code before the fix (one ids request where two were due) | fixed: 5e8a118c |

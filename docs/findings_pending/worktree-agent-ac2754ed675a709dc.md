# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: `/api/library/ids` reads `after` and `limit` from the request (`_source_asked` in tagpup/web/tagpup_routes.py returns them as `_after, _limit`) and uses neither: the routes have no cursor, the page's cursor is its place in the id list and the cards route takes the windows. The two parameters are dead, and the Views regrouping brief's "ids route with a cursor" is the id list plus `cards` windows (tests/test_library_views_scenarios.py scrolls it so). Delete the two. | open |
| B-2 | 2026-10-15 | low: `tagpup.services.tagging.apply_suggestions` still reads `entry.get("people") or []` after `suggesting.offered_tags` was made to read `entry["tags"]` and `entry["people"]` (0 of 1,662 live suggestion rows lack either list; `store.suggestions.entry` always makes both). Same case, one arm left; drop the `.get(...) or []` when that function is next touched. | open |
| B-3 | 2026-10-15 | low: CLAUDE.md ("A bulk write must tell the index what it wrote") names `record_tags_in_index()`, which does not exist; the function is `tagpup.store.photos.record_tags`. Fix the sentence. | open |
| B-4 | 2026-10-15 | low: Views regrouping A kept tests/test_thumbnail_routes.py (the thumbnail route's cache headers, 304, 503, and the Roots gate on ids/navigator/thumb for a rooted library) instead of deleting tests/test_library_view_routes.py whole: no other test holds the route's ETag/immutable/Retry-After contract or the rooted ids/cards spelling, so deleting would have lost it. The rooted-library class belongs to the Roots scenarios (D7/I6) and may be lifted into tests/test_roots_scenarios.py by the Data owner. | open |

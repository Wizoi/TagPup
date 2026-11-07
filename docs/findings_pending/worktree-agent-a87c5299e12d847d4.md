# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-07 | low: /api/library/selection/tally's "total: photos selected" still counts clips as photos (a separate count, out of #2569's scope, which gave the library view's counts by kind). | open |
| B-2 | 2026-11-07 | low: the clip statement of a library view (`SELECT p.id FROM <from_> WHERE (<where>) AND p.kind = 'video' ORDER BY p.id`) is a SCAN of photos for the whole-library view, as no index holds `kind`: +40 ms per ids request on 59,618 photos (20 -> 60 ms warm, measured by the #2569 worker). A partial index `ON photos(id) WHERE kind = 'video'` would be a migration: the owner's call. The plan test accepts the scan by name. | open |

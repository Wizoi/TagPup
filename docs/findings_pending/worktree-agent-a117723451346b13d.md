# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: Integration: Apply All (`/api/folder/auto-apply`) answered a refused batch (more than 5,000 photos, cancelled before its turn: `Refused`) 409, where bulk tags answers the same refusal 400; through `responses.of` it is now 400 as every route answers `Refused`. The page reads only `error`. Decided by the worker, for the main session to confirm. | decided: worker, mapper branch |
| B-2 | 2026-11-08 | low: Integration: eight routes answered a service's `NotFound` with Flask's HTML 404 page (`face-matches`, `face-matches-unmatched`, TagTuner's `face-crop`, `person/rename`, `person-faces`, `unmatched-faces/person-matches`, `faces/match-bulk`, and the face writes), which `api.json` cannot parse; through `responses.of` they are the JSON error, status unchanged (`tests/test_tagpup_routes.py` `WhatAServiceRaisedIsAnsweredOneWay` failed on the old code). | fixed: mapper branch |
| B-3 | 2026-11-08 | low: Integration: `face_routes.faces_write` and TagTuner's `tags/merge`, `person/rename` answer a refused Result `refuse(400, result.refused)` by hand, ignoring `refused_kind` (`responses.refused` reads STATUS); no live instance of another kind from these services was looked for. | open |

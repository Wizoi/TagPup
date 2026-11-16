# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-16 | low: a folder's Refresh answers `folder: "running"` whenever the library's walk is held in this process, which is a run of the `sync` job OR another folder's sync (the watcher's, or the same Refresh pressed again while its first sync still walks); the plan's sentence for it ("The whole library is being checked now; this folder is part of it.") is wrong for the second. Cut 2 (Views) should say a sentence true for both, e.g. "Another check of this library is under way; press Refresh when it ends." user action: Refresh pressed twice within one folder sync (seconds over the share). | open |
| B-2 | 2026-11-16 | low: `recurring.run_claimed` answers why "running" both for a claim held by a run of the `sync` job and for the library's walk held by a folder's sync; a Refresh whose `start_job` thread asks while the watcher (or a Refresh in another tab) syncs a folder of the same library is told `whole: "running"` ("a run of it is under way already") and no whole sync starts: the gone row stays until the daily run. user action: Refresh on one folder while the watcher syncs another folder of the same library. | open |
| B-3 | 2026-11-16 | low: POST /api/sync with `apply` and no `folder` runs a whole sync through `Runtime.sync` with no claim of the `sync` job and no walk (beside the daily run or the watcher's, #2102). No page posts it (grep of web/: only GET /api/sync); live: 0 callers. | decided: no live instance, not built |

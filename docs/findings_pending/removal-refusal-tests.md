# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 removal-refusal fix (#3200, #3228, #3229) | low: a CLI `sync --apply` queues the folders of its new files in its own process, and between two of its index jobs no `index` row of it is running in job_runs (each job claims and ends its own run; the sync itself is not claimed as job `sync`): a removal in an app in that gap passes tagpup.services.libraries.removal_refusal, and the CLI's next index job of a folder under it is refused at its first batch (store.photos.record_indexed raises NotHeld). user action: Remove a folder in an app while a CLI `sync --apply` of its library runs. | open |
| B-2 | 2026-11-19 removal-refusal fix (#3200, #3228, #3229) | low: decision for review: a job_runs row holds counts only, no folder, so another process's index, or any whole sync of the library (this process's included), refuses the removal of every folder of the library while it runs (tagpup.services.libraries.REMOVAL_WAITS), not only of the folders it reads; the owner waits for a sync or a CLI index of an unrelated folder to end before removing one. user action: Remove a folder in an app while the daily catch-up sync or a CLI index of another folder of the same library runs. | open |

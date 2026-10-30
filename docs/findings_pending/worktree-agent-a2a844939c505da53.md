# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 | low: the P3a design has `job_runs.failure` map a refusal's kind to a Failure kind in a table beside `responses.STATUS`; on the trunk its one share read (`details.get(SHARE_AWAY)`) served a failed sync, not a refused Result (`services/sync._sync`, the queue finding the share away after the walk: `result.fail` plus the flag), and no refused SHARE_AWAY or CHANGED_ON_DISK Result reaches `failure` by any path (the share's refusals end at a route or the CLI; the save's at its route). Found building P3a. | decided: no refusal-to-Failure table (a branch with no instance); the sync's failure says its kind as every failed Result does (`job_runs.FAILURE = SHARE`, beside WAITED and LOCKED), and `failure` reads no flag; a refused kind that reaches a run gets its row in `failure` the day one does |
| B-2 | 2026-10-30 | low: the Results that hand up another's refusal by its sentence alone (`face_people` twice, `file_only.merge`'s constructor, `photos` renaming after `preserve_names`, `naming_faces` twice, `bulk_edit._count`) would drop a refusal's kind (the #2148 class). Their sources refuse only as REFUSED today: SHARE_AWAY is refused by `shares.folder_refusal` alone (handed up by the index queue, `libraries.add`, `sync.include`, each carrying the kind now), CHANGED_ON_DISK by `tagging.save_photo` alone, read straight by its route. Found building P3a. | decided: not carried where no other kind arrives; a source that starts refusing as SHARE_AWAY or CHANGED_ON_DISK carries the kind through its hand-ups in the same change |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-02 | low: `tagpup/files/in_place.py` `held` and `put_back` open the photo's path themselves (`open(path, "rb")`, `open(path, "r+b")`), outside `tagpup.files.shares`, so a share that stops answering mid-read or mid-put-back is not time-bounded: the settle of a patched file on a sleeping NAS can wait as long as the OS does. No live instance yet (no patcher, 0 rows holding a record); the residual is Files', to be bounded when the patcher (M2) opens files on a share. | open |
| B-2 | 2026-11-02 | low: the doctor's two new checks (`checks.patch_records_outside_writing`, `checks.files_changed_around_a_patch`) each scan `change_files` (no index on `state`/`note`/`patch`): the note scan took 0.28 s on photo_index's 51,451 rows, a SCAN by its plan (counted read-only 2026-11-02; the library is at 36, so the patch scan, which needs migration 37's column, could not be timed there). Acceptable for a doctor run; to be recorded by design or given an index if `change_files` grows. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 | wrong: `file_changes._finish` (the settle at start) carries a PLANNED row whose file holds `after` with `held_out` False, so it is recorded done (wrote, followers run) though this process never wrote it (`file_journal.writing` is committed before the write): a bulk edit killed after its plan and before a photo's write, another window adding the same keyword to that photo, and the next start records the photo done in the change; undo of either change then takes the other's keyword out and the other's undo refuses. The forward loop's rule (held out unless the file is a bulk assignment's `alongside` file) now applies to settle's planned rows; a `writing` row holding `after` is this process's own interrupted write and stays done. | fixed: 3a3e2e34 |
| B-2 | 2026-10-30 | low: the comment in `tagpup/store/photos.py` and the guard in `tests/test_derived_writers.py` say `move_rows_in` is the one setter of a photo's path, but `services/relink_photos.py` sets `photos.path` through `journal.update` (the journal's generic writer, which the guard's `UPDATE photos` pattern never sees); no behaviour defect (a relink is a rows change `_newer_overlapping` sees). Reworded to the guard's true scope: literal `UPDATE photos` SQL in tagpup/, the generic writer aside; a column list built at run time is covered only by `follow_fields`' own assert. | fixed: f6316cea |

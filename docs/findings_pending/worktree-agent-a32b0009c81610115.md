# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | wrong: `bake-photos --apply`'s second Ctrl+C while the Walk read and planned the folder after one written died on an AttributeError instead of saying the totals, the snapshot and the way back: baking_report.apply's `counted` stayed True from the folder before while `under_way` was None, so the KeyboardInterrupt arm read `under_way.written`. `counted` is now reset with `under_way`; test_a_bake_is_written_folder_by_folder's two next-folder Ctrl+C tests (service and CLI) failed on the old code. | fixed (pending) |
| B-2 | 2026-10-25 | wrong: two programs writing one file's fields at once (two `bake-photos --apply` windows; the CLI and the server) each recorded it done: write_fields' forward write, finding a planned file already holding what it was to hold (another program wrote it since the plan), recorded it `done` in its own change (`_held_or_conflict`, the crash-settle rule) and counted it written, so the file was done in two changes, the first run said it wrote files it did not, and an undo of either took back the other's write. Shown by test_one_library_three_programs' two-bakes test (done rows [1, 2, 2] for three photos, each written once, on the old code); a file found so is now taken out of the change (file_changes._take_out_in, the one take-out with _read_back's) and said held already, as one found holding it at the plan is; a bulk assignment's file in the change for its rows alone keeps its done. | fixed (pending) |

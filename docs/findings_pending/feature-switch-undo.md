# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-20 | wrong (once a library has been switched and back): feature-switch-offers' finding 'an Undo in History of a switch back to FaceNet reinstates scrfd-adaface without settings.change's check'. Fixed at the one place: `services.journal.refusals` and `rehearse` (and so `undo`) ask `settings.undo_refusals`, which reads the face model each change's undo would put back (`store.journal.old_values`, one read of change_rows by its (table_name, row_key) index) and asks `_switch_to`, the check settings.change makes: the same sentence and counts; putting FaceNet back is never refused (not read). Scenario P10 (tests/test_a_switched_library_indexes_and_suggests_with_its_model.py, `test_the_switch_back_is_not_undone_onto_faces_the_pass_has_not_reached`): switch, switch back, a photo indexed under FaceNet, History lists the switch back not undoable with the counts, rehearsal and undo refused, the library still FaceNet; it failed on the old code ('undoable': True). | People | fixed |

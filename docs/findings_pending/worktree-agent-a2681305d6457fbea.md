# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-22 | low: retiring the old detector's faces (`services.retire_faces`) leaves a photo all of whose faces go faceless, and `store.faces.delete` forgets its detection (`faces_detected.forget_faceless`), so the next Suggest in its folder detects it again with the new model; an undo of the retire puts the faces back but not the `faces_detected` row (not journaled, by that module's docstring). Live, read-only 2026-10-22: 2,673 of photo_index's photos would be left faceless, 0 of them holding a `faces_detected` row now (so nothing is lost there); 7 each in kr-track and renton_parkrun, each holding one. | decided: faces_detected is not journaled (store/faces_detected.py docstring); the only loss is a second detection of a photo, and photo_index holds none to lose |
| B-2 | 2026-10-22 | low: decision, not copied: retiring re-reads the library's face model in each chunk's transaction and writes nothing once it is no longer the plan's (a switch back to FaceNet beside the run would otherwise delete every face lacking a new-model vector while the library reads FaceNet's); the run stops and says so (`MODEL_MOVED`). Scenario P8 `test_a_library_switched_back_after_the_plan_is_left_as_it_is`. | fixed: this branch |
| B-3 | 2026-10-22 | low: `launcher.BEFORE_UPDATING` named index, sync and face-pass but not retire-faces among the CLI writes to finish before an update (an update during a retiring is the owner's to avoid: a drain sees only the server's own work); `tests/test_one_library_three_programs.py` asserts that sentence. | fixed: d5bd04a6 (the sentence names retire-faces; the test's expectation with it) |

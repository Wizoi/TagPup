# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-24 | wrong: tests/test_an_index_reads_a_bake.py `test_a_bake_outlives_a_keyword_written_in_the_app_and_an_edit_of_its_picture_outside_does_not` fails on the trunk when run alone with ExifTool's kept answers (`tools/run_tests.py --no-retry test_an_index_reads_a_bake`, twice; AttributeError: 'ToBake' object has no attribute 'kind', line 447); it passes with --real-exiftool (118 s). Not the tracer's: plain unittest fails the same. Cause: `repaint` saves a solid-colour 640x480 JPEG, and teal, maroon and navy all come out 8462 bytes (counted), so the repainted file keeps its size; with the replay the app's save and the repaint are within MTIME_TOLERANCE (0.1 s), so store.photos.describes says the vector's stamp still describes the file (STAMPED) and the plan is ToBake, not NO_VECTOR. Under a loaded full run the steps are slower and it passes, which is why the gate stayed green. The product rule is right (an outside editor saving a picture of the same byte size within 0.1 s of TagPup's own write: no user action makes it); the test needs a repaint that changes the stamp, e.g. a picture with content of another size or the file's mtime moved past the tolerance, as a later editor's save would. Not fixed here (not this branch's file). | open |

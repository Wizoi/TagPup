# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 cut B, the last piece | low: tests/test_scrfd_adaface_faces.py's import-without-facenet test popped tagpup.ml.scrfd_adaface and tagpup.runtime from sys.modules inside mock.patch.dict and imported them again: patch.dict put the old modules back in sys.modules, but `tagpup.ml.scrfd_adaface` (the package attribute) stayed the new import, so tests/test_the_runtime_reaches_suggest.py's test_builds_no_model_until_one_is_asked_for, run after it in one process, patched the new module while the runtime built from the old (ScrfdAdaFace called 0 times). run_tests.py runs each file in its own process, so no tier saw it. Fixed in this branch: the test puts the packages' attributes back (addCleanup); the two files run in one process failed on the old code and pass now. | fixed: this branch |
| B-2 | 2026-10-28 cut B, the last piece | low: tests/face_rows.add_face gives a face no vector unless `embedding` is passed (58 test files call it), a shape no writer makes since every face is filed with its vector (store.faces.insert; 0 of 197,285 live faces lack one, counted read-only 2026-10-28). Every reader of a vector now inner-joins it, so such a face is simply not returned by the panel, the photo page, the person page or automatch; the fast tier and the people and files scenario tiers pass, so no test depends on it now, but a new test seeding through add_face gets a face the readers do not see. One concern of its own (a default vector changes many files' data); not done here. | open |

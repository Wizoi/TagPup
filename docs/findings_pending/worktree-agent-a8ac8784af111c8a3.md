# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-26 | low: `faces_detected` has no foreign key to `photos`, so the undo of `delete old detector rows (mtcnn)` can put back a row whose photo was deleted since the change; such a row is read as none (`tagpup.store.faces_detected`: a photo's id is never handed out again), so it changes no answer. 0 live: no `faces_detected` row lacks its photo in any of the three libraries (counted read-only 2026-10-26). | open: by design if the Data owner agrees (the row is inert; refusing the undo would need a check no reader needs) |
| B-2 | 2026-10-26 | low: `journal.UNLINKED` (`{"faces_detected": "photos"}`) is a new constant of the journal, the tables keyed by a photo's id without a foreign key; the Data owner confirms it is the one statement of that fact and that a test holds it to `KEYS` (delete-old-detector-rows branch). | open: the Data owner's review |
| B-3 | 2026-10-26 | low: `old_detector_rows.old_detector` tells MTCNN's detector names by the absence of a `model` key in the JSON, which depends on `scrfd_adaface.ScrfdAdaFace.detector` spelling its name `{"model": key}`; nothing pinned that spelling to the real class (the scenario test built the name by hand). | fixed: `tests/test_deleting_the_old_detectors_rows.py` asserts `services.faces.detector_of` of the real class gives the name the rule reads (a pin, not a bug: it passes on the old code) |
| B-4 | 2026-10-26 | low: `sync`'s dry run (`tagpup_cli.py sync`) prints `maintenance.rehearsed` but never `maintenance.failed`, so a rehearsal that raised prints "Nothing to rehearse." and exits 0, as `delete-old-detector-rows` did before this branch (found as its sibling; not fixed here: outside this branch's files). | open |

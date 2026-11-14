# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: Name faces from tags' result line says "In the open folder: N more named; ..." (`web/common/name-faces.js` `folderText`) for the folder the job was started on, which may no longer be open: open the dialog in a folder, go Back to a library view while its choice is on screen (the folder is put to rest and TagPup's button hides), press Read the plan and Yes. The plan and the write are still the folder the dialog named, and the question names it ("In the folder "Run" and its subfolders"), so nothing is written elsewhere; only the result's "open folder" is no longer true. Fix: say the folder by its leaf, as the question does (`nfDialog.chosen`). Found while moving the whole-library item to TagPup's gear; the scenario is `tests/frontend/name-faces.test.mjs`, "the view changed under the open dialog". | open |

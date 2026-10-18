# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-18 | low: a Suggest or Just look refused because its library is set to a face model it cannot keep vectors of (`FaceModelNotReady`, phase 3 merge 2b) was logged as a failure, at ERROR with its traceback, where a refusal beside another run's models (`OtherModelsInUse`) is logged at INFO: the server log showed a stack for a setting the owner chose. Fixed: `tagpup.jobs.suggestions._log_failure` logs both refusals (and a looking run's wrapper of one) at INFO; tests/test_the_new_face_model_writes_nothing.py failed on the code before (two ERROR lines with tracebacks). | fixed: the refusals named once (`_REFUSALS`) |
| B-2 | 2026-10-18 | low: the start-up warm-up of a library set to a face model it cannot keep vectors of built its CLIP model before the face model was refused (built, not loaded: `Runtime._warm` made CLIP first), where `Runtime.begin` builds nothing for it. Fixed: the warm-up asks for the face models first, as begin does; tests/test_the_new_face_model_writes_nothing.py failed on the code before (one model built). | fixed |
| B-3 | 2026-10-18 | low: `Runtime.begin` for a library refused its face model still makes the library's `PhotoIndex` object (in `_index`, before the faces refusal) and keeps it in the runtime's cache. It opens no connection and reads nothing until a run loads it, and it is the same object a later Suggest on that library uses. | decided: no read and no write, so no reorder; the cache is let go by the idle release as any other |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-22 | low: the face gate read only faces holding both models' vectors (`store.faces.under_both`), so a library the face pass has not reached (photo_index: 187,784 FaceNet vectors, none under AdaFace) gave nothing to read, and phase 4a's reading by year could not be taken there through it. Phase 4a cut 1 (worker). | fixed: `face_gate.read_alone` reads the library's own model alone through `store.faces.under_each` (`measure_face_gate.py --alone`); TheGateReadsByYearGap in tests/test_the_gate_reads_both_models.py fails on the old code |
| B-2 | 2026-10-22 | low: `store.faces.ERA_LAST` is 2026 as the owner decided (2026-10-22, #1738); from 2027-01-01 a new photo's year reads undated for any reading by era (today the gate's year bands only; after phase 4a cut 2, matching), unless the bound moves. Owner to choose: a fixed bound moved each year, or the current year. Phase 4a cut 1 (worker). | open |

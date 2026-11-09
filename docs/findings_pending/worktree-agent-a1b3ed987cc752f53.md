# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-09 | low: Data: the undo matrix (`tests/test_undo_after_every_later_kind.py`) had no guard that a journaled kind is placed: of the kinds `UNDO_CHECKS` names less `journal.RETIRED`, a new one could land with no row and no reason. Now `OPERATIONS` (each kind a row makes, the row's change asserted of its kind through the new `journal_service.kind_of`) and `LEFT` (every other kind with its reason) must together equal them (`EveryKindIsARowOrLeft`); a kind injected into `UNDO_CHECKS` and the matrix's old rows each fail it, and the two new rows ("original names", "clear duplicates") hold six refusals that fail when unpromised. Closes #2748 (of its four kinds, the two re-derives are now in `RETIRED`, their writers deleted). Decided: `OPERATIONS` also lists the retired kinds a row still makes (face pass, refresh_rows, the folder bake), so the guard compares it less `RETIRED`. | fixed: undo matrix: every kind UNDO_CHECKS names is a row or left with its reason; a kept name and the sweep of repeated captions are rows |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-24 | low: tests/test_undo_after_every_later_kind.py dropped the pairs a writer made nothing in silently (16 of 133), bounded only by "more than 100 made"; one of them, (smart rename, migration), was a defect of the test (the rename was made inside the migration's context, its service migrated the copy, so the migration found nothing to do). | fixed: NOT_HELD names the 15 pairs a library cannot hold, each with its reason, and the test asserts the None pairs EQUAL it; the earlier kind is now made before the later kind's context is entered, so (smart rename, migration) is made and undone. Run: the matrix passes. |
| B-2 | 2026-10-24 | low: the same test took the migration's expected sentence from journal_service.UNDO_CHECKS, the table under test. | fixed: asserts the literal "a migration is not undone" (as the deleted test_journal test did). |
| B-3 | 2026-10-24 | low: the same test failed hard on wall-clock time (assertLess 120 s; 43 s alone, about 55 s in the gate), a flake under load. | fixed: the assertion is removed, a comment keeps the measured time; the suite's tier timing reports it. |

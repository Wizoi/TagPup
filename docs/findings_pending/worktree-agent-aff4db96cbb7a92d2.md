# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-26 | low: `retire-faces` is deleted (cut B1) but its change kind stays as `"retire faces (%s)"` in `tagpup.services.journal.RETIRED`, an undo refusal for a kind no code makes, kept because each live journal holds one applied change of it (counted read-only 2026-10-26: photo_index change 3247, kr-track 347, renton_parkrun 1225). Under the owner's rule of 2026-10-25 (a migration is a new baseline, no compatibility kept for the old model) it goes with the baseline migration that starts the journals afresh, not now: removed today, History would offer the undo of change 3247 and put back the old detector's faces. | open: removed by the baseline migration |

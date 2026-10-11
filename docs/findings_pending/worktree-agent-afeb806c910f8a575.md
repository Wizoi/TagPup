# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-11 | low: docs/DATABASE.md (job_runs `note`) still says "for the CLI's `jobs`"; the command is deleted (2026-10-11). Not edited here because the migration-31 worker owns that file; reword to "for the Activity page's own use" when it merges. | open |
| B-2 | 2026-10-11 | low: the Name faces from tags service keeps `again=` and the refusal text AGAIN (the dialog's job uses both); the `--again` wording was removed from the text. `cluster-faces` and `tags-from-faces` remain the only CLI naming commands. | open |

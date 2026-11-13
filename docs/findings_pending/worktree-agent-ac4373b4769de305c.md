# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: #2932's premise was wrong: the fourth `.db` in `data/` is `kr-track.before-people-merge-20261102-122703.db`, a backup, so the libraries are three; counted again here, photo_index holds 62,229 photos (not 59,319) and kr-track 2,186 (not 2,181), 2,910 and 5 clips; the Files and Views pages now say so. | open |

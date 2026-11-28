# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-27 | low: `containers.Video.first_mdat`, `last_mdat` and `moov_first` (tagpup/files/containers.py) were read by no module under tagpup/ and named by no doc: after T1c lifted test_container_layout's clip tests, only two of its staying tests used them, to fetch an mdat box (grep of the trunk, 2026-11-27). Deleted; the two tests read `Video.of_type("mdat")`. live: 0 (no product reader) | fixed: 05b1766d |

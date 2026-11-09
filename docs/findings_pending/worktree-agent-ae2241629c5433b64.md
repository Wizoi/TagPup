# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-09 | low: Files: scripts/move_windows_tags.py copies scripts/clear_duplicate_captions.py's Ctrl+C handling (Progress, the three stopped sentences): one rule written twice. | decided: by design; both are one-off scripts deleted after their live run, so a shared helper in scripts/ would outlive both |
| B-2 | 2026-11-09 | low: Files: store.photos.clip_rows is a SCAN of photos (no index on kind; EXPLAIN QUERY PLAN on photo_index, kr-track and renton_parkrun, read-only, 2026-11-09): 2,179 clips in photo_index, 5 in kr-track, 0 in renton_parkrun. | decided: one scan a run of a one-time command; no index added for it |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | data: `files/identity.py` `tolerating_minor_errors` (now the one path of the indexer's mint as well as the journaled identity write) reads the keyword fields before the `-m` retry with `_keywords`, which returns None on any read failure; `or {}` then makes the forced write carry no keywords, and the check afterwards is skipped when the second read fails too, so a refused read is treated as "the photo has no keywords" rather than as a reason not to force. Fix: raise the original refusal when the read before the forced write fails. No live instance counted (it needs ExifTool to refuse the read between a minor write refusal and the retry); found while fixing #1355, not fixed there. | open |

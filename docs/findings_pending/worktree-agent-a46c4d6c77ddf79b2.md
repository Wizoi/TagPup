# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-17 | low: the BULK_LIMIT refusal sentence ("Select up to 5,000 photos at a time (N are selected).") is spelled in selection.js `confirmBulkWrite` as well as selected.js `overBulkLimit` (cut 2a made the view's and the folder Delete's one function; `confirmBulkWrite` was outside the worker's files). user action: select more than 5,000 photos in a folder and add a tag. | open (one line: `confirmBulkWrite` calls `overBulkLimit`) |

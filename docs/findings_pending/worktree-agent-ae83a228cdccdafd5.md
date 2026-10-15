# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: the grid's inline title edit (web/tagpup/grid.js) read the same save-metadata reply and also said nothing of a failed follow-up rename; it now shows the same line as the details panel (found while fixing #884). Fixed with #884; no page test of its own (the panel's covers the reply shape). | fixed with #884 |

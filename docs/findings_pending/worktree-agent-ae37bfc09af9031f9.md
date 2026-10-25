# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | wrong: History offers Undo on a sync whose re-read photo a later journaled file write (here a bake; by reading the code, a Save too) then wrote, and the rehearsal and the undo refuse it ("photos N is not what change C left: mtime, size changed", journal._not_as_left): journal.refusal finds newer changes of the same rows in the row journal only, and a file write's row update is not there. Undoing the later write first does not lift it (its undo stamps the row again). Found by the bake's row of the undo matrix, pinned there (LISTING_OFFERS_IT, NOT_LIFTED_IN_ORDER). | open: journal domain, one account in why_not |
| B-2 | 2026-10-25 | wrong: a Smart Rename followed by a field write of the same file (a bake; by reading the code, a Save) cannot be undone even after the later write is undone first: the rename's undo knows its file by size, modified time and file id (file_changes._holds), which the write changed for good, and History offers the Undo the undo then refuses ("not found under the name the change gave it"). Pinned in the undo matrix (NOT_LIFTED_IN_ORDER). | open: Files domain |
| B-3 | 2026-10-25 | low: decision made in cut 3b-2, not copied: write_fields now names the photos ExifTool did not answer for (`details["not_answered"]`: a file found away, a pre-read NotAnswered, the stalled row and every row after it), the one place that knows, and the bake counts those alone as not answered (#1942); the bake's failed sentences are printed only with --reveal, since ExifTool's sentence can name the file. | open: review |

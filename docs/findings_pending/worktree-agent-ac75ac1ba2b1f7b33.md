# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-09 | low: Files: fixing #2802: a trailing blank is not where the undo's match diverges: ExifTool keeps a Category text's blanks, so the journal and the Xtra's parse agree on it; they part on a text ExifTool answers as a JSON number or boolean ("1.50" kept as "1.5", "true" as "True"). The give-back therefore compares with fields.same_read (the one statement of "a read gives the same": trimmed, number and boolean as answered, any order), not fields.same_values, which would still miss "1.50"; a free box holding the texts in another order is now given back (the cut A test that refused it changed). Live, read-only 2026-11-09: 0 of photo_index's 7,293 WM/Category texts has a leading or trailing blank, reads otherwise through fields.as_read, holds an unpaired surrogate or a replacement character, or is of a type other than 8; kr-track's 5 clips hold no Xtra, renton_parkrun has no clip. | decided: same_read is the owner of the comparison; the test with "1.50" and a trailing blank fails on the code before it |

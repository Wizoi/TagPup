# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | low: field_values.ensure_document_id (tagpup/files/field_values.py, the identity minted for a photo the indexer reads without one) calls write() and not written_once_more: a mint whose new file's picture changed (the picture check, #2377) is logged and swallowed, the photo indexed without an identity this time, where a save or a turn of it (held or Just-look) is now made once more. Nothing is lost (the photo is untouched and the next read mints again): #2386 decided the swallow moot for damage; the new fact is #2476's decision that photo writes retry. Left out of #2476's fix, whose brief named the two writers only; route the mint through written_once_more, or record why not. | open |

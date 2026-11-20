# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-20 | low: a Delete of a photo whose file is there but cannot be read (shares.CANNOT_READ) tells it NOTHING_DELETED_NO_LOOK, "The share did not answer", though the share answered (photos.delete_planned gives one sentence to UNANSWERED and CANNOT_READ; as before cut 1b); user action: Delete a selection holding a photo the account lost rights to | open |

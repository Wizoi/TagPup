# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-03 | low: decided: the re-derive of the rows stored before XMP was read first (scripts/rederive_captions.py, refresh_rows.rederive) records one journaled change on the maintenance scaffold instead of the db.backup the brief named: tests/test_bulk_scripts_back_up.py allows a full copy only to the migrations and to tagpup_cli's delete and compact, and the change is undoable from History, which a copy is not. Each row is a skippable edit expecting its tags, captions and raw_metadata as the plan read them, so a row a running server saves meanwhile is left out and reported (it is derived by the new rule already). A row with no raw_metadata is counted and left as it is (0 in the three libraries, counted 2026-11-03), and no branch is written for unparseable JSON (0 live; json.loads raises and the run fails loudly). | decided: one journaled change, by the bulk-write guard |

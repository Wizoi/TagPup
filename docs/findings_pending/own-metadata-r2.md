# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-27 | low: a file that changed between ExifTool's refusal and stage R's probe (a Save during an index of the same folder) is taken for a lasting failure, stamped with the pre-read stamp and logged "unreadable"; its row keeps its fields and the stale stamp forces a re-read at the next index. user action: Save during an index of the same folder; live: 0 | decided: self-healing, no data lost, only the log line differs |
| B-2 | 2026-11-27 | low: record_indexed keeps tags, captions and raw_metadata on a read_error but width/height come from the record, so a refused read sets an existing row's picture size NULL; the plan names only the three kept columns. live: 0 | decided: as the plan names; the size is read again with the next good read |
| B-3 | 2026-11-27 | low: row 50 of the integration charter omitted the HELD reason, INVARIANTS :50 had no exception for a stamped refusal, and the lasting-failure log line said "its row kept" of a new row too. live: 0 | fixed: the commit after b457255d on own-metadata-r2 (the three wordings) |
| B-4 | 2026-11-27 | low: "a read that fails never empties a row" is decided by `read_error` in store/photos.py and by an empty raw_metadata in services/refresh_rows.py:82; two tests, one rule. live: 0 (nothing fails today) | decided: a second instance asks which single place owns it (a `metadata.failed(record)` predicate both read); not built without one |

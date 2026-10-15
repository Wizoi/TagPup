# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: Data: a library whose making failed holds only `schema_version` (`schema._migrate` commits that table before the baseline's own transaction; tests/test_schema.py holds the state). With the probes of #1366 gone, a look that does not run `ensure` (`services.settings.read`, the doctor's `peek_settings`, `generations.values`) raises `no such table` on such a file instead of answering defaults; the next `ensure` makes it whole. Creating `schema_version` inside the baseline's transaction would remove the state; not done here (a change to the runner). | open |
| B-2 | 2026-10-15 | low: Data decision (#1300): `file_journal.owner()` now refuses (RuntimeError, nothing written) in a process that cannot read its own start, instead of naming a start-less "host:pid" owner; `owner_parts` reads any name that is not "host:pid:start" as no process, so `owner_alive` says False for it. 0 start-less owners in changes or job_runs on the 3 live libraries (counted 2026-10-15). | open |
| B-3 | 2026-10-15 | low: Data: the name arm of `removals.same_person` is kept, not deleted with #1366: 1 face on one live library is named with no person id (counted 2026-10-15), so a removal of it records no `tag_id` and the comparison falls back to the name. It goes when People give that face its id (`names_without_a_person`). | open |
| B-4 | 2026-10-15 | low: Data, left of #1367: `schema._same` / `_columns` still copy `journal._same` / `_columns` (journal imports schema at module level); the Integration contract-table rows, `tools/doctor.py` in two charters, the 22 Data unit files and D7's six files to lift, and the moot findings to close are not this branch's. | open |
| B-5 | 2026-10-15 | low: probes of other domains seen and not touched: People `store/people.py:347,351`, `store/person_ids.py:164`, `store/taxonomy.py:256,396,406,493,529`; Views `store/library_view.has_index`; `store/folders.py:28` (a settings-table probe) and `store/inspection.py:42` (a schema_version probe). | open |

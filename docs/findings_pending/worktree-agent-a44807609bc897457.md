# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: `tests/migration_names.py` `JOURNALED_SINCE` lacked `face_embeddings` (made by migration 33, keyed by `journal.KEYS`), so `up_to(32)` gave the app before 33 a journal keying a table its library has not; no test journaled a face vector at 32, so nothing failed. The new `test_migrations.TheRealMigrations.test_the_app_before_each_one_keys_only_the_tables_its_library_has` (every real version, its library's tables against the keys) failed on the old list and passes with `{33: ("face_embeddings",), ...}` (found settling the row on JOURNALED_SINCE kept by hand). | fixed: tests: JOURNALED_SINCE is held by a test of every real version, and names face_embeddings, made at 33 |

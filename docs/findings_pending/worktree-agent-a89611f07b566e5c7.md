# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-09 | low: Data: closes #2749 (main session: `tools/add_findings.py --status 2749 "fixed: <this branch's commit>"`). INVARIANTS.md named `tests/test_a_look_does_not_migrate.py`, which does not exist; it now names `tests/test_one_library_three_programs.py` and `tests/test_a_librarys_settings_are_its_own.py`, and `tests/test_the_test_map.py` fails when any backticked `tests/` path in INVARIANTS.md or ARCHITECTURE.md is missing (run against the old docs first: it failed on exactly that name; ARCHITECTURE.md had none). | fixed: this branch's commit |

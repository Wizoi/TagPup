# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-09 | data: the newer-library guard (`schema.ensure` raising `NewerLibrary`, #1012) cached "current" by the stat of the library's main file only. In WAL mode a newer app's commit goes to the -wal file and the main file does not change while any connection holds a read mark, so a long-lived server, MCP or watcher that had cached the library was never refused after a newer app migrated it (domain review reproduced it). Fixed: a library found current has its version read again, read only, once `schema.RECHECK_SECONDS` (2 s) have passed since it was last asked; a hit inside the bound is still a stat (about 65 microseconds measured with the lock, no connection), the read about 10 ms once per bound. A read that fails is not decided: `ensure` looks at the library in full and says why. The new tests (`tests/test_newer_library_guard_in_a_long_lived_process.py`: a second process commits the higher version while this one holds a read mark; the journal write and the server's 409 follow; `reading_newer` still lets recovery through; an unchanged version is never refused) failed on the old code first. | fixed: this branch |

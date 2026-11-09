# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: Data: naming a face that confirms a guess (`services.faces.name_face`'s confirm path) returned its fingerprints as (before, None): they were read inside the accounted write's block, before the after-fingerprint was set, so the cached Identify grids were re-stamped with None and rebuilt on the next click instead of losing the face. Found while moving `accounted_write` onto `db.write_with_connection` (#2750); the test (`test_service_faces`, the confirm case) fails on the code before it. | fixed: tests: naming a face that confirms a guess re-stamps the grid with the table as written |
| B-2 | 2026-11-08 | low: Data: decided while fixing #2750: the migration runner (`schema._migrate`, `_Run.migrate`) and the index's long-lived connection (`roots.begin_write`) begin through `db.begin` and are not retried: the runner holds the write lock for the whole run on one connection (foreign keys off between transactions, the backup inside one), BEGIN IMMEDIATE waits the 30 s busy timeout, and a migration left undone is run again by the next open (`schema.ensure` on every open). No variant of `db.write_with_connection` was added; the faces' accounted write opens its IMMEDIATE transaction with `db.begin` inside the operation, as `store.faces.set_names` and `services.faces._write_guesses` already do. | decided: store: a transaction is begun only by db.begin; the faces' accounted write is db.write_with_connection's |

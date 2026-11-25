# Plan: one connection for a write's photos, not four for each

Work row #3375 (side lead, 2026-11-24). Measured, designed, nothing built. Owners: **Data** (tagpup/store/db.py, the
journal's rows) reviews; **Files** (tagpup/services/file_changes.py, the write-ahead order) reviews.

## What was measured (sandbox home, real ExifTool, replay off, one ~3.6 MB JPEG copied N times; live photo median 3.02 MB)

A bulk tag write (`POST /library/api/library/bulk-edit`, op `tags`) of a JPEG patched in place opens **four** connections
per photo, not two: `file_journal.writing` (rw), `file_journal.save_patch` (rw, from the patch's `saving`),
`file_journal.patch_of` (read-only, in `_verified`), `file_changes._record` (rw). A connection opened just after another
connection's commit costs about 5-6 ms on this machine (its first statement: `PRAGMA journal_mode=WAL`, or the first
SELECT of a read-only one); one opened otherwise about 0.8 ms; a statement on a held connection about 0.1 ms. The cause of
the 5 ms is not reproduced in isolation (not WAL size, not the GIL, not ExifTool, not a photo written beside it); it is
measured in the write path itself, before and after a commit (probe connections).

| N | baseline (no timers) | per photo | with one connection per mode held for the write (experiment) | per photo | saved |
|---|---|---|---|---|---|
| 500 | 21.86 s | 43.7 ms | 12.51 s | 25.0 ms | 9.4 s (43%) |
| 2000 | 87.88 s | 43.9 ms | 50.50 s | 25.3 ms | 37.4 s (43%) |
| 5000 (extrapolated, 18.7 ms a photo) | ~220 s | | ~126 s | | ~93 s |

Split of a photo at N=2000 (timed run, 43.3 ms): ExifTool 15.5 ms (36%: the plan's read, `read_one` twice a photo, the
chunk's read back); the four connections' cost about 18 ms (42%: `writing` 5.8, `save_patch` 5.7 of which the patch's
UPDATE 0.1, `patch_of` 5.5, `_record` 2.3 of which 0.9 is its row); the patch's own bytes 2.7 ms; the row update 0.4-0.9 ms;
commits 0.4 ms (three a photo, unchanged by the experiment). The experiment kept every commit: the saving is connection
opens alone.

The user actions: a bulk tag (or caption, people) edit of N photos, and the index's bake (`bake photos`: photo_index's
journal holds 51,367 baked files in 2,845 changes, most 380; bulk tags 2,834 files in 28 changes, most 1,638; counted
read-only 2026-11-24), an undo of either, and settle after a crash (`_settle_files`). A single save gains ~18 ms: not felt.

## The single place: a held connection scope in `tagpup/store/db.py`

`db.held(target)`: a context manager that, on **this thread only**, makes `write_with_connection(target, ...)` reuse one
rw connection, and a new sibling `db.read_with_connection(target, operation)` reuse one read-only connection, until the
block ends (both closed then). Outside a scope, and on any other thread, both open and close a connection per call, as
today. The bulk loops enter one scope each, around the chunk loop: `file_changes._write_fields` (forward),
`_settle_files` (settle), `undo`'s loop, `rename`. `file_journal.patch_of` reads through `read_with_connection` (it is
the one reader of the record, and stays so). No signature in file_journal or file_changes changes; no caller passes a
connection.

### What must not change

- **The write-ahead order**: `writing` committed, then the patch's record committed, then the bytes, then `done` with the
  row: each is still its own `write_with_connection`, its own transaction and commit. A held connection never holds a
  transaction between two writes (the scope asserts `not conn.in_transaction` when it lends it).
- **The per-file write lock and the retry**: `write()` still takes `lock_for(target)` and `retry_when_busy` around each
  attempt; the lock is not held between writes (unlike the index's and the migration runner's held connection, which
  hold the lock for their run). Other batches let in between chunks (`let_others_in`) write as before: in WAL a held
  connection with no transaction blocks nobody.
- **A crash half way**: unchanged, since every commit is where it was: `tests/test_a_crash_part_way_is_settled_at_the_next_start.py`
  passes untouched.
- **Two writers at once**: thread-local, keyed by `db.key(target)`: the server's request threads, the settle thread, and
  a CLI process each keep their own connections. A connection is never shared across threads (write_with_connection's
  docstring: two threads on one connection interleave transactions).
- **A failed photo does not block the rest**: an attempt that raises rolls back on the held connection, as now. A
  connection-level failure (anything but `sqlite3.OperationalError` that `locked()` names, or `IntegrityError`, i.e.
  `DatabaseError`, `ProgrammingError`) closes and drops the held connection; the next write opens a fresh one.
- **The roots**: `conn.roots_state` lives on the connection; a held one keeps it across writes, which `roots._held`
  already handles for long-lived connections (`PRAGMA data_version`, `RECHECK_SECONDS`, `unchanged()` before commit).
- **`write_if_free`** (busy_timeout 0) and any `connect()` with arguments are never served from the scope.

### Readers of every value moved

The connection object (its `roots_state`, `in_transaction`): `roots._held`, `roots.unchanged`, `roots.begin_write`,
`write_with_connection.attempt`. The read-only record: `file_journal.patch_of`'s callers `_verified`, `_by_bytes`,
settle. Nothing else reads a connection across calls.

### Tests

- A scenario (Files owner): a bulk tag write of 60 photos (three chunks) opens at most a fixed number of connections per
  write, counted on `sqlite3.connect` (fails today: 4 a photo), and a crash at the 30th write is settled as before.
- A scenario (Data owner): a second thread writing the same library during a held scope gets its own connection and both
  writes commit (fails on a scope that is not thread-local).
- `tests/test_db_access.py` keeps refusing any connection held outside `db.py`.

### Size and order

One cut, about 150 lines with tests: db.py's scope and `read_with_connection`, patch_of moved onto it, four `with
db.held(...)` lines. Baseline the 2000-photo run first (scratch harness in the worker's own folder), then the same run
after: target ~25 ms a photo. Removes nothing; adds one concept (the scope), which replaces four per-photo opens.

### Questions for the main session

- Leave `patch_of` reading the committed record (this plan), or let `_verified` use the record `saving` was just given
  (one read fewer, but no longer verifying what was committed)? This plan keeps the read.

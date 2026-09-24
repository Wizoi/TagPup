# Findings

This file records every review finding, bug report and known problem: what was found, and where it stands. A finding that exists only in a conversation gets lost, and a decision that is not written down gets made again.

- **Add a row when something is found**, before fixing it.
- **Status** is one of:
  - `open`
  - `fixed`: give the commit, by its hash or, for a row added in the same commit, its subject.
  - `not reproduced`: say what was tried.
  - `left as is`: say why.
  - `tabled`: say until when.
  - `planned`: give the phase in [ARCHITECTURE.md](../ARCHITECTURE.md).
- **Decisions the owner makes** are marked *(owner, date)*. Don't reopen them without a new fact.
- **Never put real names here.** The library is photographs of real people.

Findings fixed before 2026-09-23 are in the git log (`git log --grep="^fix"`). This file starts with what was still open or decided on that day.

| # | Found | Finding | Status |
|---|---|---|---|
| 1 | 2026-09-23 | Faces are detected on the photo as stored, so a sideways photo (about 1 in 600, more as photos are rotated) is searched sideways. A rotated TIFF also keeps the embedding of its old turn. Detecting upright changes the coordinate system of stored face boxes, the crops, and the rotate logic. | tabled for a future discussion *(owner, 2026-09-23)* |
| 2 | 2026-09-23 | One of the library's two PNGs cannot be rotated. The rotate refuses and changes nothing. | left as is *(owner, 2026-09-23)* |
| 3 | 2026-09-23 | Six faces in `photo_index` were both excluded and named. | fixed: the owner kept the names *(2026-09-23)*; exclusions lifted, with a backup taken first (`photo_index.before-keep-names-20260923_180819.db`) |
| 4 | 2026-09-23 | Rows had people missing that their keywords or faces name: 741 names on 597 rows in one library, 885 names in the other. | fixed: `refresh_rows_from_files.py --apply` on both libraries (550 and 591 rows), with a backup taken first. Phase 4 derives people instead of storing them. |
| 5 | 2026-09-23 | Opening New Person takes about 1.7 s. Caching its embeddings would hold about 388 MB in memory. | open: revisit when embeddings get their own table (phase 4) |
| 6 | 2026-09-23 | Duplicate face-resolution trace entries for paths that differ only in case. | not reproduced. Each library now writes its own trace file. |
| 7 | 2026-09-23 | `backups/` holds 28 GB in 41 copies. Scripts also wrote copies next to the code they ran from. | open: the owner decides which copies to keep. Phase 1 moves backups beside the library. |
| 8 | 2026-09-23 | Eight TagTuner routes are reached only by tests: its copies of rename, time shift, delete, open in Explorer, rotate, save metadata and bulk tags, plus `/api/faces/recluster`, which no page calls. | planned: phase 2 |
| 9 | 2026-09-23 | `runner.py` opened the database with `sqlite3.connect`, without WAL mode or the write lock. The database guard never saw it: each guard kept its own list of files, and that one skipped `runner.py` and the launchers. | fixed: *refactor: the foundation modules move into tagpup/*. Every guard now checks one list. |
| 10 | 2026-09-23 | `runner.py` keeps its own copy of the `photos` and `faces` schema, and two column migrations, separate from `PhotoIndex`'s. Two definitions of one schema drift apart. | planned: phase 3, where the store owns the schema |
| 11 | 2026-09-23 | `tests/test_multiple_databases.py` changes the checkout's own `config.ini`: selecting a library through the API makes the server write `default_db` there. The test restores the old value afterwards, so a run stopped in between leaves the app pointing at a test library. | planned: phase 1, `tagpup.config` with `TAGPUP_HOME`, so tests get a config of their own |
| 12 | 2026-09-23 | Moving the foundation modules into `tagpup/` (9d7e1c4) broke both measurement tools. Their sandbox copied a fixed list of folders that did not include `tagpup/`, so the sandbox server could not import its database module. Nothing tested a sandbox. | fixed: *fix: measurement sandboxes copy tagpup/ too*, with a test that imports both servers inside a sandbox that cannot see the repo |

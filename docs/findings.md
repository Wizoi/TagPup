# Findings

This file records every review finding, bug report and known problem: what was found, and where it stands. A finding that exists only in a conversation gets lost, and a decision that is not written down gets made again.

**This file holds the open findings.** A closed row (status starting `fixed`, `built`, `decided`, `closed`,
`accepted`, ...) is moved, byte for byte and with its number, to
`history/findings_closed_0001-0250.md` and so on ([index](history/README.md)) -- one file per 250 numbers.
A reference such as "#726" is found by its number there. The owner's decisions are collected in
[DECISIONS.md](DECISIONS.md); the rules that came out of findings are in [INVARIANTS.md](INVARIANTS.md).

- **Add a row when something is found**, before fixing it.
- **Severity** *(owner, 2026-10-09; CLAUDE.md, "Keeping the project small")*: start the Finding with `data:` (can lose or
  corrupt data), `wrong:` (a wrong result the owner sees) or `low:`. Only `data` and `wrong` block a merge; `low` rows are
  batched. Older rows carry none.
- **Status** is one of:
  - `open`
  - `fixed`: give the commit, by its hash or, for a row added in the same commit, its subject.
  - `not reproduced`: say what was tried.
  - `left as is`: say why.
  - `tabled`: say until when.
  - `planned`: give the phase in [ARCHITECTURE.md](ARCHITECTURE.md).
- **Decisions the owner makes** are marked *(owner, date)*. Don't reopen them without a new fact.
- **Never put real names here.** The library is photographs of real people.
- **Numbering is the main session's.** A worker does not number rows into this file (two branches that both do collide on
  merge). It writes `| ? |` rows with `tools/add_findings.py rows.md --branch-file docs/findings_pending/<branch>.md`
  (provisional `B-1`, `B-2`; never write a `B-n` into code or a commit message) and the main session, merging the branch,
  runs `tools/add_findings.py --take docs/findings_pending/<branch>.md`, which numbers the rows after the highest number
  in this file **and the archives**, and removes the file. `tools/add_findings.py --status N "fixed: abc"` sets a status
  (in the archive if the row is there); `--archive` moves the rows whose status closed.

Findings fixed before 2026-09-23 are in the git log (`git log --grep="^fix"`). This file starts with what was still open or decided on that day.

| # | Found | Finding | Status |
|---|---|---|---|
| 1 | 2026-09-23 | Faces are detected on the photo as stored, so a sideways photo (about 1 in 600, more as photos are rotated) is searched sideways. A rotated TIFF also keeps the embedding of its old turn. Detecting upright changes the coordinate system of stored face boxes, the crops, and the rotate logic. | tabled for a future discussion *(owner, 2026-09-23)* |
| 663 | 2026-10-04 | Identity by id review (low): fixtures tests/journal_library.py:103 and tests/test_mcp_server.py:114 insert faces by raw SQL and then call person_ids.sync; they hide no writer (the single-owner test and mutations cover them) but bend the "data made by the real code" rule. | left as low: the journal tests compare byte-exact shapes |
| 831 | 2026-10-07 | #791 known limit: a photo stored with EXIF Orientation 2-8 has its boxes drawn at the stored pixels' positions on the turned picture; /api/photo-faces says `turned` and the panel says so. | open: the orientation redesign (owner un-tabled it) |
| 1485 | 2026-10-15 | low: Data (#663, the rest): `tests/test_mcp_server.py` (~:114) still inserts faces by raw SQL; journal_library.py now makes its faces by the store's writers. test_mcp_server is Integration's file, not touched here. | open |
| 1489 | 2026-10-15 | low: Integration pass, by design: the page's kept scan lives 30 minutes with no server check, and `forgetCachedPhoto` cleans only the open library's; a save from a scan kept too long is refused by the stamp precondition (I8, tests/test_stale_record_precondition.py), and a scan of an older shape is scanned again (I9). FakeServer page tests (83 of the 113 page test files hold one, counted today) stay by decision (2026-10-11, #1326). `tagpup_routes._too_many` stays until the Files pass moves the cap into `file_changes.rename`. | left as is: by design, the reasons in docs/domains/integration.md |
| 1505 | 2026-10-15 | low: 38 test files and helpers (counted on this branch) seed photo rows with a bare `INSERT INTO photos` (no folder row, unlike `photo_rows.add_read`): the folders reader (`store.folders.with_rows`, #501) now reads the derived folders, so such a row is in no folder; two fixtures that sync or the folder lists read failed and were moved to `add_read` (test_folder_watcher.seed, test_tuner_server_api's subfolder counts); the rest passed. Each owner moves its own when touched. | open |
| 1513 | 2026-10-16 | low: `scripts/prepare_test_environment.py` (~:122) names the seeded face by a new `People/Puppy` while its photo is tagged `Pets/Puppy`; the `set_branch_flags("Pets")` (~:94) does nothing because a seeded tree holds only People, so `Pets/Puppy` gets has_face 0. Fix: `add_node(conn, "Pets/Puppy", root_has_face=1)` and name the face by that id: one person, as the comment intends. Dev script only. People review of the People pass. | open |
| 1514 | 2026-10-16 | low: stale docstrings after `wire` went: `face_assignment.plan_name` and `face_people.name_face` say "or a tag path or a name" but a path is refused by `who_is` (validation "name"); `faces.decide_guesses` says "or a name no person is filed under" but proposals are ids; `plan_name` refuses a node gone after `made` with `not_filed` (the several-roots sentence) where `StalePerson` belongs (a race only). Confirmed by reading. | open |

# Integration and apps: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-integration.md` (its Scope, the cross-domain contract table, the
scenario list). This page states each of the seven conditions of health (docs/OWNERS.md) with its evidence and date. A
condition not met is the domain's queue.

## The seven conditions (2026-11-08)

Counted on the trunk at "findings: #2733, #2759, #2760 fixed", 2026-11-08, read-only; the earlier statement (2026-10-15)
is replaced.

1. **No reach-ins.** Met for what the passes swept (2026-10-15: `read_face_ids` public; no `module._name` of another
   module in an Integration module). Since then the cross-domain uses the video work and the web server added are rows,
   not reach-ins: 69 (the viewer reads a clip's bytes only through `shares.read_span`), 70 (the web server alone settles
   a half-done change on a thread), 71 (the page record's `writes`), 72 (a delete for good and its token), 73 (a clip's
   frames only through `video_frames`), 74 (thumbnails and a clip's poster only through `thumbs`), 75 (what is this PC's
   alone is one list, `security.THIS_PC_ONLY`). The contract table has 77 rows today (2026-11-23). A new sweep for private names
   across modules was not run for this page; People's open #1471 lists uses of People's modules that have no row, and
   that row is People's.
2. **No rule written twice.** Met for the rules that were written more than once and now have one owner, each with its
   guard: the "this PC only" gate (three copies in `tagpup_routes`, `name_faces_routes`, `activity_routes`, now
   `security.THIS_PC_ONLY` enforced by one hook, `security.guard`; #2745,
   `tests/test_every_route_answers_this_pc_or_says_why.py`, `tests/test_local_server_socket.py`); the mapping of a
   service's exception to a reply (`responses.ANSWERS`, `responses.ANSWERED`, `responses.of`: a route catches
   `ANSWERED` and answers `of(it)`; `tests/test_rules_have_one_owner.py` holds the list of exceptions a route may
   answer); the write transaction (`db.begin`, the one place a transaction is begun, `tests/test_db_access.py`); the
   page's kept scan and its shape (`CACHE_VERSION` in `web/tagpup/cache.js`, 7 today; `tests/test_page_record_fields.py`
   fails when `page_record`'s fields change without raising it and regenerating the fixture). Open and not Integration's
   alone: what is this PC's alone is not yet one rule of what a route carries (`/api/photo-file`, `/api/face-crop`,
   `/api/folder/scan`, `/api/photos` carry the same pictures and names as the refused `/api/library/*`; and some routes
   that act on this PC, `/api/browse-folder`, `/api/photo/open`); #2769 is open and the choice of which routes join
   the list is the owner's.
3. **No hidden coupling.** Met: no row of the contract table has the guard "none" (counted 2026-11-23: 0 of 77). The settle
   thread (row 70) is coupled to the web server's start by one named call, `runtime.settle_on_a_thread`, which only
   `tagpup_web.main` makes; the CLI, the MCP server and the scripts settle inline (DECISIONS 2026-11-07); its guard is
   `tests/test_a_long_change_cut_off_is_settled_in_chunks.py` (`OnAThreadInTheServer`). Row 71's coupling between the
   record's fields and the page's cache is `CACHE_VERSION` and its guard above.
4. **No concept that could collapse.** No candidate found in this pass.
5. **No branch without an instance.** Met for the launcher (2026-10-15, #1447), for the failure kinds of a run (I10:
   a CLI from the repository beside the installed server writes a newer kind) and for ffmpeg (I11: the one kept arm,
   the thumbnail placeholder, is made by removing ffmpeg while the server runs; three unreachable arms and two tests
   went). Not re-swept for the rest of the package on 2026-11-08.
6. **Scenario tests, each file owned and mapped.** Met for what exists: I1 to I11 each have a file in tests/owners.py:
   I1 switching library mid-folder; I2 a write through a server update; I3 an update by a launch and by an install;
   I4 a library one migration behind served at start; I5 a page against the real server; I6 the same flows with and
   without roots; I7 the CLI, the MCP server and the server on one library; I8 a save from a stale record; I9 a scan
   kept by an older page, and a write's reply reaching every scan this browser keeps of its photos; I10 `test_a_failed_run_says_what_to_do` (a failed background run says what to do next); I11
   `test_ffmpeg_is_a_prerequisite` (no ffmpeg: the server and the CLI's index start nothing and say what to install,
   an install leaves the old server running). `tests/test_the_test_map.py` holds every test file to one owner and every
   module of the package and of the pages to one charter's Scope. **Not met for what the charter lists as having no
   test, plainly open** (charter, "Scenario list"):
   - scenario 2: naming faces, then tags, then files, then the index, with a watcher sync mid-job and an Undo from
     History (#893) has no test;
   - scenario 3: a folder renamed in Explorer, the watcher, sync, and the open page showing it has no test;
   - scenario 4: more flows against the real server. Counted 2026-11-08: `tests/test_pages_against_the_real_server.py`
     holds 5 tests; of 125 page test files in `tests/frontend/*.test.mjs`, 94 hold a `FakeServer` (2026-10-15: 83 of
     113); bulk add to all selected awaits the batch strip's branch;
   - scenario 5: a restart in the middle of a resumable job (naming) or a batch (face assignments), then the settle and
     a second click under a different version, has no test (the settle itself, row 70, has its own).
   This is the domain's queue; #2747 is closed by stating it here, not by building these.
7. **No open data or wrong finding, and every low fixed or by design.** Met for data and wrong: `tools/add_findings.py
   --open` lists 20 open findings today, all `low`, none `data` or `wrong`. Integration's open lows: #2747 (this page,
   closed by this commit) and #2769 (which routes join `THIS_PC_ONLY`, the owner's call). #847 stays decided by design
   (below).

## By design

- **#847**, a one-pixel light row at the bottom of the library picker's popup: not reproduced in Chromium, Chrome or
  Edge at 100% to 300% display scale; what remains is drawn by the browser's popup window at fractional scales, where no
  CSS reaches. Decided by design: the owner is asked for a screenshot with the browser, display scale and zoom, and a
  screenshot reopens it as a new fact; no fix is guessed. The next step, an own listbox for the picker, is a one-way UI
  change and is not built.
- **The page's kept scan of a folder** lives 30 minutes with no server check, in this library's keys only: a save from a scan
  kept too long is refused by the stamp precondition (I8, `tests/test_stale_record_precondition.py`), and a scan of an
  older shape (`CACHE_VERSION`) is scanned again (I9). A write's reply reaches every scan this library keeps that holds the
  photo, its folder's and every one above it (`cache.js` `photosWritten`, by `isUnder`), put in the server's order by
  `order`. Every writer reaches them through that one function: a tag change and a Time Shift directly; a single save (the
  six `postPhotoMetadata` callers), a turn, and Smart Rename of one photo and of a folder through
  `recordsWritten(records, renames)`, which gives each record the page holds the writer's reply; a Convert's end goes
  through `photosWritten` and changes only the name and stamp of a record still naming the file converted (with the stamp
  sent when it kept its name, Make smaller, so tags added meanwhile stay; any stamp when it renamed the clip, which left no
  file at the old name, the record's `base` kept so a save from a stale one is still refused); and a single save's new Date
  Taken moves the photo at once in the open grid. A rename map (`updated_paths`, a save's or a Convert's new path) is read in
  one place, `web/common/paths.js` `renamedBy`: every move resolved from the map before any is taken, so a chain (an old
  name of one photo the new name of another) or a swap leaves each photo's records (`recordsWritten`), suggestions (the
  page's, `edits.js` `photosRenamed`; each kept scan's, `photosWritten`) and place in the selection under its own new name. A Delete forgets every
  kept scan holding the photo (`photosGone(paths)`); `bulk-run.js` forgets every scan (a library-wide write). A kept scan
  that cannot be written back is forgotten (I9). Every write is queued naming its photos (`queuePhotoWrite(photos, job,
  ...)`, `photos()` asked while it is out), so the page going with a write under way (a reload, another library chosen)
  forgets every kept scan holding them (`write-queue.js` `photosOut`, `cache.js` `photosGone`, wired in `edits.js`; I9, V22).
  Two writes still out of that line, a folder Smart Rename's request and a Convert's ticket, are findings, not built.
- **Page tests that answer from a fake server**: accepted by the decision of 2026-10-11 (#1326), with the count in
  condition 6; a flow converted deletes its fake copy unless the fake holds a reply shape no seed makes (contract row
  22).
- **The cap of a write of photo files** is one function with one sentence, `file_changes.too_many` (the Files pass).

## History

One line each; the reasons are in docs/DECISIONS.md.

- 2026-10-15, the domain pass (#1225, #1303, #1447, #1448, #1449): see the conditions above and DECISIONS 2026-10-15.
- 2026-10-29, the failures work of the Activity rework: I10 (DECISIONS 2026-10-29).
- 2026-11-06, ffmpeg as a prerequisite: I11 (DECISIONS 2026-11-06, "FFMPEG IS A PREREQUISITE").
- 2026-11-06 to 2026-11-08, video: rows 69 to 74 (DECISIONS 2026-11-02, 2026-11-05 to 2026-11-08, the A5 entries),
  `CACHE_VERSION` raised to 6, the settle thread (2026-11-07, "A CANCELLED SETTLE KEEPS ITS PHOTOS UNTIL THE NEXT START").
- 2026-11-08, the this-PC gate: row 75 (#2745).
- 2026-11-13, the kept New Person pool: row 76 (#2881).
- 2026-11-23, the phone's Browse covers (docs/plans/phone-redesign.md, cut 2): row 80.

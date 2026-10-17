# Integration and apps: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-integration.md` (its Scope, the cross-domain contract table, the
scenario list). This page states each of the seven conditions of health (docs/OWNERS.md) with its evidence and date. A
condition not met is the domain's queue.

## The seven conditions (2026-10-15)

1. **No reach-ins.** Met for what this pass swept. The one alias of another domain's private name in an Integration
   module, `tuner_routes._read_face_ids = face_routes._read_face_ids`, is People's public `read_face_ids` now (the owner's
   decision 5 of 2026-10-15), and its contract row is gone. A grep of every Integration module for `module._name` of
   another module finds none; a grep of the package for an Integration module's private name finds none. People's open
   #1471 lists cross-domain uses of People's modules that have no contract row: that row is People's.
2. **No rule written twice.** Met for what this pass touched: "does the runtime have an idle timer" was asked in
   `app.create_app` and in TagTuner's `_used`, by `getattr(runtime, "idle", None)`, for one test fake; both now ask only
   for a runtime, and the fake has one.
3. **No hidden coupling.** Met: no row of the contract table has the guard "none" (row 23, the page's kept scan, has
   `tests/frontend/scenario-a-scan-kept-by-an-older-page.test.mjs`; row 45 is gone), and row 6's guard is the test that
   fails when what the row says is broken: with `services/settings` asking `sync.review` nothing on a change of roots,
   `tests/test_library_roots.py` (`FoldersNotAddedAreExcluded`, two tests) fails and nothing else does
   (`tests/test_settings.py`, the guard named before, and `tests/test_one_answer_to_the_librarys_folders.py` pass;
   mutation of 2026-10-15, #1303).
4. **No concept that could collapse.** No candidate found in this pass.
5. **No branch without an instance.** Met for the launcher: `wait_for`'s callers always pass the Popen `start_server`
   returns, so three `started is not None` arms went (#1447); the one in the KeyboardInterrupt arm stays, where nothing
   may have been started yet. The `getattr(..., None)` left in Integration modules each have a real case: a request whose
   `before_request` did not run (`g.request_started`), a socket option Windows alone has, an OSError with no `winerror`,
   a WSGI body with no `close`, a `wait_for` answer that is a sentence or a `Failed`. The runtime's `getattr(model, ...)`
   are its Views half (the runtime is shared).
6. **Scenario tests, each file owned and mapped.** Met: I1 to I9 each have a file in tests/owners.py (I9, a scan kept by
   an older page, since 2026-10-15); `tests/test_the_test_map.py` holds every test file to one owner, and every module of
   the package and of the pages (`web/**/*.js`, 99 today) to one charter's Scope.
7. **No open data or wrong finding, and every low fixed or by design.** Met: this pass fixed #1225, #1303 (row 6's
   guard), #1447, #1448 and #1449; #1275 is closed (every scenario id of tests/owners.py has a file, 0 without, counted
   2026-10-15; I9 by this pass, the rest by the other owners' regroups); #847 is decided by design (below).

## By design

- **#847**, a one-pixel light row at the bottom of the library picker's popup: not reproduced in Chromium, Chrome or
  Edge at 100% to 300% display scale; what remains is drawn by the browser's popup window at fractional scales, where no
  CSS reaches. Decided by design: the owner is asked for a screenshot with the browser, display scale and zoom, and a
  screenshot reopens it as a new fact; no fix is guessed. The next step, an own listbox for the picker, is a one-way UI change and is not built.
- **The page's kept scan of a folder** lives 30 minutes with no server check, and `forgetCachedPhoto` cleans only the
  open library's scan: a save from a scan kept too long is refused by the stamp precondition (I8,
  `tests/test_stale_record_precondition.py`), and a scan of an older shape is scanned again (I9).
- **Page tests that answer from a fake server** (83 of the 113 page test files hold a `FakeServer`, counted 2026-10-15):
  accepted by the decision of 2026-10-11 (#1326). Five flows run against the real apps
  (`tests/test_pages_against_the_real_server.py`); a flow converted deletes its fake copy unless the fake holds a reply
  shape no seed makes (contract row 22).
- **The cap of a write of photo files** is one function with one sentence, `file_changes.too_many` (the Files pass,
  2026-10-15): `tagpup_routes._too_many` is gone; write_fields, batch_exclusively, Smart Rename, the selection and a bulk face
  assignment ask it.

## History

- 2026-10-15, the domain pass: every page module placed in one Scope (58 had none, #1449); `read_face_ids` public and row
  45 gone; I9's page test; the launcher's dead arms and the runtime's idle getattr gone (#1447); 'the server' in the
  worker's definition (#1448); a library view opened while the real server migrates its library (#1225).

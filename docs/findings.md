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
  - `watching`: nothing to do until it is reported again or reproduced *(owner, 2026-11-02)*. The row is moved by
    `tools/add_findings.py --watch N "why"` to [watching.md](watching.md): not counted open, not brought up in a report;
    **read watching.md when an issue is reported**, to see whether it is one of those. A reproduced one goes back here by hand.
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
| 2728 | 2026-11-08 | low: a finding about a write over many files could be closed `fixed` with no count of what the fix wrote, the shape of #2622 (60 read, 60 reported, none written). Retro 2026-11-08 action. | fixed: 404fac14 |
| 2729 | 2026-11-08 | low: Views: `docs/domains/views.md` is behind the trunk: counts dated 2026-10-15 (no clips), scenarios V1-V9/V11-V14 while `tests/owners.py` maps V15-V17, no row for migration 38 (`embedding_frames`, best-frame matching), `/api/video-file`, posters or the accepted whole-library clip scan (DECISIONS 2026-11-08). Holistic owner review 2026-11-08. | open |
| 2730 | 2026-11-08 | low: Views: the page's card cap `web/tagpup/library-source.js:79` `BATCH = 200` copies the server's `MAX_CARDS` and `tests/fixtures/view_rules.json` has no `max_cards` (verified absent), so `test_view_rules_follow_the_server` cannot catch the cap moving; the page also names its 2,000-item cache cap `MAX_CARDS` (line 81), the same name for another concept. | open |
| 2731 | 2026-11-08 | low: Views: `store/library_view.py:642-643` (`people_counts`) keeps an arm for "an id of a node that is gone" with a per-item query; 0 such `photo_people` rows in the three libraries and no refused writer cited. Cite the user action or delete the arm. | open |
| 2732 | 2026-11-08 | low: Views: `services/search.py:108` (`PhotoEmbeddings.kept`) turns any read failure into "no vector" (the photo is embedded again instead of the run failing with its sentence) and `:296` (`_record`) turns a damaged JSON row into empty tags in a Suggest neighbour; no live instance. | open |
| 2733 | 2026-11-08 | low: Files: nine importers of Files modules are in no contract row (row 48 names seven callers): `services/search` -> `video_frames`, `services/thumbnails` -> `files/thumbs`, `photo_reembed`, `libraries`, `roots_sample`, `services/faces` -> `shares.read_stamp`, `runtime`, `ml/clip`, `ml/scrfd_adaface` -> `images`. No bypass of the writer found; condition 1 unmet on paper. | open |
| 2734 | 2026-11-08 | low: Files: the two done one-off re-derives (`store.photos.derived_apart` + `scripts/rederive_captions.py`; `clip_dates_apart` + `scripts/rederive_clip_dates.py`) show 0 rows apart in all three libraries; the clip-date one has not been run by the owner yet, so ask before deleting either. | open |
| 2735 | 2026-11-08 | low: Files: `docs/domains/files.md` restates the seven conditions as of 2026-10-15 (kr-track 1,499 vs 2,070, photo_index 59,519 vs 61,797 with 2,179 clips, DocumentIDs 546 vs 980) and lists contract rows to 59 while 60-72 name Files. | open |
| 2736 | 2026-11-08 | low: Files: scenario F12 is one id of 2,678 characters over 13 clauses and 11 test files, a bucket rather than a scenario; split per flow (indexed, written, renamed, deleted, dated, tree edit). | open |
| 2737 | 2026-11-08 | low: People: `services/tags.set_flags` (tags.py:94-106) and `tags.create` (tags.py:47-81) let a top-level leaf be given the faces flag with no refusal, the shape of watched #2360 (top-level people with bare tags in 31 files); 0 live instances; path by reading, not reproduced. Owner's call: a person at the top is legal ("wherever it sits", 2026-10-10) or both doors refuse with a sentence. | decided: a person at the top of the tree is legal (owner, 2026-11-08, DECISIONS) |
| 2738 | 2026-11-08 | low: People: the "face in the background" rule is written twice, `clustering.BACKGROUND_AREA` (2,000 px absolute, used by `face_tags._to_be_named`) and `images.BACKGROUND_SHARE` (2% of the shorter side, used by `faces._faces_shown`); a 50x50 face on a 4000x3000 photo is hidden on the photo yet named by the job. Face values are clustering's. | open |
| 2739 | 2026-11-08 | low: People: `docs/domains/people.md` is dated 2026-10-15 with history to 2026-10-28, lists P1-P7 (charter has P10, P11), condition 7 reads "met once this branch is merged", and omits the naming queue, selection suggestions, BACKGROUND_SHARE, the kr-track people merge and clips. | open |
| 2740 | 2026-11-08 | low: People: tests `selection-suggests-who-the-faces-look-like` and `faces-say-they-are-loading` are mapped to Views (`tests/owners.py:287-290`) but exercise `selection-panel.js` and `face-boxes.js`, People's. | open |
| 2741 | 2026-11-08 | low: People: `core/vocabulary.py:249-370` (TITLE_COPIES, extract_captions, title_of, own_title, holds_title, captions_held) is caption/title field reading, Files' concern, in a People-owned module changed seven times by Files work since 2026-10-29. | open |
| 2742 | 2026-11-08 | low: People: `idx_photo_people_name` (`store/schema.py:269`, verified present) is read by no statement; a leftover of the name era, to be dropped by the next migration that has its own reason; `library_view.py:17`'s comment is stale. | decided: not dropped now; one migration at a time, so it rides with the next migration that has its own reason (owner, 2026-11-08) |
| 2743 | 2026-11-08 | low: Integration: `Conflict` -> 409 is mapped by hand at nine sites (`tagpup_routes.py:425,751,851,1131`, `face_routes.py:139,166`, `name_faces_routes.py:83`, `tuner_routes.py:682`, `_view_error`) while contract row 12 says `responses.STATUS` owns the mapping; a route catching only `Refused` would answer 500 when a service starts raising `Conflict`. Owner: `responses.of(exception)`. | open |
| 2744 | 2026-11-08 | low: Integration: `web/tagpup/cache.js:17` `CACHE_VERSION` is bumped by hand when `services/photos.page_record` changes shape (4, 5, 6 in ten days) and nothing fails when a field is added without a bump; the stamp precondition (I8) protects the data, the owner would see inert controls. Guard: a fixture of the record's field names. | open |
| 2745 | 2026-11-08 | low: Integration: the "this PC only" gate is written three times (`tagpup_routes.py:586` at 11 sites, `name_faces_routes.py:28`, `activity_routes.py:72`); a new library-view route without it would serve paths and faces to another address on a server listening on every interface. Owner: one before_request in `security.guard`. | open |
| 2746 | 2026-11-08 | low: Integration: `X-TagPup-Updating` is a literal at `web/libraries.py:122` (verified) while `lifecycle.UPDATING_HEADER` defines it. | open |
| 2747 | 2026-11-08 | low: Integration: `docs/domains/integration.md` restates no condition since 2026-10-15 (condition 6 reads I1 to I9); video rows 69-72, I10, I11, `CACHE_VERSION` 4-6 and the settle thread appear only in History; charter scenarios 2-5 (naming then undo mid-watcher, a folder renamed with the page open, more real-server flows, restart mid-batch then a click under another version) still have no test. | open |
| 2748 | 2026-11-08 | low: Data: four change kinds in `UNDO_CHECKS` (`CLEAR_DUPLICATES`, "smart rename: original names", `REDERIVE_OPERATION`, `REDATE_CLIPS_OPERATION`; `services/journal.py:106-114`) are no row of `WRITERS` in `tests/test_undo_after_every_later_kind.py:209`; their undo after a later kind is held by no matrix row. | open |
| 2749 | 2026-11-08 | low: Data: `docs/INVARIANTS.md:57` names `tests/test_a_look_does_not_migrate.py` as a guard; the file does not exist (verified; the scenario lives in `tests/test_one_library_three_programs.py`) and nothing holds INVARIANTS' guard names to existing files. | open |
| 2750 | 2026-11-08 | low: Data: the write transaction is spelled by hand four times outside `db.py` (verified): `store/faces.py:155` (`accounted_write`, without the busy retry or the roots check), `store/roots.py:207`, `store/schema.py:935,975`. Owner: `db.begin`; guard: `test_db_access` fails a bare `BEGIN IMMEDIATE` outside `db.py`. | open |
| 2751 | 2026-11-08 | low: Data: a derived table's doctor check is by convention: `checks.RULES` names five, `test_doctor.py:346` holds three names, and nothing holds `derived.ALL` (6 tables) to a rule per table. | open |
| 2752 | 2026-11-08 | low: Data: `docs/domains/data.md` conditions are dated 2026-10-15, its #1375 row says 33 tables (the libraries have 35); the charter's ~2.6 GB snapshot copy is 4.03-4.13 GB for photo_index (about 16.4 GB of dailies and weekly plus the monthly); `tests/owners.py` D8 ends at "36 meets the real 37" while `test_migrations.py:1146` has 37 meeting 38. | open |
| 2753 | 2026-11-08 | low: Data: `tools/doctor.py --db data/photo_index.db` had not finished after 13 minutes (the share stats of 61,797 files) in the data owner's review run; whether that is the doctor's expected cost on this library or a missing deadline is unmeasured. | open |
| 2754 | 2026-11-08 | low: Files: `jobs/watching.py:421` `photo_actions.folder_state(dest)` is a bounded share look on watchdog's dispatch thread; a share that sleeps holds every library's notifications up to SHARE_WAIT per rename event. Possible; second pass of the Files owner. | open |
| 2755 | 2026-11-08 | low: Files: `jobs/watching.py:566-568` a folder's sync is not claimed as the whole sync is (`recurring.sync_whole`, #2102 covers the whole walk only), so a folder sync and the daily whole walk of one library can run at once (two walks of one share; both journaled, no row lost). Likely. | open |
| 2756 | 2026-11-08 | low: Files: `services/bulk_edit.py:239` the `size is None` arm is unreachable (`there` holds only present files, each with a stamp): a defensive branch with no instance. Likely. | open |
| 2757 | 2026-11-08 | low: tests: `test_a_cut_keyword_is_not_planned_again`, `test_when_a_photo_was_taken_is_kept`, `test_service_change_tags` and `test_service_replace_tag` restate functions that scenario tests already cover (F1's `test_a_file_holds_what_exiftool_kept`, `test_dates`, `test_tag_writes_keep_the_index_true`); fold the one case each adds into the scenario and delete them, mapping `test_tag_writes_keep_the_index_true` to F1. Candidates found by reading. | open |
| 2758 | 2026-11-08 | low: Integration: `home_libraries` is written twice, `runtime.py:122` and `web/libraries.py:190` (verified); one owner. Reported by the Files owner's second pass. | open |

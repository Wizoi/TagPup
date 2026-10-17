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
| 1518 | 2026-10-16 | low: `tools/retro_metrics.py` counts a merge's size against the 1,200-line budget as added plus removed lines, so a deletion reads as over budget: the data file of 2026-10-16 says 74 of 134 trunk merges are over; counted with added lines only it is 33 of 49 before the freeze and 4 of 85 after (49 of those 85 net-negative). Count added lines (or both, side by side). Retro 2026-10-16, section 2. | open |
| 1519 | 2026-10-16 | low: `tools/retro_metrics.py` reports sessions per day as distinct session ids per day (1 on each day), which hides that one main transcript spans 2026-10-02 to 2026-10-16 with 4 compact boundaries (meta fields `sessionId`, `type: system`, `subtype: compact_boundary`); "compactions" is listed as not counted. Report main sessions over the window and their compactions. Retro 2026-10-16, section 2. | open |
| 1520 | 2026-10-16 | low: `tools/retro_metrics.py` cannot tie a subagent run to a branch, so review rounds per branch and runs never merged are not counted; a subagent transcript's `cwd` (a meta field, not text) names its worktree and so its branch. Retro 2026-10-16, open question 1. | open |
| 1526 | 2026-10-16 | low: docs/AI_PIPELINE.md plans on 68,460 photos and 226k faces, and on "the resolution question (224 or 512)" as if the library ran at 224: counted read-only this session, photo_index holds 59,519 photos and 187,784 faces (35,425 named, 27 excluded), and every photo vector of the three libraries is under the key `ViT-H-14|laion2b_s32b_b79k|full-frame|1.4|512`, so today's size is 512 and phase 0's check (b) compares 224 against it. Update the document's counts when section 7 is filled. | fixed: the docs commit that reorders docs/AI_PIPELINE.md by the phase 0 numbers (59,519 photos, size 512) |
| 1527 | 2026-10-16 | low: the brief for phase 0 asks for scripts under scripts/ that time `images.opened`, `ml/clip.py` and `ml/faces.py` and count with SQL, but a program may not import store, files or ml (tests/test_layers.py, PROGRAMS_TODAY) nor hold SQL; measure_pipeline_stages times the real objects through hooks on what the runtime and services hand it (embed_image's `seen`, forward hooks, wrappers on MTCNN's detect, PhotoIndex.build_or_update, services.faces.record_batch, Pillow's save) and the census reads through services (inspect, identify, photos). By design: the guards hold; a future brief that needs a stage the hooks cannot reach needs a store read or an ml helper of its own. | open |
| 1528 | 2026-10-16 | low: the strata count a photo's faces in play (services.identify.photos_waiting: named, or unnamed and not excluded), not every face found: on photo_index the difference is 27 excluded faces, on kr-track 5,788 of 9,850 faces are excluded, so kr-track's strata read 69 photos at 10 or more faces where the faces table holds more. By design for this measurement (the services expose no all-faces count without the vectors); said in the census output. | open |
| 1529 | 2026-10-16 | wrong: the photo vectors of photos turned by their Orientation are stale: in phase 0's vectors check (a sandbox copy of photo_index, 201 photos re-embedded by the runtime's CLIP), all 4 turned photos read cosine 0.81 to 0.94 against the vector the library holds, the 197 others 0.999996 or more. The stored vector describes the file (its stamp matches), so it was kept from before CLIP was shown photos upright and never redone. photo_index holds 223 turned photos (census of 2026-10-16): Suggest's neighbours and the text match are wrong for them. A re-embed of the turned photos (or of all, at phase 5) settles it; their count in kr-track is 0. | open |
| 1530 | 2026-10-16 | wrong: `cluster-faces` on a sandbox copy of photo_index took 2,060 s (34 minutes), not the 3.5 minutes extrapolated for the pairs: the log shows the candidates done about 2 minutes in, then about 32 minutes in "Resolving multi-face photo conflicts and applying metadata consensus", single process, card idle (mean 0.5%). It runs after every index (docs/AI_PIPELINE.md, phase 2): phase 2's target is that stage, not the pair pass. | open |
| 1531 | 2026-10-16 | low: `scripts/measure_suggest_folder.py` no longer runs: opening a folder the library does not hold now shows the Add folder dialog, which intercepts the click on Start Photo Analysis (Playwright timeout after 30 s; the sandbox was deleted). Phase 0's "Suggest, a folder, cold / warm" is unmeasured until the script answers the dialog. The brief said to run it unedited. | open |
| 1532 | 2026-10-16 | low: phase 0's named-face curve on photo_index reads 98.3% precision at every likeness cut held out by folder (98.6% by photo), because the named faces are the ones FaceNet and the owner already matched: the hard faces are among the roughly 152,000 unnamed. The margin over the second-best person is what separates (0.04 or more: 99.8% on 80% of faces). A new recogniser's gate should be read on the margin curve and on a hand-checked sample of unnamed faces, not on this curve alone. | open |
| 1533 | 2026-10-16 | low: indexing the phase 0 sample peaked at 9,982 MiB of the card's 10,240 (887 MiB in use by other programs before): MTCNN at full resolution on the largest photos (a 193 MP panorama took 27 s in MTCNN alone; the 759-face photo 7 s). With the 6.3 GB the desktop apps held earlier in the day, an index would have run out of card memory or spilled into shared memory. Phase 1e (downscale before the detector) is also a memory question. | open |

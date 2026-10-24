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
| 1806 | 2026-10-23 | low: retro 2026-10-23 action 3 (guard): a hook (`.claude/hooks/`, the pattern of context-budget.mjs) that refuses a `git merge` adding over 1,200 lines (`tools/retro_metrics.py --branch` counts them) without an `Over budget: <reason>` line in the message, and a merge whose subject is only a `Worktree:` line; a test that it fires; CLAUDE.md's budget bullet then names the hook in one line. Target: unexplained over-budget merges 0, `Worktree:`-only subjects 0. | fixed: b5ca78f8 |
| 1807 | 2026-10-23 | low: retro 2026-10-23 (E3): `tools/retro_metrics.py` does not count the workers that stopped at the 150k hook and the relaunches that finished them, nor the review rounds of a branch whose reviewer started in the main checkout; the retro could not say what the three relaunches of 2026-10-23 cost. Count both (the subagent meta files and transcripts' usage fields only). | fixed: d3a4d824; review rounds of a main-checkout reviewer are counted as a group, not tied to a branch (a transcript carries no branch for them) |
| 1821 | 2026-10-24 | low: Suggest never offers a zero-shot word: its score is CLIP's cosine (suggester.suggest_for_photo, step 5), which on photo_index's 1,001 held-out photos never reaches OFFER_A_TAG 0.60 (no (photo, tag) pair at 0.40 or more under ViT-H-14 at 512; 0 of 1,084 tags offered by photo and 0 of 849 by folder came from words), so the word path reaches only the ranked list, never the panel or Apply All. Under SigLIP 2 B/16 no pair reaches even 0.23. | open |
| 1822 | 2026-10-24 | low: TagSuggester.suggest_for_photo calls self.taxonomy.people_roots() inside its loop over the neighbours (step 3), opening a read-only connection for each of 15 neighbours a photo: a per-item query for a per-run answer; model_for_run already reads the roots once (SuggestionModel.face_roots). | open |
| 1823 | 2026-10-24 | low: search.PhotoEmbeddings.keep logs a failed write and returns nothing, so a caller cannot tell a vector kept from one dropped; the measurement harness counts vectors before and after its pass to catch it. | open |
| 1824 | 2026-10-24 | data: `scripts/measure_pipeline_holdouts.py` `models_main`: `work = sandbox.outside_the_repository(abspath(args.work))` refuses only the repository and `makedirs(exist_ok=True)` accepts any existing folder; when every candidate file exists and not `--keep-work`, `sandbox.remove_sandbox(work)` rmtrees the whole folder. `--out D:\m --work D:\m` deletes every result just written; `--source D:\m\photo_index.db --work D:\m` deletes the source library; `--work <TAGPUP_HOME>` writes the sandbox placings into the owner's real `machine_roots.json` and then deletes the home. Nothing compares `work` with `--out`, the source's folder or `config.home()`, and nothing records that the harness created the folder. | open |
| 1825 | 2026-10-24 | wrong: docs/AI_PIPELINE.md section 7 "Phase 5 model spike" states numbers that appear in no printed table: "no pair reaches 0.40" (the word-cut counts are in the JSON only, `table()` prints no such column); the folder counts 2,289 and 2,564 (2,289 is `their_folders` in models-sample.json, 2,564 is computed nowhere in the script); the HF cache sizes are measured by nothing in the tree. | open |
| 1826 | 2026-10-24 | low: `cuts_needed` reads the neighbour cut as `np.interp(100*share_below_cut, PERCENTILES, percentiles)`; on the live run `share_below_cut` is 0, so the value is each model's minimum 15-NN likeness over 1,001 held-out photos, a cut that drops nothing, but section 7(g) and the "Neighbour cut" column call it "the cut each model needs". The test seeds a share of about 0.083 and never exercises the share-0 case the real data has. | open |
| 1827 | 2026-10-24 | low: `run_candidate` stop conditions (a `keep` that made a row) fire only after the whole pass and only once; the copy keeps the ghost row together with `copy-is-ready`, and the next run scores with a tagless extra neighbour. | open |
| 1828 | 2026-10-24 | low: `tagpup/services/tags.py` `people_roots(library)` is a third spelling of one read beside `TagTaxonomy.people_roots()` and `taxonomy.face_roots(db_path)` (7 roots against 3 on photo_index); the script's `_a_person` is the "root is a people root" rule that `services/person_tags.py` and `suggester.py` each write inline. | open |
| 1829 | 2026-10-24 | low: `card_use` takes "before" from the 1.5 s before `clip.load()`; `clip.unload()` drops references without `empty_cache`, so with several candidates in one process the previous model's allocator cache counts as other programs'. "Card MiB over" is reliable only one candidate a process (not demonstrated). | open |

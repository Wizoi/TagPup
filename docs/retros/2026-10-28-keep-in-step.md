# Retro 2026-10-28: phase 6, keep-in-step, the old models deleted, the share fix

Written for the owner and the next session. Numbers are from `reports/retro-2026-10-28-data.md` (made by
`tools/retro_metrics.py --since 2026-10-24 --until 2026-10-28`, untracked); the insights were drafted by an agent
that read only the data file, the findings ledger, DECISIONS and RETRO.md. No real names.

## 1. Scope and trigger

Window 2026-10-24 to 2026-10-28 (the last retro, phase 5, ended 2026-10-24): phase 6 (the bake), the keep-in-step plan
(cuts 1 to 3), Cut B (the old face and photo models deleted), the share fix. Trigger: the owner called the feature tree
done. One piece remains at the time of writing: a worker is making the indexer cover 9,882 photos of photo_index that
hold a vector and no bake record (sync owed them nothing).

## 2. Counted numbers

- Trunk: 562 commits, 70 merges; two of 57 budget-checked merges over 1,200 added lines, both with a reason.
- Lines in the tree: code +809, tests +4,503, docs +1,005.
- Findings: 305 opened (55 `wrong`, 1 `data`, 249 `low`), 296 closed (166 fixed, 117 decided), 22 open at the end.
  137 of 562 commits touched only findings files (24.4%).
- Tests: 3,784 to 3,901 `def test_`; 21 files added, 8 deleted.
- Agents: 100 worker runs and 22 small-worker runs, 58% of the tokens; the main sessions 28%. 47 of 122 worker runs
  went past 150k of context. 8 of 13 main sessions went past 300k; 0 compactions.
- Cost, API-equivalent: about 499 to 1,255 USD.

## 3. Timeline

10-24 to 10-25: phase 6 builds `bake-photos`, `face-pass` and the report, with undo. 10-25: the old model goes away
completely (DECISIONS); the fixtures still seed it. 10-26: `delete-old-detector-rows` built as an undoable command. 10-27:
the owner restates that the indexer keeps files and index in step and he runs no command; keep-in-step cuts 1 to 2c-ii.
10-28: cut 3 deletes the commands; Cut B deletes the old models; the share fix (parts 1 to 6, a guard, three move
groups) merges 09:12 to 10:53; the cuts and the share fix are installed (4651944e).

## 4. The three most expensive episodes

1. **The bake was built as a command, then rebuilt as the indexer's job.** Fifteen phase-6 merges built the backfill with
   dry run, apply and undo; on 10-27 the rule "never a command the owner runs" replaced them; cuts 3a to 3d deleted
   them. Seven `wrong` rows sat on the deleted layer (#1906, #1907, #1908, #1938, #1945, #1946, #1965), and the undo of
   bakes drove #1951, #1952 and #1960. Earliest signal: DECISIONS 2026-10-24 described only the backfill, and the pilot's
   #1974 and #1976 (10-25) were about the index reading a bake. Cheaper path: ask first "a photo added later: which writer
   keeps it right, with no command run?".
2. **A share that stops answering was fixed site by site for four days.** Eleven `wrong` rows of the class; four reached
   the owner on the live server on 10-28. Earliest signal: #1907 (10-25), the second row. The share-looks guard is in
   (10-28): no further action; the metric is `wrong` rows of the class, 11 this window, target 0.
3. **Removing the old models churned the tests and the journal.** Cut A1 left 30 of 131 test files failing (#1985) and the
   default stamp 46 (#1991) because `tests/face_rows.py` seeded FaceNet vectors; a live undo check was dropped (#1979);
   `delete-old-detector-rows` was built undoable and deleted two days later; #2122 is open. Earliest signal: DECISIONS
   2026-10-25 and #1991. Cheaper path: move the shared fixtures to the new model first, in one place; make the cleanup a
   one-off run.

## 5. Did the last retro's actions work

| Action (2026-10-24) | Metric then | Now | Verdict |
|---|---|---|---|
| Notice a findings row that looks like an older one | findings-only commits 18.8%, target under 10% | 24.4% | missed; the draft's replacement cannot work (a commit cannot name its own hash), so it is dropped, not rewritten |
| Moved code is briefed as new code | 4 `wrong`/`data` rows on moved code, target 0 | 0 opened, but no move was confirmed | met, barely tested |
| One cut per brief | worker runs past 150k 13 of 25, target under 25% | 47 of 122 (38.5%) | missed; carried into action 3 below |
| 300k main-session hook | 2 of 5 sessions over | 8 of 13 over, 0 compactions | still undecided, worse; action 3 is its rule, measured first |

## 6. Actions (owner, 2026-10-28)

1. **Brief change.** A one-time command or backfill is briefed with its steady state first. Metric: commands or modules
   merged then deleted in one window, 4 now, target 0.
2. **Guard.** `face_rows.add_face` gives every face a vector under the library's face model; a test fails on a face
   fixture no writer makes (#2122). Metric: fixture-shape rows, 4 now, target 0.
3. **Brief change.** A worker brief is one concern and names what remains; a main session compacts at about 300k at the
   latest. Metric: worker runs past 150k under 25% (38.5% now); main sessions over 300k under 4 of 13 (8 now).

## 7. The instruction diff

- CLAUDE.md: removed 4 lines of the OS-level-symptom paragraph (reasoning is in docs/DEVELOPMENT.md) and the stale
  migration-34 sentence; added action 1 (4 lines) and action 3 (one line folded into the session line). 196 to 198 lines.
- MEMORY.md: seven stale handoff lines (and their files) removed; the START HERE line rewritten. Net across files:
  removed more lines than added.
- No agent definition changed (the worker's 150k line already holds action 3's worker half).

## 8. What to carry to other projects

- Before building a one-time command, write the steady-state scenario: who keeps the thing right afterwards with no
  command. It is the cheapest question and it was the most expensive omission here.
- Give a "this may not answer" resource (a share, a service) one owner on the second row of the class, not the fifth.
- Move shared test fixtures with the default they seed, in one place, before the default changes.
- An action that asks a commit to name its own hash cannot be followed; check the mechanics before deciding it.

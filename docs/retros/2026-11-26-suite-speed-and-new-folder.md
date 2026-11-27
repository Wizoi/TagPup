# Retro 2026-11-26: the suite's speed, the metadata plan, working on a new folder

Written for the owner and the next session. Numbers are from `reports/retro-2026-11-26-data.md` (made by
`tools/retro_metrics.py --since 2026-11-24 --until 2026-11-26`, untracked) unless marked as a hand count from
`reports/retro-2026-11-26-timeline.md` (untracked). The insights were drafted by a fresh Opus agent that read only those
two files, the ledger, DECISIONS and RETRO.md; it read no code and no git. No real names.

## 1. Scope and trigger

Window 2026-11-24 to 2026-11-26 (trunk 6631f3d2..a6d8c108), since the 2026-11-23 retro. Question: what cost the most,
and what would have been cheaper? Triggers: cut C took two review rounds and the second found a regression of the first
round's fix; a main session hit the context hook mid-phase; two main sessions worked on one trunk on 2026-11-26.

## 2. Counted numbers

- 293 commits, 24 merges into the trunk, 0 reverts; 0 merges over the 1,200-line budget (largest 1,040 added).
- 67 findings opened (58 low, 7 wrong, 2 none); 66 closed (49 fixed, 17 decided); 2 open at the end. 2 escapes:
  #3407 and #3408 (`wrong`, "Owner hit it", found by the user-flow review that began the new-folder plan).
- Tests: files 508 -> 521, `def test_` 4,841 -> 4,944. The suite 756 s -> 297 s (#3375, the commit notes).
- 405..1,107 USD for the priced models; 79% of tokens in subagents; review 13% of review plus workers.
- 29 of 39 worker runs passed 150k of context (276M tokens, 203..609 USD). 4 of 6 main sessions peaked at 310k-317k.
- 34 worker branches "no merge found" (45.1% of tokens): the new-folder cuts landed by rebase, which the script does
  not tie to a merge.
- 44 of 293 commits touched only findings files.

## 3. Timeline

- 2026-11-24: work rows merged; #3375 (fast suite) planned and built in 7 cuts through the day, ExifTool record/replay
  merged 18:29 (1,040 lines), the coverage map, the page tier's timer fix; #3376 parked until replay was measured.
- 2026-11-25 00:11: the suite at 297 s; #3375 closed 06:56.
- 2026-11-26 morning: one root map per run merged; #3376's design agent cut short at 150k (owner: design agents to 450k,
  08:41); the first L0a worker hit 150k reading (owner: coding workers to 250k, 11:06); L0a-i and L0a-ii merged; five
  owner decisions on the metadata library (13:09-13:15), then decisions 1-5 from four owner reviews (17:26).
- 2026-11-26 afternoon, beside the metadata session: the new-folder plan (14:37), cuts G, A, B, D, F, C landed by rebase
  between 14:44 and 19:05; review rounds A 1 + quick, B 1, C 2, D 2 + quick, F 2 (hand count). Installed 88d39929 at 20:26.

## 4. The three most expensive episodes

**E1. Context.** 29 of 39 worker runs passed 150k (203..609 USD); the limit was raised twice in one day after a design
agent and a coding worker were cut short. Earliest signal: the share past the hook. Cheaper path: narrow what a worker
reads before raising what it may hold; the design agent's cut-off was the cost of learning (none of that size had been
briefed). Owner of the rule: the worker brief; the limits, `.claude/hooks/context-budget.mjs`. Main session's note:
most of the 29 ran before the 250k change, so a fixed 150k overstates it.

**E2. The new-folder review rounds.** 36 rows in about six hours; cut C's second round found #3442, a re-read path
(`sendChunk` / `readMoved`) still calling its entry sent after the first round fixed the other (`postOrSendAgain`,
#3438); #3417 came from a test that gave each folder a status the server never gives. Earliest signal: #3415 (cut B,
15:16) already named both re-read paths. Cheaper path: fix where the "waiting in the page" state is decided, not in one
of its callers. Owner: `web/tagpup/write-queue.js` `markBackInPage` (made the single place by the #3442 fix; no guard).

**E3. Two sessions on one trunk.** One `low` row (#3413, a scenario id taken twice) and one misread test count, both
caught before merge. No action: the cost of learning. The script's blindness to rebased branches is a script bug (#3450).

## 5. Did the last retro's actions work

| Action | Target | This window | Kept |
|---|---|---|---|
| #3343, a written photo's page copies have one writer | `wrong` rows on page copies 2 or fewer | 0 | kept |
| #3344, a third fix needs `guarded by <test>` | mechanisms with 3+ `wrong` fixes: 0 | 0 (the page following the index queue is separate mechanisms, owner); #3426 used the check | kept |
| Readers listed before a decision on where a value is written | same-day amendments 2 or fewer | 1 or 2 | kept |

## 6. Actions

The owner chose all three proposed; in DECISIONS 2026-11-26.

1. **Guard** (#3449): a frontend test fails when a module other than `write-queue.js` changes an entry between sent and
   waiting in the page. Metric: second-round regressions of a first-round fix or of a test's invented shape, 2, target 0.
2. **Brief change** (`tagpup-worker.md`): read what the brief names, the cut of the plan and the ARCHITECTURE.md section,
   then write; commit at the first green test. Metric: worker runs past the commit point (about 175k; counted once
   #3451 is built), target 1 in 4 or fewer.
3. **No action** on two sessions on one trunk; the script ties a rebased branch (#3450). Metric: tokens on "no merge
   found", 45.1%, target under 10%.

## 7. The instruction diff

- CLAUDE.md, 201 -> 199 lines: the "second fix" paragraph (2 lines) is one line naming its guard (`add_findings`, #3344);
  the feature-freeze sentence (lifted 2026-10-16, history) goes, the seven conditions kept (4 lines -> 3).
- `tagpup-worker.md`, 128 -> 128: "Read CLAUDE.md and the part of ARCHITECTURE.md first" goes (CLAUDE.md is in a
  worker's context already); the 175k line becomes the reading-then-writing rule with the commit point.
- Net: 8 lines removed, 6 added.

## 8. What to carry to other projects

A fix for a state with two writers belongs in the function that sets the state, and a review finding that names two
callers is the signal. Raising an agent's context limit treats the symptom; first narrow what it is told to read. A
metrics script that ties work to merges must also tie work that lands by rebase, or parallel sessions hide their cost.

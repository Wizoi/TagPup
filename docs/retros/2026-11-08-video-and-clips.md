# Retro 2026-11-08: video support, clip writing, A5 cuts A to D, the low batches

Written for the owner and the next session. Numbers are from `reports/retro-2026-11-08-data.md` (made by
`tools/retro_metrics.py --since 2026-10-29 --until 2026-11-08`, untracked) unless marked as a count; the insights were
drafted by an agent on the Fable model that read only the data file, the findings ledger, DECISIONS and RETRO.md.
No real names.

## 1. Scope and trigger

Window 2026-10-29 to 2026-11-08 (the last retro ended 2026-10-28): the activity rework, the own metadata writer, video
support (M0 to M7), the A5 cuts (Smart Rename, delete, Date Taken of a clip) and the low batches. Trigger: phase end.

## 2. Counted numbers

- Trunk: 901 commits, 91 merges; two over 1,200 added lines, both with a reason; 1 revert.
- Lines in the tree: code +10,553, tests +18,252, docs +1,154.
- Findings: 529 opened (70 `wrong`, 2 `data`, 454 `low`), 563 closed (355 fixed, 203 decided), 0 open at the end.
  212 of 901 commits touched only findings files (23.5%).
- Tests: 3,913 to 4,492 `def test_`; 51 files added, 1 deleted.
- Agents: workers 54% of the tokens; 87 of 177 worker runs past 150k (49%); 16 of 21 main sessions past 300k;
  0 compactions; cost 921 to 2,294 USD API-equivalent.

## 3. Timeline

10-29 to 10-30: activity rework, scratch-file and removal decisions. 11-02: the video plan and its decisions. 11-03:
the own writer, the JPEG patch, write beside / compare / move. 11-05: clips indexed and played; the clip arm (M7 B1 to
B4); 34 merges. 11-06: the title and the caption become two values (the owner hit #2579); M1 clip vectors; the caption
sweep. 11-07: clip tags written in place; the 14 "QuickTime-only" values read and the rule deleted. 11-08: A5 cuts A to
D; two `data` rows in cut B/C review; the low batch.

## 4. The three most expensive episodes

1. **The title and the caption were one value read from two places.** 20 `wrong` rows name title or caption (count).
   Earliest signal: #2432 (11-03), 104 rows whose stored captions disagreed with what a save reads; the fix re-derived
   rows instead of naming one rule. The owner hit #2579 on 11-06; the sweep after it ran cold, reported "Cleared: 0"
   and wrote nothing (#2622). Cheaper path: one function for the page record and the save at #2432, and every sweep
   briefed with "a write reports what it changed". The owner of the rule is now `vocabulary.title_of`.
2. **Clip writing was built on 14 instances nobody had read.** The QuickTime-Subject rule and the
   `CLIP_PATCH_IN_PLACE` flag merged 11-05 and were deleted 11-07: the 14 values were one binary entry, not keywords.
   Two more clip decisions were revised within a day. Earliest signal: the 11-02 census counted fields, not values.
   Cheaper path: read one value from one live row before writing the rule. Proposed as an action; the owner did not
   take it this time.
3. **A clip deleted for good while the reply said Recycle Bin.** The only two `data` rows (#2700, #2705), found in
   review, not by the owner. Windows deletes a file too large for its drive's Bin without asking. Cut B merged at 1,527
   added lines because it grew the for-good path that the 11-02 decision had assigned to cut C. Windows' behaviour:
   the cost of learning. The overrun: cuts B and C briefed as one mechanism would have been one branch.

## 5. Did the last retro's actions work

| Action (2026-10-28) | Metric then | Now | Verdict |
|---|---|---|---|
| Steady state first in a one-time command's brief | merged then deleted: 4, target 0 | 0 files; 1 flag and rule (episode 2) | met by the letter |
| Guard: `add_face` gives every face a model vector | fixture-shape rows: 4, target 0 | 1 (#2334, closed decided) | met |
| One concern per brief; compact at about 300k | past 150k 38.5%, under 25%; main sessions over 300k 8 of 13, under 4 | 49%; 16 of 21; 0 compactions | missed twice; advice, not a guard |

## 6. Actions

One (owner, 2026-11-08: "#3"). **Guard**: `tools/add_findings.py` refuses to close `fixed` a row naming a sweep,
backfill, `--apply` or bulk write without `wrote N` in the status. Metric: bulk writes that reported done and wrote
nothing, 1 now (#2622), target 0. The 150k stop and the live-value brief line were drafted and not taken.

## 7. The instruction diff

- CLAUDE.md: 200 to 199 lines. Removed the 47-of-122 count; corrected ARCHITECTURE.md's size; split two run-together
  bullets; shortened the budget bullet (the hook `merge-budget.mjs` holds it), the special-case bullet and the `low`
  bullet; added two lines for the new guard.
- `tagpup-worker.md`: three dated incident tails cut (31 smudges, the `p2.py` collision, the moved harness); no line
  added.
- MEMORY.md: five superseded handoff lines and their files removed.

## 8. What to carry to other projects

- A "live count above zero" counts rows, not values: read one value from one row before the rule is written.
- A status that closes a bulk-write finding should carry what the fix wrote; "done" without a count is the failure.
- When two cuts share a mechanism (a token, a warning, a page), brief them as one branch.

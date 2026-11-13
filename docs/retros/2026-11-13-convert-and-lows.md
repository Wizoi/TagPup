# Retro 2026-11-13: Convert, the clip saves, the low batches

Written for the owner and the next session. Numbers are from `reports/retro-2026-11-13-data.md` (made by
`tools/retro_metrics.py --since 2026-11-08 --until 2026-11-13`, untracked) unless marked as a count. The insights were
drafted by a fresh agent that read only the data file, the findings ledger, DECISIONS and RETRO.md, then checked by a
second fresh agent on the Fable model, which counted again read-only. No real names.

## 1. Scope and trigger

Window 2026-11-08 to 2026-11-13 (the last retro ended 2026-11-08): the A5 cuts, the low batches, Convert (script and
panel), the Windows Tags move, the 64 KB XMP cap. Trigger: phase end. The owner's two questions: do the proposals reach
the root or only the effects, and are the five domain owners too large.

## 2. Counted numbers

- Trunk: 480 commits, 77 merges (71 measured into the trunk; the script does not explain the 6); three merges over
  1,200 added lines, all with a reason; 1 revert.
- Lines in the tree: code +3,546, tests +7,009, docs +453.
- Findings: 239 opened (7 `data`, 29 `wrong`, 203 `low`), 322 closed (191 fixed, 131 decided), 0 open at the end;
  107 of 480 commits touched only findings files (22%).
- Tests: 4,437 to 4,613 `def test_`; 21 files added, 5 deleted.
- Agents: workers 58.5% of the tokens (74% of all tokens in subagents); 59 of 127 worker runs past 150k (46%);
  6 of 10 main sessions past 300k, none compacted. Cost 515..1,305 USD at list prices; reviewer runs are unpriced.
- Owner runs: files 20, integration 9, views-ai 7, data 3, people 2.

## 3. Timeline

- 11-08: low batches, the doctor's deadline, the domain docs restated, the Delete cuts B and C, the date cut D.
- 11-09: Show filter, Convert cut 1, scrub cut A, migration 39, the one-time Windows Tags move.
- 11-12: the one-time conversion script merged (273b7e7a), its lock reverted, the service and the panel's Convert
  merged (5bafc944), the script deleted; the move script deleted; the owner raised #2869 and #2871; Windows Tags
  carried by Convert (reverses the 11-09 "one time only"); the 64 KB XMP cap removed.
- 11-13: the clip that would not save (#2918), the folder cache fixes, the away-share fix, 0 open findings.

## 4. The three most expensive episodes

**A. Convert built twice in one day.** Cost: 53 rows name it, 50 of them on 11-12; 4 of the 7 `data` rows and 12 of
the 29 `wrong`; two of the three over-budget merges (1,523 and 1,897 added); the window's only revert, a lock the owner
then ruled not a case (#2826); 9 rows about the script alone. Earliest signal: the 11-09 decision named both the
panel's Convert and a bulk run for the old formats, and the 11-08 retro had written "two cuts sharing a mechanism are
one branch" under carry-over with no action. Cheaper path: build the journaled service once and make the one-time run
its caller; the steady-state-first rule fired as written, but its answer licensed a script without saying the script
calls the service. Third instance of the shape (the bake, 10-28).

**B. The owner's clips that would not save: three fixes for one symptom.** #2893 to #2895, then #2908 (closed "the
owner's clip saves on install", a claim, not a check), then #2918 ("still refused"). Earliest signal: #2895 said its
fixture was padded, not shaped like the live packet. Cheaper path: capture the box layout of the owner's file as the
fixture before the first fix and close only when a copy of that file saves. `add_findings` printed a notice when #2918
resembled #2908 and did not refuse.

**C. Windows Tags "one time only", then the move script deleted.** Convert refused a live clip with "move the Windows
Tags first" while the script that did it was already deleted (#2871, raised by the owner; 7 rows). Earliest signal:
the refusal's own wording. Cheaper path: before deleting a one-off, search for what names it and count what it leaves.

## 5. Did the last retro's actions work

| Action (2026-11-08) | Metric then | Now | Verdict |
|---|---|---|---|
| `add_findings` refuses `fixed` on a bulk write without `wrote N` | 1 write that reported done and wrote nothing | 0 live; the trigger matched 2 rows, and #2811 (same shape) closed `fixed` without `wrote N` | kept; widening it was not taken, it would not have changed episode A |

The 10-28 target (workers past 150k under 25%) was 49% then and is 46% now: missed twice. Left as advice both times.

## 6. Actions

Three (owner, 2026-11-13: "Three actions, for 300k, delete the line"); in DECISIONS 2026-11-13.

1. **Guard** (to build, #2923): `merge-budget.mjs` refuses an over-budget merge that adds a `scripts/` program or a
   service only a script calls; a one-off is deleted only when its dry run says 0 left. Metric: one-off merges over
   budget 1 now, target 0; scripts deleted with rows left 1 now, target 0.
2. **Guard** (to build, #2924): `similar_rows` refuses when the matched row is closed `fixed`, unless the new row says
   "second fix of #n: the single place is ...". Metric: chains of three fixes for one symptom 1 now, target 0.
3. **Delete**: the 300k-of-context line in CLAUDE.md. Metric: none; the line is gone.

Owners: no change (Files took 20 of 41 owner runs and all 7 `data` rows are journaled-write defects its checklist
covers; a Video owner would put a second owner on the write path, the integration owner's veto; integration was not a
bottleneck). One line in OWNERS.md instead: a one-off has a domain and its owner's review; a feature across two domains
has one plan naming the single place of each mechanism before a second worker starts.

## 7. The instruction diff

CLAUDE.md: 1 line removed (the 300k clause), 1 added (the one-off bullet now says it calls the service and is deleted at
0 left, in the same space as before); the fixture line folded into an existing bullet; net 0. OWNERS.md +2 lines. No
rule yet enforced by the two guards, so none removed; when they are built the one-off bullet and the "second fix"
paragraph each shorten to a line naming the guard.

## 8. What to carry to other projects

- A rule written under "carry over" with no action recurs; give it an owner and a guard or delete it.
- A one-time program is a second implementation of the feature it serves; give it the owner and the single plan.
- A similarity notice that does not refuse changes nothing; the refusal with an explicit escape does.
- A claim in a closing status ("saves on install") is not a check; the fixture of a defect in someone's own file is
  that file's shape.

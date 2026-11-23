# Retro 2026-11-22: photos-written, the rename map, the write cuts

Written for the owner and the next session. Numbers are from `reports/retro-2026-11-22-data.md` (made by
`tools/retro_metrics.py --since 2026-11-14 --until 2026-11-22`, untracked) unless marked as a hand count. The insights
were drafted by a fresh Opus agent that read only the data file, the findings ledger, DECISIONS and RETRO.md. No real
names.

## 1. Scope and trigger

Window 2026-11-14 to 2026-11-22: one-queue cuts, Manage Folders, the phone viewer cuts 1 to 3b, the photos-written
plan in four cuts. Trigger: phase end; a third review round on one branch (one-queue cut 3a-2, 11-16); a rule broken
twice (one rename map fixed four times); two merges over budget. The one question: what cost the most, and what would
have been cheaper.

## 2. Counted numbers

- Worker runs past 150k: 57, 345,751,561 tokens, 51% of worker tokens (data file, section 5). Relaunches: 0.
- Merges into the trunk: median 219.5 lines added, p90 642 (section 6). Over the 1,200 budget: 2 (7c649fb97e, 1,335,
  with a reason; f88904a2, 1,258, none).
- Rows opened: 313; review-found proxy 56 (section 2). `wrong`/`data` rows on write paths: 34 of 47 (hand count by
  the drafting agent, not verified).
- Main sessions past 300k: 6 of 25 (section 5).

## 3. Timeline

- 11-16 one-queue cut 3a-2: three review rounds.
- 11-19 f88904a2 (phone cut 3b) merged at 1,258 added lines with no `Over budget:` line.
- 11-20 to 11-22 photos-written built in four cuts; #3329, #3336, #3338, #3339 (the rename map) opened and fixed on
  11-22; 67e02091 pushed and installed.

## 4. The three most expensive episodes

**A. One rename map, fixed four times (#3329, #3336, #3338, #3339).** Cost: 4 `wrong` and 2 `low` rows, 5 fix
commits. Earliest signal: #3329 counted a chained rename, and #3330 said the page tests hand-spelled `updated_paths`.
Cheaper path: at #3329 list every page reader of a rename reply and give them one resolver (`renamedBy`); it came one
round late. Single place: `web/common/paths.js`.

**B. Write cuts whose tests passed with a `wrong` in them.** Cost: 34 of 47 `wrong`/`data` rows on write paths (hand
count). Earliest signal: the third round on cut 3a-2 (11-16). Pattern: the page holds one photo in several copies and
each cut moved some of them, with hand-spelled replies in its tests. Cheaper path: at that round ask which single
place owns every copy; it was answered on 11-22 (`cache.js photosWritten`, #3324).

**C. Workers past 150k.** 57 runs, 51% of worker tokens, 0 relaunches; cut size is not the cause (median 219.5 lines).
The data cannot split reads from test output. None: the cost of learning.

## 5. Did the last retro's actions work

| Action (2026-11-14) | Then | Now | Verdict |
|---|---|---|---|
| `low` rows need `live: N` | 7 built then reverted | 4 reverts in the window (same-day ones to be confirmed in git); 26 rows closed "decided: no live instance" (ledger grep) | met, if those are 11-14's |
| No sweep, spawned rows reported | 2 built; 1 DECISIONS row corrected | spawned rows built not counted; no DECISIONS row marked corrected | not measurable as set |
| No action on delete-for-good | 1 `data` row | 4 `data` rows on Delete paths (#3123, #3256, #3257, #3266) | missed |
| Workers past 150k under 25% | 33% (21 of 63) | 37% (57 of 156) | missed a fourth time: deleted |

## 6. Actions

Three (owner, 2026-11-22: "Let's do the actions"); in DECISIONS 2026-11-22.

1. **Guard** (built): a frontend test fails when a page module reads `updated_paths` other than handing it whole to
   `photosRenamed` / `recordsWritten`. Metric: mechanisms needing more than two `wrong` fixes in a window, 1 now,
   target 0.
2. **Guard** (#3342, to build): page tests take a write route's reply from a fixture the real route wrote. Metric:
   rows naming hand-spelled test data, 2 now, target 0.
3. **Delete** the "workers past 150k under 25%" target; **fix** the merge-budget hook (#3341): it matched only the Bash
   tool, so f88904a2 merged through PowerShell unchecked. Metrics: relaunches 0, target 0; over-budget merges with no
   reason, 1 now, target 0.

## 7. The instruction diff

CLAUDE.md: no change (201 lines, one over the 200 target; no line added, none a guard now enforces). The 25% target
lived only in DECISIONS 2026-10-28 and is superseded by the 2026-11-22 row. Net: 0 removed, 0 added.

## 8. What to carry to other projects

A PreToolUse hook is registered per tool: a guard on `Bash` does not cover the same command run through another shell
tool. List every shell tool in the matcher. A rename or move map read by several readers needs one resolver and a test
that fails on a second reader.

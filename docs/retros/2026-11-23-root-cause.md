# Retro 2026-11-23: root cause against symptom, 15 days

Written for the owner and the next session. Numbers are from `reports/retro-2026-11-22-data.md` (made by
`tools/retro_metrics.py --since 2026-11-07 --until 2026-11-22`, untracked) unless marked as a hand count. The insights
were drafted by a fresh Opus agent that read only the data file, the ledger, DECISIONS, RETRO.md and the earlier retro
documents; it read no code and no git, so every "a guard exists" claim is the ledger's word, not checked. No real names.

## 1. Scope and trigger

Window 2026-11-07 to 2026-11-22. Trigger: the owner's question, "are we addressing root cause and not just symptoms".

## 2. Counted numbers

- 1,404 commits, 183 merges into the trunk, 5 reverts. Over the 1,200 budget: 5 merges (one without a reason).
- 713 findings opened (13 data, 84 wrong, 613 low); 774 closed (496 fixed, 276 decided); 1 open before this retro.
- 357 of 1,404 commits touched only findings files (25%).
- Worker runs past 150k: 141, 56% of worker tokens. Main sessions past 300k: 14 of 41.

## 3. Findings: the symptom-against-cause audit

Areas fixed two or more times, with the single owner now (the owner's name is the ledger's, not checked in code):

| Area | Rows | Owner now | Gap |
|---|---|---|---|
| The rename map | #3329, #3336, #3338, #3339 | `renamedBy` / `rekeyed`, `web/common/paths.js` | guarded for `updated_paths` only |
| The page holds one photo in many copies (the parent) | about 23 `wrong` rows #3054..#3339 (hand count) | `cache.js photosWritten`; server `page_record` | no client-side guard (#3343) |
| Tag-write order | #3107, #3202, #3208..#3210, #3221 | `pending.js takeChunk` | #3220, #3226 wait on the server-queue review |
| The update hand-over hang | #2634, #2651, #3190, #3191, #3268, #3270, #3312 | a deadline (`IDLE_PATIENCE`) | the task whose stop never returned is not named (#3345) |
| Clip decisions | 8 DECISIONS rows reversed or amended inside 7 days | none | readers were not listed before deciding (action 3) |

Guards in name only: "a write reports what it changed" (checks a finding's text, the defect came back in code as
#2873, #3063, #3288); "a test's data is made by the real code" (Python photo rows only; page fixtures fixed piece by
piece). The third-fix check (#2924) records the place but did not stop a third fix on 3 chains.

## 4. The three most expensive episodes

**A. Page-side write coherence, 11-16 to 11-22.** 23 `wrong` rows, 3 review rounds on one cut, the rename map four
times. Earliest signal: #3102 and #3106 on 11-16. Cheaper path: at the third round, name the one place that moves every
copy of a written photo (named on 11-22, #3324).

**B. Convert, 11-12.** 4 of the window's 13 `data` rows, 2 over-budget merges, 1 revert. Cheaper path: one service with
the script as its caller. Already acted on 11-13; no repeat counted.

**C. Clip date decisions.** 8 reversals, about 4 of them on Date Taken. Earliest signal: the decision revised the same
day. Cheaper path: list every reader of the value, checked on one live file, before deciding.

## 5. Did the earlier actions work

Met as worded: the `wrote N` guard (the defect still came back in code 3 times, the guard reads text), over-budget
one-offs, `live: N` lows. Missed: "second fix needs the single place" (3 chains not stopped), delete-for-good (3 `data`
rows on the journaled Delete settle). Not measurable, dropped: 2026-11-14 action 2.

## 6. Actions

Three (owner, 2026-11-23: "follow all recommendations"); in DECISIONS 2026-11-23.

1. **Guard** (#3343): a frontend test fails when a page module writes a kept scan, `state.folderPhotos` or a view
   record other than through `cache.js photosWritten` / `photosGone`. Metric: `wrong` rows on page copies, 23, target 2.
2. **Guard** (#3344): the third-fix check needs `guarded by <test>` and counts rows fixed on a branch. Metric: mechanisms
   with 3 or more `wrong` fixes in a window, 4, target 0.
3. **Brief change**: a decision on where a value is written lists its readers first (CLAUDE.md). Metric: reversals
   inside 7 days, 8, target 2.

Open questions answered: the server-queue review stays queued; the hand-over hang's cause is investigated (#3345).

## 7. The instruction diff

CLAUDE.md: the "second fix" paragraph (4 lines) is rewritten in 2 lines naming the guard, and the brief change shares them;
201 lines before, 199 after. Net: 4 removed, 2 added.

## 8. What to carry to other projects

A "second fix is not a fix" rule that records only a sentence does not stop a third fix: it has to name a test that
fails on a second reader. A guard on a finding's wording does not guard the code. Decide where a value is written only
after listing everything that reads it.

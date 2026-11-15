# Retro 2026-11-14: lows built, reverted and rebuilt smaller

Written for the owner and the next session. Numbers are from `reports/retro-2026-11-14-data.md` (made by
`tools/retro_metrics.py --since 2026-11-13 --until 2026-11-14`, untracked) unless marked as a count. The insights were
drafted by a fresh Opus agent that read only the data file, the findings ledger, DECISIONS and RETRO.md (plus read-only
`git log`). No real names.

## 1. Scope and trigger

Window 2026-11-13 to 2026-11-14 (the last retro ended 2026-11-13): the clip-header cuts 1 to 3 and the one-off that
wrote the clips' QuickTime headers, the held-file analysis, the journal's erased changes, the low batches of 11-14.
Trigger: phase end. The one question: what cost the most, and what would have been cheaper.

## 2. Counted numbers

- Reverts in the window: 4, all on 11-14; three merges undone, 310 lines added in them.
- Merges over the 1,200-line budget: 0 of 43.
- Findings 3031 to 3035 opened and closed unbuilt on 11-14.
- Worker runs past 150k: 21 of 63 (33%); last retro 46%. Main sessions past 300k: 2 of 7 (last retro 6 of 10).
- The day's priced spend, 32..67 USD at list prices, is an upper bound; the cost of each episode was not counted.

## 3. Timeline (11-14)

- 12:42 four lows merged (2988, 2989, 2992, 3000; +156/-52); the worker's sweep opened 3031 to 3033.
- 13:10 3033's fix landed with a DECISIONS row that extended the owner's answer; 13:53 3031 and 3032 merged, their
  review opened 3034 and 3035.
- 15:08 3031 and 3032 reverted, the four rows decided "0 live instances"; 16:46 the four-lows merge reverted,
  "decided, not built"; 17:20 3033 reverted and its DECISIONS row corrected.
- 17:23 to 17:26 the four lows fixed directly in four commits (+42/-9).

## 4. The three most expensive episodes

**A. Lows built, reverted, rebuilt smaller.** Cost: all 4 reverts of the window, 3 merges undone, 5 rows opened and
closed unbuilt. Earliest signal: #2988's own row counted 0 matching notes in the three libraries; #2992 and #3000 cite
a hypothetical, not a count; #3031 said it "needs its interrupted-part-way handling designed". Cheaper path: the rule
already written ("a special case needs a real instance"): the lows briefed as small edits with no sweep, and any row a
fix spawned closed at live 0 as decided at birth. Causes: the brief applied the feature rule ("a sweep first") to lows;
`add_findings` took a low with no live count; nothing separated the owner's words from the main session's gloss in a
DECISIONS row.

**B. #2938 (data): Convert deleted the original while its re-encode drops the XMP packet.** Merged 11-12, found 11-13,
fixed in 258495a3. Earliest signal: the `clip_convert` docstring said ffmpeg does not carry the packet. Cheaper path:
an owner's question before any delete-for-good ("what does the original hold that the copy does not?"). The Media owner
was added inside the window; whether any of the 14 in-app conversions lost XMP was not counted. No action.

**C. One stop-report rule, three wrong rows.** #2974 made `.change = None` mean "erased"; that broke two callers
(#2993, #2997) and the clip-header one-off made the same mistake (#3020). #2924's guard made #3020 name its single
place, one step late and before a third fix. No action.

## 5. Did the last retro's actions work

| Action (2026-11-13) | Metric then | Now | Verdict |
|---|---|---|---|
| Merge-budget one-off guard (#2923) | 1 one-off merge over budget; 1 script deleted with rows left | 0 of 43 over; 2 one-offs deleted, both "dry run 0 left" | met |
| `similar_rows` refusal (#2924) | 1 chain of three fixes | 0 found; #3020 named its single place | met |
| Delete the 300k line | 6 of 10 main sessions past 300k | 2 of 7 | gone, no metric |
| Amendment: plan-first + Media owner | 1 cross-domain half with no plan | suggest-for-clips merges cite their plan | held |

The 10-28 target (workers past 150k under 25%) is 33% now: missed three times as advice. Not decided here; guard or
delete it at the next retro.

## 6. Actions

Three (owner, 2026-11-14: "accept the proposed actions"); in DECISIONS 2026-11-14.

1. **Guard** (built, 657e2212): `tools/add_findings.py` refuses a new `low` row with neither `live: N` nor
   `user action: ...`; `live: 0` closes it `decided: no live instance, not built`. Metric: lows built then reverted,
   7 now, target 0.
2. **Brief change**: `tagpup-worker-small` has no sweep for a batch of lows, reports spawned rows instead of building
   them, and a DECISIONS row quotes only the owner's answer. Metric: spawned rows built, 2 now, target 0; DECISIONS
   rows corrected after code was built on them, 1 now, target 0.
3. **No action** on B. Check next retro: `data` rows from a delete-for-good path, 1 now, target 0.

## 7. The instruction diff

CLAUDE.md: the "special case" bullet loses its last sentence and gains the guard's name, net 0 (201 lines before and
after; over the 200 target before this retro). `tagpup-worker.md`: "Is the case real?" cut from 3 lines to 2.
`tagpup-worker-small.md`: one parenthesis added to an existing line. Net: 6 lines removed, 5 added.

## 8. What to carry to other projects

- A rule that the sweep, the review and the worker each re-derive is a guard at the ledger, not a line in three briefs.
- A notice-only ledger rule changes nothing; a refusal with a one-token escape (`live: N`) does.
- A decision row records the owner's answer verbatim; the session's extension of it is a new finding the owner can
  refuse.

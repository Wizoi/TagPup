# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | low: `tools/retro_metrics.py` counts a merge's size against the 1,200-line budget as added plus removed lines, so a deletion reads as over budget: the data file of 2026-10-16 says 74 of 134 trunk merges are over; counted with added lines only it is 33 of 49 before the freeze and 4 of 85 after (49 of those 85 net-negative). Count added lines (or both, side by side). Retro 2026-10-16, section 2. | open |
| B-2 | 2026-10-16 | low: `tools/retro_metrics.py` reports sessions per day as distinct session ids per day (1 on each day), which hides that one main transcript spans 2026-10-02 to 2026-10-16 with 4 compact boundaries (meta fields `sessionId`, `type: system`, `subtype: compact_boundary`); "compactions" is listed as not counted. Report main sessions over the window and their compactions. Retro 2026-10-16, section 2. | open |
| B-3 | 2026-10-16 | low: `tools/retro_metrics.py` cannot tie a subagent run to a branch, so review rounds per branch and runs never merged are not counted; a subagent transcript's `cwd` (a meta field, not text) names its worktree and so its branch. Retro 2026-10-16, open question 1. | open |
| B-4 | 2026-10-16 | low: docs/domains/people.md, condition 1, says four contract rows "are still to be written into the Integration charter's table", but #1471 is closed fixed and `.claude/agents/tagpup-owner-integration.md` holds the four (`core/vocabulary`, `people_vocabulary`, `face_refs`, `taxonomy.seed`): the sentence is stale. | open |

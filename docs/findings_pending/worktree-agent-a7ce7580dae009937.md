# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | low: decision (tools/retro_metrics.py): Claude Code writes one transcript record per content block of an assistant message, each repeating the message's usage (84,796 assistant records hold 40,309 message ids across the 438 transcript files, counted read-only this task, usage numbers only); the reader folds them by `message.id` and keeps the record with the largest `output_tokens`, so a turn is a message and its tokens are counted once. Summed per record, the totals would be about twice as large. | decided: this branch |
| B-2 | 2026-10-16 | low: decision (tools/retro_metrics.py): PRICES holds the Claude 4 families' published per-million list prices as placeholders (Opus $5/$25 low to $15/$75 high, Sonnet $3/$15, Haiku $1/$5; cache write 1.25x / 2x for 1 hour, read 0.1x), labelled owner to verify and not billing; `claude-fable-5-1` (1,584 turns in 2026-10-02..16) matches no family and is reported unpriced, its tokens counted, its cost not guessed. Days are local dates; the window is the trunk's last first-parent commits before its start and end, so a branch commit dated before the window but merged in it would be listed under its own date (none in this window). | decided: this branch |
| B-3 | 2026-10-16 | low: one commit on the trunk with a `Merge worktree-agent-` subject has one parent (a squashed or re-made merge, committed 2026-10-16); retro_metrics counts it apart and leaves it out of the branch-size distribution, which needs a second parent. | decided: this branch |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | low: tools/retro_metrics.py's TOKENS BY BRANCH cannot tell a worker branch never merged from one merged under another name: 26 branches (1.06 B tokens, 16.6%) have no ref and no merge naming them, while 36 trunk merges in the window name a non-worktree branch (`Merge fix/...`, `Merge arch/...`). The state is printed as 'no merge found', an upper bound of never merged. A further 27 runs (1.20 B tokens) committed nothing on their own worktree branch (its tip predates the run), so their work is on a branch their brief named. The count becomes exact if the main session merges a worker's branch under its worktree-agent name. | left as is: by design until the main session decides how it names merges (the retro's question, not the tool's) |
| B-2 | 2026-10-16 | low: tools/retro_metrics.py prices no fable model, so review cost is understated: 1,586 of the 5,035 review calls (reviewer, quick reviewer, owners) in 2026-10-02..16 are unpriced and left out of the review cost range. Their tokens are counted. PRICES needs a fable row with the list price, which the owner or the main session must supply (the tool never guesses a price). | open |

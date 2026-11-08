# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-07 | low: the clips measurement of Suggest's neighbour cut (`scripts/measure_pipeline_holdouts.py clips`) scores the neighbour path alone: no words, no model, because the word path needs CLIP's text model and the cut (min_sim) does not gate the words. A clip's tags offered here are the neighbours' tags only; the result says nothing of what the words add. By design: the cut is read where it applies. Closed `decided:` on merge. | open |
| B-2 | 2026-11-07 | low: `TagSuggester._get_candidate_embeddings_for_year` read the embedder with no words to embed; it now returns the (empty) candidate embeddings early when `candidate_tags` is empty (a change made by the clips-measurement branch, so `neighbours_model` can run with `embedder=None`). Only the measurement builds a suggester with no candidates; Suggest always has words. Closed `decided:` on merge. | open |

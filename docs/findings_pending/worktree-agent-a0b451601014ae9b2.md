# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-24 | low: `retire-photo-vectors` drops Suggest's words only under the model of a key it retires; words cached under a model the library holds no vectors under (an orphan) are not handled. No live instance: the three libraries' tag_embeddings are all ViT-H-14's (1,080, 274 and 112 rows), and each holds ViT-H-14 vectors (counted 2026-10-24, read-only). | decided: no live instance (CLAUDE.md, a special case needs a real instance); the branch is not written. |
| B-2 | 2026-10-24 | low: the plan's coverage guard (`retire_photo_vectors.NOT_COVERED`) does not catch a run BEFORE the switch: there the library's own key is the old model's, which covers the photos, and the key that would go is the new model's pass (embed-photos). The dry run names the key that goes and the own key; the snapshot taken first is the way back, and embed-photos makes them again. No live instance: the three libraries are switched (own key SigLIP 2, the only other key ViT-H-14, counted 2026-10-24). A guard that tells "before" from "after" needs the journal's switch history. | open: question for the main session (a guard by the journal's switch, or decided as by design). |
| B-3 | 2026-10-24 | low: with two keys to retire, a run stopped after the first key finished and before the second began takes a second forced snapshot when run again (`continues` is set only for a key with a change). No live instance: each live library holds one key besides its own (counted 2026-10-24). | decided: no live instance; one key a library. |

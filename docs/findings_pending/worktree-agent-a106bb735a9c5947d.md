# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-24 | low: `embed-photos` with the library's own model at another image size (ViT-H-14:laion2b_s32b_b79k, which the command makes at native size) would make a third key's vectors no switch uses: no live instance (the three libraries are on ViT-H-14 at 512 and the only switch planned is to SO400M). | decided: refused by name and weights at any size, with a sentence, nothing written (photo_reembed._key_of; tests/test_making_a_new_photo_models_vectors.py) |
| B-2 | 2026-10-24 | low: the pass moved from the AI pipeline harness into `tagpup.services.photo_reembed.embed_pass` counts a file gone (FileNotFoundError) apart, outside the unread limit, where the harness counted it as unread: a library's rows whose file is gone (2 of the three libraries' 62,327, counted read-only 2026-10-24) are a sync's to settle, not a share that stopped answering. | decided: by design; the harness checks its copy's counts after the pass as before (CopyChanged) |

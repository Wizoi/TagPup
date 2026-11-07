# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-07 | low: #2603's symptom has no route on the trunk: the Suggest route keeps `meta["media"] == "photo"` only (tagpup_routes.py, since 9a7449b9, 2026-11-05, before the finding), so no Suggest run is handed a clip and none raised "not given ffmpeg". The structural half is fixed in 96e8631a: `Runtime.embeddings(library, photo_index, clip=None, remember=True)` is the one maker of a PhotoEmbeddings that makes vectors (begin, embed-photos, the holdouts harness), `Runtime.ffmpeg` is gone, and tests/test_rules_have_one_owner.py refuses any other `PhotoEmbeddings(` (failed on the old code: suggester.py, photo_reembed.py, the harness). No Suggest-on-a-clip scenario test was written: the case has no instance | fixed 96e8631a |
| B-2 | 2026-11-07 | low: an uncapped write (the caption sweep, and also the tag tree's rewrites, tagging.replace_tag) now waits up to shares.AWAY_SECONDS (30 s) under the lock of changes of photo files before its one more look at photos of a share found away (file_changes._looked_at_again); a tag rename over a share that is truly away answers about 30 s later than before. Decided rather than copied: the brief named uncapped writes | open |

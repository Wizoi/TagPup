# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | wrong: `repair-vectors`' sweep reads each header through `services.photos.box_shape`, the page helper that marks a whole share away for 30 s after one slow read, and stats through a bare `os.stat`: on a library on a network share the photos of that window were counted "unreached" silently (renton_parkrun, a UNC root: the first sweep unreached 1,056 of 1,309, a minute later 0; all 1,309 open). Fix: ask the share once through `shares.stamp_of`, as `thumbnails.warm` does, count and name the share's skipped photos. | fixed: this branch |
| B-2 | 2026-10-16 | low: the rule "take the stamp before opening, embed, keep the vector as of that stamp" was written twice (`PhotoEmbeddings.of`/`keep` and `store.embeddings.replace_current`). Fix: `PhotoEmbeddings` is the only writer; `replace_current` deleted. | fixed: this branch |
| B-3 | 2026-10-16 | low: `repair-vectors` passed neither progress nor the card's waiting report to its service, so a dry run over 59,519 headers was silent and `--apply` waiting for the card did not say who held it. | fixed: this branch |
| B-4 | 2026-10-16 | low: an exception from `clip.embed_image` other than OSError (CUDA out of memory, a decompression bomb) ended the run with a traceback and no summary. Fix: counted as `failed` by reason name, the run goes on. | fixed: this branch |
| B-5 | 2026-10-16 | low: a running server's Suggest keeps the old vectors until a photo row changes or the app restarts (`PhotoIndex.reload_if_changed` follows the photos generation; nothing bumps it for `embeddings.vector`). By design, no generation trigger (no schema): the command says to restart the app. | decided: the command's last line and docs/SPEC_TAGPUP_CLI.md say so |
| B-6 | 2026-10-16 | low: `repair-vectors` writes `embeddings` without a journaled change (INVARIANTS row 40). By design: `embeddings` is derived (ARCHITECTURE.md), the indexer and Suggest write it unjournaled, and a vector made again is the same vector of the same file made right. | decided: module docstring; DECISIONS row proposed in the report |
| B-7 | 2026-10-16 | low: a TIFF is never "turned" for the sweep (`box_shape`: its boxes turn with the decode) but `images.opened(upright=True)` transposes it. 0 of 17 TIFFs in photo_index declare an Orientation other than 1, none in the other libraries (counted 2026-10-16), so no branch. | decided: module docstring |
| B-8 | 2026-10-16 | low: docs/AI_PIPELINE.md 1f and its risk row promised a doctor check that cannot exist (the row holds neither the Orientation, 0 of 59,519 rows, nor the vector's age). Reworded. | fixed: this branch |

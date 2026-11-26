# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-26 | low: the index's other Re-examine sentence, said when matching the new faces failed (`tagpup_cli.py` `_match_new_faces`: "The photos are indexed, but matching their faces failed: ... Open Re-examine on their folder, or run ..."), gets no link on either page: the pages make an action only of `new_faces_said`'s words (`web/common/reexamine-offer.js` REEXAMINE_OFFER, held to the sentence by `tests/test_indexing_names_a_real_control.py`), and the failure's are capitalised and written apart. Decided in cut D of docs/plans/working-on-a-new-folder.md; to link it, the CLI's sentence would say the same words and the guard would hold both. user action: the face matching after an index raises. | open |
| B-2 | 2026-11-26 | low: decision in cut D (docs/plans/working-on-a-new-folder.md): TagTuner's index bar used to hide at every index's end, so the new-faces sentence was seen only for the moment between the faces phase and the bake; now it stays on the bar, once the queue is empty, when it offers Re-examine (`web/tuner/indexing.js` `state.indexEnding`, until the next index or a reload), its Re-examine a button there. While the index runs the sentence's words are words, no action: its bake still writes the folder's files. user action: add a folder from TagTuner's Manage folders whose new faces have a likely name. | open |

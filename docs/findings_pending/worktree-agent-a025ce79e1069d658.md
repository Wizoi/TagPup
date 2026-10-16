# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: Views: `TagSuggester` still has arms for no CLIP model and no face models (`if not self.embedder` in `_precompute_candidates` and, since the stand-in arms went, in `_get_candidate_embeddings_for_year`; `self.faces is not None` in the face path). No production caller makes either: `model_for_run` is the only constructor and always passes both (live count 0); the tests that build a suggester with faces only (test_faces_detected_record, test_suggest_reads_people_once, test_suggest_respects_face_decisions, test_two_cousins_called_alike) rely on them. Lift those to `model_for_run` with stand-ins, then drop the arms. Decision made in the Views follow-up. | open |
| B-2 | 2026-10-15 | low: Views: decision in the Views follow-up: besides `_held`, the runtime's `_index_runs` counter is gone too: whether a photo index is held is read from the leases (`Runtime._holds`), a third record of the runs under way kept in step by convention. `_retired` stays (an index replaced while held). | open |
| B-3 | 2026-10-15 | low: Views: the Suggest page's error test in `tests/frontend/progress-polling.test.mjs` (Integration's file) asserted the status line says "Error"; it now holds the server's sentence (#1403), so the assertion reads the sentence. `suggest-cancel.test.mjs` holds the refusal scenario; the apps test could go at its owner's next regrouping. | open |

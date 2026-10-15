# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: #1343 is wrong: `PhotoIndex.clear_clip_embeddings` (tagpup/services/search.py:325) has a caller, tagpup_cli.py:239, which runs when the stored vectors' length differs from the model's (`stored_mismatch`) and clears them to be re-embedded; it was not deleted (People-B3 chore). Close #1343 as not a finding. | open |
| B-2 | 2026-10-15 | low: tagpup/web/taxonomy_routes.py calls `tagpup_routes.forget_scans` in delete-confirm (:101) and rename (:122) as well as in `_tree_edit` (:44), which both go through: with the :101 call removed test_shared_taxonomy_routes still passes (mutation run). One of the two is redundant; People owns taxonomy_routes. | open |
| B-3 | 2026-10-15 | low: People-B3 left in place, with the reason: (a) the fake-server copies in tests/frontend/identify-faces*.test.mjs, person-faces, people-by-id, people-by-face were not lifted into test_pages_against_the_real_server.py: the one real Identify flow (tuner-undo-puts-faces-back) does not assert their wire bodies, the alert texts or the Undoing... button state, so a lift deletes assertions, and a mutation run of a page needs node_modules, which a scratch copy outside the repository lacks; (b) test_service_tags Creating.below_a_parent_typed_whole_or_not, Marking.a_branch_and_nothing_beside_it and Renaming.a_person_is_renamed_on_their_faces stay: they alone assert the reply's `changed` / `faces_renamed` count, which test_taxonomy_lifecycle does not (a mutation that always reports changed=1 passes it); (c) Merging and RenamingAPerson in test_service_tags have no route-level twin. | open |

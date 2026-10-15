# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: People.settle/_sync_table/follow_listed kept a photo_people row of a gone node as a name with no id; follow_tree and release delete it. Fixed: one rule, the row is deleted (live instances 0: a trigger forbids deleting a named person's node). Test fails on the old code. | fixed: this branch |
| B-2 | 2026-10-15 | low: faces._target returned a name with no id (unreachable) and let a path-shaped name make non-person nodes; now raises ValueError for both. Test fails on the old code. | fixed: this branch |
| B-3 | 2026-10-15 | low: stale docstrings named link_added/settle_faces as the owner of linking (person_ids, taxonomy); now follow_tree. | fixed: this branch |
| B-4 | 2026-10-15 | low: tests/face_rows.person_of made a top-level node by raw INSERT and returned None for an ambiguous or group name; now uses taxonomy.ensure_node and raises. Fixtures that seeded the unreal shape changed (test_service_tags names faces by id after creating them unnamed; test_store_people drops the empty-name input; test_answers_carry_their_person states tag_id=None on purpose: a face whose node left). Scenario/slow tier files were not run: they may seed an ambiguous name through add_face/add_people. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-23 | low: once the index's branch of `indexing.while_clustering` goes (DECISIONS 2026-10-23 (5)), the flag `indexing.clustering` is set only by Name faces from tags (`web/name_faces_routes._hold`, held inside the job's APPLYING phase, `jobs/naming_faces.py` ~274), the same window `naming_faces.writing(library)` already answers: `tuner_routes.clustering_refusal`'s second arm, `name_faces_routes._busy`'s flag check and the `only_naming` parameter become a second copy of one rule (a concept that collapses). Delete the flag, `while_clustering`, `_hold`, `only_naming`; the refusal reads `naming_faces.writing` alone; the five tests that set the flag by hand (test_organize_faces, test_shared_taxonomy_routes, test_tuner_server_api TestClusteringLock, test_name_faces_routes, tests/tuner_client.forget) drive a writing job or patch `writing` instead. Found by the automatch-after-index sweep. | fixed: 1a1d3f40 |
| B-2 | 2026-10-23 | low: docs/SPEC_TAGPUP_GUI.md's Name faces from tags dialog described "a second box, off, [that] also groups the rest of the faces by who they look like ... can take about an hour ... cannot be undone from History": no page has the box (web/common/name-faces.js, web/tagpup/name-faces.js, web/tuner/name-faces.js hold no such checkbox) and the job (tagpup/jobs/naming_faces.py) never groups faces; a spec that disagrees with the code. The line is deleted. Found by the automatch-after-index part 2 sweep of "being clustered". | fixed: 487093bf |

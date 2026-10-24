# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-23 | low: once the index's branch of `indexing.while_clustering` goes (DECISIONS 2026-10-23 (5)), the flag `indexing.clustering` is set only by Name faces from tags (`web/name_faces_routes._hold`, held inside the job's APPLYING phase, `jobs/naming_faces.py` ~274), the same window `naming_faces.writing(library)` already answers: `tuner_routes.clustering_refusal`'s second arm, `name_faces_routes._busy`'s flag check and the `only_naming` parameter become a second copy of one rule (a concept that collapses). Delete the flag, `while_clustering`, `_hold`, `only_naming`; the refusal reads `naming_faces.writing` alone; the five tests that set the flag by hand (test_organize_faces, test_shared_taxonomy_routes, test_tuner_server_api TestClusteringLock, test_name_faces_routes, tests/tuner_client.forget) drive a writing job or patch `writing` instead. Found by the automatch-after-index sweep. | open |

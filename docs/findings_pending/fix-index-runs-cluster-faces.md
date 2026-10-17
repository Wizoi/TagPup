# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | wrong: the server ran `cluster-faces` (when asked) as a second process after the index process had exited, so the decision "after an index" lived in the server alone: the CLI's `index`, which the owner uses for long indexes, never clustered, and a server restarted between the two processes (a save in a repository-run app, an update) lost the clustering though the index finished; the flag that refuses assignments while it runs (`tuner_routes.clustering`) was held only by TagTuner's indexer, not by TagPup's or sync's queue (#1544's branch). | open |
| B-2 | 2026-10-16 | low: `identities.resolve` tries `cuml.cluster.DBSCAN` (the graphics card) before scikit-learn's: cuML is not installed in the venv (counted: no `cuml` module), so the arm has no live instance, and if it were installed clustering would put every face vector on the card without a turn (tagpup.ml.gpu, one process at a time), beside another library's indexer. | open |
| B-3 | 2026-10-16 | low: `tests/test_tuner_server_api.py` `test_indexing_does_not_cluster_unless_asked` held the opposite of DECISIONS 2026-10-16 ("Clustering rewrites every name in the database, so it is never implicit"), and the docstrings of both index routes and SPEC_TAGTUNER called clustering "opt-in"; the decision of 2026-10-15 said the indexer ran it. | open |

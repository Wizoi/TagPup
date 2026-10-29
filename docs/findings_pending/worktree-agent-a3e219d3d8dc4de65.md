# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: the bake's folder line ("Bake folder N of M ...: <path>") carries the folder's whole path on the indexer's stdout, in its run log and in Now's live line, where the bar's summary used to cut a line at 90 characters; a path names the owner's folders (B1, `tagpup.services.bake_after_index.folder_said`). | decided: owner, DECISIONS 2026-10-28 ACTIVITY REWORK (3): live progress shows folder paths, this PC only (the Activity routes answer 127.0.0.1 alone) |
| B-2 | 2026-10-28 | low: a CLI `index` run by hand prints each bake folder line twice: once by the INFO stdout handler (`tagpup_cli.py`'s basicConfig, with time and level) and once bare by the console (`_bake_after_index`'s `each=say`), which the server's index queue reads; under the server the logged copy is dropped as noise (`_INDEXER_NOISE`). Not fixed in B1: the cheap fix (`each=say` only when `TAGPUP_LOG_TO` is set) has no test short of a CLI run with stand-in models (tests/test_a_share_asleep.py's setUp). | open: fix with B5 (logs), which owns what the CLI prints and logs |
| B-3 | 2026-10-28 | low: the line Now reads aloud names an index's phase by the words of its message before the first number or colon (`phaseOf` in web/activity/now.js): "Bake folder", "Metadata", "Matching the new faces with the named ones"; the server sends no phase of its own for an index (it does for a sync: `sync.WALKING` ...). | open: B2 records the index's phase in its `job_runs` row's progress; the page reads that instead |

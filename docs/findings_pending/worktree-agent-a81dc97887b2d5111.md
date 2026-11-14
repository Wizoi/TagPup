# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-14 | low: Media/indexing: an index the server's queue runs that the CLI stops for ffmpeg (the start check's 'Not run: <install sentence>', or since the fix of #3031 'Stopped: <install sentence>' part-way) exits 1, which `tagpup.services.indexing.index_folder` records as 'Indexing failed with exit code 1.', a run failed of kind ERROR ('Something went wrong that TagPup did not expect. Try once more'): the run's note on Activity does not say to install ffmpeg, though the live line showed the sentence as it went by. The roots' stop has its own exit code mapped to its sentence (EXIT_ROOTS_CHANGED); ffmpeg's has none. Instance: ffmpeg uninstalled while the server runs (the server's own start check covers its start); 0 counted, no record of a run failed so | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 | wrong: run by hand, the CLI `index` printed each bake folder line twice, the log's copy and the console's (#2169): the console copy was there only for the server's queue to scrape. Now the server asks for typed facts (`tagpup.core.progress_line`, `TAGPUP_PROGRESS_LINES`) and by hand each folder line is the log's alone. The test (`tests/test_index_progress_end_to_end.py`, the by-hand run) failed on the trunk with `1 != 2` (the previous worker's run). | fixed: 9cf81f14 |
| B-2 | 2026-10-30 | low: the server read the metadata phase in batches of 500 (a folder of 400 photos said `done/of` 1/1), scraped from tqdm's bar; the `phase` fact now counts photos (`done`, `of`, `percent`), said as each batch is read. A decision, not copied: the run's row and Now count photos read. | decided: the metadata phase counts photos (9cf81f14) |
| B-3 | 2026-10-30 | low: `tagpup_cli._NOT_SAYING` (a fact that could not be said silences the rest) was per process: right for a CLI process, which runs one index, but an in-process index after one whose pipe failed (a test's CliRunner) said nothing. It is emptied as each `index` begins. The test (`tests/test_an_index_reads_a_bake.py`, the server gone mid-index) failed without the reset with `'new_faces' not found in []`. | fixed: 896d4e6d |

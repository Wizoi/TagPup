# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-27 | low: the oracle's whole read is never kept for replay: `field_values.WHOLE_READ` (`-j -G0:1 -a -all:all`) answers `File:System:Directory` and the file's dates, and `tests/exiftool_replay.py` keeps no answer that names a folder or a file's dates (`STAT_TAGS`, `Keeper.execute`'s "its answer names a folder"), so every `Oracle.whole` in a replayed run starts and runs the real ExifTool; the plan (docs/plans/own-metadata-library.md section 1, "Record/replay ... reads only (the oracle's)", guard named `tests/test_exiftool_answers_kept_for_tests.py`, which holds no oracle case) and section 4 ("every gate ... under replay") assume it replayed. Probed on a synthetic JPEG in a scratch cache: record and replay both "not kept: its answer names a folder", ExifTool started on replay; the same read with `--System:all` kept, then a hit with no start. user action: a run of the conformance suite (T1) or any replayed test calling `field_values.oracle` (today only `test_the_two_writers_compared_on_copies`, which STAYS_REAL). docs/plans/own-metadata-t1.md T1a adds `--System:all` with a test that fails on the old code | open |

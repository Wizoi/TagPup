# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-24 | low: tests/corpus.py `_make` builds an item under `<key>-part-<pid><ext>`, and ExifTool's write in that build (corpus.jpeg, noise_jpeg) names the part file; the test keeper (tests/exiftool_replay.py) keys a file by its name, so every corpus make was a keeper miss in every run: the 7 warm misses of test_file_journal, 3 of test_time_shift, 3 of test_a_photo_patched_in_place were all this (the input bytes were identical; the name differed). In a scratch copy with the part named `<key>-part<ext>` (the stripe/item lock already lets one maker at a time; `_discard` before the build still clears a killed maker's part) the warm runs hit 244 of 244, 56 of 56, 70 of 72 (the 2 left are a file the test makes unreadable, run real by design). With the corpus kept across runs (2f995279) the miss happens only when an item is made (once per machine per generation), so the fix is one line in the corpus owner's file. user action: any test run that makes a corpus item | open |

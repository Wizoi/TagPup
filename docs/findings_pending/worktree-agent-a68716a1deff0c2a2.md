# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | wrong: Files: `xmp_packet.LIMIT` was a second 64 KB bound on a clip's packet, so deleting `OLD_MOST` alone would still have refused the live clip (one in photo_index, 206,986-byte Premiere-style payload) the owner could not save. | fixed: 95dea7de (the packet reader has no size bound; the journal's PATCH_CAP is the one; wrote 0 rows, no live file written; the test fails on the old code) |
| B-2 | 2026-11-12 | low: Store: `file_journal.PATCH_CAP` counted only the old bytes, not the record's base64 new bytes, so a record could pass the cap and exceed it. | fixed: 95dea7de (the cap counts both; wrote 0 rows) |
| B-3 | 2026-11-12 | low: Tests: the 206,986-byte clip fixture was padding-shaped, not shaped like the live Premiere packet. | fixed: 95dea7de (the fixture is Premiere-shaped; wrote 0 rows) |
| B-4 | 2026-11-12 | low: Files: a clip's box over 1 MiB is read whole before the journal refuses it; no pre-read guard. Live instances: 0 (the largest live box is 206,986 B); a guard would restate the journal's bound in files. | decided: by design, no branch without an instance; the journal's refusal (PATCH_TOO_LARGE) is the one bound |

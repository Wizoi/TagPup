# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-14 | low: Views: `tests/test_thumbnails.py` `test_sweeping_takes_what_no_photo_owns_and_keeps_the_rest` (~319) asserts `"4242"` not in the joined entry names, but an entry's name holds its mtime in milliseconds (`2_b898ed2a_1794681764242_8228.jpg`), so it fails whenever a kept entry's stamp contains 4242 (seen once in the fast tier 2026-11-14, passed alone). Assert no name starts with `4242_`. | open |

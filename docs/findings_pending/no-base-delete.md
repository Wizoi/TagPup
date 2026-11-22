# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-21 | low: tests/roots_library.py Side seeds each real photo's row with READ (keywords Activity/Sailing) through photo_rows.add_read, stamped as the file, while the JPEG make_jpeg wrote holds no keyword: a page record made from such a row (photos.scan_folder takes a row that describes its file) names a base the file does not hold, and a save from it is refused as changed on disk. test_roots_old_place's save now names the base read at the time of the save (after the move, read from the file) rather than one read before the move. live: 0 (a test fixture; no library holds it) | decided: no live instance, not built |

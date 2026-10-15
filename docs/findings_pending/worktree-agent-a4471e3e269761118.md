# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: tests/owners.py placed the 111 `.test.mjs` files by name keywords (a page test imports through the harness, not a web/ module), so their owners are a first guess; 4 are `unmapped` (legacy, owner apps). Place them by the page module each loads when the regrouping touches them. | open |
| B-2 | 2026-10-15 | low: the fold of single-owner guards (sql, scripts, models, paths, exiftool, bucket names; the OneOwner and TheDecisions classes of test_face_values_have_one_owner; the rule tests of test_rules_have_one_owner) lowers the Python test count by 37 (87 to 50 across the files involved). tools/run_tests.py's count ratchet (#290) fails test_rules_have_one_owner and test_face_values_have_one_owner, which now run fewer tests than last time, until `--accept-fewer` records the new counts; run it once on the merged trunk. | open |
| B-3 | 2026-10-15 | low: CLAUDE.md still names tests/test_scripts_are_entry_points.py (Running things), tests/test_paths_single_owner.py and tests/test_exiftool_single_owner.py (rules); they now live in tests/test_layers.py and tests/test_rules_have_one_owner.py. A worker may not edit CLAUDE.md; the main session changes the three names. | open |
| B-4 | 2026-10-15 | low: in the folded page pins (TagTuner's buckets, reasons, unknown year, camera labels) a line that is only a `//` or `*` comment is no longer counted as a copy; the old tests counted it. A rule copy in a comment is not a copy, but the check is a little looser than before. | open |

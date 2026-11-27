# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-27 | wrong: the index's bake (services/bake_after_index.py bake_folders) opened field_values.opened lazily, so with an ExifTool that will not start the first command ran inside baking.plan_photo, whose broad except turned the start error into Cannot(UNREADABLE) per photo: the run returned "Bake: 0 written; N unreadable" as a success after one failed spawn per photo, where the base raised after one attempt. wrote 0 (no file written; the photos stay owed and the next index bakes them) | fixed: b332acef |
| B-2 | 2026-11-27 | low: a Save (tagging.py read_one raises) and a just-look write without `held` (file_only.py) still answer 500 when ExifTool will not start, the same as before L0b, while a journaled write answers each photo's error; the L0b merge note must say "each photo's error" holds for journaled writes only. live: 0 (ExifTool is installed on all four libraries' machine) | decided: unchanged from the base, live 0 |
| B-3 | 2026-11-27 | low: convert.py catches Unreadable only where a refused read could also arise; tagging.py save_photo flattens a refused write to empty errors. live: 0 | decided: no instance; L0c's single `fields` parameter and X's import rule close the seam |
| B-4 | 2026-11-27 | low: the session/StaysDead guard rules are line regexes an alias could dodge. live: 0 | decided: X's import rule closes it |
| B-5 | 2026-11-27 | low: module-level field_values.read/read_one/write/written_once_more still take a raw session while `et` names a Fields, and tests/test_minor_errors_in_a_journaled_write.py builds a session by hand. live: 0 | decided: L0c renames and makes the four private and carries the test |

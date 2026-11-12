# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: People: `tests/test_rules_have_one_owner.py` RULES kept a guard of the deleted background rules (`0.10 * max_area` or `area < 2000` spelled only in clustering.py) after the one 2% rule replaced them: a guard of a rule with no owner left, beside the guard of the one rule (`tests/test_face_values_have_one_owner.py`, BACKGROUND_RULE). The line deleted with the old rules' tests. | fixed: 9a977564 |
| B-2 | 2026-11-12 | low: People: cluster-faces' last pass asks no background rule, so a face in the background of its stored picture, left unnamed by the rounds, is named by the last pass when it is like a named person at the offer cut and the photo's tag names them: building P12, a 45 x 45 stranger on a sized photo 0.5 like a face just named Bram by elimination on another photo was named Bram. The scenario's two strangers are made unlike each other (0.0). | decided: owner 2026-11-12, DECISIONS 2026-11-12 'A FACE ON A PICTURE OF NO STORED SIZE IS NOT IN THE BACKGROUND' item 4 (the last pass may name such a face again) |

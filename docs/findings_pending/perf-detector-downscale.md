# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | low: the Face detection group's consequence, shown when the owner changes a face setting, said "Only photos indexed after the change are detected with it"; Suggest also detects again, under the new settings, a photo with no face whose detection was recorded under the old ones (`faces_detected` is read by detector name; kr-track 1, renton_parkrun 9, photo_index 0 such photos, counted read-only 2026-10-16). Found building phase 1a, whose cap is one more face setting. | fixed: *faces: a library may cap the picture the face detector looks at (off by default)* (the text in `validation.SETTING_GROUPS`, published rules regenerated) |

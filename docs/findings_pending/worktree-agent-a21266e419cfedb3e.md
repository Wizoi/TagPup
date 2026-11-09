# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | wrong: Data: `inspect.files_that_fail_to_open` (the doctor's "photos whose file fails to open", sandbox.census `deep`) looks at every row of `inspection.photo_files`, videos included, and Pillow cannot identify a video: each is counted "the file is not a picture that can be read". Live: photo_index holds 2,179 video rows, kr-track 5 (counted read-only this task); in a 300-photo sample of photo_index on its share, the 9 rows counted failing were the sample's 9 videos and nothing else; a stand-in MP4 raises UnidentifiedImageError, which `images.is_damage` takes as damage. Found while bounding the look (#2753); not fixed there, one concern a branch. | open |

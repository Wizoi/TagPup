# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | wrong: `scripts/convert_old_clips.py --also-mpeg4-part-2`: an MPEG-4 Part 2 .mp4 (converted to an .mp4 of its own name) whose run was killed after its original went to the Recycle Bin was never finished: the survey probed the original, found it gone, and passed the clip over, its verified temp and marker left beside nothing (the next run said "0 clip(s) to look at"). The survey now chooses such a clip by its verified temp's marker (`old_clip_convert._marked`). Test failed on the old code first: `MPEG4Part2` in tests/test_a_librarys_old_clips_converted.py. | fixed: 3d45ea7f |
| B-2 | 2026-11-12 | low: `scripts/convert_old_clips.py`: a clip passed over (its .mp4's name taken by a file or a row, a fresh temp under way, a share away) was not counted: the run ended "0 not converted" and exit 0 with a clip left. It is now counted in the last line, exit 1 (`old_clip_convert.LEFT`). Test failed on the old code first: `test_a_stale_temp_is_removed_and_the_clip_converted_a_fresh_one_refuses`. | fixed: 3d45ea7f |
| B-3 | 2026-11-12 | low: the caption carry of the old-clip conversion had no instance: the 45 photo_index .wmv rows said to hold a caption hold an ASF Description of blanks alone (counted read-only 2026-11-12: 0 non-blank of 629), so `old_clip_convert` no longer reads or passes one (3d45ea7f). `tagpup/files/clip_convert.py`'s `description` parameter (`arguments`, `convert`, from 017551a9) now has no caller and its docstring says "45 photo_index .wmv rows hold one": remove the parameter and that sentence (not this worker's file). Also measured: ffmpeg's ASF muxer writes a clip's ASF Description from `-metadata comment=`, not `description=`. | open |

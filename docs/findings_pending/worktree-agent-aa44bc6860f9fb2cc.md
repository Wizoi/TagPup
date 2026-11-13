# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: decided, not copied: with the packet carry's no-room arm deleted (#2951), `field_values.packet_given` raises `Refused(images.CLIP_NOT_PATCHED)` when `in_place.patch_clip` places nothing, as `_clip_written` does, and convert's existing Refused arm says it; no ffmpeg-made copy reaches it (an MP4 of clip_convert is ftyp, moov, free, one sized mdat, and took an appended box, checked on a .wmv and an .mp4 2026-11-13). | open |
| B-2 | 2026-11-13 | low: `tests/owners.py` C5's scenario text still describes the deleted clip-dating one-off (dry run, --apply refused, wrote N), and C7's does not name a clip written by another program while Convert reads it (refused CHANGED, nothing planned; the test added for #2945). Outside this branch's files. | open |
| B-3 | 2026-11-13 | low: the clips a browser cannot play could not be counted this session: every clip of the four library files is on a share, and ffprobe (`video_frames.streams`) found the share not answering for all 2,920; the rows hold no codec (raw_metadata has no QuickTime:CompressorID). photo_index's split of change 8261 (620 appended, 49 in place) cannot be recounted either: the journal erases a file's patch record when it is done (669 files done, counted). | open |

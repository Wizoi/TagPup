# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: Convert reads the clip in `convert._ready` (its carry by ExifTool, and since #2938 its XMP packet, `in_place.packet_held`) before `before = file_changes.fingerprint(source)` is taken under the lock in `convert_clip`; a write by another program to the original in that window (seconds: ExifTool's read and ffprobe's) gives a `before` of the changed file, so ffmpeg converts the changed file, its copy carries the stale read, and the original is still deleted (its fingerprint matches `before`). Confirmed by reading the code (the carry has had this window since Convert was built); not reproduced, no live instance counted. A fix would compare `before` with `_ready`'s stamp (mtime, size) under the lock and refuse CHANGED. | open |

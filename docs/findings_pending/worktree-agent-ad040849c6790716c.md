# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: in_place.held_open adds nothing to the refusal when Restart Manager names no holder of a clip on this PC's own disk (only a clip on a share gets NOT_ON_THIS_PC): with no holder named locally the program has closed it or Restart Manager failed, and no sentence about who would be true. | decided: no sentence that cannot be true; the refusal itself is unchanged |
| B-2 | 2026-11-13 | low: services/convert.py _ready's no-sharing open, failing with anything but a sharing or lock violation (in_place.held_open answers None), answers UNREADABLE % strerror, the sentence its packet read on the next line already gives for an OSError, not a new constant; that open is not bounded by shares.bounded as in_place's is, so a share that stops answering between stamp_of and it waits for Windows' SMB timeout and then says UNREADABLE with "network name is no longer available", not AWAY. | decided: one existing sentence; bounding that open is a separate change with no live report |
| B-3 | 2026-11-13 | low: in_place.held_open logs its warning with the winerror and the holder named, never the clip's path (a folder or file name can name a person; as clip_convert logs ffmpeg's words unnamed); the page's sentence already belongs to the clip it was saved from. | decided: no path in the log |

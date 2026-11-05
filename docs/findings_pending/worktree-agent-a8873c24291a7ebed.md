# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | low: the video streaming design's page half says a video's `error` with code 4 is "this video's format cannot be played in this browser", but a browser reports a refused request for a media src (a 404 for a file gone, a 503 for a share away) as the same MEDIA_ERR_SRC_NOT_SUPPORTED: the sentence would blame the format for a share that is not answering. web/tagpup/media.js asks the server for one byte of the video (`Range: bytes=0-0`) before it says which: 404 the file is not there, any other refusal the share or the server is not answering, an answer with code 4 the format (HEVC named), else "could not be read"; an answer arriving after the video was left says nothing. A decision made, not copied from the design. | decided: the probe is one request on an error only; tests/frontend/video-viewer.test.mjs fails with the probe removed (broken copy) |
| B-2 | 2026-11-05 | low: with the video player focused, ArrowLeft/ArrowRight step to the next photo (navigation.js wireKeyboard; `ownsItsKeys` does not name the player) instead of seeking, and Space is the player's. Stepping is what the arrows do everywhere else in the panel; the player's own controls seek with the mouse. Not changed in this branch (navigation.js is not its file). | open |

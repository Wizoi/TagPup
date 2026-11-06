# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-06 | low: with ffmpeg a prerequisite checked before the server and the CLI's `index`, `thumbs warm` and `sync --apply` start, three no-ffmpeg arms had no instance left: `thumbnails.warm`'s count of clips left for want of ffmpeg (`no_ffmpeg`, `ffmpeg`) and the CLI's line "N video(s) get no poster", and `video_frames.frames`'s `if tools.problem` refusal (its one caller, `thumbs.poster`, is reached only through `thumbnails._draw`'s check). Their tests went with them. Kept: `_draw`/`_make`'s NoFfmpeg placeholder, the one case with a user action behind it (ffmpeg uninstalled while the server runs). | decided: deleted with the prerequisite check (CLAUDE.md, a special case needs a real instance) |
| B-2 | 2026-11-06 | low: a CLI index the server queued on a machine whose ffmpeg went away after the server started is refused by the CLI; Activity shows "Not run: ffmpeg not found; ..." as a progress line, then "Indexing failed with exit code 1." (tagpup/services/indexing.py relays a plain line, then fails on the code). Not given a code of its own: it needs ffmpeg uninstalled under a running server, which its own start-up check refused before. | decided: the two lines say what to do; no exit code of its own |
| B-3 | 2026-11-06 | low: the server's start-up check runs `ffmpeg -version` and `ffprobe -version` (about 0.5 s each, kept per process once passed), so each server start and each CLI `index`, `thumbs warm` and `sync --apply` costs about a second more; `--check` (an install's `starts`) pays it again in its own process. | decided: the owner's rule (checked early, 2026-11-06); a pass is kept for the process |

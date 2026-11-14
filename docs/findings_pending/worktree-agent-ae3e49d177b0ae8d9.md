# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: `embed-photos` (photo_reembed.embed_pass) meets search.FfmpegGone (ffmpeg gone mid-pass) in its generic per-photo `except Exception` and counts it as "unread: FfmpegGone" per clip until UNREAD_LIMIT stops the pass, instead of stopping at once with the install sentence as Suggest now does (DECISIONS 2026-11-13). The CLI's index lets it through (vector_of catches only NoFrames), so it fails loudly. Live instance: ffmpeg removed while `embed-photos` runs over clips; the CLI's start check covers the normal case | open |

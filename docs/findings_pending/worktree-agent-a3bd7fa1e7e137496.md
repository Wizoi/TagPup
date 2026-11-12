# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: `scripts/convert_old_clips.py` sent each original to the Recycle Bin (copy through this PC from the share, a Bin-refusal stop that said to empty it). Owner decision 2026-11-12: no Bin, the videos are backed up elsewhere. After the .mp4 is verified and its `.ok` marker written, the original is deleted for good (`old_clip_convert._remove_original`: `os.remove` in one `shares.bounded` call), then the temp renamed, the same order and kill points. Removed with it: `recycle_bin.delete_file` call, `bin_refused`, `BIN_DID_NOT_TAKE`, the Bin-refusal stop wording and 'Empty the Recycle Bin'. A refused delete (a held file, a share that dropped) still stops the run, original and verified copy kept, 'run this again'. The earlier Bin-refusal rows (#2829 and the Bin wording of #2819) are by design obsolete. Tests: `ADeleteRefused`, `OneClipEndToEnd`, `KilledPartWay` fail with the delete made a no-op. | fixed: the commit named by its subject "convert_old_clips: originals are deleted for good" |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 | low: a move off a photo asked while another waits for the photo's save now takes its place (the last asked wins, the replaced move told it stayed through its onStay), where before every move waited and ran in turn: arrow presses during a slow save skipped photos, and a folder or view opened during the wait opened after the step. The alternative (refuse every move after the first) left a folder click doing nothing while the note showed. Escape and the note's Stay here let the waiting move go; during the "Save changes?" question a second move still stays, as before. user action: pressing the arrow keys or clicking another photo while a save of the open photo is out (edits.js leavePhotoThen). | decided: one held move, the last asked; scenario V27 |

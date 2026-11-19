# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 | low: Mirror reuses photo.js `rotatePhoto` as the plan says, so pressing it shows "Rotating..." in the status line and, on a failure, the alert "Error rotating image: ..." (web/tagpup/photo.js rotatePhoto); the write itself is right. user action: pressing Mirror on a photo. Either the sentences become "Turning..." / "Error turning the photo" for all three buttons, or this is recorded as by design. | open |

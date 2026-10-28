# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: code comments still speak of the deleted face pass as present, or of the faces switch's share: `settings.photo_switch_on`'s docstring ("the faces switch's share (switch_on): a pass leaves a few photos it could not read"; switch_on counts no share since 2026-10-28), `services/snapshots.py` (the long run's snapshot "the CLI's face pass with --apply"), `services/identify.py` (`NO_VECTOR`'s comment: "a photo the pass has not reached"), `services/rotation.py` ("the next index or face pass"), `services/face_gate.py`, `tagpup_cli.py` (index's comment "with a face pass or without"), `ml/scrfd_adaface.py`, `files/images.py`, `services/faces.py`, `core/clustering.py`. Wording only, no behaviour; outside a docs-only branch. | open |

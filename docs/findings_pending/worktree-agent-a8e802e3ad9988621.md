# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | low: `in_place._OPEN_ELSEWHERE_ERRORS` (32, 33) spells beside `lock_owners.HELD` (5, 32, 33) which Windows errors mean "another program holds the file"; HELD cannot be reused as is, since 5 (access denied) is also a read-only file, which patch_clip must not call "open in another program". The single place would be lock_owners: `SHARING = (32, 33)`, `HELD = (5,) + SHARING`, in_place importing SHARING. lock_owners.py was not this worker's file (M7 B2.3). | open |
| B-2 | 2026-11-05 | low: `containers.read` answers `Unreadable` for a read that fails, never raising, so `in_place.patch_clip` answers None ("not a patch's") for a clip whose share failed part-way through the walk (an OSError at once, not a hang, which is Away); once wired (B3) the owner would be told the clip cannot be patched instead of that it could not be read. Nothing is written either way. B3's caller should tell an Unreadable from a clip clip_patch refuses (M7 B2.3). | open |
| B-3 | 2026-11-05 | low: `in_place._Lent.bounded` now raises an error that is no OSError (a writer's fault, a ValueError from `record`) as itself instead of the OSError of its text that `shares.bounded` makes of it; for patch_jpeg a writer-fault ValueError is now a ValueError, not an OSError, both a failed write to field_values' caller. Needed so a process killed part-way in a test, and any writer bug, is told as itself (M7 B2.3). | decided: a bounded call's own error is raised as itself (M7 B2.3) |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | wrong: a clip's save refused as held (in_place._opened_to_patch, OPEN_ELSEWHERE) never says who holds it, though lock_owners.holders names a holder on this PC in 100-200 ms (measured: python.exe held through \\localhost\C$ was named "this TagPup process" / "another TagPup process"). _alone opens with share mode 0, so ANY other open handle (Explorer's preview or thumbnailer, Defender, a server-side indexer, another PC, a TagPup CLI, ffmpeg) is "(a player, an editor)"; the owner's date save of a 418.5 MB clip in a folder of 25 clips was refused repeatedly with no way to tell. Evidence against TagPup's own player: read_span opens and closes per 1 MiB piece (0.5 ms local, 5.8 ms over SMB loopback), closed handles do not linger (0/30 alone-open failures after each kind of close, local and SMB), and 12/12 Date Taken saves succeeded while Edge played a 381 MB clip from \\localhost\C$ (28 reads). Fix to build: the refusal appends lock_owners.describe(holders(path)), or "no program on this PC was found holding it" on a share, and logs it, so the next refusal names the holder; no page unload, no retry (no evidence either helps). | open |
| B-2 | 2026-11-13 | low: services/convert.py:352 maps any OSError of the NO_SHARING open (not found, access denied, a share gone) to HELD_OPEN, "it is open in another program"; only winerror in lock_owners.SHARING means that (in_place's rule). | open |

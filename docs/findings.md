# Findings

This file records every review finding, bug report and known problem: what was found, and where it stands. A finding that exists only in a conversation gets lost, and a decision that is not written down gets made again.

**This file holds the open findings.** A closed row (status starting `fixed`, `built`, `decided`, `closed`,
`accepted`, ...) is moved, byte for byte and with its number, to
`history/findings_closed_0001-0250.md` and so on ([index](history/README.md)) -- one file per 250 numbers.
A reference such as "#726" is found by its number there. The owner's decisions are collected in
[DECISIONS.md](DECISIONS.md); the rules that came out of findings are in [INVARIANTS.md](INVARIANTS.md).

- **Add a row when something is found**, before fixing it.
- **Severity** *(owner, 2026-10-09; CLAUDE.md, "Keeping the project small")*: start the Finding with `data:` (can lose or
  corrupt data), `wrong:` (a wrong result the owner sees) or `low:`. Only `data` and `wrong` block a merge; `low` rows are
  batched. Older rows carry none.
- **Status** is one of:
  - `open`
  - `fixed`: give the commit, by its hash or, for a row added in the same commit, its subject.
  - `not reproduced`: say what was tried.
  - `left as is`: say why.
  - `tabled`: say until when.
  - `planned`: give the phase in [ARCHITECTURE.md](ARCHITECTURE.md).
  - `watching`: nothing to do until it is reported again or reproduced *(owner, 2026-11-02)*. The row is moved by
    `tools/add_findings.py --watch N "why"` to [watching.md](watching.md): not counted open, not brought up in a report;
    **read watching.md when an issue is reported**, to see whether it is one of those. A reproduced one goes back here by hand.
- **Decisions the owner makes** are marked *(owner, date)*. Don't reopen them without a new fact.
- **Never put real names here.** The library is photographs of real people.
- **Numbering is the main session's.** A worker does not number rows into this file (two branches that both do collide on
  merge). It writes `| ? |` rows with `tools/add_findings.py rows.md --branch-file docs/findings_pending/<branch>.md`
  (provisional `B-1`, `B-2`; never write a `B-n` into code or a commit message) and the main session, merging the branch,
  runs `tools/add_findings.py --take docs/findings_pending/<branch>.md`, which numbers the rows after the highest number
  in this file **and the archives**, and removes the file. `tools/add_findings.py --status N "fixed: abc"` sets a status
  (in the archive if the row is there); `--archive` moves the rows whose status closed.

Findings fixed before 2026-09-23 are in the git log (`git log --grep="^fix"`). This file starts with what was still open or decided on that day.

| # | Found | Finding | Status |
|---|---|---|---|
| 2377 | 2026-11-02 | data: photo files rewritten through this PC have image data silently altered: a few bytes (14 to 63 in the five repaired files) at a 64-byte stride, always the same spacing (64, 64, 192, 64, 320 ...), after any kind of write (a single save, a tag-rename batch, a bake) and over weeks. Found by the owner, from a photo showing glitch lines (kr-track, Westside Classic 01), then 36, then Sub-District 001 and 002 and Lake Wilderness 51: the share file against the desktop original in `D:\Training\Pictures`, entropy-coded data only (equal length, only those bytes differ; ffmpeg decode errors alone find some). Counted 2026-11-02: kr-track 5 of 1,455 compared, all five repaired (damaged versions kept in the session scratchpad); photo_index sample 3 of 3,000 (2,719 of them rewritten by the 10-25..10-29 bake), not repaired, so about 60 more may exist in photo_index (estimate, not counted); renton_parkrun not audited. Not reproduced: about 366 ExifTool or save rewrites on a local disk and on the share, plain copies, and the app's own save with readers at work, clean (too few for a 0.1% fault). Not TagPup logic (nothing alters every 64th byte; the other library registered on the same folder only reads). Suspected hardware: this PC logged 16 fatal WHEA errors 10-03..10-14 and 5 unexpected shutdowns, and has two 16 GB modules at 4000 MT/s on an i9-11900K (an XMP profile, probably); the server is not ruled out. To do (owner's, set aside 2026-11-02): test this PC's memory (Windows Memory Diagnostic or MemTest86, XMP off); audit all of photo_index against the desktop copy and repair from it; a check after every write that the image data is what it was, putting the file back when it is not (a change to the write path, to the data and files owners first). The in-place patcher of the video work (M2) writes through the same path: the check should exist before it does. | open |

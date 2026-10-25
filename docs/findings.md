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
| 1855 | 2026-10-24 | low: `tools/retro_metrics.py` ties 21 worker runs (80.1M tokens, 38.6%) to no branch, so cost per branch and relaunches are not counted (retro 2026-10-24, E1). | planned: tie a run with no `worktreeBranch` to its worktree by the transcript's first `cwd` or the worktree folder named in the brief's tool calls |
| 1877 | 2026-10-24 | low: a photo whose vector the library kept from a run killed before its batch was saved (its row unread, the vector stamped as the file is) is read again without a decode when it holds no bake, so its row is recorded with no `photo_fingerprints` row; so is every row indexed before cut 1c (phase 6 cut 1c, `tagpup_cli.py index`'s `read`). Nothing reads the table yet. | open: the writer (cut 2) decodes a photo with no fingerprint row, as it must for the rows indexed before cut 1c; decide there |
| 1880 | 2026-10-25 | low: cut 2a (review): `bake._serialised` escapes only `\|,[]{}`, so a foreign region text with a leading blank (`Name="  Lead  "`) is written back by an undo without it (ExifTool drops a leading blank unless `\|` precedes it); 0 live instances (the 4 live MWG regions are Apple's and carry no Name). | open |
| 1881 | 2026-10-25 | low: cut 2a (review): ExifTool answers an unknown region field or an unwritable Extensions namespace with a WARNING and exit status 0 and writes the rest, and an undo has no read back, so the row is recorded "undone" holding more than the file; likewise a session whose config file vanished before the process started writes the regions and no own field with status 0 ("Tag ... is not defined"). A write that reports what it attempted. 0 live instances. | open |
| 1882 | 2026-10-25 | low: cut 2a (review): a line break inside a region text splits pyexiftool's argument and the failed write has already changed the file (the region truncated, Type gone), so the write ends as a conflict with the original lost; 0 live instances (no live region has a Name; the bake's own text never holds one). | open |
| 1883 | 2026-10-25 | low: cut 2a (review): the worker's rows B-4 and B-5 are inaccurate: `_read_whole` swallows the ValueError of a struct read beside `also` (the row is recorded without `whole`, silently, not raised), and the shapes of the live regions are now known (4 Apple Photos regions with Extensions, none with a Name; they round-trip exactly) but no test uses that shape. | open |

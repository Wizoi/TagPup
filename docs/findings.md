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
| 1869 | 2026-10-24 | wrong: cut 1c (review): the Orientation a baked box is turned by is ExifTool's `EXIF:Orientation`, while the fingerprint, CLIP's picture and `images.shown_shape` use Pillow, which falls back to XMP `tiff:Orientation` when EXIF has none; for a file with only the XMP tag the decode is turned and the boxes are not, so wrong boxes are recorded as faces (demonstrated on a scratch file; live count not available, the census asked `EXIF:Orientation` only). Also the TIFF-boxes rule is decided twice (`images.shown_shape` by Pillow format, `bake_reading` by MIME). | open |
| 1870 | 2026-10-24 | low: cut 1c (review): `face_key` is dropped when `face_processor.key != settings.faces["model"]`, which is always false (`Runtime.faces` builds from that setting, contract row 64): a restated rule with no instance. | open |
| 1871 | 2026-10-24 | low: cut 1c (review): `getattr(picture, "failed", False)` in `bake_reading.taken` covers a file deleted between two decodes, which fails today as any deleted-before-decode file does (logged, no row). | open |
| 1872 | 2026-10-24 | low: cut 1c (review): the bake tally counts per photo before its batch is saved, and `build_or_update` catches and logs every exception, so "N read from their file" can name photos never committed. | open |
| 1873 | 2026-10-24 | low: cut 1c (review): contract rows 16, 32 and 64 not amended for the bake read (row 66 added alone). | open |
| 1874 | 2026-10-24 | low: cut 1c (review): row 66's guard covers the photo-model half only; no test has a bake whose `FaceModel` is not the library's. | open |
| 1875 | 2026-10-24 | low: cut 1c (review): the bake summary line is cut to 90 characters by the server's progress scrape and never reaches Activity; `Tally.said()` is silent when every bake is under another model; `face_records.ran(picture)` is handed a picture where a detection result is documented. | open |

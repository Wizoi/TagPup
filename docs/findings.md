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
| 1856 | 2026-10-24 | low: the census brief names `XMP-MP:RegionInfoMP`, a struct, which ExifTool 13.59 answers only under `-struct` and answers nothing without it (measured on a hand-made file holding a Microsoft Photo region): asked, it would count every file as holding no MP region. The census asks the struct's flattened fields `XMP-MP:RegionRectangle` and `RegionPersonDisplayName` instead, and MWG regions by `RegionType` or `RegionAreaX` (`tests/test_xmp_census.py`, the real-ExifTool class). | decided: flattened fields, as measured; the read side (cut 1c) asks the same way or loads `-struct` |
| 1857 | 2026-10-24 | wrong (found in review): ExifTool says a JPEG holds two XMP blocks ("Duplicate XMP at JPEG-APP1-XMP") only under `-validate`, and keeps ONE `Warning` a file; 119 of 120 sampled real photos already carry an earlier minor validation warning, which hid the duplicate, so `duplicate_xmp` could count only about one file in ten that has it and "another warning" counted nearly every file. | fixed: the census passes `-api NoWarning=^(?!Duplicate XMP)` beside `-validate` (every other warning is suppressed) and `other_warnings` is removed; the real-ExifTool test now gives the duplicate file an earlier minor warning (`-IFD0:Rating=3`) |
| 1858 | 2026-10-24 | low: the census's sample is systematic over the photos in path order (every total/sample-th photo), so each folder gives its share by its size and a folder smaller than total/sample photos may give none (photo_index: about one photo in 60). Deterministic, so a second run reads the same files; the XMP size counted is the standard block's only, never an Extended XMP's. | decided: proportional by folder, deterministic; owner may ask for one-per-folder first |
| 1859 | 2026-10-24 | low (found in review): the default sample (1,000) read the two small libraries (1,499 and 1,309 photos) in part, not whole as docs/PHASE6_DESIGN.md says. | fixed: `CENSUS_SAMPLE` is 3,000; a library of fewer photos is read whole |
| 1860 | 2026-10-24 | low: the cut-1b brief says `field_values.read` accepts `params=["-struct"]`; it takes no params. A bake is read through a session opened with `bake.READ_ARGS` (the default `-G -n` plus `-struct`) and `field_values.read(et, paths, [], also=bake.FIELDS, records=...)`; without `-struct` ExifTool answers no `XMP:RegionInfo` and a one-item FaceVectors as a bare string (probed, ExifTool 13.59). The read side (cut 1c) opens its own session for the bake read, or the indexer's session takes `-struct` for every read (a change of what every other field reads as: not made here). | decided: `bake.READ_ARGS`, a session of its own; the choice for the indexer's session is cut 1c's |
| 1861 | 2026-10-24 | low: ExifTool's JSON answers a number of 17 significant digits as a string (`"0.20833333333333334"`, an MWG Area X as `orientation.area_of` gives it for 10/48) and a shorter one as a number; `bake.parse` read the string as "not a finite number" and refused a valid bake. `_number` now reads a numeric string; the real-file test failed before the change. The writer (cut 3) can also round Areas to about 6 decimals. | fixed: tests/test_a_bake_is_used_only_whole.py, test_faces_one_and_two_and_none |
| 1862 | 2026-10-24 | low: decision, not copied: the Fingerprint value is `<key>:<sha256 hex>` (`upright-sha256-1:` + 64 hex), parsed to the key and the 32-byte digest migration 34's `hash BLOB` holds; the vector lengths are `bake.PHOTO_DIM` 1152 and `FACE_DIM` 512, constants, since no module states a model's dimension (the bake's model key is checked against the library's by the read side first); an MWG region of another Type than Face, or an Area not `normalized`, rejects the bake (no instance: the bake writer writes RegionInfo whole). | decided: as built; the owner may change the Fingerprint spelling before cut 3 writes any file |
| 1863 | 2026-10-24 | low (found in review): `bake.py`'s docstring called every value far under 64 KB; the limit is the APP1 segment holding all of them: 310 photos in photo_index hold 35 or more faces (232 hold 40 or more, the largest 412), each face costing about 1,600 characters, so their bake becomes Extended XMP, which other programs often do not read. | decided: the docstring says so; the owner decides before cut 3 writes (cap, accept Extended XMP, or skip those photos): PHASE6_DESIGN section 8 |
| 1864 | 2026-10-24 | low (found in review): the Pillow pin in requirements.txt had no guard; the installed copy runs from the repository's own venv. | fixed: `test_the_decoder_is_the_pinned_one` |
| 1865 | 2026-10-24 | low (found in review): `parse` accepted an Area outside the picture. | fixed: a centre outside [0, 1] or a size over 1 is refused, test `test_a_region_outside_the_picture` |
| 1866 | 2026-10-24 | low (found in review): the writer of cut 3 must delete `RegionInfo` for a photo with no faces (a present struct with FacesFound 0 is refused), and a file's other programs' mwg-rs regions are replaced by the bake. | decided: stated in PHASE6_DESIGN section 8; the census says how many files hold mwg-rs regions |
| 1867 | 2026-10-24 | low: migration 34's `RowsKept(but=("photo_fingerprints",))` counts no table (an ADDITIVE step's `touches` minus `but` is empty), yet is recorded as a passed check; the synthetic steps in `tests/migration_names.py` have the same shape. The guard that holds is `_Watch` ("nothing that was there changed"), asserted by the migration test. | decided: by the precedent of the other additive steps; no check is wrong, one is empty |
| 1868 | 2026-10-24 | low: `docs/ARCHITECTURE.md` lists `photo_fingerprints` in the "Derived (rebuilt or computed; feature code never writes)" row beside `faces_detected`, while `store/photo_fingerprints.py` and DATABASE.md section 28 say it is not a table of `store.derived` and the bake (feature code) writes it. | decided: the row means "made from pixels, not from other rows" for both tables; nothing rebuilds either |

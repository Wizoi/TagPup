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
| 1894 | 2026-10-25 | low: `tagpup/store/photo_fingerprints.py` says "Nothing writes a row yet but the tests", but `services/search.py:348` puts a row for every photo the index decodes (kr-track 136 rows, counted 2026-10-25); and the module says the row is a fact about the picture that no journaled change restores, while the cut 2b brief asked that an undo clearing a bake remove it. | decided: the docstring is the rule (a fact of the picture, not journaled): the bake's follower only PUTS the row, in the done transaction of a write holding the Fingerprint; an undo leaves it, a bake changes no pixel; the stale docstring line is corrected in cut 2b-i |

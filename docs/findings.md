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
| 1805 | 2026-10-23 | low: retro 2026-10-23 action 1 (guard): the refusal of a way back is owned by three places (`journal.refusals`, `settings.undo_refusals`, `retire_faces.undo_refusals`); six `wrong`/`data` rows of the class in the window (#1719, #1771, #1779, #1781, #1782, #1783). One owner, each change kind naming its undo check, and a scenario matrix test that undoes every change kind after every later kind through the real writers; a sweep of the change kinds first. Target at the next retro: 0 rows of the class. | planned: cut A merged (why_not over UNDO_CHECKS and its guard); cut B, the matrix test and the tests it replaces, is next |
| 1806 | 2026-10-23 | low: retro 2026-10-23 action 3 (guard): a hook (`.claude/hooks/`, the pattern of context-budget.mjs) that refuses a `git merge` adding over 1,200 lines (`tools/retro_metrics.py --branch` counts them) without an `Over budget: <reason>` line in the message, and a merge whose subject is only a `Worktree:` line; a test that it fires; CLAUDE.md's budget bullet then names the hook in one line. Target: unexplained over-budget merges 0, `Worktree:`-only subjects 0. | planned: after action 1 (owner, 2026-10-23) |
| 1807 | 2026-10-23 | low: retro 2026-10-23 (E3): `tools/retro_metrics.py` does not count the workers that stopped at the 150k hook and the relaunches that finished them, nor the review rounds of a branch whose reviewer started in the main checkout; the retro could not say what the three relaunches of 2026-10-23 cost. Count both (the subagent meta files and transcripts' usage fields only). | planned: with action 3 (owner, 2026-10-23) |

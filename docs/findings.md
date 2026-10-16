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
| 1518 | 2026-10-16 | low: `tools/retro_metrics.py` counts a merge's size against the 1,200-line budget as added plus removed lines, so a deletion reads as over budget: the data file of 2026-10-16 says 74 of 134 trunk merges are over; counted with added lines only it is 33 of 49 before the freeze and 4 of 85 after (49 of those 85 net-negative). Count added lines (or both, side by side). Retro 2026-10-16, section 2. | open |
| 1519 | 2026-10-16 | low: `tools/retro_metrics.py` reports sessions per day as distinct session ids per day (1 on each day), which hides that one main transcript spans 2026-10-02 to 2026-10-16 with 4 compact boundaries (meta fields `sessionId`, `type: system`, `subtype: compact_boundary`); "compactions" is listed as not counted. Report main sessions over the window and their compactions. Retro 2026-10-16, section 2. | open |
| 1520 | 2026-10-16 | low: `tools/retro_metrics.py` cannot tie a subagent run to a branch, so review rounds per branch and runs never merged are not counted; a subagent transcript's `cwd` (a meta field, not text) names its worktree and so its branch. Retro 2026-10-16, open question 1. | open |

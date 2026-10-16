---
name: retro
description: Run a retrospective of AI-assisted work per docs/RETRO.md. Use at a phase end, or when a trigger fires (a defect the owner hit, a third review round on one branch, a branch above its size budget, a usage-limit hit, a rule broken twice).
---

# Retro

The process, its rules and the document's template are in `docs/RETRO.md`; read it first. This is the checklist.
The owner decides; you gather and record. You never grade the work, your own or the retro's.

## Checklist

- [ ] **Fresh session.** If this session did the work being judged, say so and suggest `/clear` first.
- [ ] **Set the stage.** Ask the owner for the window (dates or a commit range of the trunk) and the trigger, or
      propose them from the last retro in `docs/retros/README.md`. Write the scope paragraph.
- [ ] **(a) Gather.** Run the metrics script for the window, into an untracked file:
      `.venv/Scripts/python.exe tools/retro_metrics.py --since <date> --until <date> > reports/retro-<date>-data.md`
      (`--json` for a machine-readable copy). Never edit the file; a wrong number is fixed in the script and the file
      made again. Add the dated timeline (merges, reverts, escapes, decisions, restarts, compactions) from git and
      the ledger. Counts only, no interpretation, no real names.
- [ ] **(b) Draft the insights in a fresh context.** Launch ONE agent on Opus that did none of the work, with only
      these files: the data file, `docs/findings.md` (and `docs/history/findings_closed_*.md` for the window),
      `docs/DECISIONS.md` and `docs/RETRO.md`. Not the transcripts, not this conversation, no summary of yours. Its
      brief: the three most expensive episodes, each with the earliest signal and the cheaper path with what was
      known then (or "no action: the cost of learning"); whether the last retro's actions met their metrics; at most
      three proposed actions, each delete / guard / brief change / no action, with a metric and a target. Blameless:
      causes are context, briefs and guards.
- [ ] **(c) Present to the owner.** The counted numbers, the draft episodes and the proposed actions, as drafted.
      Mark anything you disagree with as your note beside it; do not rewrite the draft. The owner decides the
      actions (at most three).
- [ ] **(d) Record and do the instruction diff.**
  - [ ] Each decided action goes into `docs/DECISIONS.md`, dated, in the owner's words, with its metric and target;
        a code change also gets a findings row (`tools/add_findings.py`).
  - [ ] The instruction diff: remove from CLAUDE.md, the agent definitions and memory every line a guard now
        enforces (keep one line naming the guard), every stale number, every rule nothing breaks; then add the
        actions. Lines removed >= lines added. CLAUDE.md stays under its target (under 200 lines a file).
  - [ ] Write `docs/retros/<date>-<scope>.md` from the template in `docs/RETRO.md`; add it to
        `docs/retros/README.md`.
  - [ ] Commit the retro document and the instruction diff, one concern each, staged by path.
  - [ ] Update the memory handoff (the retro's date, its actions, where the document is), then suggest `/clear`.

## To use this in another project

Copy:
- `docs/RETRO.md` (the process; generic but for its examples and the 1,200-line budget);
- `.claude/skills/retro/SKILL.md` (this file; generic);
- `tools/retro_metrics.py`. Its GIT and CLAUDE CODE SESSION TRANSCRIPTS sections are generic (the transcripts'
  usage fields are Claude Code's, in `~/.claude/projects/<project>/`); WORKERS is generic if the project uses
  subagents. TagPup-specific: the FINDINGS LEDGER section (it parses `docs/findings.md` and its archives, the
  `| n | date | severity: ... | status |` rows) and the TESTS counters (`tests/owners.py`, `tests/tiers.py`, the
  ratchets).

Adapt:
- the trunk's branch name and the size budget;
- the findings section to the project's ledger (or drop it, and gather escapes and review rounds by hand);
- the tests counters to the project's test layout;
- where decisions are recorded (`docs/DECISIONS.md` here) and where the retros go (`docs/retros/`).

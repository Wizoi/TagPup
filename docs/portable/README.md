# The portable kit

What this project learned about running AI-assisted work, in a form another Claude Code project can take: the rules
that held, the agent roster, the guards, the tools and the retro. It holds no fact of this project; an example from it
is marked as such. It came from the retro of 2026-10-16 (`docs/retros/2026-10-16-freeze.md`, sections 8 and 9).
How to install it, at user level or per project: [INSTALL.md](INSTALL.md).

| part | what it is | copy to | basis |
|---|---|---|---|
| [CLAUDE.starter.md](CLAUDE.starter.md) | a starter project instruction file, the general rules only | `CLAUDE.md`, or the general sections to `~/.claude/CLAUDE.md` | mixed: see below |
| `docs/RETRO.md` (not duplicated: take the project's own copy) | the retro: when, who, five phases, at most three actions each with a metric | `docs/RETRO.md` | practice: run once (2026-10-16) |
| `.claude/skills/retro/SKILL.md` (the project's own copy) | the retro's checklist as a skill | `~/.claude/skills/retro/` or `.claude/skills/retro/` | practice |
| [agents.example/](agents.example/) | worker, small worker, reviewer, quick reviewer, each with its own `model:` line | `.claude/agents/` | evidence: the worker had no `model:` and inherited the main session's, the model whose calls had the longest contexts (65% over 150k tokens) |
| [hooks.example/no-heredoc-escapes.mjs](hooks.example/no-heredoc-escapes.mjs) | refuses a shell heredoc holding a backslash escape | `.claude/hooks/` | evidence: mangled escapes cost time five times in two days; the rule as prose did not hold |
| the context-budget hook | tells the main session to hand over and `/clear` above about 300k tokens of context | copy `.claude/hooks/context-budget.mjs` once it lands in this project (being written now) | practice: proposed by the retro, its metric not yet measured |
| [tools.example/README.md](tools.example/README.md) | which of `tools/patch.py`, `add_findings.py`, `stale_findings.py`, `retro_metrics.py` to copy, what to adapt, the ledger's format | `tools/` | evidence for the ledger and the stale check; practice for the rest |

## Which rules are evidence-backed, and which are practice

Evidence-backed (each was broken here and cost something counted in the record):
- a number told to the owner is counted this session (a relayed "38 folders, 13,816 photos" was 4 and 188);
- a one-way choice is asked first (two such choices were built, reviewed and thrown away in one day);
- the second fix for one symptom is an ownership question (one branch ran six review rounds before the
  owner-of-the-rule fix);
- a special case needs a real instance (machinery for a handful of cases was built and deleted within two weeks);
- a write reports what it changed (a backfill reported 60 done and wrote nothing);
- test data is made by the code that makes the real thing (two regressions in one day from fixtures nothing real made);
- findings are recorded before fixing, and verified before briefing (23% of open rows were no longer true of the code);
- a new rule is applied to the backlog the day it is made (risky rows sat ungraded for weeks);
- patch scripts through a file tool, one concern per commit, commit before launching background agents (each an
  incident: mangled escapes, two concerns in one commit, five agents lost to restarts);
- a size budget counts lines added, never deleted (counting both called 74 merges over budget; 37 were).

Practice (held here, not measured): a fix's test fails on the old code; no real names in tests; the file under 200
lines with a rule moving to a guard when broken twice; at most two review rounds; the retro's process itself.

Not carried: the source project's own rules (its paths, tools, data model), its domain owners and their seven health
conditions (worth it only once a project has domains to own), its scenario ids and invariants.

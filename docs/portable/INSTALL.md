# Installing the kit

Two ways: at **user level** (every project on this machine gets the retro skill and the general rules) or **per
project** (one repository gets all of it). Installing at user level writes into your own `~/.claude/`: **that is
your step**; no agent of the source project writes there. Paths below are from the source project's root; `SRC` is
that checkout and `DEST` the other project's.

## User level (the owner's step)

The retro skill, so `/retro` works in every project:

```
mkdir -p ~/.claude/skills/retro
cp "$SRC/.claude/skills/retro/SKILL.md" ~/.claude/skills/retro/SKILL.md
```

The skill reads `docs/RETRO.md` of the project it runs in: copy that file into each project that will run a retro
(per project, below). The general rules: append `docs/portable/CLAUDE.starter.md` to `~/.claude/CLAUDE.md` (it is
made if there is none), keeping its sections "Numbers and choices", "Tests", "Findings" and "Editing and committing":

```
cat "$SRC/docs/portable/CLAUDE.starter.md" >> ~/.claude/CLAUDE.md
```

then open `~/.claude/CLAUDE.md` and delete what does not fit every project (the `<project>` headings, the agents
section if a project has no subagents). Keep it short: the user-level file is read in every session of every project.
The agents can also go to user level (`~/.claude/agents/`), but their rules name a project's files: per project is
the better place.

## Per project

```
cd "$DEST"
mkdir -p .claude/agents .claude/hooks .claude/skills/retro docs/retros docs/findings_pending tools
cp "$SRC/docs/portable/CLAUDE.starter.md" CLAUDE.md            # or merge into an existing CLAUDE.md
cp "$SRC/docs/portable/agents.example/"*.md .claude/agents/
cp "$SRC/.claude/hooks/no-heredoc-escapes.mjs" .claude/hooks/
cp "$SRC/.claude/skills/retro/SKILL.md" .claude/skills/retro/  # unless installed at user level
cp "$SRC/docs/RETRO.md" docs/RETRO.md
cp "$SRC/tools/patch.py" "$SRC/tools/add_findings.py" "$SRC/tools/stale_findings.py" tools/
```

Then:
1. Register the hook in `.claude/settings.json` (merge with any `hooks` already there):
   ```
   { "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [
       { "type": "command", "command": "node \"$CLAUDE_PROJECT_DIR/.claude/hooks/no-heredoc-escapes.mjs\"", "timeout": 10 } ] } ] } }
   ```
   It needs `node` on the PATH; a hook that fails to run lets the call through.
2. The context-budget hook: copy `.claude/hooks/context-budget.mjs` from the source project once it lands there (it is
   being written now) and register it as its own header says (a `UserPromptSubmit` hook).
3. Fill in `CLAUDE.md`: the project's name, its own rules (one line each, its owner and its guard), and `<project>` in
   the agent definitions. Give each agent the `model:` you want; the point is that each says one.
4. Start `docs/findings.md` with the header table of `docs/portable/tools.example/README.md`, and an empty
   `docs/DECISIONS.md` (`| date | decision |`).
5. Adapt `docs/RETRO.md`: its examples are the source project's; keep the process, the rules and the template. Copy
   `tools/retro_metrics.py` when it is project-neutral (`docs/portable/tools.example/README.md`).
6. Run the first retro at the first phase end, in a fresh session: `/retro`.

# The tools to copy

Copy these four from the source project's `tools/` (the copies kept up to date are there; none is duplicated here).
Each is plain Python, standard library only, except where noted.

| tool | what it does | copy | adapt |
|---|---|---|---|
| `tools/patch.py` | Exact, counted, all-or-nothing edits from a patch script, in the file's own newlines (`Patch(path).replace / between / after / before`, `new_file`). | as is | nothing; the docstring's example path |
| `tools/add_findings.py` | Numbers `\| ? \|` rows into `docs/findings.md`; a branch keeps provisional `B-1`, `B-2` in `docs/findings_pending/<branch>.md` and the main session gives the numbers at merge (`--take`); `--status N "fixed: <hash>"`; `--archive` moves closed rows to `docs/history/findings_closed_<range>.md`. | as is | `FINDINGS`, `ARCHIVE_DIR` and `CHUNK` (250 rows an archive) if the layout differs |
| `tools/stale_findings.py` | Lists open rows that name a file or symbol the code no longer has, so a stale row is re-checked before it is briefed. Imports `add_findings`. | as is | `CODE_DIRS`, `CODE_TYPES` and `UNDER` (the folders a row may leave out) to the project's layout |
| `tools/retro_metrics.py` | The retro's data file: git, the findings ledger, the tests, Claude Code's token use, the workers. Read-only. Imports `add_findings`. | after it is project-neutral (below) | `TRANSCRIPTS` (the `~/.claude/projects/<folder>` of the project), `PRICES`, the trunk's name, the size budget |

`tools/retro_metrics.py` as of 2026-10-16 imports the source project's process helper (`tagpup.core.processes`) to
run git, and its FINDINGS LEDGER and TESTS sections read that project's ledger and test map: replace the import with
`subprocess.run`, and drop or adapt those two sections (`.claude/skills/retro/SKILL.md`, "To use this in another
project", says which parts are generic). It is being changed in the source project now (the branch budget counting
added lines, tokens by agent type and branch); copy it after that lands.

## The findings ledger

`docs/findings.md` holds the open rows, one table:

```
| # | Found | Finding | Status |
|---|---|---|---|
| 12 | 2026-10-16 | wrong: `app/report.py:88` counts the rows it meant to delete, not the rows deleted; a delete that matched none reports "3 removed". | open |
```

- The Finding starts with a severity word: `data:` (can lose or corrupt data), `wrong:` (a wrong result the owner
  sees), `low:`. Only `data` and `wrong` block a merge; `low` rows are batched, then fixed or closed as by design.
- The Status is `open`, or a closing word with its evidence: `fixed: <hash>`, `decided: <reason or DECISIONS date>`,
  `not reproduced: <what was tried>`. A closed row moves to the archive, byte for byte, with its number.
- A worker never numbers into the ledger: two branches doing it collide on merge.
- A row is recorded before the fix, and re-checked against the code before it is briefed (`stale_findings.py`).

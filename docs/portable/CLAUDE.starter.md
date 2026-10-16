# Working on <project>

Short on purpose: every rule here is one that has cost time. Keep this file under 200 lines. A rule broken twice moves
into a guard (a test, a ratchet or a hook) and keeps one line here naming that guard; a rule nothing breaks is deleted.
The reasoning behind the rules lives in `docs/DEVELOPMENT.md`; the owner's decisions, dated, in `docs/DECISIONS.md`.

## Numbers and choices

- **A number told to the owner is one counted this session**, read-only, from the record: not relayed from an agent's
  report, not recalled. An agent's number is a claim until counted.
- **A one-way choice is asked before it is built**: a migration or stamp on live data, anything run automatically on
  the owner's data, a new meaning for an existing idea, a default the owner never chose. Put the question in the report.
- **The second fix for one symptom is not a fix.** It is a question: which single place should own this rule? Ask it
  in the second review round at the latest; a third round on one branch is a retro trigger.
- **A special case needs a real instance.** Every fallback, compatibility arm or defensive branch cites a count above
  zero in the live data, or the user action that produces it. With neither, refuse the write that could make the case,
  at its source, and write no branch. A fixture is not an instance.
- **A write reports what it changed, not what it attempted**: counts from the effect (rows re-read, `rowcount`), never
  from the input (`len(items)`).

## Tests

- **Every fix gets a test that fails on the old code**, run against the old code to prove it (`git archive HEAD | tar -x
  -C <folder outside the worktree>`).
- **A test's data is made by the code that makes the real thing.** A fixture seeded through the helper under test
  agrees with the helper, not with the data; a fixture holding a shape nothing real makes agrees with the bug.
- **No real names in tests, fixtures, comments or commit messages.** Fictional names of the same shape.
- Tests answer scenarios a person lives through (a crash half way, two writers at once, rapid clicks), not what one
  function does.

## Findings

- **Record a finding before fixing it**, as a row in `docs/findings.md`: `| n | date | severity: what, where, the
  failure | status |`, the severity word first: `data` (can lose or corrupt data), `wrong` (a wrong result the owner
  sees), `low`. Only `data` and `wrong` block a merge. A finding that lived only in a conversation is lost.
- **A finding is verified against the code (and a live count where it names one) before it is briefed.** A stale row
  sends a worker after something that is gone.
- A new rule is applied to the backlog the day it is made, not only to new rows.
- A `low` is fixed or recorded as by design with its reason; there is no third state.

## Editing and committing

- **Patch scripts are written with a file tool, never a shell heredoc**: backslash and `\u` escapes are mangled in
  transit (`.claude/hooks/no-heredoc-escapes.mjs` refuses one).
- **One concern per commit**, staged by path (`git add -A` lumps two together).
- **Commit before launching background agents**, and keep their work in worktrees: a restart kills an agent, and only
  committed or worktree work survives.
- A branch adds about 1,200 lines or fewer (a deletion is never over budget); a larger piece is cut into merges that
  each leave the trunk whole.
- Bulk operations on data: dry-run by default, `--apply` to write, a backup first.

## Agents

- A worker's standing rules live in its definition (`.claude/agents/worker.md`); a brief says only the task, which
  files are the worker's, and how this task fails (interrupted part-way, two at once, a read that fails, the real
  data's shape, what the owner sees).
- The agent for the size, each with an explicit `model:` line: a small worker for a bounded fix, the worker for design,
  a reviewer for a branch's first review, a quick reviewer for the follow-up. At most two review rounds.
- One session per phase: `/clear` at a phase boundary, a retro at each phase end (`docs/RETRO.md`, the `retro` skill).
- Decide by the rules here; ask the owner only what they alone can answer.

## <project>'s own rules

<!-- One line each: the rule, its one owner (module or file), the guard that fails the build. Example, marked as such
(from the project this kit came from): "Never call sqlite3.connect: the db module owns journal mode, busy timeout and
the write lock (tests/test_db_access.py)." -->

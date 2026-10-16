# Working on TagPup

Short on purpose. Every rule here is one that has actually cost time on this project. The reasoning, and the incident
behind each rule, live in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md); this is what to do. A rule a guard enforces is one
line naming its owner and its guard; a rule with no guard keeps its words here. A rule broken twice moves into a guard.

**New code goes where [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) puts it.** That file is a map
(about 35 KB): the layers, what each package owns, the data model on one page, the processes, and where to
look for each concern. Check it before adding a module, a route or a query.
**[docs/INVARIANTS.md](docs/INVARIANTS.md)** is one page of the rules whose violation loses data, each with its
owner and the test that enforces it; review against it. **[docs/DECISIONS.md](docs/DECISIONS.md)** is the
owner's decisions, dated, not reopened without a new fact. **[docs/history/](docs/history/README.md)** holds the
design logs (the phases, as built, with measurements) and the closed findings; read it only when asked.
**Record every review finding in [docs/findings.md](docs/findings.md) (the open ones) before fixing it**; a
worker's rows go to its own file, `tools/add_findings.py --branch-file`, and the main session numbers them with
`--take` (see the header of findings.md). Findings that lived only in a conversation were lost, and decisions
were made twice.

**Imports and `scripts/`**: new code imports from the package (`from tagpup.store import db`); `scripts/` holds programs
with a `__main__` block and the few helpers they share (`_root`, `code_snapshot`, `reloader`), imported by nothing but
their own tests; a module that moves takes every importer with it and leaves nothing at its old name (`tests/test_layers.py`).

## Running things

```
.venv/Scripts/python.exe tools/run_tests.py --fast      # the fast tier, the loop while editing
.venv/Scripts/python.exe tools/run_tests.py             # every tier: the commit gate (about 4,300 tests, about 5 minutes)
.venv/Scripts/python.exe tools/affected_tests.py        # the test files a change reaches
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/frontend/*.test.mjs                   # the glob: `node --test tests/frontend/` fails on harness.mjs
.venv/Scripts/python.exe -m ruff check .                # also inside the suite (tests/test_lint.py)
```

No `pip.exe`, no `Activate.ps1`, no pytest: invoke the interpreter by path. `ruff.toml` selects rules that catch defects;
one wrong for this codebase is turned off there with a comment saying why. **A worker does not run the whole suite**
(owner, 2026-10-11): it runs the files it changes and `affected_tests.py` while it edits, and once at the end `--fast`
plus every test file it added or changed; the main session runs the whole suite once on the merged trunk, never beside
another run. The tiers, the flaky re-run and the logs: docs/DEVELOPMENT.md, "Running things".

**The owner runs the apps from an installed copy** (`%LOCALAPPDATA%\TagPup\*.cmd`, made by `scripts/install_app.py`),
with `TAGPUP_HOME` set to the repository: saving a file here changes nothing they are running, and a merge reaches them
only when the app is installed again (`--apply` hands over to the new version without a window, `tagpup.launcher.hand_over`;
docs/DEVELOPMENT.md). **Ask before installing**; offer to after a merge they want to use. A sandbox or test is a home of
its own (`tagpup.config.own_home_environment`): never start a server in the owner's home to measure or test. An app
started from the repository restarts whenever a `.py` is saved, orphaning any indexer: check for one before editing.
Run long indexes through the CLI (`TagPup CLI.cmd`).

## Rules that keep being broken

Each names its one owner and the guard that fails the build when it is broken.
- **Never call `sqlite3.connect`**: `tagpup/store/db.py` owns journal mode, busy timeout, the per-file write lock and the
  retry (`tests/test_db_access.py`).
- **Never call `subprocess` yourself**: `tagpup/core/processes.py` (`start`, `run`, `kill_tree`, `is_alive`) gives every
  child a hidden console (`tests/test_processes_single_owner.py`).
- **Never construct `ExifToolHelper` or `ExifTool`**: `ExifToolSession` (`tagpup/files/exiftool_session.py`) drains both
  pipes and gives each command a deadline (`tests/test_rules_have_one_owner.py`).
- **Never load a model on the GPU yourself**: `tagpup/ml/gpu.py`, one process at a time; take a turn through
  `tagpup.runtime` (`begin` for Suggest, `gpu_turn` for the indexer) (#750; `tests/test_gpu_single_owner.py`).
- **Never spell or compare a photo path by hand**: `tagpup/core/paths.py` (`stored()`, `key()`, `sql_equals()`,
  `sql_under()`); in the pages `pathKey` / `samePath` (`web/common/paths.js`) (`tests/test_rules_have_one_owner.py`,
  `tests/frontend/path-helpers.test.mjs`).
- **A library's settings live in the library**, read through `tagpup.services.settings` and changed only by a journaled
  change; nothing reads `config.ini` (`tagpup/config.py` owns only where the libraries are) (`tests/test_config_single_owner.py`).
- **Never convert between a person's name and their tag by hand**: identity is a leaf (`Rowan Thackeray`), the tag a path
  (`People/Rowan Thackeray`): `leafOf`, `rootOf`, `samePerson`, `photoAlreadyHas` in `web/common/vocabulary.js`;
  `tagpup.core.vocabulary` and `taxonomy.person_paths()` in Python (`tests/test_vocabulary.py`, `tag-vocabulary.test.mjs`).
- **A bulk write must tell the index what it wrote**: every file write goes through `file_changes.write_fields`, which
  records what the file holds in its row (`photos.follow_fields`) in the transaction that marks it done
  (`tests/test_rules_have_one_owner.py`, `tests/test_derived_writers.py`).
- **A page's `main.js` only wires it, and starts it at the very end**; each feature is a module in `web/tagpup/` or
  `web/tuner/`, the page's mutable state is `state` in `state.js`, a call back up goes through `upper` (`hooks.js`)
  (`tests/frontend/page-state.test.mjs`, `tag-vocabulary.test.mjs`, `tuner-starts-last.test.mjs`).
- **A page's request goes through `web/common/api.js`** (`api.json`, `api.fetch`, `api.image`, `api.url`); a bare `fetch`
  reaches no library (`tests/frontend/database-routing.test.mjs`; one scope per module: `page-modules.test.mjs`).
- **Write patch scripts with a file tool, not a shell heredoc**: escapes are mangled in transit;
  `.claude/hooks/no-heredoc-escapes.mjs` refuses an interpreter heredoc holding one.
- **A test's data is made by the code that makes the real thing**: a photo row through `tests/photo_rows.py` (`add_read`,
  `add_unread`), a page's record as the page builds it (#246, #247; `tests/test_fixtures_make_photo_rows_like_the_indexer.py`);
  a test that selects or creates a library runs in a home of its own (`tests/test_tests_have_homes_of_their_own.py`).

**SQL on this library is not SQL on a test fixture.** 225,000 faces, most carrying a
6 KB crop, and every one of these has shipped:
- `LIKE` is not equality. It ignores case and reads `_` and `%` as wildcards, so
  `IMG_0001.jpg` also matches `IMGX0001.jpg` -- in an `UPDATE`, other photos change.
- A function on a column (`LOWER(path) = ?`) cannot use its index. Neither can a
  comparison whose collation differs from the index's. (On a path column both fail `tests/test_rules_have_one_owner.py`.)
- Never select a BLOB you do not use. `SELECT *` on `faces` is ten seconds cold.
- A query in a per-item loop that answers the same thing each time belongs outside it.
- Check `EXPLAIN QUERY PLAN` against `data/photo_index.db` (read-only, via
  `db.readonly_uri`) for anything that touches `faces` or `photos`.
- Look at real rows before writing a lookup. A fixture seeded through the helper under
  test agrees with the helper, not with the data.

**Never put real names in tests, fixtures, comments or commit messages.** This library
is photographs of real people, many of them minors. Use fictional names of the same
shape.

## Before changing behaviour

**Name where each side gets its data.** When two things disagree, find both sources
before touching either. Adjusting a threshold to make two lists agree hides the fact
that they were different lists.

**The second fix for one symptom is not a fix.** It is a question: what single place
should own this? That question produced `db.py`, the tag vocabulary, and
`write_keyword_fields` — each ended a whole class of bug. Reaching for it after the
second round beats reaching for it after the fourth.

**Watch an OS-level symptom before fixing it.** A window on the desktop, a port held,
a file that will not delete: run `tools/watch_processes.ps1` while it happens and read
which process did it. Three rounds of per-site fixes for terminal windows found nothing;
one watched run found the test-home reaper.

**Check the destination before moving data into it.** Re-pointing rows at paths that
already had rows created 233 duplicate faces.

**A write reports what it changed, not what it attempted.** A backfill read 60 photos,
reported 60 done, and wrote nothing; the paths did not match and nothing said so.

**Performance work**: a speed claim is a claim about a user action, timed in a real browser in a sandbox, baseline
first. Read docs/DEVELOPMENT.md, "Performance work", before claiming anything got faster.

## Working in few turns

A worker's standing rules (how the work fails, one-way choices, findings, what it runs, its report) are in
`.claude/agents/tagpup-worker.md`; delegate multi-file work to it in a worktree, and the brief says only the task, the
domain and which files are its, plus what is particular to the task.
- Edit through `tools/patch.py` from a script written with the Write tool, one script per concern (patch, lint, test).
- Before briefing a new tool, script or module, find the existing owner: grep `tagpup/services/`, the MCP server's
  tools and `scripts/`.
- A brief names how the work fails (interrupted part-way, two at once, a read that fails, the real data's shape, what
  the owner sees) and the scenario the owner works in (rapid clicks, switching library mid-folder, two libraries
  holding one folder, an update while working).
- A one-way choice -- a stamp or migration on the live libraries, anything automatic on the owner's data, a new meaning
  for an existing idea -- is the owner's or the main session's, asked before it is built.
- The agent for the size: `tagpup-worker-small` for a bounded fix, `tagpup-worker` for design and anything touching
  sync, the journal or writes; `tagpup-reviewer` for a branch's first review, `tagpup-quick-reviewer` for the follow-up;
  a branch in one domain also gets its owner, `-integration` across two ([docs/OWNERS.md](docs/OWNERS.md)). After any
  change more than one agent made, a reviewer before committing. A one-file fix is often cheapest done directly.
- A number told to the owner about their libraries is one counted this session, not relayed.
- Commit before launching background agents, and keep their work in worktrees.
- One session per phase: `/clear` at a phase boundary (the memory handoff carries the state), `/compact` inside a long
  one. A retro runs at each phase end and on its triggers, by [docs/RETRO.md](docs/RETRO.md) (the `retro` skill).
- Decide by the rules here and in docs/ARCHITECTURE.md; ask the owner only what they alone can answer. The cheapest
  correct change first: no extra backup, no workaround around the model where the model can be fixed.

## Keeping the project small

The feature freeze (2026-10-09) is lifted (2026-10-16): the five domains meet the seven conditions of health, and each
keeps them by its owner's review of every change ([docs/OWNERS.md](docs/OWNERS.md), "Every owner's standing goal and
checklist"; DECISIONS 2026-10-15 and 2026-10-16). A feature is taken one at a time, its live count in the brief, planned
by the owners of the domains it touches, built by one worker, reviewed once by each owner: it adds no debt, or it is
refused. No schema work while the owner's note of DECISIONS 2026-10-15 stands.
- **A branch adds about 1,200 lines or fewer, one concern.** A larger piece is cut into merges that each leave the trunk
  whole; a deletion is never over budget (DECISIONS 2026-10-16).
- **At most two review rounds**, the ownership question ("which single place should own this?") in the second; a round
  after it only for a finding that can change a file or lose data. A finding carries a severity: `data` (can lose or
  corrupt data), `wrong` (a wrong result the owner sees), `low`. Only `data` and `wrong` block a merge.
- **One migration in flight at a time**: no new migration until the last one is installed
  and has run on the three live libraries.
- **A new feature's brief states its live count** (how many rows or cases in the three
  libraries it touches). Under about 20, it is a one-off script, not a feature.
- **Each branch removes at least as many concepts, shims or tests as it adds**, or says why.
- **Test files are named for what they test, never for a review round** (`..._followup`,
  `..._third_review` are renamed or merged when touched).
- **A special case needs a real instance** (owner, 2026-10-11: three imagined cases had grown into a whole name-keyed fallback).
  Every fallback, compatibility arm, defensive branch and 'what if' path must cite either a live count above zero (counted
  this session, read-only) or the user action, through the real writers, that produces it. A case with neither is not
  handled: the write that could make it is refused at the source with a plain sentence, and the branch is not written. An
  owner decision that rules a class out (a shared leaf, a group as a person, a keyword with a person's name) is cited, not
  re-argued. A fixture that holds a shape nothing real makes is not an instance.
- **Tests answer scenarios, not code** (owner, 2026-10-11). A test is a scenario a person lives through (rapid clicks, a
  crash half way, two libraries, an update while working), grouped by domain owner and named for it; a test that only
  restates what a function does is too granular and misses the scenario between functions. New work adds or lifts a
  scenario test, and deletes the granular tests it makes redundant. Every test file has one owner in `tests/owners.py`
  (`tests/test_the_test_map.py`).
- **A `low` finding is fixed or recorded as by design**, with its reason in DECISIONS.md or the domain's document and the
  row closed `decided:`; there is no third state, no list of accepted debt (owner, 2026-10-15). A finding is verified
  against the trunk, and against a live count where it names one, before it is reported or briefed.

## Before committing

- One concern per commit. `git add -A` has lumped two together twice; stage by path.
- Every fix gets a test, **run against the old code first** to prove it fails.
- Bulk operations on photos or databases: dry-run by default, `--apply` to write, and
  back up first.
- Re-read the diff. The spec tests fail on undocumented routes and schema drift.

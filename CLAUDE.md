# Working on TagPup

Short on purpose. Every rule here is one that has actually cost time on this project.
The reasoning lives in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md); this is what to do.

**New code goes where [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) puts it.** Today two servers,
a CLI and many scripts each implement parts of the photo library. The code is moving
into one `tagpup/` package, with one owner for each concern. Check the phase table
before adding a module, a route or a query. **Record every review finding in
[docs/findings.md](docs/findings.md) before fixing it.** Findings that lived only in a
conversation were lost, and decisions were made twice.

New code imports from the package (`from tagpup.store import db`). `scripts/` holds
programs to run, each with a `__main__` block, and the few helpers they share (`_root`,
`code_snapshot`, `reloader`); nothing but a script's own tests imports from it. A module
that moves takes every importer with it in the same commit and leaves nothing at its old
name. `tests/test_scripts_are_entry_points.py` fails a module in `scripts/` that is
neither, and `tests/test_layers.py` fails package code that imports from `scripts/` or
goes up a layer.

## Running things

```
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/frontend/*.test.mjs
.venv/Scripts/python.exe -m ruff check .
```

The linter runs inside the Python suite (`tests/test_lint.py`), so a finding fails the
build. `ruff.toml` selects rules that catch defects, not style; if one is wrong for
this codebase, add it there with a comment saying why rather than working around it.

No `pip.exe`, no `Activate.ps1`, no pytest — invoke the interpreter by path. Use the
glob for the frontend suite; `node --test tests/frontend/` fails on `harness.mjs`.

**The whole Python suite is `tools/run_tests.py`: 3,930 tests, about 4.5 minutes (200-600 s
under load, counted 2026-10-04).** Run all of it
before a commit that touches Python; choosing which files to run cost more turns than
it saved. `tools/affected_tests.py` is for the loop while editing, not for the commit.

**The owner runs the apps from an installed copy** (`%LOCALAPPDATA%\TagPup\*.cmd`,
made by `scripts/install_app.py`), with `TAGPUP_HOME` set to the repository. Saving a
file here changes nothing they are running, and a merge reaches them only when the
app is installed again: each launcher installs a newer clean commit, then replaces a
server of another version still running once it has finished its work
(`tagpup.launcher`; a server started before that existed has to be closed once by
hand). Ask before installing; offer to after a merge they want to use. An app started from the repository itself still restarts whenever a `.py` is
saved, wiping its in-memory state and orphaning any indexer, so check for one before
editing. Run long indexes through the CLI (`TagPup CLI.cmd`).

## Rules that keep being broken

**Never call `sqlite3.connect`.** Use `tagpup/store/db.py`. It owns journal mode, busy
timeout, the per-file write lock and the retry. `tests/test_db_access.py` enforces it.

**Never call `subprocess` yourself.** `tagpup/core/processes.py` owns `start`, `run`,
`kill_tree` and `is_alive`, and gives every child a hidden console. A spawn that decided
for itself put a terminal window on the desktop for every test process, 140 a run, and
it took watching the process list to find which one. `tests/test_processes_single_owner.py`
enforces it.

**Never construct `ExifToolHelper` or `ExifTool` directly.** Use `ExifToolSession` from
`tagpup/files/exiftool_session.py`. pyexiftool reads stdout to the end before stderr; a batch
with ~4 KB of warnings fills the stderr pipe and both sides wait forever -- two scripts
sat at 0% CPU for two days. The session drains both and gives each command a deadline.
`tests/test_exiftool_single_owner.py` enforces it.

**Never load a model on the GPU yourself.** `tagpup/ml/gpu.py` owns the graphics card: one
process at a time on the machine has a model on it, in turn, and a waiter says who has it
and since when. Take a turn through `tagpup.runtime` (`begin` for a Suggest run, `gpu_turn`
for the indexer). The server's Suggest and the indexer it had started each loaded a ViT-H-14
onto one 10 GB card and both sat at 0 of 165 for 48 minutes (#750).
`tests/test_gpu_single_owner.py` enforces it.

**Never spell or compare a photo path by hand.** `tagpup/core/paths.py` owns it: `stored()`
for anything written to the database, walked or sent to the browser; `key()` for
in-memory comparison; `sql_equals()` / `sql_under()` for SQL. A helper that turned
`D:\x` into `D:/x` made tag writes, renames and deletes match no row for three
months while reporting success. In the pages, `pathKey` / `samePath` (`web/common/paths.js`).
`tests/test_paths_single_owner.py` and `tests/frontend/path-helpers.test.mjs` enforce it.

**A library's settings live in the library.** The CLIP model, face detection, Suggest's
words, the rename format and ExifTool's path are each library's own (`settings`, read
through `tagpup.services.settings`, changed only by a journaled change). An entry point
reads them from the library it was given (`tagpup.runtime.library_settings`); nothing
reads `config.ini` but the one-time stamping of a library that has none. `tagpup/config.py`
owns only where the libraries are (`TAGPUP_HOME/data`) and where ExifTool is found by
default. 26 places once read `config.ini` and disagreed on four things. A test that
selects or creates a library runs with a `TAGPUP_HOME` of its own.
`tests/test_config_single_owner.py` enforces it.

**SQL on this library is not SQL on a test fixture.** 225,000 faces, most carrying a
6 KB crop, and every one of these has shipped:
- `LIKE` is not equality. It ignores case and reads `_` and `%` as wildcards, so
  `IMG_0001.jpg` also matches `IMGX0001.jpg` -- in an `UPDATE`, other photos change.
- A function on a column (`LOWER(path) = ?`) cannot use its index. Neither can a
  comparison whose collation differs from the index's.
- Never select a BLOB you do not use. `SELECT *` on `faces` is ten seconds cold.
- A query in a per-item loop that answers the same thing each time belongs outside it.
- Check `EXPLAIN QUERY PLAN` against `data/photo_index.db` (read-only, via
  `db.readonly_uri`) for anything that touches `faces` or `photos`.
- Look at real rows before writing a lookup. A fixture seeded through the helper under
  test agrees with the helper, not with the data.

**Never convert between a person's name and their tag by hand.** Identity is a leaf
(`Rowan Thackeray`); the tag is a path (`People/Rowan Thackeray`). Use `leafOf`,
`rootOf`, `samePerson`, `photoAlreadyHas` in `web/common/vocabulary.js`. In Python,
`tagpup.core.vocabulary` takes a tag apart and `taxonomy.find_person_path()` finds where
a name is filed. `tests/frontend/tag-vocabulary.test.mjs` and `tests/test_vocabulary.py`
enforce it.

**A test's data is made by the code that makes the real thing.** A photo row is what
the indexer records of a read (`tests/photo_rows.py`: `add_read`), or what Suggest makes
of a photo it never read (`add_unread`); a record the page sends is built as the page
builds it. Two regressions reached the owner in one day (#246, #247) because a fixture
held a shape nothing real makes -- a year only in raw metadata, grouped field names
without their bare copies -- and the tests agreed with the bug.

**A bulk write must tell the index what it wrote.** Saving one photo always did;
the bulk paths did not, and rows described what photos used to hold.
`record_tags_in_index()`.

**A page's `main.js` only wires it, and starts it at the very end.** Each feature is a
module in `web/tagpup/` or `web/tuner/`. The page's mutable state is `state` in its
`state.js`, never a module's `let`, since a module cannot reassign what it imports. A call
back up the import order goes through `upper` (`hooks.js`), which `main.js` fills in.

**A page's request goes through `web/common/api.js`.** `api.json`, `api.fetch`,
`api.image` and `api.url` put the library from the page's URL in front of `/api/`; a bare
`fetch` reaches no library. The pages are ES modules with no build step; the page tests
load a page's modules into jsdom in import order, each in a scope of its own
(`tests/frontend/harness.mjs`), and refuse an import cycle or a name used without its
import.

**Write patch scripts with a file tool, not a shell heredoc.** Backslashes and `\u`
escapes are mangled in transit. This cost time five times in two days, so it is no
longer a rule to remember: `.claude/hooks/no-heredoc-escapes.mjs` (a PreToolUse hook in
`.claude/settings.json`) refuses an interpreter heredoc containing an escape.

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

## Working in few turns

- Edit through `tools/patch.py` from a script written with the Write tool: exact,
  counted, newline-preserving, all-or-nothing. One script per concern that patches,
  lints and runs the tests, not one tool call per step.
- Before briefing a new tool, script or module, find the existing owner: grep
  `tagpup/services/`, the MCP server's tools and `scripts/`. A worker asked for a new
  repair script had half of it built before `refresh_rows` turned out to own the job.
- A worker's brief names how the work fails, not only what it does: interrupted part-way,
  two at once (threads, processes, two libraries sharing a folder), a read that fails, the
  real data's shape, what the owner sees. `.claude/agents/tagpup-worker.md` holds the list;
  the brief adds what is particular to the task. Most review rounds were one of these, missed.
- A one-way choice -- a stamp or migration on the live libraries, anything automatic on the
  owner's data, a new meaning for an existing idea -- is the owner's or the main session's,
  asked before it is built. Two were built and thrown away in one day.
- Test the scenario the owner works in: rapid clicks, switching library mid-folder, two
  libraries holding one folder, an update while working. Both bugs that reached the owner
  on 2026-09-28 lived between features each tested alone.
- The right agent for the size: `tagpup-worker-small` (cheaper model) for a bounded fix,
  `tagpup-worker` for design and anything touching sync, the journal, the supervisor or
  writes; `tagpup-reviewer` for a branch's first review, `tagpup-quick-reviewer` for the
  follow-up on fixes it asked for. A one-file fix is often cheapest done directly.
- A number told to the owner about their libraries is one counted this session, not relayed.
- One session per phase: `/clear` at a phase boundary (the memory handoff carries the state),
  `/compact` inside a long one. Most of the cost was context re-read, not work.
- Findings are numbered with `tools/add_findings.py`, from a file of `| ? |` rows.
- Delegate multi-file work to the `tagpup-worker` agent in a worktree; its definition
  holds the standing rules, so the brief says only the task and which files are its.
- After any change more than one agent made, run `tagpup-reviewer` over it before
  committing. It found the defects that lived between two agents' briefs.
- A review loop on one symptom ends at the second round, with the ownership question: the
  9d-1 resume ran six rounds, each finding a deeper way to shift a photo twice, and the
  fix that ended it (the journal's prune never takes a resumable job's changes) was the
  owner-of-the-rule fix nobody asked for until round six. Put "which single place should
  own this?" in the second round's brief. Rounds after that are for findings that can
  change a file or lose data; wording and polish are batched, not re-reviewed.
- Commit before launching background agents and keep their work in worktrees. Two
  restarts on 2026-10-03/04 killed five agents; the uncommitted work survived only
  because it sat in a worktree.
- Decide by the rules here and in docs/ARCHITECTURE.md; ask the owner only what they
  alone can answer. The cheapest correct change first: no extra backup, no workaround
  around the model where the model can be fixed.

## Performance work

Read this before claiming anything got faster. Every line of it was paid for by
shipping a "43s → 0.19s" result for a screen that was exactly as slow as before.

**A performance claim is a claim about a user action.** The unit is the thing the
person described — "ignoring a cluster is slow" means click Ignore Cluster, wait until
the app is usable. Not an endpoint, not a query, not a function. If the report names a
click, the result names the same click or there is no result.

**Measure the scenario, not your hypothesis.** The failure was measuring
`POST /exclude` then `GET /person-matches` — a pair of calls the app never makes,
invented because the suspect was the server. It made the hypothesis look right while
the real cost, a full DOM rebuild in the browser, was never in the number at all. Ask
what the app actually does on that click, in order, and time exactly that.

**Baseline first, with the harness that will report the result.** Reproduce the
reported slowness before changing a line. A harness that cannot reproduce it is not
measuring the reported thing, and every conclusion drawn from it afterwards is about
something else. If the baseline comes out fast, the harness is wrong — not the report.

**The browser is half the system.** Server timings are a diagnosis, never a result.
Rendering tens of thousands of cards, clearing tens of thousands of `img.src`, and
rebuilding the DOM cost seconds, and no server timing can see any of it.
`scripts/measure_identify_faces.py` drives the real app in a real browser; use it, or
write its equivalent for the screen in question.

**Usable, not merely returned.** A page that removes the cards and *then* blocks for
ten seconds rebuilding passes a "are the cards gone" check. Wait for the main thread
as well.

**Validate in a sandbox; never in the app somebody is using.** Copy the library, copy
the code, give it its own `TAGPUP_HOME` and a free port, and run it as a separate
process. All four, not some of them — a copy on a different port that still lives in
`data/` shows up in the database picker of the app they have open, and code run from
the repo shares the reloader with their server. `scripts/measure_identify_faces.py`
builds that sandbox and deletes it afterwards; measurement runs should leave nothing
behind and nothing changed.

**Delete what you created, and say so if you cannot.** The first version of that script
used `rmtree(..., ignore_errors=True)` and quietly left 2.7 GB in the temp directory on
every run, because Windows had not released the server's file handles yet. Retry, then
report.

**Don't edit `.py` while somebody is testing from the repository.** The reloader
restarts the server and wipes every in-memory cache, so their run and yours are both
measuring a cold start. Say when you are about to, or wait. (The installed apps are not
affected.)

## Before committing

- One concern per commit. `git add -A` has lumped two together twice; stage by path.
- Every fix gets a test, **run against the old code first** to prove it fails.
- Bulk operations on photos or databases: dry-run by default, `--apply` to write, and
  back up first.
- Re-read the diff. The spec tests fail on undocumented routes and schema drift.

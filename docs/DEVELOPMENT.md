# Working on TagPup

---
[◀ Back to README](../README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

Notes for changing this codebase: how to run things, how the tests work, and the traps
that have cost real time. The specs describe what the system does; this describes what
it is like to work on. [ARCHITECTURE.md](ARCHITECTURE.md) describes where the code is
going, and [findings.md](findings.md) tracks known problems.

## Running things

The virtualenv has `python.exe` but **no `pip.exe` and no `Activate.ps1`**. Invoke the
interpreter by path:

```
.venv/Scripts/python.exe -m pip install ...
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
.venv/Scripts/python.exe -u tagpup_cli.py --db kr-track.db index "D:\path\to\folder"
```

The suite is `unittest`; **pytest is not installed**.

**The servers auto-reload on `.py` changes.** Saving any Python file restarts a running
TagPup or TagTuner, which destroys every piece of in-memory state — the folder queue,
`index_status`, the identify cache — and can leave an indexer subprocess running with
nothing watching it. Check for a running server before editing, and run long indexes
through the CLI where no reloader can reach them.

**Run the apps from an installed copy** to stop that. `scripts/install_app.py` (a dry
run; `--apply` to install) copies the code into `%LOCALAPPDATA%\TagPup\versions\<when>-<commit>`
and writes `TagPup.cmd`, `TagTuner.cmd` and `TagPup CLI.cmd` beside
it. They run that copy with `TAGPUP_HOME` set to the checkout, so `data/` -- the
libraries, which hold their own settings -- stays where it is. Saving a file in the repository changes nothing they are
running. To update, install again; the two versions before stay, and `current.txt`
names the one the launchers start.

**Switching a library's photo model** (phase 5; docs/SPEC_TAGPUP_CLI.md, `embed-photos` and `set-photo-model`),
through `TagPup CLI.cmd`, with `--db` naming the library, one library at a time:

1. Back up first: `snapshots list` shows a daily from today. The running app takes one each day (the scheduled job
   `snapshots`); with none from today, run that job now from the Activity page. The CLI has no command to take one.
2. `embed-photos --model ViT-SO400M-14-SigLIP2:webli`: the dry run counts the photos to embed, those with no file, and
   the time (about 40 minutes for photo_index). Then the same with `--apply`, logged (redirect it). Ctrl+C stops it
   after the photo under way; run it again to make the rest. Repeat until it embeds nothing new.
3. `set-photo-model ViT-SO400M-14-SigLIP2:webli`: the dry run says the gate's counts and whether the switch passes, and
   how many words Suggest will make again. Then with `--apply`: note the change id it prints; `undo <id> --apply`
   switches back.
4. `.venv/Scripts/python.exe tools/doctor.py --db <library>`: the library's checks after the switch.
5. Once the owner is content with the new model (the switch can be undone until now): `retire-photo-vectors`, the dry
   run naming the own key, the photos holding it, and the old key's vectors and words that go. Then with `--apply`, the
   apps closed: a snapshot first (the way back; the change's undo and the switch back are refused after it). Then
   `compact`, with the apps closed, gives the space back.

The old model's vectors stay beside the new until step 5; nothing reads them after the switch.

**Logs are in `data/logs/`**: `tagpup.log` and `tagtuner.log`, one per
program, rotating at 5 MB and keeping five old files. They hold everything the console
shows, plus every request slower than a second (`slow: GET /api/... took 2.31s`) and
every request that failed, with its traceback. Look there before reproducing a report.
The CLI still logs to the console only; redirect it when it matters.

**Time a click, in a sandbox.** `scripts/measure_identify_faces.py` copies a library
(SQLite's backup API; the original opened read-only), snapshots the code, runs TagTuner
on it as its own process with the sandbox as `TAGPUP_HOME` on a free port, drives the
Identify grid in headless Chromium, and deletes the sandbox afterwards, saying so if it
cannot. It times Ignore Cluster by default and New Person with `--action new-person`,
each from the click until the page is usable again. Take the baseline with it before a
change and the result with it after ("Performance work", below).

**The suite's tiers.** `tools/run_tests.py --fast` runs only the fast tier (`tests/tiers.py`): scenario tests, which run
a whole job, a crash or two writers at once, and slow ones, the hand-over and the 68,000-photo library, are left out.
The default is every tier, and that is the commit gate: about 3,600 tests, about 5 minutes (350-700 s under load). A
file that fails is run once more alone: if it passes, the report says "flaky: passed alone" and keeps the first output;
if it fails twice it fails. The main session runs the whole suite once on the merged trunk and never beside another
suite run (two or three at once on one machine tripped load-sensitive tests); a break it finds goes back to the worker
with the file names, and a change made after a review runs only the files it touched.

**An install hands over to the running server.** Each launcher installs a newer clean commit, then replaces a server
of another version still running once it has finished its work; `scripts/install_app.py --apply` does the same itself:
it waits for the running server's work, ends it and starts the new version on the same ports with no window, so an
open page shows the "updated" banner (`tagpup.launcher.hand_over`). `--no-restart` only installs; it refuses a `--home`
with no library.

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


## Tests

| suite | command | count |
| --- | --- | --- |
| Python | `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` | 3,603 (the loader's count, 2026-10-16) |
| Python, across the cores | `.venv/Scripts/python.exe tools/run_tests.py` | the same |
| Python, what a change affects | `.venv/Scripts/python.exe tools/affected_tests.py --run` | a subset |
| Frontend | `node --test tests/frontend/*.test.mjs` | 1,586 (2026-10-16) |
| Lint | `.venv/Scripts/python.exe -m ruff check .` | runs inside the Python suite |

`tools/affected_tests.py` picks the test files that import what changed since the last
commit (`--since` for another point), directly or through other modules, or name a
changed file; use it while working. A change to a core module (`paths`, `db`, `schema`)
reaches most of the suite anyway. The full suite still runs before each commit and each
merge: what a test reaches through a subprocess or a file named at run time is not an
import.

`tools/run_tests.py` runs each test file as its own process across half the cores, each
with a `TAGPUP_HOME` of its own. A file that named the checkout's own `data/` or
`config.ini` -- or imported a test file that did -- would run in a lane of its own, one
after another; none does since the tests moved into homes of their own
(findings.md, #14), and `tests/test_tests_have_homes_of_their_own.py` keeps it so.
A failed run's output is also kept in `data/logs/run_tests-<time>.log` (the last ten),
and the run names the file; a file that exits 0 having run no tests fails (#101).

Use the glob for the frontend suite. `node --test tests/frontend/` treats `harness.mjs`
as a test file, finds no tests in it, and reports a failure that is purely the
invocation.

### The frontend harness

Each page is ES modules: `web/<page>/index.html` starts `main.js`, whose main closure
runs on `DOMContentLoaded`, and it imports the modules both pages share from
`web/common/` (as `./common/<name>.js`, relative to the page, so the server serves them
under the library's URL). `tests/frontend/harness.mjs` loads the real `index.html` into
jsdom and evaluates the page's modules in its window as one strict script: in the order
the browser runs them, import lines dropped and `export` words taken off
(`pageModules`, `pageScript`). jsdom cannot load modules, and modules run under node
would keep their timers on node's clock, which closing the window cannot stop. The
harness stubs `fetch` and drives the app through DOM events. What is under test is the
shipped files.

Because a page's modules share one scope there, the loader refuses what a browser would
treat differently: a name declared at the top of two of a page's modules, a renamed,
default or namespace import, an import of something not exported, and a name used from
another module without importing it (`tests/frontend/page-modules.test.mjs`). The
shared modules hold no DOM state, and node tests import them directly
(`tests/frontend/common-modules.test.mjs`, `path-helpers`, `tag-rules`).

Two jsdom gaps are shimmed there, both of which have masqueraded as application bugs:

- **`CSS.escape`** does not exist. Without it `selectPhoto` throws before it can open
  the details panel.
- **`scrollIntoView`** does not exist. Unshimmed it throws, and where that call sits
  inside a promise chain the rejection is swallowed by an outer `.catch` and surfaces
  as "Error loading people". That one cost an hour.

When a frontend test fails in a way that makes no sense, check whether jsdom implements
the API before suspecting the app.

### Guards worth knowing about

Some tests exist to stop a whole class of mistake rather than to cover a feature:

- `tests/test_spec_accuracy.py` fails when a route exists in the code but not in the
  spec. Add the endpoint to the spec in the same commit.
- `tests/frontend/dom-contract.test.mjs` fails when a script looks up an element id the
  markup does not define, and when a retired concept (`Unmatched`) returns.
- `tests/test_path_locker.py` covers locks left behind by a killed process.
- `tests/test_keyword_fields.py` covers a tag being written whole rather than
  scattered into its segments.
- `tests/frontend/person-tag-form.test.mjs` covers a person being written as the tag
  they are filed under. Face recognition and the suggester both speak in leaf names, so
  every path that accepts one has to resolve it before writing; the ones that did not
  added a person a second time, bare, beside the `People/<name>` already there.
- `tests/frontend/tag-vocabulary.test.mjs` fails on a raw `.split('/')` in a page's
  modules outside the helpers, and on a closure-level `let` in `web/tagpup/main.js`
  declared after the startup call but read above it. See the next section for why
  both exist.
- `tests/frontend/common-modules.test.mjs` fails when a page's module declares its own
  copy of a helper `web/common/` exports, which would quietly shadow the import.
- `tests/frontend/database-routing.test.mjs` fails on a `fetch(` or an `/api/` image
  `src` in a page that does not go through `web/common/api.js`.
- The single-owner guards (`test_db_access`, `test_rules_have_one_owner`,
  `test_config_single_owner`) all check one list of files,
  `tests/shipped_sources.py`: the launchers, `scripts/` and `tagpup/`. When each kept
  its own list, the database guard skipped `runner.py`, which opened its own connection.
- `tests/test_layers.py` fails package code whose imports go up a layer
  ([ARCHITECTURE.md](ARCHITECTURE.md)), or that imports an old `scripts/` module by
  name. It also fails a `scripts/` module that imports `tagpup` without importing
  `_root` first.
- `tests/test_sandbox_has_all_the_code.py` builds the measurement tools' sandbox and
  imports both servers inside it, in an interpreter that cannot see the repository.
  Moving code into `tagpup/` once broke that sandbox without any test noticing.

A test that makes a library, or needs settings, logs or anything else a library keeps
beside it, takes a home of its own from `tests/own_home.py`: `own_home.for_class(cls)`
in `setUpClass`, `own_home.for_test(self)` in a test, then `home.library("x.db")`.
Without one, the servers create libraries in the checkout's `data/`, where the owner's
are, and read the checkout's `data/`. A test that shows an old `config.ini` is ignored
writes it with `home.write_old_config({...})`. A server a test starts holds its library
until the process ends, so a home it still holds is deleted once the process has gone.

## Traps

**Trailing whitespace breaks exact-match patching.** `web/tagpup/main.js` and others
carry trailing spaces on many lines, so a quoted block retyped from a diff will not
match. Match with trailing whitespace tolerated, or locate the block by its boundaries.

**Backslashes and unicode escapes do not survive a shell heredoc.** `"D:\\x"` and
`\u2014` written into a patch script through bash come out mangled, producing invalid
JavaScript escapes or a literal em-dash where the source has `\u2014`. Write patch
scripts with a file-writing tool rather than a heredoc, or locate lines by index.

**Abort a patch script before it writes and nothing is written.** Several multi-part
patch scripts failed on their last assertion, silently discarding the earlier edits that
had already succeeded in memory. If a patch reports a failure, re-check every part of it,
not only the part that failed.

**Never convert between a person's name and their tag by hand.** A person has two
shapes and they are not interchangeable: their identity is a leaf (`Hazel Brookmire`),
which is what the faces table, the suggester and `photo.people` speak in, and their tag
is a path (`People/Hazel Brookmire`), which is what the keywords must hold and what the
server matches on, exactly. Use `leafOf`, `rootOf`, `samePerson` and `photoAlreadyHas`
in `web/common/vocabulary.js`, `preferPathed` in `web/tagpup/tags.js`,
and `taxonomy.person_paths()` in Python.

Every bug in this area was a site doing the conversion itself, and none of them looked
related: clicking a recognised face added the person a second time in the bare form;
the `×` on a selection chip removed nothing while still rewriting every selected file;
a suggested `Activity/Cross Country` was written as `Cross Country`.

**`web/tagpup/main.js` starts itself at the very end, and must stay that way.** The
`?path=` startup block calls into most of the app. Run from anywhere but the bottom of
the closure it can reach a `let` declared further down, and reaching a `let` early does
not give you `undefined` — it throws, which takes the rest of the closure's body with
it. Every binding below that point is then permanently uninitialised, so **the error a
user sees names an unrelated line**.

That is not hypothetical. `checkIndexingStatus` touched `indexProgressTimer`, declared
2,300 lines below; the throw left `facesRequestToken` uninitialised too, and the
visible failure was `Error scanning folder: Cannot access 'facesRequestToken' before
initialization` — two removes from the line at fault, and it survived one fix that
addressed only the symptom. `tests/frontend/tag-vocabulary.test.mjs` enforces the
position.

**Never call `sqlite3.connect` directly — use `tagpup/store/db.py`.** This program is always
a reader and a writer at once, from many threads: both servers handle each request on
its own thread, the suggester runs a thread pool, and indexing runs in a background
thread beside all of it. `db.py` owns the journal mode, the busy timeout, the per-file
write lock and the busy retry. `tests/test_db_access.py` fails if a module connects on
its own, because such a connection gets none of it.

This was learned the slow way: `database is locked` was fixed three times in one
afternoon, appearing from a different write path each time — recording faces, then tag
embeddings, then the embedding cache — because each site had its own settings. See
[DATABASE.md](DATABASE.md) for why WAL alone does not fix it.

**Indexing writes to photos that have no identity.** A path is a bad name for a photo —
rename it and the index row describes something that no longer exists, while the photo
looks unindexed. `tagpup/files/identity.py` reads `XMP-xmpMM:DocumentID`, which most photos
already carry (1,075 of 1,129 sampled here), and mints `xmp.did:<uuid>` into the few
that do not. That means a normal index pass *writes* to some files: only the indexer
does, through `IdentityWriter` (`tagpup/files/metadata.py`), and only into a photo whose
picture has decoded in full and whose metadata ExifTool read without an error -- a
truncated file took an identity, which changed its modified time, which queued it for
indexing again (#407). `MetadataExtractor` only reads. A photo given an identity keeps
the stamp from before its read, and the index reads the photo again before recording it
(`tagpup_cli._read_again`), so its row holds the file as the write left it and the next
pass finds it current (#1599).

**ExifTool treats an empty list as "no change".** Clearing a photo's last keyword has to
be an explicit `-TAG=` deletion, which is what `write_keyword_fields()` does. Assigning
`[]` silently leaves the old keywords in place.

**ExifTool exits non-zero if any file in a batch is unreadable**, and pyexiftool raises
on that status. A failed batch is re-read one file at a time so that one bad file costs
only itself; it once cost 500 files their metadata.

## Maintenance scripts

All of these are dry-run by default and take `--apply` to write.

Three of them are thin entry points on one scaffold, `tagpup.services.maintenance`: the
operation plans (reads only), and its dry run rehearses the change -- applies and undoes
it inside a transaction that is rolled back, and says whether the undo restored every
row exactly. With `--apply` the scaffold records the change in the library's journal
(`tagpup.store.journal`; the `changes` and `change_rows` tables, DATABASE.md) instead of
copying the library: applied only where every row is still what the plan read, else
refused whole, naming the rows. It returns a `Result` whose `changed` is what the write
changed, read from the database, not what was planned, and whose `details["change"]`
is the change's id. `tagpup_cli.py history` lists the changes, `undo <id>` rehearses
taking one back and `undo <id> --apply` does. The MCP server (`tagpup.mcp`) calls the same
services as write tools, and has `history` and `undo` too, each a dry run unless called with
`apply=true`, showing paths and names only with `reveal=true`.

The two scripts not on the scaffold still back the database up first through
`db.backup()`: SQLite's backup API, into `backups/` beside the library
(`data/backups/` for the libraries in `data/`). Each library keeps its newest five
(`db.KEEP_BACKUPS`), none expires by age; making a sixth deletes the oldest. `tests/test_bulk_scripts_back_up.py`
lists every place that may still copy a whole library, and why.

| script | what it does |
| --- | --- |
| `scripts/verify_workflow.py` | End-to-end pass over both apps against a throwaway copy of a database. |

Retired on 2026-09-24 with nothing left to do on either library, and in git's history: `canonicalize_paths.py`, `repair_bare_person_tags.py`, `tidy_exclusion_reasons.py`, `restore_face_names.py` and `cache_all_crops.py` (findings.md, #54).

## Things that were true and are worth not re-learning

- **Re-indexing does not destroy curation.** `save_faces_batch(overwrite=False)` skips
  photos that already have faces, so manual names and exclusions survive. This has been
  verified across three full runs of the main gallery.
- **A candidate list is keyword-driven.** A face is offered under a name because its
  photo's keywords mention that name — not because it resembles anyone. This surprises
  everybody who meets it, including the person who built it.
- **Unknown Faces is mostly photos with no people keywords at all.** Of 6,393 unnamed
  faces with no name to file them under, 5,464 were in photos naming nobody.

## The incidents behind CLAUDE.md's working rules

CLAUDE.md keeps each rule as a line; the story of a rule whose incident is not told elsewhere (docs/INVARIANTS.md, the
closed findings in docs/history/, docs/retros/) is here.

- **Find the existing owner before briefing a new tool.** A worker asked for a new repair script had half of it built
  before `refresh_rows` turned out to own the job.
- **Test the scenario the owner works in.** Both bugs that reached the owner on 2026-09-28 lived between features each
  tested alone.
- **A one-way choice is asked first.** Two were built, reviewed and thrown away in one day (automatic roots; a row for
  every photo of an added folder).
- **The review loop ends at the second round.** The 9d-1 resume ran six rounds, each finding a deeper way to shift a
  photo twice; the fix that ended it (the journal keeps every change a job may still need) was the owner-of-the-rule fix
  nobody asked for until round six. Wording and polish are batched, not re-reviewed.
- **Commit before launching background agents.** Two restarts on 2026-10-03/04 killed five agents; the uncommitted work
  survived only because it sat in a worktree.
- **One session per phase.** Most of the cost was context re-read, not work (docs/retros/2026-10-16-freeze.md).

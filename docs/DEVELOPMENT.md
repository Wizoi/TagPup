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
and writes `TagPup.cmd`, `TagTuner.cmd`, `TagPup Runner.cmd` and `TagPup CLI.cmd` beside
it. They run that copy with `TAGPUP_HOME` set to the checkout, so `config.ini` and
`data/` stay where they are. Saving a file in the repository changes nothing they are
running. To update, install again; the two versions before stay, and `current.txt`
names the one the launchers start.

**Logs are in `data/logs/`**: `tagpup.log`, `tagtuner.log` and `runner.log`, one per
program, rotating at 5 MB and keeping five old files. They hold everything the console
shows, plus every request slower than a second (`slow: GET /api/... took 2.31s`) and
every request that failed, with its traceback. Look there before reproducing a report.
The CLI still logs to the console only; redirect it when it matters.

## Tests

| suite | command | count |
| --- | --- | --- |
| Python | `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` | ~1,300 |
| Python, across the cores | `.venv/Scripts/python.exe tools/run_tests.py` | the same |
| Python, what a change affects | `.venv/Scripts/python.exe tools/affected_tests.py --run` | a subset |
| Frontend | `node --test tests/frontend/*.test.mjs` | ~500 |
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
- `tests/test_person_tag_duplication.py` covers a tag being written whole rather than
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
- The single-owner guards (`test_db_access`, `test_exiftool_single_owner`,
  `test_paths_single_owner`, `test_config_single_owner`) all check one list of files,
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
Without one, the servers write the checkout's `config.ini` and create libraries in the
checkout's `data/`, where the owner's are. A server a test starts holds its library
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
in `web/common/vocabulary.js`, `ancestorsOf` and `preferPathed` in `web/tagpup/main.js`,
and `taxonomy.find_person_path()` in Python.

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
that do not. That means a normal index pass *writes* to some files, which is new: pass
`MetadataExtractor(mint_identities=False)` where that must not happen. A minted
identity also re-reads the file's mtime and size, or the next pass would see a file
modified since indexing and re-index it for a write the last pass made.

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
taking one back and `undo <id> --apply` does, and `prune-journal` lets changes older than
90 days go. The MCP server (`tagpup.mcp`) calls the same services as write tools, and
has `history`, `undo` and `prune_journal` too, each a dry run unless called with
`apply=true`, showing paths and names only with `reveal=true`.

The two scripts not on the scaffold still back the database up first through
`db.backup()`: SQLite's backup API, into `backups/` beside the library
(`data/backups/` for the libraries in `data/`). Each library keeps its newest five
(`db.KEEP_BACKUPS`); making a sixth deletes the oldest. `tests/test_bulk_scripts_back_up.py`
lists every place that may still copy a whole library, and why.

| script | what it does |
| --- | --- |
| `scripts/verify_workflow.py` | End-to-end pass over both apps against a throwaway copy of a database. |
| `scripts/refresh_rows_from_files.py` | Re-reads the photos whose rows no longer describe their files, and fixes rows that list a caption twice. `tagpup.services.refresh_rows`; MCP tool `refresh_rows`. Planning reads the files, so a dry run is slow. |
| `scripts/merge_duplicate_person_tags.py` | Removes a bare person tag where a `People/<name>` already names them. `tagpup.services.person_tags`; MCP tool `merge_duplicate_person_tags`. |
| `scripts/dedupe_faces.py` | Removes face rows that copy another face of the same photo and know no more than it; disputed copies are left for a person. `tagpup.services.duplicate_faces`; MCP tool `dedupe_faces`. |
| `scripts/backfill_document_ids.py` | Gives already-indexed photos the identity new ones get. Resumable. |
| `scripts/relink_renamed_photos.py` | Re-points index rows at photos renamed under them, by identity then `PreservedFileName`. |

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

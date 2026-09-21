# Working on TagPup

---
[◀ Back to README](README.md) | [📖 Tutorial](TUTORIAL.md) | [💡 CLI Examples](EXAMPLE.md) | [🖥️ TagPup GUI Spec](SPEC_TAGPUP_GUI.md) | [🎯 TagTuner UI Spec](SPEC_TAGTUNER.md) | [🐶 CLI Engine Spec](SPEC_TAGPUP_CLI.md) | [🗄️ Database Spec](DATABASE.md)
---

Notes for changing this codebase: how to run things, how the tests work, and the traps
that have cost real time. The specs describe what the system does; this describes what
it is like to work on.

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

## Tests

| suite | command | count |
| --- | --- | --- |
| Python | `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` | ~494 |
| Frontend | `node --test tests/frontend/*.test.mjs` | ~237 |

Use the glob for the frontend suite. `node --test tests/frontend/` treats `harness.mjs`
as a test file, finds no tests in it, and reports a failure that is purely the
invocation.

### The frontend harness

Both apps are a single `DOMContentLoaded` closure with no exports, so nothing inside
them can be imported. `tests/frontend/harness.mjs` loads the real `index.html` and
`app.js` into jsdom, stubs `fetch`, and drives the app through DOM events. What is under
test is the shipped file.

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
  markup does not define, and when retired concepts (`Unmatched`, `Non Person`) return.
- `tests/test_path_locker.py` covers locks left behind by a killed process.
- `tests/test_person_tag_duplication.py` covers a tag being written whole rather than
  scattered into its segments.
- `tests/frontend/person-tag-form.test.mjs` covers a person being written as the tag
  they are filed under. Face recognition and the suggester both speak in leaf names, so
  every path that accepts one has to resolve it before writing; the ones that did not
  added a person a second time, bare, beside the `People/<name>` already there.
- `tests/frontend/tag-vocabulary.test.mjs` fails on a raw `.split('/')` in
  `gui_tagpup/app.js` outside the helper block, and on a closure-level `let` declared
  after the startup call but read above it. See the next section for why both exist.

## Traps

**Trailing whitespace breaks exact-match patching.** `gui_tagpup/app.js` and others
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
server matches on, exactly. Use `leafOf`, `rootOf`, `ancestorsOf`, `samePerson`,
`preferPathed` and `photoAlreadyHas` in `gui_tagpup/app.js`, and
`taxonomy.find_person_path()` in Python.

Every bug in this area was a site doing the conversion itself, and none of them looked
related: clicking a recognised face added the person a second time in the bare form;
the `×` on a selection chip removed nothing while still rewriting every selected file;
a suggested `Activity/Cross Country` was written as `Cross Country`.

**The app starts itself from the middle of `app.js`.** A `?path=` in the URL calls
`scanFolder()` around line 459, so any closure-level `let` or `const` declared below
that and read above it can be reached before its declaration runs — which throws
`Cannot access X before initialization`, and inside a promise chain surfaces as
something unrelated ("Error scanning folder"). Declare state at the top with the rest.

**Never call `sqlite3.connect` directly — use `scripts/db.py`.** This program is always
a reader and a writer at once, from many threads: both servers handle each request on
its own thread, the suggester runs a thread pool, and indexing runs in a background
thread beside all of it. `db.py` owns the journal mode, the busy timeout, the per-file
write lock and the busy retry. `tests/test_db_access.py` fails if a module connects on
its own, because such a connection gets none of it.

This was learned the slow way: `database is locked` was fixed three times in one
afternoon, appearing from a different write path each time — recording faces, then tag
embeddings, then the embedding cache — because each site had its own settings. See
[DATABASE.md](DATABASE.md) for why WAL alone does not fix it.

**ExifTool treats an empty list as "no change".** Clearing a photo's last keyword has to
be an explicit `-TAG=` deletion, which is what `write_keyword_fields()` does. Assigning
`[]` silently leaves the old keywords in place.

**ExifTool exits non-zero if any file in a batch is unreadable**, and pyexiftool raises
on that status. A failed batch is re-read one file at a time so that one bad file costs
only itself; it once cost 500 files their metadata.

## Maintenance scripts

All of these are dry-run by default and take `--apply` to write. Take a backup first —
`sqlite3.Connection.backup()` into `backups/` — for anything that touches a database.

| script | what it does |
| --- | --- |
| `scripts/verify_workflow.py` | End-to-end pass over both apps against a throwaway copy of a database. |
| `scripts/merge_duplicate_person_tags.py` | Removes a bare person tag where a `People/<name>` already names them. |
| `scripts/repair_bare_person_tags.py` | Rewrites a photo's keywords so a person carries their full path, not a bare leaf. |
| `scripts/restore_face_names.py` | Restores face names from a JSON snapshot. |

## Things that were true and are worth not re-learning

- **Re-indexing does not destroy curation.** `save_faces_batch(overwrite=False)` skips
  photos that already have faces, so manual names and exclusions survive. This has been
  verified across three full runs of the main gallery.
- **A candidate list is keyword-driven.** A face is offered under a name because its
  photo's keywords mention that name — not because it resembles anyone. This surprises
  everybody who meets it, including the person who built it.
- **Unknown Faces is mostly photos with no people keywords at all.** Of 6,393 unnamed
  faces with no name to file them under, 5,464 were in photos naming nobody.

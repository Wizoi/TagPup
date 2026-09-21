# Working on TagPup

Short on purpose. Every rule here is one that has actually cost time on this project.
The reasoning lives in [DEVELOPMENT.md](DEVELOPMENT.md); this is what to do.

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

**Saving any `.py` restarts a running server**, wiping its in-memory state and orphaning
any indexer. Check for one before editing; run long indexes through the CLI.

## Rules that keep being broken

**Never call `sqlite3.connect`.** Use `scripts/db.py`. It owns journal mode, busy
timeout, the per-file write lock and the retry. `tests/test_db_access.py` enforces it.

**Never convert between a person's name and their tag by hand.** Identity is a leaf
(`Rowan Thackeray`); the tag is a path (`People/Rowan Thackeray`). Use `leafOf`,
`rootOf`, `samePerson`, `photoAlreadyHas` in `gui_tagpup/app.js`, and
`taxonomy.find_person_path()` in Python. `tests/frontend/tag-vocabulary.test.mjs`
enforces it.

**A bulk write must tell the index what it wrote.** Saving one photo always did;
the bulk paths did not, and rows described what photos used to hold.
`record_tags_in_index()`.

**`gui_tagpup/app.js` starts itself at the very end.** Anything below the startup block
can be reached before its `let` runs, which throws and reports an unrelated line.

**Write patch scripts with a file tool, not a shell heredoc.** Backslashes and `\u`
escapes are mangled in transit. This has cost time twice in one day.

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

**Check the destination before moving data into it.** Re-pointing rows at paths that
already had rows created 233 duplicate faces.

**A write reports what it changed, not what it attempted.** A backfill read 60 photos,
reported 60 done, and wrote nothing; the paths did not match and nothing said so.

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

**Don't edit `.py` while somebody is testing.** The reloader restarts the server and
wipes every in-memory cache, so their run and yours are both measuring a cold start.
Say when you are about to, or wait.

## Before committing

- One concern per commit. `git add -A` has lumped two together twice; stage by path.
- Every fix gets a test, **run against the old code first** to prove it fails.
- Bulk operations on photos or databases: dry-run by default, `--apply` to write, and
  back up first.
- Re-read the diff. The spec tests fail on undocumented routes and schema drift.

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

## Before committing

- One concern per commit. `git add -A` has lumped two together twice; stage by path.
- Every fix gets a test, **run against the old code first** to prove it fails.
- Bulk operations on photos or databases: dry-run by default, `--apply` to write, and
  back up first.
- Re-read the diff. The spec tests fail on undocumented routes and schema drift.

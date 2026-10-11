# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-11 | low: `config.add_machine_root`, `Machine.add` and `store.roots.insert`/`delete` serve only the sandbox, the CLI's Machine and tests now (adoption is gone) but are kept: `roots.place_in_sandbox`, `scripts/sandbox.py`, `tagpup_cli.py` and 12 test files build libraries and maps through them, so switching them is not trivial. | open: the next roots deletion, with the sandbox |
| B-2 | 2026-10-11 | low: with Verify gone, an old `verify root <name>` run in a library's `job_runs` is only history (0 of those in the three live libraries, counted this task); one left `running` by a live process of an older version would be named "the verify root <name> job is running" by Change location's busy check until it ends. | left as is: no such run exists |
| B-3 | 2026-10-11 | low: the sample Change location shows is still `roots_verify.verify` (module name kept; `full`, `cancel`, `progress`, `mode` and `not_in_library` removed with the full-run job), and its `outside` / `native_inside` fields are still shown in the dry run's lines. | left as is: rename `roots_verify` to `roots_sample` with the next roots change |

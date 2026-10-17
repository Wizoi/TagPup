# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-17 | low: `store_faces.set_names` (tagpup/store/faces.py:405-419) reads the People tree that decides who is gone (`known = person_ids.read(conn)`) before any transaction is open (Python's implicit transaction opens at the first UPDATE), so a person merged away between that read and the first UPDATE is still "known": their face's guess is taken off and the planned person refused by the `EXISTS tag_taxonomy` guard, leaving the face unnamed, which the docstring and the test of a merge meanwhile say never happens (that test merges before set_names starts, so it never reaches the gap). Review of the #1582 fix. | fixed: set_names reads who is gone in its own write transaction |
| B-2 | 2026-10-17 | low: `identities.resolve` calls `step("left", left + len(unnamed), len(unnamed))` (tagpup/services/identities.py:764): the stage's `total` is a subset of `done`, not its denominator, unlike every other stage of `on_step`, where done/total is progress. Only the CLI hears it (tagpup_cli.py:1133-1138). Review of the #1582/#1583 fix. | fixed: the left stage gives two named counts; the CLI's sentence is unchanged |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-11 | low: decision made, not copied: History still lists the `roots adopt` change (and `roots repair-address`) each live library holds (counted: 3 adopt changes, 1 repair change in the three libraries); `journal.refusal` now answers their undo with a sentence ("the copy of the library taken before it is the way back") instead of running the removed conversion. Pinned by `tests/test_roots_unplaced.py` (TheHistoryOfTheRemovedCommands). | open |
| B-2 | 2026-10-11 | low: decision made, not copied: the test fixture `tests/roots_library.py` `convert_to_root` is now the only code that converts a library's paths under a root (a trimmed forward conversion: no refusals, no backup, no undo); the roots tests need a rooted library and nothing in the program makes one any more. If a new conversion is ever wanted it is a new feature, not this fixture. | open |
| B-3 | 2026-10-11 | low: left in place, now used only by tests and the sandbox: `store.roots.insert` / `delete`, `config.add_machine_root` and `Machine.add` (the sandbox's `place_in_sandbox` still uses the last two). Candidates for the next deletion once the sandbox no longer needs `add`. | open |
| B-4 | 2026-10-11 | low: the brief said to keep "the CLI Change location", but the CLI has no such command; Change location exists only as TagTuner's Roots dialog (`/api/roots/change-location`, `roots_location.change_location`). It was kept as it is, with the dialog's Verify and Change back for the next deletion. | open |
| B-5 | 2026-10-11 | low: the Verify half of the deletion (Verify dialog, `tagpup.jobs.verifying`, the full run in `roots_verify`, `/api/roots/verify`, `/verify-cancel`, `/change-back`, `Machine.change_back`) was not done: with the adoption half it exceeds the 1,200-line branch size. `roots_verify.reach/verify` (the sample) must stay: `change_location`'s dry run uses it. | open |

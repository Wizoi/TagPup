# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: migration 33: DECISIONS 2026-10-15 ('ANSWERS TO THE HEALTH ASSESSMENTS', item 4) says migration 33 drops `faces_pending` AND 'the unused `pruned` status'; the brief (and the decision row it cites) kept the `changes.status` CHECK as it is, since dropping `pruned` rebuilds the journal's table for nothing. Built as briefed (DATABASE.md says the CHECK stays); the DECISIONS row should be read or amended to match. | open |
| B-2 | 2026-10-15 | low: migration 33 is DESTRUCTIVE, so `journal.schema_gap_blocker` refuses undo of every change of ROWS made at schema 32 once it runs (it drops a table no change can name and an index; the gap rule does not look at what a destructive step touched). Counted read-only 2026-10-15: 0 such applied changes in each of the three libraries (their changes of photo files at 32, 27/2/0, stay undoable). A row change made between now and the install loses undo at the install; said in DATABASE.md, not changed in the journal. | open |
| B-3 | 2026-10-15 | low: the Data owner's charter (`.claude/agents/tagpup-owner-data.md`, Scope, 'State on 2026-10-15') says the next migration is 33 and none is in flight; with this branch merged 33 is in flight until it has run on the three libraries. Not this worker's file. | open |

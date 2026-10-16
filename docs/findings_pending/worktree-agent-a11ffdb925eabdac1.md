# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: Data/People: the journal's key text of a face is spelled three times: `journal._key_text((face_id,))`, `store/removals._key` and `store/face_batches._key` (each `json.dumps([face_id])`), and both stores query `change_rows.row_key` by it; a change to how the journal writes a key would leave two lookups matching nothing. The owner of the spelling is a question: removals imports nothing of the journal on purpose (face_tags asks it, and the journal imports the face stores), so it needs a home below both, not `journal.key_text`. The Data owner's answer: `schema.row_key(key)`, beside change_rows' CREATE. | fixed in this branch: the three call schema.row_key |
| B-3 | 2026-10-15 | low: Integration: contract row 6's guard was named `tests/test_settings.py`, and the brief's replacement `tests/test_one_answer_to_the_librarys_folders.py` does not hold it either: with `settings.excluded_under` answering `[]` (never asking sync) both pass, and `tests/test_library_roots.py::FoldersNotAddedAreExcluded` fails twice. The charter now names test_library_roots. | fixed in this branch |
| B-4 | 2026-10-15 | low: Data (#663, the rest): `tests/test_mcp_server.py` (~:114) still inserts faces by raw SQL; journal_library.py now makes its faces by the store's writers. test_mcp_server is Integration's file, not touched here. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: People: `PeopleVocabulary.from_rows` (tagpup/core/vocabulary.py) still writes the person-or-group rule a second time beside `person_ids.People` (roots, categories, group tags, the ids of a shared leaf). Building it from People is not a small cut: the class is core and cannot import the store, so `taxonomy.read_people_vocabulary` would hand it People's listing and groups and from_rows would lose its root/category arm, which changes what `extract_refs` gives every caller (indexer, refresh_rows, the photo_people rebuild). Left for a branch of its own with the People owner's review (#1434's last clause). | open |
| B-2 | 2026-10-15 | low: decided (#1432 fix): TagTuner reads `/api/people` twice, as TagPup does: `?include_hidden=1` into `state.people` (who a name is, whether a person exists) and the bare read into `state.offeredPeople` (the datalist), rather than a `hidden` flag on each record; one more request when the page opens. `tuner-hidden-people-exist.test.mjs` failed on the old page (the hidden person was offered). | fixed: this branch |
| B-3 | 2026-10-15 | low: page tests: `FakeServer.on("/api/people", ...)` matched by substring, so it also answered `/api/people-with-counts`, `/api/people-faces` and the rest wherever no earlier route did. The harness takes a RegExp now (`PEOPLE_ROUTE`) and every stub of the people that holds someone uses it; the 60 or so `.on("/api/people", [])` stubs (a library with nobody, a real answer) were left as they are. | open |

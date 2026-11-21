# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-21 | low: the page tests' fake replies of POST /api/photos/write op `tags` are still spelled by hand, with no fixture written by the real route (the sibling of #3296 for the tag queue): scenario-editing-a-photo-on-the-phone.test.mjs `phone()` (the 409 `changed_on_disk` of a tag change naming photo 1 at an old path, and the 200 `{success, written}` with no envelope), scenario-taking-tags-off-the-open-photo.test.mjs `refuseWrite` (409) and its resolve `{success, written}`; also shifting-a-folders-date-taken.test.mjs "a shift refused before any write" builds its 409 as `{success, error}` alone. A `tags_replies.json` from test_a_write_through_the_one_entry_point.py (its renamed-since chunk already makes the 409) would hold them to the route. user action: tagging a photo renamed in another tab; a Time Shift while names are being given from tags | open |

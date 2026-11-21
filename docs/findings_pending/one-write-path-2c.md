# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-21 | wrong: POST /api/photos/write had no catch-all: a `set` whose read raised (a clip's share stopping while its QuickTime headers are read, `field_values.NotAnswered`; a header past every date, `Unreadable`) went out of the route, and Flask answered a page of HTML the page cannot read, where /api/photo/save-metadata had answered 500 with the sentence. Fixed in branch one-write-path-2c (tagpup_routes.photos_write answers it 500 with its sentence, logged); the moved tests test_a_clip_dated_through_the_journal (share stops answering; header holds a time no date holds) fail without it. user action: Save a clip's Date Taken on a share that sleeps | fixed in branch |
| B-2 | 2026-11-21 | low: a save of a photo whose file is gone answers differently on POST /api/photos/write than on save-metadata: the page names the photo's row id, the entry point passes the path's 400 through to the write (tagpup_routes.photos_write: an id is answered under the lock), and the save's stamp check refuses it 409 "This photo changed on disk since you opened it: reload it first.", where save-metadata answered 400 "Invalid file path". Kept as the moved scenario's expectation (test_a_share_asleep, APhotoOnAShareThatDoesNotAnswerIsRefusedAsAway, GONE), not bent; the owner sees "reload it first", which a reload answers. user action: delete a photo in Explorer while its panel is open, then save it | open |

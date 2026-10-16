# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: `person_ids.Directory.of_name` and `of_reference`'s bare-name arm stay (#1365 listed them): 98 saved Suggest chips in kr-track hold a person as a bare name that only `of_name` finds (601 chips there; 0 in the other two libraries; counted read-only 2026-10-15). The suggester writes the tag path now (`suggester.offered`: `item["tag"]`), so the arm goes once those saved suggestions are made again or cleared. | open |
| B-2 | 2026-10-15 | low: person-or-group is still decided three times (#1364): `person_ids.People` (a has_face node with a node under it), `PeopleVocabulary.from_rows` (`group_tags` plus the `categories` it works out) and the page's `namesAPerson`. Not a small cut: `from_rows` also builds `by_keyword` and `ids` from the same rows, and the page reads neither; making both take `person_ids.People` changes every reader of a PeopleVocabulary. | open |
| B-3 | 2026-10-15 | low: `person_ids.wire` stays where a caller may pass a typed name as well as an id or a Ref (`faces.who_is`, `face_people.Filer.tag`, `made`, `shown`, `person_tags`, `untag_plan`); `key_of` and `wire_key` are gone. Collapsing `wire` means every caller of those six passes the id, and a typed name is resolved at the route. | open |
| B-4 | 2026-10-15 | low: `tagpup/core/fields.py` (Files) still asks ExifTool for XMP PersonInImage and RegionName (`METADATA_FIELDS`, `LIST_FIELDS`), so `raw_metadata` keeps them although nothing reads them as people since the reader went (owner, 2026-10-15). 0 photos in the three libraries hold either. Dropping them from the read changes what every row's `raw_metadata` holds: Files' call. | open |
| B-5 | 2026-10-15 | low: `taxonomy.ensure_node`'s `grown` arm (a name that is a group's at the top) and `faces._target`'s "no person can be made" raise are unreachable from the one caller (`person_ids.target` raises GroupNotPerson first); kept, since each stands between a typed name and a has_face write on a group. Delete with a test that the refusal is at the source, if wanted. | open |

# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-26 | low: a Date Taken save is still refused bare while its folder indexes: `tagpup/services/tagging.py` `_differs_from_base` compares the tags and title only, so `web/common/photo-write.js` `postOrSendAgain` does not send again a save naming `date_taken` (the panel's Date Taken, `web/tagpup/date-taken.js`, left on `postPhotoMetadata`; a phone Save with a date typed), which could otherwise write over another program's date. Sending them again needs the date in the base the page sends and the server compares: a server change, the owner's or the main session's to choose. user action: change a photo's Date Taken in a folder while the index bakes it. (review round two of cut B, docs/plans/working-on-a-new-folder.md) | open |

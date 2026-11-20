# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 tag reads vs pending changes | low: a library view's photo read while its removal is out is held against the replies only until the read answers (pending.js readHeld); openLibraryPhoto then asks to leave the open photo (leavePhotoThen) before it places the record, and a reply that lands while that question is open reaches no record: the photo shows the tag taken off. The one place to own it is still readHeld (held until placed). user action: in a library view take a pill off photo A, open B and type in its title, click A, and answer Discard after the write has landed. | open |
| B-2 | 2026-11-19 tag reads vs pending changes | low: a folder read again (Refresh: folder.js scanFolder with forceRefresh, or edits.js photoChangedOnDisk's rescan) that is sent while a tag write is out and answered after its reply puts in the page the records from before the write; nothing corrects them. The one place to own it: pending.js readHeld, given the scan's records. user action: take a pill off and press Refresh as the write lands. | open |
| B-3 | 2026-11-19 tag reads vs pending changes | low: a library view's selection chips are counted by the server (tally.js) and follow the tag changes waiting only once a write is answered (takeReply), not at the click as a folder's now do; showing a waiting change there needs the server to know the map (docs/plans/server-side-queue-review.md, decision B). user action: in a library view select photos and take a tag off the selection; look at its chips while the write is out. | open |

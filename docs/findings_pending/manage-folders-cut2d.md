# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-19 review of Manage Folders cut 2 | low: gear > Manage folders > add a folder, while the page shows that unindexed folder (Just look): the gear's `queued` only asks membership again (checkFolderMembership), so the page does not poll index-status and rescan when the index finishes; membership.js's own Add does. user action: open an unindexed folder in TagPup, Just look, gear > Manage folders > add it. (web/tagpup/gear.js) | fixed |
| B-2 | 2026-11-19 review of Manage Folders cut 2 | low: the removal list's cap of 100 is written twice, SHOWN_MOST (web/common/folder-manager.js) and FOLDER_STATES_MOST (tagpup/services/photos.py), with no guard; and a failed on-disk look only goes to console.error, the dialog says nothing. user action: type in the removal filter while the share does not answer. | fixed |
| B-3 | 2026-11-19 review of Manage Folders cut 2 | low: close the dialog while a removal is on its way, reopen: removeChosen's stale branch does not reload, so the reopened list still lists the removed folder. user action: Remove, Close, open Manage folders again before the reply. (web/common/folder-manager.js) | fixed |
| B-4 | 2026-11-19 review of Manage Folders cut 2 | low: lookSoon excludes only paths already answered, so keystrokes spaced over 250 ms with a slow share queue overlapping looks at the same folders. user action: type in the removal filter slowly while the share is slow. (web/common/folder-manager.js) | fixed |
| B-5 | 2026-11-19 review of Manage Folders cut 2 | low: docs/SPEC_TAGPUP_GUI.md does not name the gear's Manage folders... or web/common/folder-manager.js, still says "Remove Folder" twice, and lacks own_indexed in the /api/folder/subfolders row. user action: read the spec against the page. | fixed |

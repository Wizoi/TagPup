# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: `state.moves.banner.kind` has one value, `'offer'`, since the folder view's check link was folded into Refresh view (the owner, 2026-11-13): `web/tagpup/sync-state.js` `renderSyncInfo` still tests `banner.kind === 'offer'` and `library-banner.js` `addThem` likewise; the kind could go and both read `Boolean(state.moves.banner)`. Left in the branch because sync-state.js was not this worker's file. | open |
| B-2 | 2026-11-13 | low: decided rather than copied: Refresh view's walk of a large folder view (over 1,000 photos with subfolders) also runs after a Delete of the selection, since `photosDeleted` refreshes through the same `refreshLibraryView` -> `checkNotHeld(lib, { refreshed: true })`; the server walks a folder once however often it is asked (`membership_checked`), nothing is shown unless the disk holds photos the library does not, so one meaning of "refreshed" was kept instead of a second flag. Close `decided:` if the owner agrees. | open |

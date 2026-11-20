# Findings

This file records every review finding, bug report and known problem: what was found, and where it stands. A finding that exists only in a conversation gets lost, and a decision that is not written down gets made again.

**This file holds the open findings.** A closed row (status starting `fixed`, `built`, `decided`, `closed`,
`accepted`, ...) is moved, byte for byte and with its number, to
`history/findings_closed_0001-0250.md` and so on ([index](history/README.md)) -- one file per 250 numbers.
A reference such as "#726" is found by its number there. The owner's decisions are collected in
[DECISIONS.md](DECISIONS.md); the rules that came out of findings are in [INVARIANTS.md](INVARIANTS.md).

- **Add a row when something is found**, before fixing it.
- **Severity** *(owner, 2026-10-09; CLAUDE.md, "Keeping the project small")*: start the Finding with `data:` (can lose or
  corrupt data), `wrong:` (a wrong result the owner sees) or `low:`. Only `data` and `wrong` block a merge; `low` rows are
  batched. Older rows carry none.
- **Status** is one of:
  - `open`
  - `fixed`: give the commit, by its hash or, for a row added in the same commit, its subject.
  - `not reproduced`: say what was tried.
  - `left as is`: say why.
  - `tabled`: say until when.
  - `planned`: give the phase in [ARCHITECTURE.md](ARCHITECTURE.md).
  - `watching`: nothing to do until it is reported again or reproduced *(owner, 2026-11-02)*. The row is moved by
    `tools/add_findings.py --watch N "why"` to [watching.md](watching.md): not counted open, not brought up in a report;
    **read watching.md when an issue is reported**, to see whether it is one of those. A reproduced one goes back here by hand.
- **Decisions the owner makes** are marked *(owner, date)*. Don't reopen them without a new fact.
- **Never put real names here.** The library is photographs of real people.
- **Numbering is the main session's.** A worker does not number rows into this file (two branches that both do collide on
  merge). It writes `| ? |` rows with `tools/add_findings.py rows.md --branch-file docs/findings_pending/<branch>.md`
  (provisional `B-1`, `B-2`; never write a `B-n` into code or a commit message) and the main session, merging the branch,
  runs `tools/add_findings.py --take docs/findings_pending/<branch>.md`, which numbers the rows after the highest number
  in this file **and the archives**, and removes the file. `tools/add_findings.py --status N "fixed: abc"` sets a status
  (in the archive if the row is there); `--archive` moves the rows whose status closed.

Findings fixed before 2026-09-23 are in the git log (`git log --grep="^fix"`). This file starts with what was still open or decided on that day.

| # | Found | Finding | Status |
|---|---|---|---|
| 3094 | 2026-11-16 | low: Tag writes (cut 3a-2): each chunk rebuilds the whole file list and re-serialises the whole folder to localStorage (pending.js:207, 211); once per action is enough. user action: Add to all in a 400-photo folder (16 rebuilds). live: 18 folders over 200 photos. | decided: owner 2026-11-19: no measured slowness and none felt on a large folder; a speed claim needs a browser timing first (docs/DEVELOPMENT.md Performance work); reopens if Add-to-all on a big folder ever feels slow |
| 3106 | 2026-11-16 | low: Tag writes (cut 3a-2 follow-up 2): takeReply sends an item to the kept copy of `item.folder`, the folder open when the change was pended, not the photo's folder; an Undo pressed in another folder or a view writes a photo of folder A and A's kept copy keeps the old stamp and tags, so a save from it within 30 minutes is refused 409 (the refusal re-reads the folder). user action: Ctrl+D carry a tag in A, open B, Ctrl+Z, open A again within 30 minutes, save that photo. | fixed: f6829c72 (takeReply and pagehide name folderOf(photo path); test failed on the old code) |
| 3124 | 2026-11-17 | low: `folderMayDelete` waits for the OPEN photo's write (`leavePhotoThen` -> `openPhotoWrite`) and for pending.js's map (`pendingAmong`), but not for a single save queued for a selected photo that is no longer open (`state.photoWrites[key]`, `queuePhotoWrite`). user action: in a folder, add a tag to photo A and Save, click photo B, select A and B, Delete before the save answers: the server's lock orders them, so the save either lands on a file about to go or fails "file gone" after it; nothing lost, a wrong error shown. Fix is `pendingAmong` (or a sibling) also counting `state.photoWrites` keys among the paths. | fixed: 122d9209 (deleteWaits refuses, naming the save; test failed on the old code) |
| 3159 | 2026-11-19 review of phone cut 3b | low: the one-write-at-a-time chain is written twice: edits.js queuePhotoWrite and mobile-edit.js inTurn. Move one `inTurn(state,key,job)` into web/common/photo-write.js, queuePhotoWrite calls it. live: 2 copies | fixed: 6565ff9b (one inTurn in web/common/photo-write.js; contract: rejects with the job's failure, queuePhotoWrite turns it into false) |
| 3180 | 2026-11-19 review of the details panel | low (possible): photo-path.js:297-302 clicking the folder name of a photo opened from Organize replaces Organize with a recursive library view of that folder (an empty view for a Just-look folder) instead of going back to the Organize grid. user action: Organize -> open a folder -> open a photo -> click the folder's name | open |
| 3220 | 2026-11-19 | low: Tag writes (cut 3b): not built from docs/plans/one-queue-for-tag-changes.md: the tag part of edits.js `writeDetailEdits` (a tag or person typed in the open photo's panel) still writes through /api/photo/save-metadata with the photo's whole tag list and its `base`, not through pending.js; a save and a pill removal of one photo are two kinds of write in the one queue, kept apart only by the queue's order (V20 "a removal and a save around it"). user action: a tag typed and saved on the open photo while one of its pills' removals waits. | decided: owner 2026-11-19: waits for the server-queue decision; option A's one entry point replaces both routes, and a typed tag's base (the file-changed-on-disk refusal that save-metadata carries and bulk-tags lacks) is settled there (docs/plans/server-side-queue-review.md, question 9) |

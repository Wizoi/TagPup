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
| 3272 | 2026-11-20 | low: a Delete of a photo whose file is there but cannot be read (shares.CANNOT_READ) tells it NOTHING_DELETED_NO_LOOK, "The share did not answer", though the share answered (photos.delete_planned gives one sentence to UNANSWERED and CANNOT_READ; as before cut 1b); user action: Delete a selection holding a photo the account lost rights to | open |
| 3279 | 2026-11-20 | low: the bulk Delete (bulk_edit._delete -> photos.delete_in_chunks) still plans its DELETE change without a settle first; #3263 settles for the single Delete only, so one path settles and the other does not (which single place owns it: plan_deletes has no exiftool_path, or bulk_edit._delete calls file_changes.settle with the path the bulk route has); user action: bulk Delete of a photo a killed write left half done (the Bin keeps the file, the next settle logs a conflict) | open |
| 3280 | 2026-11-20 | low: photos.delete does not ask file_changes.unsettled(photo_path) as _write_fields, rename and convert do; user action: Delete a photo during the start settle of a killed bulk write (the Bin has the file, the settle thread meets it gone and logs a conflict); fix is one refuse after the settle plus a scenario in tests/test_a_delete_cut_short.py | open |
| 3281 | 2026-11-20 | wrong: /api/folder/time-shift on one-write-path-2b answers from `_folder_photos` (FolderCache.get, which drops the map because the shift's row updates bump the photos generation) and rescans after a successful write: a listing that does not answer (shares.LISTING_WAIT) gives `updated_photos: []`, so rename.js empties the folder view while saying "Time shift applied to N photo(s)", and a rescan that raises answers 500 for a journaled write; trunk answered from `cache.snapshot` or the patched pre-write map; also one cold scan per shift and `_told`'s shift `cache.move` is dead work for a held folder; user action: Time Shift on a held folder whose share is slow | open |
| 3282 | 2026-11-20 | low: which single place decides whether a write of photo files holds the lock across its chunks: tagpup_routes._write (always, batch_exclusively) and photo_writes.write (`named`) both do, and photo_writes' module docstring says a folder's Time Shift lets a waiter in, which B-1 and the spec deny; single place: the caller holds, photo_writes.write takes no lock; user action: any write through /api/photos/write | open |
| 3283 | 2026-11-20 | low: no contract-table row names the one entry point (pages and bulk_edit -> photo_writes.write -> tagging/photos); proposed text in the Integration owner's 2b review; also tagging.change_tags has no production caller since 2b (live: 0 callers under tagpup/, about 45 test call sites); live: every page write | decided: no live instance, not built |
| 3284 | 2026-11-20 | low: /api/photos/write op shift answers written {mtime, size} with no taken and drops group (shift_date_taken takes none), so cut 3's page cannot patch its records as it does for a set, and a chunked shift would be several History lines; decide in the cut-3 brief; user action: cut 3 | open |
| 3285 | 2026-11-20 | low: _set_change parses an entry's keys and id itself beside _entries, the parser _tag_changes and _shift_change share; user action: a save | open |
| 3286 | 2026-11-20 | low: op shift is refused while names are given (GUARDED) where DECISIONS 2026-10-15 (4) allows a time shift then; the folder route stays ALLOWED until 2c; a dated DECISIONS row when cut 3 moves the page; live: 0 time shifts journaled | decided: no live instance, not built |

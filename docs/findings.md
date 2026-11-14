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
| 2974 | 2026-11-13 | low: a Date Taken save of a clip held open by another program creates a `changes` row before the file is refused (seen by the worker of #2969/#2970 in tests/test_a_clip_dated_through_the_journal.py, which dropped its "nothing is journaled" check; true before that fix). The row's fate (settled as nothing written, or left in History as an undoable change that wrote nothing) is not established: check with the Data owner, count such rows read-only in the three libraries' `changes` (status and file counts), and either make the refusal leave no row or record that History shows it. | open |
| 2987 | 2026-11-13 | low: People: DECISIONS 2026-11-13 "PEOPLE ON CLIPS ARE TRANSIENT" says frames ffmpeg does not give leave the clip its tags with `faces_unread`, "not an error the next run would retry"; since the fix of the moment's NoFrames (no ffmpeg, its deadline, a share away) that holds only for the file's (`NoFrames.lasting`): a clip whose frames are not given for the moment, or whose every frame the face model failed on, is failed and retried, and with no ffmpeg a clip with a kept vector is no longer offered its tags that run (0 live instances: ffmpeg is found on the owner's machine, every clip of photo_index holds a vector). The row's sentence needs the main session's amendment. | open |
| 2988 | 2026-11-13 | low: Files: `lock_owners.DENIED_WORDS` keeps ExifTool's 'Error renaming temporary file' as a held file's words, but no write of the code can make it: every ExifTool write is `-o` to a new file beside the photo (`field_values.py` 487, 489; never -overwrite_original), and the move over the photo is `field_values._landed`'s `os.replace`, whose OSError carries a winerror. Counted read-only 2026-11-13: change_files notes holding it 0 of 139 noted rows in photo_index, 0 in kr-track and renton_parkrun (no note at all there); job_runs notes 0. Not proof alone: a write withdrawn on failure (`on_failure="withdraw"`) leaves no note. Two tests still feed the wording (`test_lock_owners` `test_exiftools_words_count_as_its_numbers_do`, `test_a_journaled_write_names_the_holder`). Delete the wording, and 'permission denied'/'access is denied' with it if their numberless source is likewise none, or name the source. | open |
| 2989 | 2026-11-13 | low: Files: `names.rename_all` logs a failed Smart Rename (`logger.error("Smart Rename failed and was undone: %s", why)`) and raises RenameFailed with the OSError's own text, which on Windows names both full paths (WinError 32 '...: <old> -> <tmp_rename_...>'); lock_owners.explain's warning is path-free (#2973) but this site's line is not. Seen in the test output of `tests/test_lock_owners.py` (a temp folder's paths). | open |
| 2990 | 2026-11-13 | low: Files: decided while moving recycle_bin to lock_owners.explain: `send_to_recycle_bin` raises `ctypes.WinError(res)` when SHFileOperationW answers 32 or 33 (was a bare False), and `_delete_file` wraps any OSError of the send as 'Failed to move file to Recycle Bin (<explain>)'; `recycle_through_this_pc` explains the copy's failure (its OSError's own filename, else the original), the Bin's refusal of the copy (asked before the copy is taken away) and the original's delete. `tests/test_delete_a_photo.py` `DeletingAPhoto` now expects the wrapped sentence for a stand-in's OSError. Shown failing on the trunk. | decided: every delete step that can meet a held file says who (owner, 2026-11-13) |
| 2991 | 2026-11-13 | low: Files: `lock_owners._who` (343-346) appends NOT_ON_THIS_PC for every held failure on a share with no local holder, now including access-denied writes with `writable=True` (file_changes `_after_failure`, file_only `_write_one`); on the trunk it followed only a sharing violation (32/33). An os.replace denied by the share's permissions now gets a sentence asserting a holder (#2971 'no sentence that cannot be true'). Keep NOT_ON_THIS_PC to `number in SHARING`, or cite an owner decision. | open |
| 2992 | 2026-11-13 | low: Files: `file_only._write_one` (171, 174) calls `file_changes.writable(path)` (an unbounded `os.stat`) on every failed Just-look write before `looks_held` decides the error is access denied; on a share that stopped answering that is one more unbounded look per photo outside `shares.bounded`. Ask lazily (only for a denied error) or route through `shares`. | open |

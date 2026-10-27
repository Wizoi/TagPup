# Plan: the indexer keeps a photo file and the index in step

Owner's rule (DECISIONS 2026-10-27): the vectors and faces baked into a photo file are the data; the index holds a copy;
the indexer and sync keep the two in step on their own. `bake-photos` and `face-pass` were a one-time backfill, and the
owner never runs a bake again. A GPU client only saves other machines the computing.

Reviewed 2026-10-27 by the Files, Data, Integration and Views/AI owners (read-only). Counts below were taken read-only
that day: photos 59,524 / 1,635 / 1,309 (photo_index / kr-track / renton_parkrun); photo fingerprints 48,799 / 1,634 /
1,309; vectors whose stamp differs from the file's 74 / 0 / 0; photos with a current vector and no fingerprint 10,724 / 0 / 0.
No migration is needed (all three at schema 34).

## What went wrong

PHASE6_DESIGN 3.3 made the write side "a CLI command like embed-photos" and cut 4 (the no-card queue) "only if wanted";
every scenario in section 5 was about the backfill. The owner's goal ("I add or edit a photo and run nothing; its file and
its row agree") was never written as a scenario. Today: the indexer only reads a bake; nothing writes one; a file changed
outside the app is stuck (sync re-reads its row and leaves the vector, so the indexer skips it and the bake plan says
"changed since its vector, index it again", which can never succeed: 74 photos in photo_index).

## The one rule

A vector is current for a picture when its fingerprint row (`photo_fingerprints`) equals the picture's fingerprint.
(mtime, size) only spares the decode. One function, `photo_fingerprints.same_picture`, answers it; the indexer, sync and
baking all call it. A file's bake is current when its keys equal the library's and its Fingerprint equals the row.
Sync decides and queues (it still writes no file); the index job acts (adopts, computes, writes).

Per photo: file bake current, row not -> the row adopts it, no GPU. Row current, file lacks a current bake -> the file is
written from the row (journaled, one change per folder). Both current -> nothing. Picture changed (different fingerprint)
-> computed again, written to both. A metadata-only write (XMP, IPTC, keywords) never makes a vector stale.

## Scenarios (what the owner does -> what they must see; test file; rule owner)

1. **I never run a bake command.** A photo copied in goes watcher -> sync -> index -> file baked; no prompt asks the owner to run anything. NEW `test_a_folder_keeps_its_bake_on_its_own.py` (Integration); cuts 2-3.
2. **A new photo or folder is added.** Indexed; its file holds a bake matching its row; a second run writes nothing; History shows one "bake photos" change per folder. `test_an_index_reads_a_bake.py` (F11) lifted; `baking.plan_photo`; cut 2.
3. **XMP or keywords written by another program** (the three photos whose corrupt XMP was cleared). Nothing is recomputed, no GPU; the stuck state is gone; the bake is written back from the row. `test_sync.py` (F4) + F11; `photo_fingerprints.same_picture`; cuts 1-2.
4. **A picture is cropped in an editor.** Fingerprint differs: vectors computed again, file re-baked; old boxes are never kept on a new picture (faces of a changed picture stay People's existing rule, #885 #336). F11; cuts 1-2.
5. **Keywords saved rapidly in the app.** Each save restamps the vector; nothing queued, no bake rewritten. `test_tag_writes_keep_the_index_true`; unchanged.
6. **A folder is copied to a client with no GPU.** A current bake is adopted with no card; a photo without one is worked out on the processor (today's behaviour). Never a stale bake taken as current. NEW `test_a_machine_without_a_card.py`; cut 4.
7. **Two libraries hold one folder.** Each adopts the other's bake; neither rewrites a bake that is current by keys + fingerprint because its face rows differ. `test_scenario_two_libraries_share_a_folder` (F5); cut 2.
8. **A keyword save and an index bake hit one file together.** One meets a conflict, the file is not overwritten, the keywords are kept, the next run settles it. `test_file_changes_one_at_a_time` (F1); no change to the owner.
9. **The file is edited in Explorer between plan and write.** A conflict, never overwritten. `test_bulk_writes_start_from_the_file` (F3).
10. **The share goes to sleep mid-folder.** Files already written stay done; the rest are untouched; nothing is called missing; the next index resumes. A share that is slow to wake (first answer after idle took 1.3 s, measured 2026-10-27, where the share's deadline is 1.0 s and it is then "away for 30 s") must not make the whole library unreadable: the 2026-10-27 kr-track backfill run counted all 1,634 photos "could not be reached" in seconds, wrote nothing, and exited 1. The run waits out the away period and retries a folder before it counts a photo unreachable. `test_a_share_asleep` (F7); `shares.bounded`; cut 2.
11. **The app is killed between a file write and its row.** The next start settles it; the fingerprint row follows in the done transaction. `test_a_bake_is_written_folder_by_folder`; cut 2.
12. **An update is installed mid-run.** The drain waits for the index's folder; nothing half-written. `test_server_drain`, `test_one_library_three_programs` (I2/I3); cut 2.
13. **I delete a face.** The next index writes the file from the index; the face does not come back. `test_a_bake_is_planned_from_the_library.py`; cut 2 (rule: the file gives to the row only where the row has no current copy).
14. **A bake is not an operation I undo.** A bake stamps the index's values on the file; if the index later changes (a face deleted, a keyword, a rotation), the next index bakes the correct values over it. History lists no bake and offers no undo of one. `test_undo_after_every_later_kind` (D2) loses its bake rows; cut 2.
15. **I install this version with the backlog still open** (59k / 1.6k / 1.3k unbaked, about 223 never-examined photos, the 74 stuck, the 30 Pixel JPEGs, the 3 cleared-XMP photos). Sync and the index bring every library in step on their own, paced, resumable, the card taken one turn at a time; I run no command and see progress in Activity. NEW, in the cut-1 scenario file; cut 3.
16. **The model changes.** Old-key bakes count as other models; vectors computed and files re-baked (decision below). `test_switching_a_librarys_photo_model.py` (V12); cut 2.

## Cuts (each <= 1,200 lines, one concern, trunk whole after each)

1. **The fingerprint is a vector's currency (~350).** `same_picture`; `search.current` also checks the vector; the indexer keeps and restamps on a matching fingerprint with no GPU; sync queues a changed file whose vector stamp no longer describes it (same path new files take); `plan_photo` compares keys + fingerprint. Deletes `CHANGED`, `size_changed`, sync's "left as they are / fix by hand" paragraph. Fixes the 74 and the three photos. Reviewers: Files, Data.
2. **The indexer writes the bake (~600).** After each folder's batch is recorded and the card turn is released, every photo `plan_photo` marks ToBake is written through `file_changes.write_fields`, one "bake photos" change per folder; the indexer hands its decode fingerprint and vectors over with no second decode. Reviewers: Files, People, Integration (contract rows 15, 16, 19, 28, 66; drain).
3. **The one-time catch-up (~400).** (Owner, 2026-10-27: an indexed photo that was never baked is not a supported steady state, so no permanent way to find one is built; an outside edit changes the file's stamp and takes the ordinary changed-file path.) After install, once per library, every photo the backlog left unbaked (the backfill's misses, the never-examined photos, the three cleared-XMP photos, the 30 Pixel JPEGs) is brought in step by that same path: paced, resumable, the card held one folder at a time, no command from the owner. The index treats a never-examined photo as a photo to detect (replaces `face-pass`). Cut 5 deletes this with `bake-photos`. Reviewers: Files, Data, Integration.
4. **A client with no GPU (~400).** Per the decision below. Reviewers: Views/AI, Integration.
5. **DECIDED (owner, 2026-10-27): delete `bake-photos` and `face-pass` after cut 3.**

Until cut 3 is installed there is nothing for the owner to run. The bake already running may be left or stopped.

## Decisions that are the owner's (recommendation first)

1. **DECIDED (owner, 2026-10-27): compute on the processor, as today.** A client with no GPU adopts a current bake with no card and works a photo out on the processor otherwise; no queue.
2. **DECIDED (owner, 2026-10-27): a bake is never undone.** It is not an operation: it is the index's values stamped on the file, and a later change is baked over it. No History row, no undo; the crash-safety record a write needs (settling a half-written file at the next start) stays as an internal record, not a change the owner sees. Supersedes PHASE6_DESIGN 6.3 ("journaled and undoable") for bakes written from now on; the 2,908 bake changes already in the three journals stay as they are.
3. **DECIDED (owner, 2026-10-27): the most recent change wins, and the fingerprint is how a change is told.** A picture that differs is a change of the file; a face deleted in TagPup is a change of the index, newer than the file's bake, so the file is written from the index and the face does not come back. Cut 2 settles how 'newer' is told for an index-side change (the face's change time against the bake's write).
4. **DECIDED (owner, 2026-10-27): nothing to decide.** A model switch re-embeds every file, and any GPU reindex re-bakes what it computed; the bake follows automatically.
5. **DECIDED (owner, 2026-10-27): delete `bake-photos` and `face-pass` after cut 3.**

Low: the indexer's closing line says "worked out on the graphics card" for a processor-made vector (`tagpup_cli.py:512`); fixed in cut 2.

Counted read-only 2026-10-27: the three cleared-XMP photos of photo_index (ids 39695, 39863, 40018) have NO fingerprint row. So cut 1's rule for a vector is: stamp matches the file -> trusted; stamp differs and a fingerprint row exists -> compare, same hash keeps and restamps with no GPU; stamp differs and no row -> computed again. The GPU is used for at most the 74 photos whose vector stamp differs now (3 of them are these).

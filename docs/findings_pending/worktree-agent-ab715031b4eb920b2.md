# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

Statuses to set at the merge (`tools/add_findings.py --status N "..."`):
- #1921: `fixed: file_changes: a share that stops answering in a write stops it, as a Ctrl+C does, instead of marking the file under way and the rest conflicts` (its scenario tests failed on the trunk with the false conflicts first)
- #1922: `fixed: launcher: the sentence before an update names bake-photos --apply among the CLI writes to finish first`

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | low: a single-photo save (tagging.save_photo through file_changes.write_fields with the caller's own ExifToolSession, not StaysDead) whose write times out reads the file again after the failure (_after_failure), which restarts ExifTool and can wait a second deadline (300 s) on a share still away before the share-stopped refusal. A bulk write waits one (StaysDead). User action: Save on a photo of a share that stops answering mid-write. Found while fixing #1921; not changed in cut 3a (the save keeps its session for the read before it). | open |
| B-2 | 2026-10-25 | wrong: the file a share-stop leaves `writing` becomes a false conflict at the next journaled write while the share is still away, after a 300 s wait inside that unrelated write: `_write_fields` settles first, and `settle -> _finish` reads the file in a plain session without asking whether its share answers, then `_held_or_conflict` marks it `conflict: could not be read` and applies the change. The refusal also promised "settled the next time the library is opened". Reviewer's scenario: 60-photo edit, stall at file 36, share still away, another photo's caption saved. | open |
| B-3 | 2026-10-25 | low: the page does not recognise a share stop: web/tagpup/bulk-run.js finds a stopped batch only by `why.startsWith('Stopped as asked')`; the share-stop refusal starts otherwise, so the strip shows the ended sentence and one identical error line per photo not written. | open |
| B-4 | 2026-10-25 | low: the share-stop sentences name a cause the code did not establish: field_values.NotAnswered is also raised when ExifTool exits in the middle of a command, not only for a share gone away. | open |
| B-5 | 2026-10-25 | low: the per-photo error of a share stop says "as it was written" also for a file never begun (the stall came in the read before its write). | open |

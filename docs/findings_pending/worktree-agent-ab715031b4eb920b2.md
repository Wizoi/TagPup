# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

Statuses to set at the merge (`tools/add_findings.py --status N "..."`):
- #1921: `fixed: file_changes: a share that stops answering in a write stops it, as a Ctrl+C does, instead of marking the file under way and the rest conflicts` (its scenario tests failed on the trunk with the false conflicts first)
- #1922: `fixed: launcher: the sentence before an update names bake-photos --apply among the CLI writes to finish first`

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-25 | low: a single-photo save (tagging.save_photo through file_changes.write_fields with the caller's own ExifToolSession, not StaysDead) whose write times out reads the file again after the failure (_after_failure), which restarts ExifTool and can wait a second deadline (300 s) on a share still away before the share-stopped refusal. A bulk write waits one (StaysDead). User action: Save on a photo of a share that stops answering mid-write. Found while fixing #1921; not changed in cut 3a (the save keeps its session for the read before it). | open |

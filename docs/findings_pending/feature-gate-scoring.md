# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-20 | low: the gate's skipped, too-small and failed counts are kept neither by the library nor by its journal, only by the face-pass run's own line; keeping them would be schema work, which DECISIONS 2026-10-15 holds back. Review of slice C. | decided: by design (slice C, 2026-10-20): the counts are the run's line, a stored count is schema work held off by DECISIONS 2026-10-15 |
| B-2 | 2026-10-20 | low: a read-only connection leaves an empty -wal and -shm file beside a WAL library, so the gate's scenario test checks the library file's bytes and the log's size instead of the folder's file list. Review of slice C. | decided: by design (slice C, 2026-10-20): the files are SQLite's, as every `db.readonly_uri` read leaves them; the test checks the library's bytes and the log size |
| B-3 | 2026-10-20 | low: the gate's size bands use the old MTCNN box of each matched face, not SCRFD's, so they are a lower bound on the new detector's sizes. Review of slice C. | decided: by design (slice C, 2026-10-20): the gate scores faces holding both vectors, whose stored box is MTCNN's; the note in docs/AI_PIPELINE.md says so |

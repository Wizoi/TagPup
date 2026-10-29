# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-29 B2 part 2 | low: one spelling of a recorded time could not live in `store/job_runs` as the brief named it: `files/shares.py` (files layer) and eight spellings in the store (journal, file journal, roots, added folders, faces detected, schema, suggestions, job runs) cannot import services, and a store module importing job_runs for a time would be a reach-in (file_journal and schema are imported by job_runs). The format is `tagpup.core.dates.RECORDED` (`recorded`, `recorded_seconds`); `job_runs.stamp / seconds` read it and remain what jobs/ and web/ call through services.job_runs. | decided: core/dates owns it, below every layer that records a time; guard in tests/test_rules_have_one_owner.py |
| B-2 | 2026-10-29 B2 part 2 | low: the sweep's note in the brief ("the server stopped while it ran; ... the next index finishes it") is untrue for a CLI run that died or a job that is not an index; the note written is `job_runs.STOPPED`, "its process stopped while it ran; what it wrote is kept; the next run finishes it". B3's sentence mapper words it for the page. | decided: one note true for every run the start sweeps |
| B-3 | 2026-10-29 B2 part 2 | low: a claim that abandons a dead run now forgets the run's owner (owner NULL), as the snapshot's abandon always did: one UPDATE, `job_runs.abandon`. Nothing reads the owner of an abandoned row (`naming_faces.stored_status` asks `process_alive` only for a `running` one). A claim that finds one live holder among several running rows now also abandons the dead ones before refusing (before, it stopped at the first live one). | decided: one shape of an abandoned run |

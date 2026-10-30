# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-30 | low: three Activity buttons read their section beside its poll (More, `timeline.readMore`; Cancel of a batch, `now.cancelBatch`; Unload models now, `server.unloadModels`), so a poll's read in flight that answered after theirs was drawn over the newer answer: More's longer list replaced by the shorter one, a cancelled batch shown running again for a poll (the sequence-guard class). | fixed: e17bad21 (each asks through `touched()`, the section's own poll; activity-page.test.mjs "More while the timeline is being read" and "no module asks a polled section itself" fail on a4b7d300) |
| B-2 | 2026-10-30 | low: the sync, the server and the timeline were one poll of the Activity page (`rest`, web/activity/main.js), so one route failing (a library's timeline unreadable) backed the other two off with it, each said "could not be refreshed" only for itself but asked less and less often. | fixed: e17bad21 (a poll a section; "one route failing is said in its own section" asserts the sync keeps its pace, fails on a4b7d300) |
| B-3 | 2026-10-30 | low: #1913 was closed with a run's end shown in Scheduled jobs "within 30 s, by design (only Now is live)"; P7 of the design review (owner's design) takes the wait away: a run that starts or ends in Now reads Scheduled jobs at once. Decision made here, beside the design's jobs: What happened is read at once too, the same end (one more read a run's start or end). | fixed: e17bad21 (`now.runsOf`, touched('jobs', 'timeline'); "a run ends in Now" fails on a4b7d300) |

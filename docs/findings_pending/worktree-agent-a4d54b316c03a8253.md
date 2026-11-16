# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-16 #3057 fix worker | low: a save refused because its photo is still to be written by a change under way or cut short (file_changes.UNSETTLED, now also a running library view bulk's planned photos) reaches POST /api/photo/save-metadata as result.fail, not result.refuse: the route answers 500 and logs it at ERROR ("Error saving metadata for <path>: ...") though it is an expected refusal; the page shows the sentence all the same ("Not saved: ..." and an alert, edits.js). user action: save a photo in the panel while a library view tag edit of 26+ photos that holds it runs. Fix: the plan loop's unsettled() refusal a Refused (as WRITER_REFUSED is told apart), the route's responses.refused, or recorded by design. | open |

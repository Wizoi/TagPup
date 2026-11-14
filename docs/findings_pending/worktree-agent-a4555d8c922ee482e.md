# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: Files: `clip_patch.same_read` sorts the header texts, so a per-box undo is verified by membership, not box order: a clip whose tracks were swapped since the save would verify as given back. 0 live instances (per the brief). Fix when a real instance appears: compare in box order. | open |
| B-2 | 2026-11-13 | wrong: Web: Edit Date Taken on a clip was refused with CLIP_DATE_UNSPELLED for a time on the minute: a `datetime-local` input with step 1 answers "2024-08-15T09:30" (no seconds when they are zero), and `fields.date_taken_value` passed it on without them, which the clip's QuickTime header conversion does not accept. Found by the route test `test_a_date_on_the_minute_as_the_date_input_sends_it_without_seconds_is_written_to_the_second`, which failed with a 400 first; `date_taken_value` now gives a bare HH:MM its ":00". | fixed: 8b53a905 |

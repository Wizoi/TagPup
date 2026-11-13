# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | wrong: convert: convert_clip told the copy's size by file_changes.fingerprint(target)["size"], an unbounded look: a share asleep as a conversion ends (it says "not found" at once, #1543) answered None and a TypeError went up out of a conversion that was done, so the panel said "The video could not be converted (TypeError)" of a clip that was converted. Found writing the scenario test of #2890. Now the size the copy's row records (_size_recorded); test_its_share_asleep_once_its_original_is_deleted_says_it_converted_and_how_large_its_copy_is failed on the old code first. | fixed: dd1fff90 |
| B-2 | 2026-11-12 | low: convert: a renamed conversion whose original another program changed after its copy was verified (between the carry and the delete) is marked done in silence, the changed original kept beside its copy and the row at the copy: never deleted, as it must not be, but nothing tells the owner, and the next sync indexes the original as a new clip. Pre-existing (the same arm #2890 was in); _original_kept keeps it. A window of seconds; no live instance counted. | open |
| B-3 | 2026-11-12 | low: convert: the refusal sentences keep no full stop of their own (every refusal of convert and clip_convert is a fragment the panel puts after "Could not convert NAME: "); TEMP_LEFT and the stale-temp sentences begin with ". " so what follows a fragment is a sentence of its own with its full stop. Kept as the convention rather than adding a stop to CHANGED, NOT_RENAMED and NOT_DELETED alone (#2884). NOT_DELETED now says the copy "is not put in its place" rather than "was removed". | decided: one convention for every refusal fragment, 675e018e |

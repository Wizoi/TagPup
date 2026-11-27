# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-27 | low: L0c part 1 names a service's Fields parameter `fields`, and six services (baking, convert, file_changes, file_only, metadata_check, tagging) imported the module tagpup.core.fields as `fields`, used inside ten of the 26 functions taking the parameter: a decision made rather than copied, the module is imported there as `core_fields` (relink_photos' spelling), so the name means one thing in each file. Part 2 (the 85 `exiftool_path` functions) meets the same clash in more files and should take the same spelling. live: 0 (code only) | decided: core_fields where a Fields parameter `fields` arrives |
| B-2 | 2026-11-27 | low: after L0c part 1, field_values.ensure_document_id is the one public helper of field_values still taking a raw session (only IdentityWriter calls it in tagpup/); tests call it with sessions built by hand (test_photo_identity, test_minor_errors_in_a_journaled_write, test_a_file_holds_what_exiftool_kept, test_a_photo_is_known_by_its_picture), and 33 test calls in 8 files hand a hand-built or stand-in session to the now private _read, _read_one, _write and _written_once_more. Not in part 1's brief (four helpers named); the conformance suite (T1, section 5) lifts these granular tests. live: 0 (code only) | decided: left to T1, not renamed here |

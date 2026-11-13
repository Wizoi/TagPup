# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: `dates.record_year` falls back to `photo_year(raw_metadata, path, media)` for a record with no "year", but both record builders (`services.photos.page_record`, `services.search._record`) always set "year", so the fallback has no live instance: only `tests/test_metadata_extraction.TestParseYearFromMetadata` and `tests/test_one_photo_whatever_its_spelling` reach it, with records nothing real makes (and `search._record` carries no "media" for its kind). Remove it, and point those tests at `photo_year` with a kind. | open |
| B-2 | 2026-11-13 | low: decision (#2952 fix): `services.photos.page_record` dates a record by `images.kind_of(path)`, its own "media", not by a row's `photos.kind`: a record read from a file has no row, and the index gave every row its kind by that same rule; the two agree on all 65,724 live rows (62,809 photos, 2,915 clips) and the stored `taken`/`year` equal the kind-read rule on every one (counted read-only 2026-11-13), so no rederive is owed. | decided: one rule, the index's |
